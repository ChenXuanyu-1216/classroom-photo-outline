from concurrent.futures import ThreadPoolExecutor
import threading
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import Task


@pytest.fixture(autouse=True)
def enable_submission_after_test_app_startup(client, monkeypatch):
    # conftest disables automatic consumer startup; tests drive run_once.
    monkeypatch.setattr(settings, 'task_queue_enabled', True)


def upload(client, image):
    return client.post('/api/tasks', files=[('files', ('p.png', image, 'image/png'))]).json()['task_id']


def enqueue(client, task, op='pipeline'):
    return client.post(f'/api/tasks/{task}/{op}', headers={'Prefer': 'respond-async'})


def test_202_is_persisted_and_refresh_does_not_execute(client, monkeypatch, valid_image_bytes):
    from app.services.pipeline import pipeline
    calls = []
    monkeypatch.setattr(pipeline.recognizer, 'recognize', lambda *args: calls.append(args))
    task = upload(client, valid_image_bytes)
    response = enqueue(client, task)
    assert response.status_code == 202
    job = response.json()
    assert job['status'] == 'queued' and job['operation'] == 'pipeline'
    assert client.get(f'/api/tasks/{task}').json()['active_job']['job_id'] == job['job_id']
    assert client.get(f'/api/tasks/{task}/jobs/{job["job_id"]}').json()['status'] == 'queued'
    assert calls == []
    assert enqueue(client, task).status_code == 409
    assert client.post(f'/api/tasks/{task}/pipeline').status_code == 409
    assert client.delete(f'/api/tasks/{task}').status_code == 409
    assert client.put(f'/api/tasks/{task}/outline', json={'markdown': '# 不能覆盖'}).status_code == 409


def test_worker_fifo_persists_results_and_word(client, valid_image_bytes):
    from app.services.task_jobs import TaskJobQueue
    tasks = [upload(client, valid_image_bytes) for _ in range(2)]
    jobs = [enqueue(client, task).json() for task in tasks]
    queue = TaskJobQueue()
    assert queue.run_once() is True
    assert client.get(f'/api/tasks/{tasks[0]}/jobs/{jobs[0]["job_id"]}').json()['status'] == 'success'
    assert client.get(f'/api/tasks/{tasks[1]}').json()['status'] == 'queued'
    assert TaskJobQueue().run_once() is True  # New consumer resumes durable work.
    assert queue.run_once() is False
    for task, job in zip(tasks, jobs):
        state = client.get(f'/api/tasks/{task}/jobs/{job["job_id"]}').json()
        assert state['result']['module_count'] > 0
        assert client.get(f'/api/tasks/{task}').json()['active_job'] is None
        assert client.get(f'/api/tasks/{task}/outline').status_code == 200
        assert client.get(f'/api/tasks/{task}/export').status_code == 200


def test_capacity_is_bounded_and_invalid_page_never_queues(client, monkeypatch, valid_image_bytes):
    monkeypatch.setattr(settings, 'task_queue_capacity', 1)
    first, second = [upload(client, valid_image_bytes) for _ in range(2)]
    assert client.post(f'/api/tasks/{first}/recognize?page_no=99', headers={'Prefer': 'respond-async'}).status_code == 404
    assert enqueue(client, first).status_code == 202
    assert enqueue(client, second).status_code == 429
    assert client.get(f'/api/tasks/{second}').json()['status'] == 'pending'


def test_failed_job_preserves_old_draft_and_records_safe_error(client, monkeypatch, valid_image_bytes):
    from app.errors import AppError
    from app.services.pipeline import pipeline
    from app.services.task_jobs import TaskJobQueue
    task = upload(client, valid_image_bytes)
    assert client.post(f'/api/tasks/{task}/pipeline').status_code == 200
    saved = client.get(f'/api/tasks/{task}/outline').json()['markdown']
    def fail(*args):
        raise AppError('CLUSTERING_FAILED', '归组暂不可用。', retryable=True, status_code=502)
    monkeypatch.setattr(pipeline.clusterer, 'cluster', fail)
    job = enqueue(client, task, 'regenerate').json()
    assert TaskJobQueue().run_once()
    state = client.get(f'/api/tasks/{task}/jobs/{job["job_id"]}').json()
    assert state['status'] == 'failed' and state['error']['code'] == 'CLUSTERING_FAILED'
    assert state['error']['retryable'] is True
    assert client.get(f'/api/tasks/{task}/outline').json()['markdown'] == saved
    snapshot_id = client.get(f'/api/tasks/{task}/evaluations').json()['snapshots'][0]['id']
    snapshot = client.get(f'/api/tasks/{task}/evaluations/{snapshot_id}').json()
    evidence = snapshot['evidence']
    assert evidence['outline']['origin'] == 'retained_before_attempt'
    module = snapshot['modules'][0]
    review = client.post(f'/api/tasks/{task}/evaluations/{snapshot_id}/reviews', json={
        'reviewer': 'self-01', 'review_kind': 'self', 'scope': 'module:' + module['heading'], 'module_id': module['id'],
        'expected': '核对本轮生成结果', 'observed': '本轮失败，保留旧稿',
        'structure': 4, 'usability': 4,
    })
    assert review.status_code == 201 and review.json()['module_pass'] is None


def test_orphan_running_job_fails_without_paid_replay(client, valid_image_bytes):
    from app.models import TaskJob
    task = upload(client, valid_image_bytes)
    queued = enqueue(client, task).json()
    with SessionLocal() as db:
        job = db.get(TaskJob, queued['job_id'])
        job.status = 'running'
        db.get(Task, task).status = 'running'
        db.commit()
    observed = client.get(f'/api/tasks/{task}').json()
    assert observed['status'] == 'failed'
    assert observed['last_job']['error']['code'] == 'TASK_INTERRUPTED'
    assert observed['active_job'] is None
    snapshot_id = client.get(f'/api/tasks/{task}/evaluations').json()['snapshots'][0]['id']
    evidence = client.get(f'/api/tasks/{task}/evaluations/{snapshot_id}').json()['evidence']
    assert evidence['task']['status'] == 'failed'
    assert evidence['job']['error']['code'] == 'TASK_INTERRUPTED'
    assert enqueue(client, task).status_code == 202


