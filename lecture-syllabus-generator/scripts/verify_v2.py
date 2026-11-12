"""Opt-in synthetic upload-to-Word verification of the V2 durable queue.

Dry by default. A live run must use a fresh process, an existing shared ledger
and a new output directory; it never touches the everyday application database.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import time

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
DEFAULT_LEDGER = BACKEND / 'data/content-regressions/budget-20261023-3cny/deepseek-test-budget.json'
IMAGE = BACKEND / 'tests/fixtures/deepseek-synthetic-slide.png'


def now():
    return datetime.now(timezone.utc).isoformat()


def _verification_database(expected_database):
    from app.db import engine
    if engine.url.get_backend_name() != 'sqlite' or Path(engine.url.database).resolve() != Path(expected_database).resolve():
        raise ValueError('Verification database does not match the isolated output database.')


def _verification_headers(expected_database):
    # The caller has already selected a fresh verification.db before importing app.db.
    # Use ordinary authentication; no HTTP bypass or production identity is added.
    _verification_database(expected_database)
    from datetime import timedelta
    import secrets
    from app.db import SessionLocal
    from app.models import AppUser, AuthSession
    from app.services.auth_service import token_digest
    token = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        user = AppUser(provider='local', app_id='synthetic-verification', subject=secrets.token_hex(16))
        db.add(user)
        db.flush()
        db.add(AuthSession(token_hash=token_digest(token), user_id=user.id,
                           expires_at=datetime.utcnow() + timedelta(minutes=10)))
        db.commit()
    return {'Authorization': 'Bearer ' + token}


def _revoke_verification_session(headers, expected_database):
    _verification_database(expected_database)
    from app.db import SessionLocal
    from app.models import AuthSession
    from app.services.auth_service import token_digest
    with SessionLocal() as db:
        session = db.get(AuthSession, token_digest(headers['Authorization'].removeprefix('Bearer ')))
        if session is not None:
            db.delete(session)
            db.commit()


def run(*, live=False, output=None, ledger=DEFAULT_LEDGER):
    ledger = Path(ledger).resolve()
    budget = json.loads(ledger.read_text(encoding='utf-8'))
    if (type(budget.get('reserved_fen')) is not int or budget['reserved_fen'] < 0
            or type(budget.get('cap_fen')) is not int or not 0 < budget['cap_fen'] <= 300):
        raise ValueError('必须使用原有累计三元以内的有效账本，不能清零或扩额。')
    remaining = budget['cap_fen'] - budget['reserved_fen']
    if not live:
        return {'status': 'not_called', 'remaining_cny': remaining / 100, 'input': 'repository_synthetic_image'}
    if remaining < 30:
        raise ValueError('原累计额度至少需余 0.30 元；未调用模型。')
    if 'app.db' in sys.modules:
        raise ValueError('真实验证必须使用独立进程。')
    if output is None:
        raise ValueError('请提供全新的本地输出目录。')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('输出目录已存在，不能覆盖原始验证记录。')
    sys.path.insert(0, str(BACKEND))
    from app import config
    from app.config import settings
    if (settings.model_base_url.rstrip('/') not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}
            or settings.model_name != 'deepseek-flash' or not settings.model_api_key.strip()):
        raise ValueError('需要已配置的官方 DeepSeek 接口与密钥。')
    output.mkdir(parents=True)
    config.DATA_DIR, config.STORAGE_DIR = output / 'runtime', output / 'storage'
    settings.database_url = 'sqlite:///' + (output / 'verification.db').as_posix()
    settings.model_provider, settings.cluster_provider = 'deepseek', 'model'
    settings.knowledge_expansion_enabled, settings.task_queue_enabled = True, True
    settings.model_max_retry = 0
    settings.model_budget_enforced, settings.model_budget_cny = True, budget['cap_fen'] / 100
    from app.services import model_budget
    model_budget.BUDGET_PATH = ledger
    import httpx
    from docx import Document
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import engine
    events = []
    report = {'status': 'started', 'started_at': now(), 'mode': 'live', 'input': 'repository_synthetic_image',
              'input_sha256': hashlib.sha256(IMAGE.read_bytes()).hexdigest(), 'budget_before_fen': budget['reserved_fen'],
              'max_retry': 0, 'events': events, 'long_term_reliability': 'not_established'}
    def save():
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    original = httpx.Client
    class RecordedClient(original):
        def send(self, request, *args, **kwargs):
            if request.url.host != 'api.deepseek.com' or len(events) >= 3:
                raise ValueError('本轮只允许最多三次官方合成图核心流程请求。')
            event = {'started_at': now(), 'status': 'started'}
            events.append(event)
            save()
            started = time.monotonic()
            try:
                response = super().send(request, *args, **kwargs)
                event.update(status='completed', http_status=response.status_code)
                if response.is_success:
                    body = response.json()
                    event.update(request_id=body.get('id'), usage=body.get('usage'))
                return response
            except httpx.HTTPError as exc:
                event.update(status='failed', error_type=type(exc).__name__)
                raise
            finally:
                event.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
                save()
    httpx.Client = RecordedClient
    started = time.monotonic()
    def checked(response):
        if not response.is_success:
            raise ValueError(response.json().get('code', 'API_ERROR'))
        return response
    verification_headers = None
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            verification_headers = _verification_headers(output / 'verification.db')
            client.headers.update(verification_headers)
            task = checked(client.post('/api/tasks', files=[('files', ('synthetic.png', IMAGE.read_bytes(), 'image/png'))])).json()['task_id']
            report['task_id'] = task
            submitted = client.post(f'/api/tasks/{task}/pipeline', headers={'Prefer': 'respond-async'})
            if submitted.status_code != 202:
                raise ValueError('EXPECTED_202')
            job = submitted.json()
            report['job_id'], report['submission_status'] = job['job_id'], 202
            save()
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                job = checked(client.get(f'/api/tasks/{task}/jobs/{job["job_id"]}')).json()
                if job['status'] not in {'queued', 'running'}:
                    break
                time.sleep(.2)
            report['job'] = job
            report['pages'] = checked(client.get(f'/api/tasks/{task}/pages')).json()['pages']
            if job['status'] != 'success':
                raise ValueError((job.get('error') or {}).get('code', 'JOB_NOT_COMPLETED'))
            outline = checked(client.get(f'/api/tasks/{task}/outline')).json()
            md = outline['markdown']
            if not all(value in md for value in ('P(y|x)', '<input v-model="name">', 'P1', 'AI生成')):
                raise ValueError('SYNTHETIC_CONTENT_CHECK_FAILED')
            checked(client.put(f'/api/tasks/{task}/outline', json={'markdown': md}))
            reopened = checked(client.get(f'/api/tasks/{task}/outline')).json()
            if reopened['markdown'] != md:
                raise ValueError('SAVE_REOPEN_MISMATCH')
            word = checked(client.get(f'/api/tasks/{task}/export')).content
            text = '\n'.join(p.text for p in Document(BytesIO(word)).paragraphs)
            if not all(value in text for value in ('P(y|x)', '<input v-model="name">', 'AI生成')):
                raise ValueError('WORD_CONTENT_MISMATCH')
            (output / 'outline.md').write_text(md, encoding='utf-8')
            (output / 'outline.docx').write_bytes(word)
            # A new browser/session reads the same result without a model request.
            count = len(events)
        with TestClient(app, raise_server_exceptions=False) as client:
            client.headers.update(verification_headers)
            if checked(client.get(f'/api/tasks/{task}/outline')).json()['markdown'] != md or len(events) != count:
                raise ValueError('REOPEN_REPEATED_MODEL_CALL')
        report.update(status='passed', checks=['202_queue', 'real_recognition', 'grouping', 'expansion', 'save_reopen', 'word', 'restart_read_only'])
    except Exception as exc:
        report.update(status='failed', error_code=str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    finally:
        httpx.Client = original
        if verification_headers is not None:
            try:
                _revoke_verification_session(verification_headers, output / 'verification.db')
                report['verification_session_revoked'] = True
            except Exception:
                report.update(status='failed', verification_session_revoked=False, session_cleanup_error='SESSION_CLEANUP_FAILED')
        report.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3),
                      request_count=len(events), budget_after_fen=json.loads(ledger.read_text(encoding='utf-8'))['reserved_fen'])
        save()
        engine.dispose()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--budget-ledger', type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()
    try:
        report = run(live=args.live, output=args.output, ledger=args.budget_ledger)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['status'] in {'passed', 'not_called'} else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'stopped', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
