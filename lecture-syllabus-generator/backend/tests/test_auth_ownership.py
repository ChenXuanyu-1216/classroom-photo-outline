"""Exercise real auth dependencies and DB ownership; never call live WeChat."""
from datetime import datetime, timedelta
import hashlib

from fastapi.testclient import TestClient
import httpx
import pytest

from app.db import SessionLocal
from app.main import app
from app.models import AppUser, AuthSession, Task
from app.services.auth_service import issue_session


@pytest.fixture
def raw_client(isolated_database):
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value


@pytest.fixture
def identities():
    result = []
    with SessionLocal() as db:
        for name in ['alice', 'bob']:
            user = AppUser(provider='wechat', app_id='wx-test-app', subject=name)
            db.add(user)
            db.flush()
            token = issue_session(db, user)
            result.append((user.id, token, {'Authorization': 'Bearer ' + token}))
        db.commit()
    return result


def test_unauthenticated_and_forged_identity_cannot_access_tasks(raw_client):
    assert raw_client.get('/api/tasks').status_code == 401
    assert raw_client.get('/api/tasks', headers={'X-OpenId': 'alice', 'X-User-Id': '1'}).status_code == 401
    assert raw_client.get('/api/tasks', headers={'Authorization': 'Bearer guessed'}).status_code == 401


def test_owner_is_assigned_by_server_and_list_hides_other_and_legacy_tasks(raw_client, identities, valid_image_bytes):
    alice, bob = identities
    uploaded = raw_client.post('/api/tasks', headers=alice[2], data={'owner_id': bob[0], 'openid': 'bob'},
                               files=[('files', ('photo.png', valid_image_bytes, 'image/png'))])
    assert uploaded.status_code == 201, uploaded.text
    task_id = uploaded.json()['task_id']
    with SessionLocal() as db:
        assert db.get(Task, task_id).owner_id == alice[0]
        legacy = Task(title='未认领旧任务', page_count=0)
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
    assert [task['task_id'] for task in raw_client.get('/api/tasks', headers=alice[2]).json()['tasks']] == [task_id]
    assert raw_client.get('/api/tasks', headers=bob[2]).json()['tasks'] == []
    assert raw_client.get(f'/api/tasks/{legacy_id}', headers=alice[2]).status_code == 404


@pytest.mark.parametrize('method,suffix', [
    ('GET', ''), ('GET', '/pages'), ('GET', '/pages/1/image'), ('GET', '/outline'),
    ('PUT', '/outline'), ('GET', '/export'), ('POST', '/pipeline'),
    ('POST', '/recognize?page_no=1'), ('POST', '/regenerate'),
    ('GET', '/jobs/guessed-job'), ('DELETE', ''),
])
def test_every_object_entry_rejects_cross_user_before_side_effects(raw_client, identities, valid_image_bytes, method, suffix):
    alice, bob = identities
    task_id = raw_client.post('/api/tasks', headers=alice[2], files=[('files', ('photo.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    kwargs = {'headers': dict(bob[2], Prefer='respond-async')}
    if method == 'PUT':
        kwargs['json'] = {'markdown': '# 恶意覆盖'}
    response = raw_client.request(method, f'/api/tasks/{task_id}{suffix}', **kwargs)
    assert response.status_code == 404, response.text
    assert response.json()['code'] == 'TASK_NOT_FOUND'
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task is not None and task.status == 'pending' and task.owner_id == alice[0]
        assert not task.jobs and not task.outlines and not task.export_logs


def test_session_is_hashed_expirable_and_revocable(raw_client, identities):
    alice = identities[0]
    digest = hashlib.sha256(alice[1].encode()).hexdigest()
    with SessionLocal() as db:
        session = db.get(AuthSession, digest)
        assert session and session.token_hash != alice[1]
        session.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert raw_client.get('/api/tasks', headers=alice[2]).status_code == 401
    bob = identities[1]
    assert raw_client.get('/api/auth/me', headers=bob[2]).json()['user']['id'] == bob[0]
    assert raw_client.post('/api/auth/logout', headers=bob[2]).status_code == 204
    assert raw_client.get('/api/tasks', headers=bob[2]).status_code == 401


def test_wechat_login_uses_verified_identity_and_does_not_return_secrets(raw_client, monkeypatch):
    from app.services import wechat_login
    monkeypatch.setattr(wechat_login, 'exchange_code', lambda code: 'verified-openid')
    response = raw_client.post('/api/auth/wechat', json={'code': 'one-time-code', 'openid': 'forged', 'owner_id': 999})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload['access_token']) >= 40 and payload['expires_in'] > 0
    assert not any(secret in response.text for secret in ['verified-openid', 'session_key', 'app_secret'])
    with SessionLocal() as db:
        user = db.get(AppUser, payload['user']['id'])
        assert user.subject == 'verified-openid'
    assert raw_client.get('/api/tasks', headers={'Authorization': 'Bearer ' + payload['access_token']}).status_code == 200


def test_missing_wechat_config_fails_closed(raw_client):
    response = raw_client.post('/api/auth/wechat', json={'code': 'unused'})
    assert response.status_code == 503
    assert response.json()['code'] == 'WECHAT_NOT_CONFIGURED'


def test_wechat_upstream_errors_are_sanitized(raw_client, monkeypatch):
    from app.config import settings
    from app.services import wechat_login
    monkeypatch.setattr(settings, 'wechat_app_secret', 's' * 32)
    factory = httpx.Client
    def respond(request):
        assert request.url.host == 'api.weixin.qq.com'
        assert request.url.params['js_code'] == 'temporary-code'
        assert request.url.params['secret'] == 's' * 32
        return httpx.Response(200, json={'errcode': 40029, 'errmsg': 's' * 32})
    monkeypatch.setattr(wechat_login, '_wechat_client', lambda: factory(transport=httpx.MockTransport(respond)))
    response = raw_client.post('/api/auth/wechat', json={'code': 'temporary-code'})
    assert response.status_code == 400
    assert 's' * 32 not in response.text
    assert 'temporary-code' not in response.text


def test_wechat_httpx_info_logs_redact_credentials(raw_client, monkeypatch, caplog):
    import logging
    from app.config import settings
    from app.services import wechat_login
    secret = 'private-placeholder-' + 's' * 32
    monkeypatch.setattr(settings, 'wechat_app_secret', secret)
    factory = httpx.Client
    monkeypatch.setattr(wechat_login, '_wechat_client', lambda: factory(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'openid': 'verified-user'}))))
    with caplog.at_level(logging.INFO, logger='httpx'):
        response = raw_client.post('/api/auth/wechat', json={'code': 'sensitive-code-placeholder'})
    assert response.status_code == 200
    assert secret not in caplog.text
    assert 'sensitive-code-placeholder' not in caplog.text