def test_consumer_exclusion_and_writes_during_running_job(client, monkeypatch, valid_image_bytes):
    from app.services.pipeline import pipeline
    from app.services.task_jobs import TaskJobQueue
    original = pipeline.recognizer.recognize
    entered, release = threading.Event(), threading.Event()
    def hold(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(pipeline.recognizer, 'recognize', hold)
    task = upload(client, valid_image_bytes)
    enqueue(client, task)
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(TaskJobQueue().run_once)
        try:
            assert entered.wait(10)
            assert TaskJobQueue().run_once() is False
            assert client.get(f'/api/tasks/{task}').json()['status'] == 'running'
            assert enqueue(client, task).status_code == 409
            assert client.put(f'/api/tasks/{task}/outline', json={'markdown': '冲突'}).status_code == 409
            other = upload(client, valid_image_bytes)
            assert enqueue(client, other).status_code == 202
        finally:
            release.set()
        assert running.result(10)


def test_other_process_consumer_cannot_be_bypassed(client, valid_image_bytes):
    from app.services.task_jobs import TaskJobQueue
    task = upload(client, valid_image_bytes)
    enqueue(client, task)
    code = '''
import sys
from app.db import SessionLocal
from app.services.task_jobs import _queue_path
from app.services.process_lock import exclusive_file_lock
with SessionLocal() as db:
    with exclusive_file_lock(_queue_path(db, '.consumer.lock')):
        print('READY', flush=True)
        sys.stdin.readline()
'''
    child = subprocess.Popen([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                             env=os.environ.copy(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(child.stdout.readline).result(10).strip() == 'READY'
        assert TaskJobQueue().run_once() is False
        assert client.get(f'/api/tasks/{task}').json()['status'] == 'queued'
    finally:
        child.communicate('\n', timeout=10)
    assert child.returncode == 0
    assert TaskJobQueue().run_once() is True


def test_single_page_job_can_finish_while_other_pages_are_pending(client, valid_image_bytes):
    from app.services.task_jobs import TaskJobQueue
    task = client.post('/api/tasks', files=[('files', (f'p{i}.png', valid_image_bytes, 'image/png')) for i in range(2)]).json()['task_id']
    job = client.post(f'/api/tasks/{task}/recognize?page_no=1', headers={'Prefer': 'respond-async'}).json()
    assert TaskJobQueue().run_once()
    result = client.get(f'/api/tasks/{task}/jobs/{job["job_id"]}').json()
    assert result['status'] == 'success'
    assert result['result']['status'] == 'pending'


def test_lifecycle_consumer_executes_accepted_job(client, valid_image_bytes):
    import time
    from app.services.task_jobs import TaskJobQueue
    task = upload(client, valid_image_bytes)
    job = enqueue(client, task).json()
    queue = TaskJobQueue()
    queue.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = client.get(f'/api/tasks/{task}/jobs/{job["job_id"]}').json()
            if result['status'] not in {'queued', 'running'}:
                break
            time.sleep(.02)
        assert result['status'] == 'success'
    finally:
        queue.stop()


def test_sync_generation_rejects_edit_save_but_keeps_draft_exportable(client, monkeypatch, valid_image_bytes):
    from app.services.pipeline import pipeline
    task = upload(client, valid_image_bytes)
    assert client.post(f'/api/tasks/{task}/pipeline').status_code == 200
    saved = client.get(f'/api/tasks/{task}/outline').json()['markdown']
    original = pipeline.clusterer.cluster
    entered, release = threading.Event(), threading.Event()
    def hold(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(pipeline.clusterer, 'cluster', hold)
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(client.post, f'/api/tasks/{task}/regenerate')
        try:
            assert entered.wait(10)
            assert client.put(f'/api/tasks/{task}/outline', json={'markdown': '# 不能被覆盖的新稿'}).status_code == 409
            assert client.get(f'/api/tasks/{task}/outline').json()['markdown'] == saved
            assert client.get(f'/api/tasks/{task}/export').status_code == 200
        finally:
            release.set()
        assert running.result(10).status_code == 200


def test_short_execution_lock_contention_keeps_accepted_job_queued(client, valid_image_bytes):
    from app.services.process_lock import exclusive_file_lock
    from app.services.task_execution import execution_lock_path
    from app.services.task_jobs import TaskJobQueue
    task = upload(client, valid_image_bytes)
    job = enqueue(client, task).json()
    with SessionLocal() as db, exclusive_file_lock(execution_lock_path(db, task)):
        assert TaskJobQueue().run_once() is False
    observed = client.get(f'/api/tasks/{task}').json()
    assert observed['status'] == 'queued'
    assert observed['active_job']['job_id'] == job['job_id']
    assert TaskJobQueue().run_once() is True
    assert client.get(f'/api/tasks/{task}').json()['status'] == 'success'


def test_disabled_queue_rejects_async_instead_of_accepting_unconsumable_work(client, monkeypatch, valid_image_bytes):
    task = upload(client, valid_image_bytes)
    monkeypatch.setattr(settings, 'task_queue_enabled', False)
    response = enqueue(client, task)
    assert response.status_code == 503
    assert response.json()['code'] == 'TASK_QUEUE_UNAVAILABLE'
    assert client.get(f'/api/tasks/{task}').json()['status'] == 'pending'
