from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ExportLog, KnowledgePoint, Outline, OutlineNode, Page, Task
from app.services.process_lock import exclusive_file_lock
from app.services.task_execution import execution_lock_path


def upload(client, image):
    response = client.post('/api/tasks', files=[('files', ('page.png', image, 'image/png'))])
    assert response.status_code == 201
    return response.json()['task_id']


def saved_file(task_id):
    with SessionLocal() as db:
        return Path(db.query(Page).filter_by(task_id=task_id).one().file_path)


@pytest.mark.parametrize('status', ['failed', 'success', 'partial', 'pending'])
def test_delete_removes_only_selected_record_its_outline_and_photo(client, valid_image_bytes, status):
    selected = upload(client, valid_image_bytes)
    untouched = upload(client, valid_image_bytes)
    assert client.post(f'/api/tasks/{selected}/pipeline').status_code == 200
    assert client.get(f'/api/tasks/{selected}/export').status_code == 200
    photo = saved_file(selected)
    other_photo = saved_file(untouched)
    with SessionLocal() as db:
        task = db.get(Task, selected)
        task.status = status
        outline_ids = [row.id for row in db.query(Outline).filter_by(task_id=selected)]
        assert db.query(OutlineNode).filter(OutlineNode.outline_id.in_(outline_ids)).count() > 0
        assert db.query(ExportLog).filter_by(task_id=selected).count() == 1
        db.commit()

    response = client.delete(f'/api/tasks/{selected}')
    assert response.status_code == 204, response.text
    assert response.content == b''
    assert client.get(f'/api/tasks/{selected}').status_code == 404
    assert client.get(f'/api/tasks/{selected}/outline').status_code == 404
    assert client.get(f'/api/tasks/{selected}/export').status_code == 404
    assert [row['task_id'] for row in client.get('/api/tasks').json()['tasks']] == [untouched]
    with SessionLocal() as db:
        assert db.get(Task, selected) is None
        for model in (Page, KnowledgePoint, Outline, ExportLog):
            assert db.query(model).filter_by(task_id=selected).count() == 0
        assert db.query(OutlineNode).filter(OutlineNode.outline_id.in_(outline_ids)).count() == 0
        assert db.get(Task, untouched) is not None
    assert not photo.exists()
    assert other_photo.is_file()
    # Missing and inaccessible tasks share 404; a retry must remain harmless.
    assert client.delete(f'/api/tasks/{selected}').status_code == 404


def test_running_task_cannot_be_removed_while_generation_may_write(client, valid_image_bytes):
    task_id = upload(client, valid_image_bytes)
    photo = saved_file(task_id)
    with SessionLocal() as db:
        db.get(Task, task_id).status = 'running'
        db.commit()
        lock_path = execution_lock_path(db, task_id)
    with exclusive_file_lock(lock_path):
        response = client.delete(f'/api/tasks/{task_id}')
        assert response.status_code == 409
        assert response.json()['code'] == 'TASK_BUSY'
        assert client.get(f'/api/tasks/{task_id}').json()['status'] == 'running'
    assert photo.is_file()


def test_database_failure_preserves_record_and_upload(client, valid_image_bytes, monkeypatch):
    task_id = upload(client, valid_image_bytes)
    photo = saved_file(task_id)
    original_commit = Session.commit

    def failed_delete_commit(db):
        if any(isinstance(row, Task) and row.id == task_id for row in db.deleted):
            raise OperationalError('injected delete failure', {}, RuntimeError('offline'))
        return original_commit(db)

    monkeypatch.setattr(Session, 'commit', failed_delete_commit)
    response = client.delete(f'/api/tasks/{task_id}')
    assert response.status_code == 500
    assert response.json()['code'] == 'TASK_DELETE_FAILED'
    assert client.get(f'/api/tasks/{task_id}').json()['status'] == 'pending'
    assert photo.is_file()


def test_deletion_never_unlinks_paths_outside_upload_storage(client, valid_image_bytes, tmp_path):
    task_id = upload(client, valid_image_bytes)
    outside = tmp_path / 'unrelated.png'
    outside.write_bytes(b'not an application upload')
    with SessionLocal() as db:
        db.query(Page).filter_by(task_id=task_id).one().file_path = str(outside)
        db.commit()
    assert client.delete(f'/api/tasks/{task_id}').status_code == 204
    assert outside.read_bytes() == b'not an application upload'


def test_shared_legacy_photo_survives_until_its_last_reference_is_deleted(client, valid_image_bytes):
    first, second = upload(client, valid_image_bytes), upload(client, valid_image_bytes)
    photo = saved_file(first)
    with SessionLocal() as db:
        db.query(Page).filter_by(task_id=second).one().file_path = str(photo)
        db.commit()
    assert client.delete(f'/api/tasks/{first}').status_code == 204
    assert photo.is_file()
    assert client.get(f'/api/tasks/{second}').status_code == 200
    assert client.delete(f'/api/tasks/{second}').status_code == 204
    assert not photo.exists()


def test_deleted_task_id_is_never_reused_for_a_new_record(client, valid_image_bytes):
    old = upload(client, valid_image_bytes)
    assert client.delete(f'/api/tasks/{old}').status_code == 204
    new = upload(client, valid_image_bytes)
    assert new > old, 'stale record URLs and repeated DELETE must not target a new task'
    assert client.delete(f'/api/tasks/{old}').status_code == 404
    assert client.get(f'/api/tasks/{new}').status_code == 200
