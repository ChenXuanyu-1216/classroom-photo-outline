import base64
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import AppUser, Page, Task, UploadChunk, UploadIntent, ObjectCleanup
from app.services.auth_service import issue_session
from storage_fakes import FakeCosClient


@pytest.fixture
def cloud_store(monkeypatch):
    from app.services import object_storage
    store = object_storage.CosStorage('fixture-bucket', 'ap-guangzhou', client=FakeCosClient())
    monkeypatch.setattr(object_storage, 'get_storage', lambda: store)
    monkeypatch.setattr(settings, 'storage_backend', 'cos')
    return store


def begin(client, content, key='fixture-upload-one'):
    response = client.post('/api/files/uploads', json={
        'client_key': key, 'files': [{'name': 'page.png', 'size': len(content)}]})
    assert response.status_code == 201, response.text
    return response.json()['upload_id']


def put(client, identifier, content, index=0, headers=None):
    return client.put(f'/api/files/uploads/{identifier}/0/{index}',
                      json={'data': base64.b64encode(content).decode()}, headers=headers)


def complete(client, identifier, headers=None):
    return client.post(f'/api/files/uploads/{identifier}/complete', headers=headers)


def other_headers():
    with SessionLocal() as db:
        user = AppUser(provider='wechat', app_id='wx-test', subject='other-fixture-user')
        db.add(user)
        db.flush()
        token = issue_session(db, user)
        db.commit()
        return {'Authorization': 'Bearer ' + token}


def test_unknown_source_copy_failure_cannot_consume_quota_again_on_completion_retry(client, cloud_store, valid_image_bytes, monkeypatch):
    from app.errors import AppError
    from app.services.cloud_objects import usage
    content = valid_image_bytes + b'x' * (1024 * 1024)
    monkeypatch.setattr(settings, 'cloud_max_user_bytes', len(content) * 2)
    monkeypatch.setattr(settings, 'cloud_max_total_bytes', len(content) * 2)
    identifier = begin(client, content)
    for index, offset in enumerate(range(0, len(content), 256 * 1024)):
        assert put(client, identifier, content[offset:offset + 256 * 1024], index).status_code == 200
    original = cloud_store.put
    def unavailable(key, value):
        if '/source/' in key:
            raise AppError('STORAGE_UNAVAILABLE', 'fixture copy interrupted', retryable=True, status_code=503)
        return original(key, value)
    monkeypatch.setattr(cloud_store, 'put', unavailable)
    assert complete(client, identifier).status_code == 503
    monkeypatch.setattr(cloud_store, 'put', original)
    retry = complete(client, identifier)
    assert retry.status_code == 429, retry.text
    with SessionLocal() as db:
        assert usage(db) <= len(content) * 2


def test_bounded_upload_is_durable_and_completion_is_idempotent(client, cloud_store, valid_image_bytes):
    content = valid_image_bytes + b'x' * (300 * 1024)
    identifier = begin(client, content)
    assert begin(client, content) == identifier
    assert put(client, identifier, content[:256 * 1024]).status_code == 200
    assert put(client, identifier, content[256 * 1024:], 1).status_code == 200
    response = complete(client, identifier)
    assert response.status_code == 201, response.text
    task = response.json()['task_id']
    assert complete(client, identifier).json()['task_id'] == task
    assert client.get(f'/api/tasks/{task}/pages/1/image').content == content
    with SessionLocal() as db:
        assert db.query(Task).count() == 1
        page = db.query(Page).one()
        assert page.file_path.startswith('cos://fixture-bucket/kezhao/source/')
        assert cloud_store.read(page.file_path, limit=1024 * 1024) == content


def test_file_identifiers_do_not_bypass_account_ownership(client, cloud_store, valid_image_bytes):
    identifier = begin(client, valid_image_bytes)
    headers = other_headers()
    assert put(client, identifier, valid_image_bytes, headers=headers).status_code == 404
    assert complete(client, identifier, headers=headers).status_code == 404
    assert put(client, identifier, valid_image_bytes).status_code == 200
    task = complete(client, identifier).json()['task_id']
    assert client.get(f'/api/tasks/{task}/pages/1/image', headers=headers).status_code == 404
    with SessionLocal() as db:
        assert db.query(UploadChunk).count() <= 1


