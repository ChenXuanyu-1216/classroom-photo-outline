import base64
from datetime import datetime, timedelta
from io import BytesIO
import zipfile

from app.db import SessionLocal
from app.models import CloudFileHandle, Page, Task, ObjectCleanup
from test_cloud_uploads import cloud_store, other_headers, begin, put, complete


def upload(client, content):
    response = client.post('/api/tasks', files=[('files', ('page.png', content, 'image/png'))])
    assert response.status_code == 201, response.text
    return response.json()['task_id']


def handle(client, task, kind):
    body = {'kind': kind}
    if kind == 'source':
        body['page_no'] = 1
    response = client.post(f'/api/tasks/{task}/files', json=body)
    assert response.status_code == 201, response.text
    return response.json()


def download(client, task, descriptor):
    output = []
    count = (descriptor['size'] + descriptor['chunk_size'] - 1) // descriptor['chunk_size']
    for index in range(count):
        response = client.get(f'/api/tasks/{task}/files/{descriptor["file_id"]}/chunks/{index}')
        assert response.status_code == 200, response.text
        output.append(base64.b64decode(response.json()['data']))
    return b''.join(output)


def test_owned_original_and_latest_saved_word_use_bounded_file_handles(client, cloud_store, valid_image_bytes):
    task = upload(client, valid_image_bytes)
    original = handle(client, task, 'source')
    assert original['chunk_size'] == 256 * 1024
    assert download(client, task, original) == valid_image_bytes
    assert client.put(f'/api/tasks/{task}/outline', json={'markdown': '# 修改过的大纲\n\n保存后的内容。'}).status_code == 200
    document = handle(client, task, 'export')
    data = download(client, task, document)
    with zipfile.ZipFile(BytesIO(data)) as archive:
        assert '修改过的大纲' in archive.read('word/document.xml').decode('utf-8')
    assert 'cos://' not in str(document)


def test_foreign_expired_and_out_of_range_file_handles_fail(client, cloud_store, valid_image_bytes):
    task = upload(client, valid_image_bytes)
    source = handle(client, task, 'source')
    path = f'/api/tasks/{task}/files/{source["file_id"]}/chunks/0'
    assert client.get(path, headers=other_headers()).status_code == 404
    assert client.get(path.replace('/chunks/0', '/chunks/999')).status_code == 400
    with SessionLocal() as db:
        db.get(CloudFileHandle, source['file_id']).expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get(path).status_code == 410


def test_cloud_deletion_blocks_handles_then_cleans_only_unreferenced_objects(client, cloud_store, valid_image_bytes):
    from app.services.cloud_cleanup import cleanup_objects
    task = upload(client, valid_image_bytes)
    source = handle(client, task, 'source')
    with SessionLocal() as db:
        reference = db.query(Page).filter_by(task_id=task).one().file_path
    assert client.delete(f'/api/tasks/{task}').status_code == 204
    assert client.get(f'/api/tasks/{task}/files/{source["file_id"]}/chunks/0').status_code == 404
    cleanup_objects()
    with SessionLocal() as db:
        assert db.get(ObjectCleanup, reference) is None
    assert not cloud_store.client.objects


def test_cloud_cleanup_does_not_delete_another_tasks_shared_original(client, cloud_store, valid_image_bytes):
    from app.services.cloud_cleanup import cleanup_objects
    task = upload(client, valid_image_bytes)
    with SessionLocal() as db:
        original = db.query(Page).filter_by(task_id=task).one()
        reference = original.file_path
        shared = Task(owner_id=1, title='保留的记录', page_count=1)
        db.add(shared)
        db.flush()
        db.add(Page(task_id=shared.id, page_no=1, filename='shared.png', file_path=reference))
        db.commit()
    assert client.delete(f'/api/tasks/{task}').status_code == 204
    cleanup_objects()
    assert cloud_store.read(reference, limit=1024 * 1024) == valid_image_bytes


def test_completion_retry_after_deletion_never_resurrects_the_task(client, cloud_store, valid_image_bytes):
    identifier = begin(client, valid_image_bytes)
    assert put(client, identifier, valid_image_bytes).status_code == 200
    task = complete(client, identifier).json()['task_id']
    assert client.delete(f'/api/tasks/{task}').status_code == 204
    assert complete(client, identifier).status_code == 404
    with SessionLocal() as db:
        assert db.query(Task).count() == 0


def test_mismatched_task_and_anonymous_handles_cannot_read(client, cloud_store, valid_image_bytes):
    task = upload(client, valid_image_bytes)
    other = upload(client, valid_image_bytes)
    source = handle(client, task, 'source')
    assert client.get(f'/api/tasks/{other}/files/{source["file_id"]}/chunks/0').status_code == 404
    assert client.get(f'/api/tasks/{task}/files/{source["file_id"]}/chunks/0',
                      headers={'Authorization': ''}).status_code == 401
    assert handle(client, task, 'source')['file_id'] == source['file_id']


def test_expired_export_cleanup_preserves_original_and_retries_failed_delete(client, cloud_store, valid_image_bytes, monkeypatch):
    from app.services.cloud_cleanup import cleanup_objects
    task = upload(client, valid_image_bytes)
    assert client.put(f'/api/tasks/{task}/outline', json={'markdown': '# 保存的资料'}).status_code == 200
    document = handle(client, task, 'export')
    with SessionLocal() as db:
        row = db.get(CloudFileHandle, document['file_id'])
        reference = row.object_ref
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.get(ObjectCleanup, reference).not_before = datetime.utcnow() - timedelta(seconds=1)
        source = db.query(Page).filter_by(task_id=task).one().file_path
        db.commit()
    original_delete = cloud_store.client.delete_object
    def fail(**kwargs):
        raise OSError('fixture unavailable')
    monkeypatch.setattr(cloud_store.client, 'delete_object', fail)
    assert cleanup_objects() == 0
    with SessionLocal() as db:
        pending = db.get(ObjectCleanup, reference)
        assert pending.attempts == 1
        pending.not_before = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(cloud_store.client, 'delete_object', original_delete)
    assert cleanup_objects() == 1
    assert cloud_store.read(source, limit=1024 * 1024) == valid_image_bytes


def test_oversized_word_is_rejected_without_allocating_object(client, cloud_store, valid_image_bytes, monkeypatch):
    from app.services import object_storage
    task = upload(client, valid_image_bytes)
    assert client.put(f'/api/tasks/{task}/outline', json={'markdown': '# 内容'}).status_code == 200
    previous = set(cloud_store.client.objects)
    monkeypatch.setattr(object_storage, 'MAX_READ', 10)
    response = client.post(f'/api/tasks/{task}/files', json={'kind': 'export'})
    assert response.status_code == 413
    assert set(cloud_store.client.objects) == previous
