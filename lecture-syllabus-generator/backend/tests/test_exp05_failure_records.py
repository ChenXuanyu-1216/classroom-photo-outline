"""EXP05 handoff evidence: real job/page persistence with offline HTTP faults."""
import json

import httpx
import pytest

from app.config import settings
from app.services import model_network
from app.services.recognizer import MultimodalRecognizer


HTTP_CLIENT = httpx.Client


@pytest.fixture
def model_flow(client, monkeypatch):
    from app.services.pipeline import pipeline
    # TestClient started with automatic queue consumption disabled by conftest.
    monkeypatch.setattr(settings, 'task_queue_enabled', True)
    monkeypatch.setattr(settings, 'model_provider', 'multimodal')
    monkeypatch.setattr(settings, 'model_base_url', 'https://model.invalid/v1')
    monkeypatch.setattr(settings, 'model_api_key', 'offline-only')
    monkeypatch.setattr(settings, 'model_max_retry', 1)
    monkeypatch.setattr(settings, 'knowledge_expansion_enabled', False)
    monkeypatch.setattr(settings, 'cluster_provider', 'rule')
    monkeypatch.setattr(pipeline, 'recognizer', MultimodalRecognizer())
    monkeypatch.setattr(model_network.time, 'sleep', lambda seconds: None)
    calls, reply = [], {'kind': 'timeout'}

    def handler(request):
        calls.append(request)
        kind = reply['kind']
        if kind == 'timeout':
            raise httpx.ReadTimeout('offline timeout', request=request)
        if kind == 'connection':
            raise httpx.ConnectError('offline connection failure', request=request)
        if kind == 'empty_http_body':
            return httpx.Response(200, content=b'')
        point = {'content': 'EXP05 可复核知识点', 'kp_type': 'concept', 'source_type': '课堂原话'}
        page = {'title': 'EXP05 合成页', 'points': [] if kind == 'empty_points' else [point],
                'confidence': .9, 'readability': 'clear', 'uncertain_regions': []}
        content = {'empty_content': '', 'malformed_json': '{"title":'}.get(kind, json.dumps(page, ensure_ascii=False))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    return client, calls, reply


