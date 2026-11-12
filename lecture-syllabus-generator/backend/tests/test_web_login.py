"""Real challenge/session/ownership integration with mocked WeChat image generation."""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal
from app.models import AppUser, AuthSession, Task
from app.services.auth_service import issue_session, token_digest


@pytest.fixture
def browser(monkeypatch):
    from app.services import wechat_qrcode
    monkeypatch.setattr(wechat_qrcode, 'generate', lambda scene: b'\x89PNG\r\n\x1a\nimage')
    with TestClient(app) as client:
        yield client


@pytest.fixture
def account():
    with SessionLocal() as db:
        user = AppUser(provider='wechat', app_id='wx-test-app', subject='existing-wechat')
        db.add(user)
        db.flush()
        token = issue_session(db, user)
        db.add(Task(title='原有课堂', owner_id=user.id))
        db.commit()
        return user.id, {'Authorization': 'Bearer ' + token}


def create(browser):
    response = browser.post('/api/auth/web/challenges')
    assert response.status_code == 201, response.text
    assert response.headers['cache-control'] == 'no-store'
    payload = response.json()
    assert len(payload['scene']) == 32 and payload['qr_image'].startswith('data:image/png;base64,')
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']
    assert 'verifier' not in payload
    return payload['scene']


def poll(browser, scene):
    return browser.post('/api/auth/web/poll', json={'scene': scene})


def approve(browser, scene, account):
    assert browser.post('/api/auth/web/scan', json={'scene': scene}, headers=account[1]).status_code == 200
    return browser.post('/api/auth/web/confirm', json={'scene': scene, 'approve': True}, headers=account[1])


def test_qr_confirmation_logs_into_existing_account_and_shared_tasks(browser, account):
    scene = create(browser)
    assert poll(browser, scene).json()['status'] == 'waiting'
    assert browser.post('/api/auth/web/confirm', json={'scene': scene, 'approve': True}, headers=account[1]).status_code == 409
    assert approve(browser, scene, account).status_code == 200
    result = poll(browser, scene).json()
    assert result['status'] == 'confirmed' and result['user']['id'] == account[0]
    web_headers = {'Authorization': 'Bearer ' + result['access_token']}
    assert browser.get('/api/tasks', headers=web_headers).json()['tasks'][0]['title'] == '原有课堂'
    with SessionLocal() as db:
        assert db.query(AppUser).count() == 1
        assert db.query(AuthSession).count() == 2
    assert 'access_token' not in poll(browser, scene).json()
    assert browser.post('/api/auth/web/scan', json={'scene': scene}, headers=account[1]).status_code == 410


def test_wrong_browser_cannot_poll_cancel_or_replace_user(browser, account):
    scene = create(browser)
    assert browser.post('/api/auth/web/scan', json={'scene': scene}).status_code == 401
    assert approve(browser, scene, account).status_code == 200
    with TestClient(app) as stranger:
        assert poll(stranger, scene).status_code == 404
        assert stranger.post('/api/auth/web/cancel', json={'scene': scene}).status_code == 404
    assert poll(browser, scene).json()['user']['id'] == account[0]


def test_expired_cancelled_and_rejected_challenges_never_issue_session(browser, account):
    from app.models import WebLoginChallenge
    scene = create(browser)
    with SessionLocal() as db:
        db.get(WebLoginChallenge, token_digest(scene)).expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    assert approve_expired(browser, scene, account).status_code == 410
    assert poll(browser, scene).json()['status'] == 'expired'
    scene = create(browser)
    assert browser.post('/api/auth/web/cancel', json={'scene': scene}).status_code == 200
    assert approve_expired(browser, scene, account).status_code == 410
    scene = create(browser)
    browser.post('/api/auth/web/scan', json={'scene': scene}, headers=account[1])
    assert browser.post('/api/auth/web/confirm', json={'scene': scene, 'approve': False}, headers=account[1]).status_code == 200
    assert poll(browser, scene).json()['status'] == 'denied'
    with SessionLocal() as db:
        assert db.query(AuthSession).count() == 1


def approve_expired(browser, scene, account):
    return browser.post('/api/auth/web/scan', json={'scene': scene}, headers=account[1])


def test_scan_binds_identity_and_local_accounts_cannot_approve(browser, account):
    scene = create(browser)
    browser.post('/api/auth/web/scan', json={'scene': scene}, headers=account[1])
    with SessionLocal() as db:
        other = AppUser(provider='wechat', app_id='wx-test-app', subject='other')
        local = AppUser(provider='local', app_id='desktop', subject='operator')
        db.add_all([other, local]); db.flush()
        tokens = [issue_session(db, user) for user in [other, local]]
        db.commit()
    for token in tokens:
        assert browser.post('/api/auth/web/confirm', json={'scene': scene, 'approve': True}, headers={'Authorization': 'Bearer ' + token}).status_code in (403, 404)


def test_login_creation_is_rate_limited_and_rejects_cross_origin(browser):
    assert browser.post('/api/auth/web/challenges', headers={'Origin': 'https://attacker.example'}).status_code == 403
    for _ in range(5):
        create(browser)
    assert browser.post('/api/auth/web/challenges').status_code == 429


def test_simultaneous_poll_only_issues_one_session(browser, account):
    scene = create(browser)
    approve(browser, scene, account)
    cookie_name = 'web_login_proof_' + scene
    cookie = browser.cookies.get(cookie_name)
    def redeem(_):
        with TestClient(app) as tab:
            tab.cookies.set(cookie_name, cookie, path='/api/auth/web')
            return poll(tab, scene).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(redeem, range(2)))
    assert sum('access_token' in result for result in results) == 1
    with SessionLocal() as db:
        assert db.query(AuthSession).count() == 2


def test_two_tabs_keep_independent_browser_proofs(browser, account):
    first, second = create(browser), create(browser)
    assert approve(browser, first, account).status_code == 200
    assert poll(browser, first).json()['status'] == 'confirmed'
    assert approve(browser, second, account).status_code == 200
    assert poll(browser, second).json()['status'] == 'confirmed'
