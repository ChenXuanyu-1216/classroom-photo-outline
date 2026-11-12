"""Deterministic HTTP/SQLite interleavings: deleted tasks cannot gain new rows."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ExportLog, Outline, Task


def saved_task():
    with SessionLocal() as db:
        task = Task(title='并发保存与删除', status='success', page_count=0, owner_id=1)
        db.add(task)
        db.flush()
        db.add(Outline(task_id=task.id, markdown='# 已保存稿\n- 原内容', version=1,
                       recognition_provider='mock'))
        db.commit()
        return task.id


def write(client, task_id, operation):
    if operation == 'save':
        return client.put(f'/api/tasks/{task_id}/outline', json={'markdown': '# 并发新稿\n- 新内容'})
    return client.get(f'/api/tasks/{task_id}/export')


def assert_no_task_data(task_id):
    with SessionLocal() as db:
        assert db.get(Task, task_id) is None
        assert db.query(Outline).filter_by(task_id=task_id).count() == 0
        assert db.query(ExportLog).filter_by(task_id=task_id).count() == 0


@pytest.mark.parametrize('operation', ['save', 'export'])
def test_delete_cannot_finish_between_writer_parent_read_and_insert(client, monkeypatch, operation):
    task_id, other_task = saved_task(), saved_task()
    entered, release = Event(), Event()
    original_commit = Session.commit
    model = Outline if operation == 'save' else ExportLog

    def pause_before_insert(db):
        if any(isinstance(row, model) and row.task_id == task_id for row in db.new):
            entered.set()
            assert release.wait(10), 'test did not release pending writer'
        return original_commit(db)

    monkeypatch.setattr(Session, 'commit', pause_before_insert)
    with ThreadPoolExecutor(max_workers=1) as workers:
        owner = workers.submit(write, client, task_id, operation)
        try:
            assert entered.wait(5), 'writer never reached its insert boundary'
            rejected = client.delete(f'/api/tasks/{task_id}')
            # A short data write must not masquerade as model work or block
            # unrelated records. This uses the real other-record endpoints.
            snapshot = client.get(f'/api/tasks/{task_id}')
            assert write(client, other_task, 'save').status_code == 200
            assert write(client, other_task, 'export').status_code == 200
        finally:
            release.set()
        completed = owner.result(10)
    assert rejected.status_code == 409, rejected.text[:200]
    assert rejected.json()['code'] == 'TASK_BUSY'
    assert snapshot.status_code == 200 and snapshot.json()['status'] == 'success'
    assert completed.status_code == 200, completed.text
    with SessionLocal() as db:
        assert db.get(Task, task_id).status == 'success'
        if operation == 'save':
            assert db.query(Outline).filter_by(task_id=task_id).count() == 2
        else:
            assert db.query(ExportLog).filter_by(task_id=task_id).count() == 1
    assert client.delete(f'/api/tasks/{task_id}').status_code == 204
    assert_no_task_data(task_id)
    assert client.get(f'/api/tasks/{other_task}').status_code == 200


@pytest.mark.parametrize('operation', ['save', 'export'])
def test_delete_owner_rejects_writes_before_its_database_claim(client, monkeypatch, operation):
    task_id = saved_task()
    entered, release = Event(), Event()
    original_get = Session.get

    def pause_after_parent_read(db, entity, ident, *args, **kwargs):
        row = original_get(db, entity, ident, *args, **kwargs)
        if entity is Task and ident == task_id and not entered.is_set():
            entered.set()
            assert release.wait(10), 'test did not release pending deletion'
        return row

    monkeypatch.setattr(Session, 'get', pause_after_parent_read)
    with ThreadPoolExecutor(max_workers=1) as workers:
        owner = workers.submit(client.delete, f'/api/tasks/{task_id}')
        try:
            assert entered.wait(5), 'deletion never reached its parent read'
            rejected = write(client, task_id, operation)
        finally:
            release.set()
        deleted = owner.result(10)
    assert rejected.status_code == 409, rejected.text[:200]
    assert rejected.json()['code'] == 'TASK_BUSY'
    assert deleted.status_code == 204, deleted.text
    assert write(client, task_id, operation).status_code == 404
    assert client.delete(f'/api/tasks/{task_id}').status_code == 404
    assert_no_task_data(task_id)


@pytest.mark.parametrize('operation', ['save', 'export'])
def test_failed_writer_releases_ownership_and_preserves_existing_draft(client, monkeypatch, operation):
    task_id = saved_task()
    original_commit = Session.commit
    model = Outline if operation == 'save' else ExportLog
    fail_once = True

    def interrupted_commit(db):
        nonlocal fail_once
        if fail_once and any(isinstance(row, model) and row.task_id == task_id for row in db.new):
            fail_once = False
            db.flush()  # Exercise a real uncommitted write, not just a pending object.
            raise RuntimeError('injected writer failure before commit')
        return original_commit(db)

    monkeypatch.setattr(Session, 'commit', interrupted_commit)
    assert write(client, task_id, operation).status_code == 500
    assert client.get(f'/api/tasks/{task_id}/outline').json()['markdown'] == '# 已保存稿\n- 原内容'
    assert client.get(f'/api/tasks/{task_id}').json()['status'] == 'success'
    assert write(client, task_id, operation).status_code == 200
    assert client.delete(f'/api/tasks/{task_id}').status_code == 204
    assert_no_task_data(task_id)


@pytest.mark.parametrize('outcome', ['complete', 'exit'])
def test_other_worker_cannot_write_or_delete_until_save_owner_finishes_or_dies(client, outcome):
    task_id = saved_task()
    source = '''
import json, os, sys
from sqlalchemy.orm import Session
from app.db import SessionLocal, engine
from app.models import Outline
from app.routers.outline import update_outline
from app.schemas import OutlineUpdateRequest
task_id = int(sys.argv[1])
original_commit = Session.commit
def pause_after_flush(db):
    if any(isinstance(row, Outline) and row.task_id == task_id for row in db.new):
        db.flush()
        print('READY', flush=True)
        action = sys.stdin.readline().strip()
        if action == 'exit':
            os._exit(73)
        assert action == 'release'
    return original_commit(db)
Session.commit = pause_after_flush
with SessionLocal() as db:
    response = update_outline(task_id, OutlineUpdateRequest(markdown='# child saved draft'), db)
    print(json.dumps({'version': response.version}), flush=True)
engine.dispose()
'''
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    child = subprocess.Popen([sys.executable, '-c', source, str(task_id)],
                             cwd=Path(__file__).resolve().parents[1], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8')
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            ready = workers.submit(child.stdout.readline)
            try:
                assert ready.result(15).strip() == 'READY'
            except BaseException:
                child.kill()
                raise
        for operation in ['save', 'export']:
            rejected = write(client, task_id, operation)
            assert rejected.status_code == 409, rejected.text[:200]
            assert rejected.json()['code'] == 'TASK_BUSY'
        rejected = client.delete(f'/api/tasks/{task_id}')
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()['code'] == 'TASK_BUSY'
        child.stdin.write('exit\n' if outcome == 'exit' else 'release\n')
        child.stdin.flush()
        output, errors = child.communicate(timeout=15)
        if outcome == 'exit':
            assert child.returncode == 73, output + errors
            expected = '# 已保存稿\n- 原内容'
        else:
            assert child.returncode == 0, output + errors
            assert json.loads(output)['version'] == 2
            expected = '# child saved draft'
        assert client.get(f'/api/tasks/{task_id}/outline').json()['markdown'] == expected
        assert client.get(f'/api/tasks/{task_id}').json()['status'] == 'success'
        assert write(client, task_id, 'save').status_code == 200
        assert write(client, task_id, 'export').status_code == 200
        assert client.delete(f'/api/tasks/{task_id}').status_code == 204
        assert_no_task_data(task_id)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