def test_wechat_verified_exchange_accepts_openid_without_persisting_session_key(raw_client, monkeypatch):
    from app.config import settings
    from app.services import wechat_login
    monkeypatch.setattr(settings, 'wechat_app_secret', 's' * 32)
    factory = httpx.Client
    monkeypatch.setattr(wechat_login, '_wechat_client', lambda: factory(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'openid': 'real-verified-subject', 'session_key': 'never-store-me'}))))
    response = raw_client.post('/api/auth/wechat', json={'code': 'valid-code'})
    assert response.status_code == 200
    assert 'never-store-me' not in response.text
    with SessionLocal() as db:
        assert db.query(AppUser).filter_by(subject='real-verified-subject').count() == 1


def test_legacy_schema_migration_preserves_task_unassigned(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    from app import db as database
    legacy = create_engine('sqlite:///' + (tmp_path / 'legacy.db').as_posix())
    with legacy.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE task (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, status TEXT, page_count INTEGER, error_msg TEXT, created_at DATETIME, updated_at DATETIME)')
        connection.exec_driver_sql("INSERT INTO task (id,title,status,page_count) VALUES (99,'旧记录','success',2)")
    monkeypatch.setattr(database, 'engine', legacy)
    try:
        database.init_db()
        database.init_db()
        with legacy.connect() as connection:
            assert connection.scalar(text('SELECT title FROM task WHERE id=99')) == '旧记录'
            assert connection.scalar(text('SELECT owner_id FROM task WHERE id=99')) is None
    finally:
        legacy.dispose()


def test_local_management_can_only_explicitly_claim_unassigned_tasks(identities):
    from app.manage import claim_unassigned
    alice, bob = identities
    with SessionLocal() as db:
        legacy = Task(title='旧记录')
        owned = Task(title='已有归属', owner_id=bob[0])
        db.add_all([legacy, owned]); db.commit()
        assert claim_unassigned(db, alice[0], [legacy.id]) == 1
        assert db.get(Task, legacy.id).owner_id == alice[0]
        with pytest.raises(ValueError):
            claim_unassigned(db, alice[0], [owned.id])
        assert db.get(Task, owned.id).owner_id == bob[0]


def test_interim_owner_schema_can_upgrade_autoincrement(tmp_path):
    from sqlalchemy import create_engine, text
    from app.db import ensure_task_id_sequence
    interim = create_engine('sqlite:///' + (tmp_path / 'interim.db').as_posix())
    try:
        with interim.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE app_user (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE task (id INTEGER PRIMARY KEY, title TEXT, owner_id INTEGER REFERENCES app_user(id))')
            connection.exec_driver_sql('INSERT INTO app_user VALUES (1)')
            connection.exec_driver_sql("INSERT INTO task VALUES (7,'preserved',1)")
        with interim.connect() as connection:
            ensure_task_id_sequence(connection)
            assert connection.scalar(text('SELECT owner_id FROM task WHERE id=7')) == 1
    finally:
        interim.dispose()
