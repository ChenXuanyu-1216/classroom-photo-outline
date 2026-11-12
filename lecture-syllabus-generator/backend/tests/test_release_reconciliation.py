"""Contracts at the local-evaluation/cloud-backend reconciliation boundary."""
import hashlib

import pytest
from sqlalchemy import create_engine, inspect, select, text

from test_cloud_uploads import cloud_store
from test_cloud_migration import source, target


def legacy_cloud_database(tmp_path):
    from app.db import Base
    from app.models import SchemaRevision, AppUser
    engine = create_engine('sqlite:///' + (tmp_path / 'legacy-cloud.db').as_posix())
    tables = [table for table in Base.metadata.sorted_tables
              if table.name not in {'evaluation_snapshot', 'evaluation_review'}]
    Base.metadata.create_all(engine, tables=tables)
    with engine.begin() as db:
        db.execute(SchemaRevision.__table__.insert().values(id=1, revision='cloud-0001'))
        db.execute(AppUser.__table__.insert().values(id=7, provider='wechat', app_id='fixture', subject='keep-owner'))
    return engine


def test_explicit_cloud_upgrade_adds_evidence_tables_and_preserves_owners(tmp_path):
    from app.models import AppUser
    from app.schema_migrations import migrate_schema, check_schema
    engine = legacy_cloud_database(tmp_path)
    try:
        migrate_schema(engine)
        migrate_schema(engine)
        check_schema(engine)
        assert {'evaluation_snapshot', 'evaluation_review'} <= set(inspect(engine).get_table_names())
        with engine.connect() as db:
            assert db.scalar(select(AppUser.subject).where(AppUser.id == 7)) == 'keep-owner'
    finally:
        engine.dispose()


def test_cloud_upgrade_refuses_a_damaged_legacy_database_without_changing_revision(tmp_path):
    from app.schema_migrations import migrate_schema
    engine = legacy_cloud_database(tmp_path)
    try:
        with engine.begin() as db:
            db.execute(text('DROP TABLE upload_chunk'))
        with pytest.raises(ValueError, match='schema'):
            migrate_schema(engine)
        with engine.connect() as db:
            assert db.scalar(text('SELECT revision FROM schema_revision WHERE id=1')) == 'cloud-0001'
    finally:
        engine.dispose()


def test_cloud_upgrade_refuses_an_incomplete_existing_evidence_table(tmp_path):
    from app.schema_migrations import migrate_schema
    engine = legacy_cloud_database(tmp_path)
    try:
        with engine.begin() as db:
            db.execute(text('CREATE TABLE evaluation_snapshot (id VARCHAR(36) PRIMARY KEY)'))
        with pytest.raises(ValueError, match='schema'):
            migrate_schema(engine)
        with engine.connect() as db:
            assert db.scalar(text('SELECT revision FROM schema_revision WHERE id=1')) == 'cloud-0001'
    finally:
        engine.dispose()


def test_evaluation_snapshot_hashes_cloud_original_without_exposing_storage_reference(client, cloud_store, valid_image_bytes):
    from app.db import SessionLocal
    from app.models import Page
    response = client.post('/api/tasks', files=[('files', ('synthetic.png', valid_image_bytes, 'image/png'))])
    assert response.status_code == 201
    task = response.json()['task_id']
    with SessionLocal() as db:
        source = db.query(Page).filter_by(task_id=task).one().file_path
    assert source.startswith('cos://')
    response = client.post(f'/api/tasks/{task}/evaluations')
    assert response.status_code == 201
    snapshot = client.get(f"/api/tasks/{task}/evaluations/{response.json()['id']}")
    assert snapshot.status_code == 200
    evidence = snapshot.json()['evidence']
    assert evidence['pages'][0]['input_sha256'] == hashlib.sha256(valid_image_bytes).hexdigest()
    assert 'cos://' not in str(evidence)
    assert cloud_store.read(source) == valid_image_bytes
    cloud_store.delete(source)
    # An unavailable source must not prevent saving a truthful snapshot.
    missing = client.post(f'/api/tasks/{task}/evaluations')
    assert missing.status_code == 201
    missing = client.get(f"/api/tasks/{task}/evaluations/{missing.json()['id']}").json()
    assert missing['evidence']['pages'][0]['input_sha256'] is None


