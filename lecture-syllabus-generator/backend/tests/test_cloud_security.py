from app.config import settings, DATA_DIR
from app.db import SessionLocal
from app.services.process_lock import exclusive_file_lock
from test_web_login import browser


def cloud_web(monkeypatch, enabled=True):
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    monkeypatch.setitem(settings.__dict__, 'public_web_enabled', enabled)
    monkeypatch.setitem(settings.__dict__, 'public_origin', 'https://notes.example.com')


def test_cloud_browser_login_requires_explicit_public_entry(browser, monkeypatch):
    cloud_web(monkeypatch, False)
    response = browser.post('/api/auth/web/challenges')
    assert response.status_code == 503
    assert response.json()['code'] == 'PUBLIC_WEB_DISABLED'


def test_cloud_login_uses_fixed_https_origin_and_secure_cookie_behind_proxy(browser, monkeypatch):
    cloud_web(monkeypatch)
    assert browser.post('/api/auth/web/challenges', headers={'Origin': 'https://evil.example',
        'Host': 'evil.example', 'X-Forwarded-Proto': 'https'}).status_code == 403
    response = browser.post('/api/auth/web/challenges', headers={'Origin': 'https://notes.example.com',
        'Host': 'internal:8000', 'X-Forwarded-Proto': 'http'})
    assert response.status_code == 201, response.text
    assert 'Secure' in response.headers['set-cookie']


def test_public_cloud_meta_does_not_publish_global_spending(client, monkeypatch):
    from app.services.database_budget import initialize_budget
    initialize_budget(456)
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    budget = client.get('/api/meta', headers={'Authorization': ''}).json()['model_budget']
    assert 'available' in budget
    assert not {'cap_cny', 'reserved_cny', 'remaining_cny'} & set(budget)


def test_storage_contention_returns_retryable_conflict(client, valid_image_bytes):
    with exclusive_file_lock(DATA_DIR / '.storage-quota.lock'):
        response = client.post('/api/files/uploads', json={'client_key': 'blocked-fixture',
            'files': [{'name': 'photo.png', 'size': len(valid_image_bytes)}]})
    assert response.status_code == 409
    assert response.json()['retryable'] is True


def test_one_cloud_user_cannot_fill_the_global_task_queue(client, monkeypatch, valid_image_bytes):
    from app.models import TaskJob
    monkeypatch.setattr(settings, 'task_queue_enabled', True)
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    monkeypatch.setattr(settings, 'cloud_allow_mock', True)
    monkeypatch.setitem(settings.__dict__, 'cloud_max_active_jobs_user', 2)
    tasks = [client.post('/api/tasks', files=[('files', ('photo.png', valid_image_bytes, 'image/png'))]).json()['task_id']
             for _ in range(3)]
    assert client.post(f'/api/tasks/{tasks[0]}/pipeline').status_code == 202
    assert client.post(f'/api/tasks/{tasks[1]}/pipeline').status_code == 202
    response = client.post(f'/api/tasks/{tasks[2]}/pipeline')
    assert response.status_code == 429
    with SessionLocal() as db:
        assert db.query(TaskJob).count() == 2
