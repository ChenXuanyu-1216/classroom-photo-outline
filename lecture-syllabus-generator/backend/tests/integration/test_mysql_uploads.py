import base64

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.main import app
from app.models import AppUser, Page, Task
from app.services.auth_service import issue_session
from storage_fakes import FakeCosClient


def test_mysql_cloud_upload_commits_one_owned_task(mysql_engine, monkeypatch, valid_image_bytes):
    from app.services import object_storage
    store = object_storage.CosStorage('fixture-bucket', 'ap-guangzhou', client=FakeCosClient())
    monkeypatch.setattr(object_storage, 'get_storage', lambda: store)
    monkeypatch.setattr(settings, 'storage_backend', 'cos')
    with Session(mysql_engine) as db:
        owner = AppUser(provider='wechat', app_id='wx-test', subject='upload-fixture')
        db.add(owner)
        db.flush()
        token = issue_session(db, owner)
        owner_id = owner.id
        db.commit()
    def dependency():
        with Session(mysql_engine) as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    try:
        with TestClient(app) as client:
            client.headers['Authorization'] = 'Bearer ' + token
            response = client.post('/api/files/uploads', json={'client_key': 'mysql-upload-fixture',
                'files': [{'name': 'photo.png', 'size': len(valid_image_bytes)}]})
            assert response.status_code == 201, response.text
            identifier = response.json()['upload_id']
            response = client.put(f'/api/files/uploads/{identifier}/0/0',
                                   json={'data': base64.b64encode(valid_image_bytes).decode()})
            assert response.status_code == 200, response.text
            first = client.post(f'/api/files/uploads/{identifier}/complete')
            assert first.status_code == 201, first.text
            again = client.post(f'/api/files/uploads/{identifier}/complete')
            assert again.json()['task_id'] == first.json()['task_id']
            task_id = first.json()['task_id']
            descriptor = client.post(f'/api/tasks/{task_id}/files', json={'kind': 'source', 'page_no': 1})
            assert descriptor.status_code == 201, descriptor.text
            path = f'/api/tasks/{task_id}/files/{descriptor.json()["file_id"]}/chunks/0'
            assert base64.b64decode(client.get(path).json()['data']) == valid_image_bytes
        with Session(mysql_engine) as db:
            assert db.query(Task).count() == 1
            assert db.query(Task).one().owner_id == owner_id
            assert store.read(db.query(Page).one().file_path, limit=1024 * 1024) == valid_image_bytes
        with TestClient(app) as client:
            client.headers['Authorization'] = 'Bearer ' + token
            assert client.delete(f'/api/tasks/{task_id}').status_code == 204
            assert client.get(path).status_code == 404
            assert client.post(f'/api/files/uploads/{identifier}/complete').status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