def test_duplicate_chunks_must_match_and_missing_chunks_never_publish(client, cloud_store, valid_image_bytes):
    content = valid_image_bytes + b'x' * (300 * 1024)
    identifier = begin(client, content)
    first = content[:256 * 1024]
    assert put(client, identifier, first).status_code == 200
    assert put(client, identifier, first).status_code == 200
    assert put(client, identifier, first[:-1] + b'y').status_code == 409
    assert complete(client, identifier).status_code == 409
    with SessionLocal() as db:
        assert db.query(Task).count() == 0
        assert db.query(UploadChunk).count() == 1


def test_expired_intent_cannot_write_and_large_json_is_rejected(client, cloud_store, valid_image_bytes):
    identifier = begin(client, valid_image_bytes)
    with SessionLocal() as db:
        db.get(UploadIntent, identifier).expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert put(client, identifier, valid_image_bytes).status_code == 410
    assert complete(client, identifier).status_code == 410
    response = client.put(f'/api/files/uploads/{identifier}/0/0', content=b'x' * (512 * 1024 + 1),
                          headers={'Content-Type': 'application/json'})
    assert response.status_code == 413


def test_lost_commit_acknowledgment_does_not_create_or_delete_another_task(client, cloud_store, valid_image_bytes):
    identifier = begin(client, valid_image_bytes)
    assert put(client, identifier, valid_image_bytes).status_code == 200
    failed = []
    def lose_ack(db):
        if not failed and any(isinstance(value, Task) for value in db.identity_map.values()):
            failed.append(True)
            raise RuntimeError('Synthetic lost acknowledgment after durable commit')
    event.listen(Session, 'after_commit', lose_ack)
    try:
        response = complete(client, identifier)
    finally:
        event.remove(Session, 'after_commit', lose_ack)
    assert response.status_code == 201, response.text
    task = response.json()['task_id']
    assert complete(client, identifier).json()['task_id'] == task
    assert client.get(f'/api/tasks/{task}/pages/1/image').content == valid_image_bytes
    with SessionLocal() as db:
        assert db.query(Task).count() == 1


def test_web_multipart_uses_private_storage_too(client, cloud_store, valid_image_bytes):
    response = client.post('/api/tasks', files=[('files', ('page.png', valid_image_bytes, 'image/png'))])
    assert response.status_code == 201, response.text
    with SessionLocal() as db:
        reference = db.query(Page).one().file_path
        assert reference.startswith('cos://')
        assert cloud_store.read(reference, limit=1024 * 1024) == valid_image_bytes


def test_pending_uploads_and_manifest_sizes_are_bounded(client, cloud_store, valid_image_bytes):
    for index in range(3):
        begin(client, valid_image_bytes, f'fixture-pending-{index}')
    response = client.post('/api/files/uploads', json={
        'client_key': 'fixture-pending-overflow', 'files': [{'name': 'page.png', 'size': len(valid_image_bytes)}]})
    assert response.status_code == 429
    oversized = client.post('/api/files/uploads', json={
        'client_key': 'fixture-too-large', 'files': [{'name': 'page.png', 'size': 8 * 1024 * 1024 + 1}]})
    assert oversized.status_code in {413, 422}
    with SessionLocal() as db:
        assert db.query(UploadIntent).count() == 3


def test_cloud_original_reaches_existing_model_preprocessing(client, cloud_store, valid_image_bytes):
    from hashlib import sha256
    from app.services.image_preprocessor import prepare_image
    response = client.post('/api/tasks', files=[('files', ('page.png', valid_image_bytes, 'image/png'))])
    assert response.status_code == 201
    with SessionLocal() as db:
        reference = db.query(Page).one().file_path
    image = prepare_image(reference)
    assert image.data_url.startswith('data:image/png;base64,')
    assert image.metadata['sha256'] == sha256(valid_image_bytes).hexdigest()
    assert cloud_store.read(reference, limit=1024 * 1024) == valid_image_bytes


def test_cloud_upload_rejects_a_fake_image_before_publishing_a_task(client, cloud_store):
    content = b'not a valid image'
    identifier = begin(client, content)
    assert put(client, identifier, content).status_code == 200
    response = complete(client, identifier)
    assert response.status_code == 400
    with SessionLocal() as db:
        assert db.query(Task).count() == 0
