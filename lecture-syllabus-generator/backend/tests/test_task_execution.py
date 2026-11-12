"""Execution ownership is verified through real HTTP, SQLite and child workers."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
import threading

import pytest

from app.db import SessionLocal
from app.models import Task
from test_acceptance_reliability import (
    BACKEND, _image, _new_process_snapshot, _upload, offline_provider,
)


@pytest.mark.parametrize("owner_endpoint", ["recognize", "pipeline", "regenerate"])
@pytest.mark.parametrize("competing_endpoint", ["recognize", "pipeline", "regenerate"])
def test_every_model_route_rejects_a_same_task_owner_without_extra_calls(
        client, offline_provider, owner_endpoint, competing_endpoint):
    """Removing any route's ownership guard would spend a second allowance."""
    task = _upload(client, _image("red"))
    assert client.post(f"/api/tasks/{task}/pipeline").status_code == 200
    saved = client.get(f"/api/tasks/{task}/outline").json()["markdown"]
    entered, release = threading.Event(), threading.Event()

    def hold_first_request(stage, context, body, request):
        if not entered.is_set():
            entered.set()
            assert release.wait(10), "Owner was not released"

    offline_provider["hook"] = hold_first_request
    before = len(offline_provider["requests"])
    with ThreadPoolExecutor(max_workers=1) as workers:
        owner = workers.submit(client.post, f"/api/tasks/{task}/{owner_endpoint}")
        try:
            assert entered.wait(10), "Owner never entered provider transport"
            rejected = client.post(f"/api/tasks/{task}/{competing_endpoint}")
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["code"] == "TASK_BUSY"
            assert client.get(f"/api/tasks/{task}").json()["status"] == "running"
            listed = client.get("/api/tasks").json()["tasks"]
            assert next(row for row in listed if row["task_id"] == task)["status"] == "running"
            assert client.get(f"/api/tasks/{task}/outline").json()["markdown"] == saved
            assert len(offline_provider["requests"]) == before + 1
        finally:
            release.set()
        completed = owner.result(10)
    assert completed.status_code == 200, completed.text


@pytest.mark.parametrize("endpoint", ["task", "list"])
def test_existing_worker_reconciles_an_unowned_running_status_on_read(
        client, valid_image_bytes, endpoint):
    """Startup alone cannot recover a sibling worker that dies afterwards."""
    task = _upload(client, valid_image_bytes)
    with SessionLocal() as db:
        db.get(Task, task).status = "running"
        db.commit()
    if endpoint == "task":
        observed = client.get(f"/api/tasks/{task}").json()
        assert observed["status"] == "failed"
        assert observed["error_msg"]
    else:
        observed = client.get("/api/tasks").json()["tasks"]
        assert next(row for row in observed if row["task_id"] == task)["status"] == "failed"
    # Recovery is a status repair; it never retries a paid operation.
    with SessionLocal() as db:
        assert db.get(Task, task).status == "failed"


@pytest.mark.parametrize("owner_outcome", ["complete", "exit"])
def test_other_process_owner_survives_startup_reconciliation_and_rejects_requests(
        client, offline_provider, owner_outcome):
    """A new worker must not overwrite or bypass a live worker's ownership."""
    task = _upload(client, _image("red"))
    assert client.post(f"/api/tasks/{task}/pipeline").status_code == 200
    saved = client.get(f"/api/tasks/{task}/outline").json()["markdown"]
    source = '''
import json, os, sys
import httpx
from app.config import settings
from app.db import SessionLocal
from app.services.pipeline import pipeline
settings.cluster_provider = "model"
settings.knowledge_expansion_enabled = False
settings.model_provider = "deepseek"
settings.model_api_key = "offline-child-only"
original_client = httpx.Client
def grouping(request):
    context = json.loads(json.loads(request.content)["messages"][1]["content"])
    print("READY", flush=True)
    action = sys.stdin.readline().strip()
    if action == "exit":
        os._exit(73)
    assert action == "release"
    body = {"modules": [{"title": "子进程模块", "point_ids": [p["id"] for p in context["points"]]}]}
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(body, ensure_ascii=False)}}]})
httpx.Client = lambda **kwargs: original_client(transport=httpx.MockTransport(grouping), **kwargs)
with SessionLocal() as db:
    print(json.dumps(pipeline.run_pipeline(db, int(sys.argv[1]))), flush=True)
'''
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    child = subprocess.Popen([sys.executable, "-c", source, str(task)], cwd=BACKEND,
                             env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            ready = workers.submit(child.stdout.readline)
            try:
                assert ready.result(15).strip() == "READY"
            except BaseException:
                child.kill()
                raise
        snapshot = _new_process_snapshot(task)
        assert snapshot["status"] == "running"
        assert snapshot["markdown"] == saved
        before = len(offline_provider["requests"])
        for endpoint in ("recognize", "pipeline", "regenerate"):
            rejected = client.post(f"/api/tasks/{task}/{endpoint}")
            assert rejected.status_code == 409, rejected.text
        assert len(offline_provider["requests"]) == before
        assert client.get(f"/api/tasks/{task}").json()["status"] == "running"
        child.stdin.write("exit\n" if owner_outcome == "exit" else "release\n")
        child.stdin.flush()
        output, errors = child.communicate(timeout=15)
        if owner_outcome == "exit":
            assert child.returncode == 73, output + errors
            recovered = client.get(f"/api/tasks/{task}").json()
            assert recovered["status"] == "failed"
            assert recovered["error_msg"]
            assert client.get(f"/api/tasks/{task}/outline").json()["markdown"] == saved
            assert len(offline_provider["requests"]) == before
            retried = client.post(f"/api/tasks/{task}/pipeline")
            assert retried.status_code == 200, retried.text
            assert retried.json()["status"] == "success"
        else:
            assert child.returncode == 0, output + errors
            assert json.loads(output)["status"] == "success"
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_recovered_task_can_be_explicitly_retried(client, offline_provider):
    """An orphan status cannot permanently block the teacher's explicit retry."""
    task = _upload(client, _image("red"))
    with SessionLocal() as db:
        db.get(Task, task).status = "running"
        db.commit()
    assert client.get(f"/api/tasks/{task}").json()["status"] == "failed"
    completed = client.post(f"/api/tasks/{task}/pipeline")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "success"
