import hashlib
import json

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.db import Base
from app.models import AppUser, AuthSession, Task, Page, Outline, OutlineNode, StoredObject, ObjectCleanup
from test_cloud_uploads import cloud_store


@pytest.fixture
def source(tmp_path, valid_image_bytes):
    root = tmp_path / 'originals'
    root.mkdir()
    image = root / 'one.png'
    image.write_bytes(valid_image_bytes)
    path = tmp_path / 'source.db'
    engine = create_engine('sqlite:///' + path.as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = AppUser(id=17, provider='wechat', app_id='wx-fixture', subject='private-openid-fixture')
        db.add(user)
        db.flush()
        task = Task(id=23, owner_id=17, title='保存的课堂', page_count=2, status='running')
        db.add(task)
        db.flush()
        db.add_all([Page(id=31, task_id=23, page_no=1, filename='one.png', file_path=str(image)),
                    Page(id=32, task_id=23, page_no=2, filename='missing.png', file_path=str(root / 'missing.png')),
                    Outline(id=41, task_id=23, markdown='# 保留的大纲\n已校对。', version=2)])
        db.flush()
        db.add(OutlineNode(id=51, outline_id=41, level=1, title='标题', sort_order=1))
        from datetime import datetime, timedelta
        db.add(AuthSession(token_hash='do-not-copy-session', user_id=17,
                           expires_at=datetime.utcnow() + timedelta(days=1)))
        db.commit()
    engine.dispose()
    return path, root


@pytest.fixture
def target(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'target.db').as_posix())
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_inventory_is_read_only_and_public_report_omits_identities_and_credentials(source):
    from app.cloud_migration import inventory
    path, root = source
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = inventory(path, root)
    assert report['counts']['task'] == 1
    assert report['missing_page_ids'] == [32]
    serialized = json.dumps(report)
    assert 'private-openid' not in serialized and 'do-not-copy-session' not in serialized
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_import_preserves_shared_identity_and_outline_but_not_sessions_or_running_jobs(source, target, cloud_store):
    from app.cloud_migration import inventory, import_data, verify_import
    path, root = source
    expected = inventory(path, root)['fingerprint']
    report = import_data(path, root, target, expected=expected, allow_missing=True)
    assert report['missing_page_ids'] == [32]
    with Session(target) as db:
        assert db.get(AppUser, 17).subject == 'private-openid-fixture'
        assert db.get(Task, 23).owner_id == 17
        assert db.get(Task, 23).status == 'failed'
        assert db.get(Outline, 41).markdown == '# 保留的大纲\n已校对。'
        assert db.query(AuthSession).count() == 0
        page = db.get(Page, 31)
        assert cloud_store.read(page.file_path, limit=1024 * 1024) == (root / 'one.png').read_bytes()
        assert db.get(Page, 32).error_code == 'SOURCE_MISSING'
        assert db.query(StoredObject).count() == 1
    assert verify_import(path, root, target, expected=expected)['verified_objects'] == 1
    with pytest.raises(ValueError, match='empty'):
        import_data(path, root, target, expected=expected, allow_missing=True)


def test_missing_or_changed_source_is_refused_before_target_mutation(source, target, cloud_store):
    from app.cloud_migration import inventory, import_data
    path, root = source
    expected = inventory(path, root)['fingerprint']
    with pytest.raises(ValueError, match='missing'):
        import_data(path, root, target, expected=expected)
    (root / 'one.png').write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        import_data(path, root, target, expected=expected, allow_missing=True)
    with Session(target) as db:
        assert db.query(AppUser).count() == 0 and db.query(StoredObject).count() == 0


def test_failed_private_copy_rolls_back_business_rows_and_keeps_cleanup_record(source, target, cloud_store, monkeypatch):
    from app.cloud_migration import inventory, import_data
    path, root = source
    def fail(*args, **kwargs):
        raise OSError('fixture interrupted transfer')
    monkeypatch.setattr(cloud_store, 'put', fail)
    with pytest.raises(Exception):
        import_data(path, root, target, expected=inventory(path, root)['fingerprint'], allow_missing=True)
    with Session(target) as db:
        assert db.query(Task).count() == 0 and db.query(AppUser).count() == 0
        assert db.query(ObjectCleanup).count() == 1


def test_backup_reopens_with_the_same_inventory_and_never_overwrites(source, tmp_path):
    from app.cloud_migration import inventory, backup_source
    path, root = source
    destination = tmp_path / 'verified-backup.db'
    backup_source(path, destination)
    assert inventory(destination, root) == inventory(path, root)
    with pytest.raises(FileExistsError):
        backup_source(path, destination)