def test_cloud_import_preserves_evaluation_evidence_and_detects_review_tampering(source, target, cloud_store):
    from sqlalchemy.orm import Session
    from app.models import EvaluationSnapshot, EvaluationReview
    from app.cloud_migration import inventory, import_data, verify_import
    path, root = source
    original = create_engine('sqlite:///' + path.as_posix())
    try:
        with Session(original) as db:
            db.add(EvaluationSnapshot(id='snapshot-fixture', task_id=23, job_id='historical-job',
                                      sha256='c' * 64, evidence={'protocol': 'fixture', 'pages': []}))
            db.flush()
            db.add(EvaluationReview(id='review-fixture', snapshot_id='snapshot-fixture',
                                    body={'reviewer': 'fixture', 'notes': 'preserve this review'}))
            db.commit()
    finally:
        original.dispose()
    fingerprint = inventory(path, root)['fingerprint']
    import_data(path, root, target, expected=fingerprint, allow_missing=True)
    with Session(target) as db:
        snapshot = db.get(EvaluationSnapshot, 'snapshot-fixture')
        assert snapshot is not None
        assert snapshot.task_id == 23 and snapshot.job_id == 'historical-job'
        assert snapshot.sha256 == 'c' * 64
        assert snapshot.evidence == {'protocol': 'fixture', 'pages': []}
        assert db.get(EvaluationReview, 'review-fixture').body['notes'] == 'preserve this review'
    assert verify_import(path, root, target, expected=fingerprint)['verified_objects'] == 1
    with Session(target) as db:
        db.get(EvaluationReview, 'review-fixture').body = {'notes': 'tampered'}
        db.commit()
    with pytest.raises(ValueError, match='contents'):
        verify_import(path, root, target, expected=fingerprint)


def test_cloud_import_accepts_old_source_without_evaluation_tables(source, target, cloud_store):
    from app.cloud_migration import inventory, import_data, verify_import
    path, root = source
    original = create_engine('sqlite:///' + path.as_posix())
    try:
        with original.begin() as db:
            db.execute(text('DROP TABLE evaluation_review'))
            db.execute(text('DROP TABLE evaluation_snapshot'))
    finally:
        original.dispose()
    fingerprint = inventory(path, root)['fingerprint']
    import_data(path, root, target, expected=fingerprint, allow_missing=True)
    assert verify_import(path, root, target, expected=fingerprint)['verified_objects'] == 1


@pytest.mark.parametrize('defect', ['primary_key', 'foreign_key', 'unique_job', 'nullable_hash', 'hash_length', 'json_type', 'task_index'])
def test_cloud_upgrade_rejects_full_column_lists_with_broken_integrity(tmp_path, defect):
    from app.schema_migrations import migrate_schema
    engine = legacy_cloud_database(tmp_path)
    definition = '''CREATE TABLE evaluation_snapshot (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        task_id INTEGER NOT NULL REFERENCES task(id),
        job_id VARCHAR(36) UNIQUE,
        created_at DATETIME NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        evidence JSON NOT NULL
    )'''
    substitutions = {
        'primary_key': ('id VARCHAR(36) NOT NULL PRIMARY KEY', 'id VARCHAR(36) NOT NULL'),
        'foreign_key': ('task_id INTEGER NOT NULL REFERENCES task(id)', 'task_id INTEGER NOT NULL'),
        'unique_job': ('job_id VARCHAR(36) UNIQUE', 'job_id VARCHAR(36)'),
        'nullable_hash': ('sha256 VARCHAR(64) NOT NULL', 'sha256 VARCHAR(64)'),
        'hash_length': ('sha256 VARCHAR(64) NOT NULL', 'sha256 VARCHAR(32) NOT NULL'),
        'json_type': ('evidence JSON NOT NULL', 'evidence TEXT NOT NULL'),
    }
    if defect in substitutions:
        definition = definition.replace(*substitutions[defect])
    try:
        with engine.begin() as db:
            db.execute(text(definition))
            if defect != 'task_index':
                db.execute(text('CREATE INDEX fixture_task_idx ON evaluation_snapshot(task_id)'))
        with pytest.raises(ValueError, match='schema'):
            migrate_schema(engine)
        with engine.connect() as db:
            assert db.scalar(text('SELECT revision FROM schema_revision WHERE id=1')) == 'cloud-0001'
    finally:
        engine.dispose()


def test_cloud_upgrade_resumes_after_one_intact_added_table(tmp_path):
    from app.models import EvaluationSnapshot
    from app.schema_migrations import migrate_schema, check_schema
    engine = legacy_cloud_database(tmp_path)
    try:
        EvaluationSnapshot.__table__.create(engine)
        migrate_schema(engine)
        migrate_schema(engine)
        check_schema(engine)
        assert {'evaluation_snapshot', 'evaluation_review'} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