@pytest.mark.parametrize('kind,code,retryable,count', [
    ('timeout', 'MODEL_TIMEOUT', True, 2),
    ('connection', 'MODEL_CONNECTION_FAILED', True, 2),
    ('empty_http_body', 'MODEL_EMPTY_RESPONSE', True, 1),
    ('empty_content', 'MODEL_EMPTY_CONTENT', True, 1),
    ('malformed_json', 'MODEL_OUTPUT_INVALID', True, 1),
    ('empty_points', 'IMAGE_UNREADABLE', False, 1),
])
def test_exp05_failure_is_persisted_without_false_success(model_flow, valid_image_bytes, kind, code, retryable, count):
    from app.services.task_jobs import TaskJobQueue
    client, calls, reply = model_flow
    reply['kind'] = kind
    task = client.post('/api/tasks', files=[('files', ('input.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    response = client.post(f'/api/tasks/{task}/pipeline', headers={'Prefer': 'respond-async'})
    assert response.status_code == 202
    job_id = response.json()['job_id']
    assert TaskJobQueue().run_once()
    job = client.get(f'/api/tasks/{task}/jobs/{job_id}').json()
    page = client.get(f'/api/tasks/{task}/pages').json()['pages'][0]
    # The job reports no usable input; page records retain the specific cause.
    assert job['status'] == 'failed' and job['error']['code'] == 'PIPELINE_ERROR'
    assert page['error_code'] == code
    assert page['error_retryable'] is retryable
    assert page['failed_flag'] is True and page['confidence'] == 0
    assert len(calls) == count
    assert client.get(f'/api/tasks/{task}/outline').status_code != 200


def test_exp05_explicit_recovery_keeps_failed_job_but_clears_latest_page_error(model_flow, valid_image_bytes):
    from app.services.task_jobs import TaskJobQueue
    client, calls, reply = model_flow
    task = client.post('/api/tasks', files=[('files', ('input.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    first = client.post(f'/api/tasks/{task}/recognize', headers={'Prefer': 'respond-async'}).json()
    assert TaskJobQueue().run_once()
    assert client.get(f'/api/tasks/{task}/pages').json()['pages'][0]['error_code'] == 'MODEL_TIMEOUT'
    reply['kind'] = 'success'
    second = client.post(f'/api/tasks/{task}/recognize?page_no=1', headers={'Prefer': 'respond-async'}).json()
    assert TaskJobQueue().run_once()
    assert first['job_id'] != second['job_id']
    page = client.get(f'/api/tasks/{task}/pages').json()['pages'][0]
    assert page['error_code'] is None and page['error_retryable'] is None and not page['failed_flag']
    old = client.get(f'/api/tasks/{task}/jobs/{first["job_id"]}').json()
    assert old['status'] == 'failed'
    assert old['error']['code'] == 'RECOGNITION_FAILED'
    # Job history survives, but the old job has no page-level timeout snapshot.
    assert 'pages' not in old
    assert len(calls) == 3


def test_exp05_evidence_survives_recovery_and_manual_review(model_flow, valid_image_bytes):
    from app.services.task_jobs import TaskJobQueue
    client, calls, reply = model_flow
    task = client.post('/api/tasks', files=[('files', ('input.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    first = client.post(f'/api/tasks/{task}/pipeline', headers={'Prefer': 'respond-async'}).json()
    assert TaskJobQueue().run_once()
    response = client.get(f'/api/tasks/{task}/evaluations')
    assert response.status_code == 200
    evidence = response.json()['snapshots'][0]
    assert evidence['job_id'] == first['job_id']
    frozen = client.get(f'/api/tasks/{task}/evaluations/{evidence["id"]}').json()
    assert frozen['evidence']['pages'][0]['error']['code'] == 'MODEL_TIMEOUT'
    reply['kind'] = 'success'
    client.post(f'/api/tasks/{task}/recognize?page_no=1', headers={'Prefer': 'respond-async'})
    assert TaskJobQueue().run_once()
    assert client.get(f'/api/tasks/{task}/pages').json()['pages'][0]['error_code'] is None
    after = client.get(f'/api/tasks/{task}/evaluations/{evidence["id"]}').json()
    assert after['evidence'] == frozen['evidence']
    assert after['sha256'] == frozen['sha256']
    assert len(client.get(f'/api/tasks/{task}/evaluations').json()['snapshots']) == 2
    assert len(calls) == 3


def test_exp05_review_requires_evidence_and_cannot_invent_a_pass(client, valid_image_bytes):
    task = client.post('/api/tasks', files=[('files', ('input.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    response = client.post(f'/api/tasks/{task}/evaluations')
    assert response.status_code == 201
    snapshot = response.json()['id']
    url = f'/api/tasks/{task}/evaluations/{snapshot}/reviews'
    body = {'reviewer': 'self-01', 'review_kind': 'self', 'scope': 'P1',
            'expected': '有可读定义', 'observed': '未运行，尚无输出',
            'counts': {'expected': 1, 'matched': 0, 'output': 0, 'supported': 0, 'source_correct': 0, 'source_total': 0},
            'structure': None, 'usability': None, 'critical_errors': [], 'notes': ''}
    saved = client.post(url, json=body)
    assert saved.status_code == 201
    assert saved.json()['metrics']['fidelity'] is None
    assert saved.json()['metrics']['coverage'] == 0
    assert saved.json()['module_pass'] is None
    body['counts']['matched'] = 2
    assert client.post(url, json=body).status_code == 422
    body['counts']['matched'] = 0
    body['critical_errors'] = [{'category': 'formula', 'expected': '', 'actual': '', 'impact': ''}]
    assert client.post(url, json=body).status_code == 422
    assert len(client.get(f'/api/tasks/{task}/evaluations/{snapshot}').json()['reviews']) == 1
    client.headers.pop('Authorization')
    assert client.get(f'/api/tasks/{task}/evaluations/{snapshot}').status_code == 401


def test_exp05_evidence_is_owner_scoped_and_deleted_with_task(client, valid_image_bytes):
    from app.db import SessionLocal
    from app.models import AppUser, EvaluationSnapshot, EvaluationReview
    from app.services.auth_service import issue_session
    task = client.post('/api/tasks', files=[('files', ('input.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    snapshot = client.post(f'/api/tasks/{task}/evaluations').json()['id']
    review_body = {'reviewer': 'self-01', 'review_kind': 'self', 'scope': 'P1',
                   'expected': '未运行的合成图', 'observed': '验证证据的归属隔离与删除'}
    assert client.post(f'/api/tasks/{task}/evaluations/{snapshot}/reviews', json=review_body).status_code == 201
    owner_header = client.headers['Authorization']
    with SessionLocal() as db:
        other = AppUser(provider='local', app_id='test', subject='exp05-other')
        db.add(other)
        db.flush()
        token = issue_session(db, other)
        db.commit()
    client.headers['Authorization'] = 'Bearer ' + token
    for url in [f'/api/tasks/{task}/evaluations', f'/api/tasks/{task}/evaluations/{snapshot}',
                f'/api/tasks/{task}/evaluations/{snapshot}/export']:
        assert client.get(url).status_code == 404
    assert client.post(f'/api/tasks/{task}/evaluations').status_code == 404
    assert client.post(f'/api/tasks/{task}/evaluations/{snapshot}/reviews', json=review_body).status_code == 404
    client.headers['Authorization'] = owner_header
    exported = client.get(f'/api/tasks/{task}/evaluations/{snapshot}/export')
    assert exported.status_code == 200
    assert 'attachment' in exported.headers['content-disposition']
    assert client.delete(f'/api/tasks/{task}').status_code == 204
    with SessionLocal() as db:
        assert db.query(EvaluationSnapshot).filter_by(task_id=task).count() == 0
        assert db.query(EvaluationReview).count() == 0
