from pathlib import Path
import runpy

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/verify_v2.py'


def test_default_v2_verification_is_read_only(tmp_path):
    ledger = tmp_path / 'ledger.json'
    ledger.write_text('{"reserved_fen":250,"cap_fen":300,"enforced":true}')
    original = ledger.read_bytes()
    output = tmp_path / 'result'
    result = runpy.run_path(str(SCRIPT))['run'](live=False, output=output, ledger=ledger)
    assert result['status'] == 'not_called'
    assert result['remaining_cny'] == .5
    assert not output.exists() and ledger.read_bytes() == original


@pytest.mark.parametrize('body', ['{"reserved_fen":290,"cap_fen":300}', '{"reserved_fen":0,"cap_fen":500}', 'bad'])
def test_v2_probe_refuses_insufficient_or_changed_allowance(tmp_path, body):
    ledger = tmp_path / 'ledger.json'
    ledger.write_text(body)
    with pytest.raises(ValueError):
        runpy.run_path(str(SCRIPT))['run'](live=True, output=tmp_path/'result', ledger=ledger)
    assert not (tmp_path/'result').exists()


def test_verification_session_exercises_real_auth_and_is_revoked(client, valid_image_bytes):
    from app.db import engine, SessionLocal
    from app.models import AppUser
    runner = runpy.run_path(str(SCRIPT))
    database = Path(engine.url.database)
    headers = runner['_verification_headers'](database)
    try:
        result = client.get('/api/auth/me', headers=headers)
        assert result.status_code == 200
        user_id = result.json()['user']['id']
        with SessionLocal() as db:
            assert db.get(AppUser, user_id).app_id == 'synthetic-verification'
        uploaded = client.post('/api/tasks', headers=headers, files=[('files', ('synthetic.png', valid_image_bytes, 'image/png'))])
        assert uploaded.status_code == 201
        task_id = uploaded.json()['task_id']
        assert client.get(f'/api/tasks/{task_id}').status_code == 404
        assert client.get(f'/api/tasks/{task_id}', headers=headers).status_code == 200
    finally:
        runner['_revoke_verification_session'](headers, database)
    assert client.get('/api/auth/me', headers=headers).status_code == 401


def test_verification_session_refuses_to_touch_an_unexpected_database(tmp_path):
    from app.db import SessionLocal
    from app.models import AppUser
    with pytest.raises(ValueError, match='database'):
        runpy.run_path(str(SCRIPT))['_verification_headers'](tmp_path/'wrong.db')
    with SessionLocal() as db:
        assert db.query(AppUser).count() == 0
