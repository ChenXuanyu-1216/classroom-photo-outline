"""Offline acceptance evidence, not an estimate of live service availability.

Only provider HTTP is replaced. The real upload, preprocessing, adapters,
grouping, expansion, SQLite persistence and Word exporter remain in the path.
All ledgers/databases/storage belong to pytest; the user's allowance is untouched.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import httpx
from docx import Document
from PIL import Image
import pytest

from app.config import settings
from app.db import SessionLocal, engine
from app.errors import AppError
from app.models import KnowledgePoint, Outline, OutlineNode, Page
from app.prompts_loader import get_prompt_text
from app.services import model_budget
from app.services.pipeline import pipeline
from app.services.recognizer import MultimodalRecognizer
from conftest import TEST_DATABASE


HTTP_CLIENT = httpx.Client
BACKEND = Path(__file__).resolve().parents[1]


def _completion(body, finish="stop"):
    return httpx.Response(200, json={
        "id": "offline-acceptance", "model": "deepseek-flash",
        "choices": [{"finish_reason": finish, "message": {
            "content": json.dumps(body, ensure_ascii=False),
        }}],
    })


def _image(color):
    image = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(image, format="PNG")
    return image.getvalue()


def _upload(client, image, pages=1):
    response = client.post("/api/tasks", files=[
        ("files", (f"page-{index}.png", image, "image/png"))
        for index in range(1, pages + 1)
    ])
    assert response.status_code == 201, response.text
    return response.json()["task_id"]


@pytest.fixture
def offline_provider(tmp_path, monkeypatch):
    """Exercise live adapters while making their only HTTP transport local."""
    monkeypatch.setattr(settings, "model_provider", "deepseek")
    monkeypatch.setattr(settings, "cluster_provider", "model")
    monkeypatch.setattr(settings, "knowledge_expansion_enabled", True)
    monkeypatch.setattr(settings, "model_api_key", "offline-no-real-credentials")
    monkeypatch.setattr(settings, "model_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "model_name", "deepseek-flash")
    monkeypatch.setattr(settings, "model_max_retry", 2)
    ledger = tmp_path / "offline-budget.json"
    monkeypatch.setattr(model_budget, "BUDGET_PATH", ledger)
    monkeypatch.setattr(pipeline, "recognizer", MultimodalRecognizer())
    state = {"requests": [], "hook": None, "ledger": ledger}
    trace_lock = threading.Lock()
    cluster_prompt = get_prompt_text("cluster")

    def handler(request):
        payload = json.loads(request.content)
        content = payload["messages"][1]["content"]
        if isinstance(content, list):
            stage = "recognize"
            context = json.loads(content[0]["text"])
            raw_image = base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])
            with Image.open(io.BytesIO(raw_image)) as image:
                course = "甲课" if image.getpixel((0, 0))[0] > 128 else "乙课"
            body = {
                "title": f"{course}第{context['page_no']}页",
                "points": [{"content": f"{course}：第{context['page_no']}页课堂概念",
                            "kp_type": "concept", "source_type": "课堂原话"}],
                "confidence": 0.95, "readability": "clear", "uncertain_regions": [],
            }
        else:
            context = json.loads(content)
            course = context["points"][0]["content"].split("：", 1)[0]
            ids = [point["id"] for point in context["points"]]
            stage = "cluster" if payload["messages"][0]["content"] == cluster_prompt else "expand"
            body = ({"modules": [{"title": f"{course}主题", "point_ids": list(reversed(ids))}]}
                    if stage == "cluster" else {"points": [{
                        "content": f"{course}：拓展适用边界", "kp_type": "conclusion",
                        "based_on_ids": ids,
                    }]})
        with trace_lock:
            state["requests"].append({"stage": stage, "context": context, "payload": payload})
        if state["hook"] is not None:
            response = state["hook"](stage, context, body, request)
            if response is not None:
                return response
        return _completion(body)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: HTTP_CLIENT(
        transport=httpx.MockTransport(handler), **kwargs))
    return state


def _run_child(source, argument, expected_exit=0):
    # The child does not import pytest/conftest (which would allocate another DB).
    # Pin every location before app imports, and disable credentials/model calls.
    assert Path(engine.url.database).resolve() == TEST_DATABASE.resolve()
    env = os.environ.copy()
    env.update({
        "APP_DATABASE_URL": str(engine.url),
        "APP_DATA_DIR": str(TEST_DATABASE.parent),
        "APP_STORAGE_DIR": os.environ["APP_STORAGE_DIR"],
        "APP_MODEL_PROVIDER": "mock", "APP_MODEL_API_KEY": "",
        "APP_CLUSTER_PROVIDER": "rule", "APP_KNOWLEDGE_EXPANSION_ENABLED": "false",
        "PYTHONIOENCODING": "utf-8",
    })
    deny_network = '''
import httpx
def deny_network(*args, **kwargs):
    raise AssertionError("Offline acceptance child attempted external HTTP")
httpx.HTTPTransport.handle_request = deny_network
'''
    result = subprocess.run([sys.executable, "-c", deny_network + source, str(argument)],
                            cwd=BACKEND, env=env, capture_output=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == expected_exit, result.stdout + result.stderr
    return result


def _new_process_snapshot(task_id):
    result = _run_child('''
import io, json, os, sys
from docx import Document
from fastapi.testclient import TestClient
from app.db import SessionLocal, engine
from app.main import app
from app.models import KnowledgePoint
task = int(sys.argv[1])
with TestClient(app, headers={"Authorization": "Bearer " + os.environ["TEST_AUTH_BEARER_TOKEN"]}) as client:
    outline = client.get(f"/api/tasks/{task}/outline")
    exported = client.get(f"/api/tasks/{task}/export")
    assert outline.status_code == exported.status_code == 200
    with SessionLocal() as db:
        points = db.query(KnowledgePoint).filter_by(task_id=task).count()
    print(json.dumps({"markdown": outline.json()["markdown"], "points": points,
        "database": engine.url.database,
        "status": client.get(f"/api/tasks/{task}").json()["status"],
        "paragraphs": [p.text for p in Document(io.BytesIO(exported.content)).paragraphs]},
        ensure_ascii=False))
engine.dispose()
''', task_id)
    snapshot = json.loads(result.stdout)
    assert Path(snapshot["database"]).resolve() == TEST_DATABASE.resolve()
    return snapshot


def test_bounded_fault_recovery_persists_cross_page_outline_and_reopens_word(
        client, offline_provider):
    """Catch lost retry reservations, cross-page anchors or durable draft data."""
    remaining = {"recognize": ["timeout", "503"], "cluster": ["503"], "expand": ["timeout"]}

    def transient(stage, context, body, request):
        faults = remaining[stage]
        if faults:
            fault = faults.pop(0)
            if fault == "timeout":
                raise httpx.ReadTimeout("injected offline timeout", request=request)
            return httpx.Response(503)

    offline_provider["hook"] = transient
    task = _upload(client, _image("red"), pages=2)
    response = client.post(f"/api/tasks/{task}/pipeline")
    assert response.status_code == 200, response.text
    assert response.json() == {"task_id": task, "status": "success",
                               "knowledge_point_count": 3, "module_count": 2}
    saved = client.get(f"/api/tasks/{task}/outline").json()["markdown"]
    assert "甲课：第1页课堂概念（来源：P1）【课堂原话】" in saved
    assert "甲课：第2页课堂概念（来源：P2）【课堂原话】" in saved
    assert "甲课：拓展适用边界（关联课堂页：P1,P2）【AI生成】" in saved
    assert saved.index("第2页课堂概念") < saved.index("第1页课堂概念")
    edited = saved.replace("# 课程大纲", "# 人工校对稿")
    assert client.put(f"/api/tasks/{task}/outline", json={"markdown": edited}).status_code == 200
    with SessionLocal() as db:
        pages = db.query(Page).filter_by(task_id=task).order_by(Page.page_no).all()
        assert [page.recognized_structured["_meta"]["attempts"] for page in pages] == [3, 1]
        assert [len(page.recognized_structured["points"]) for page in pages] == [1, 1]
        assert db.query(KnowledgePoint).filter_by(task_id=task).count() == 3
        assert db.query(OutlineNode).filter_by(source_type="AI生成").one().source_pages == "P1,P2"
    assert [call["stage"] for call in offline_provider["requests"]] == [
        "recognize", "recognize", "recognize", "recognize", "cluster", "cluster", "expand", "expand"]
    assert json.loads(offline_provider["ledger"].read_text())["reserved_fen"] == 80
    reopened = _new_process_snapshot(task)
    assert reopened["markdown"] == edited
    assert "甲课：拓展适用边界（关联课堂页：P1,P2） 【AI生成】" in reopened["paragraphs"]
    assert reopened["points"] == 3


@pytest.mark.parametrize("stage,fault,code,extra_calls", [
    ("cluster", "truncated", "CLUSTERING_FAILED", 1),
    ("cluster", "foreign_id", "CLUSTERING_FAILED", 1),
    ("expand", "forged_source", "KNOWLEDGE_EXPANSION_FAILED", 2),
    ("expand", "malformed", "KNOWLEDGE_EXPANSION_FAILED", 2),
    ("cluster", "unavailable", "CLUSTERING_FAILED", 3),
])
def test_rejected_regeneration_preserves_edited_draft_and_old_knowledge(
        client, offline_provider, stage, fault, code, extra_calls):
    """Catch validation after destructive replacement, or unbounded retries."""
    task = _upload(client, _image("red"))
    assert client.post(f"/api/tasks/{task}/pipeline").status_code == 200
    edited = client.get(f"/api/tasks/{task}/outline").json()["markdown"].replace(
        "# 课程大纲", "# 教师手工修订，必须保留")
    assert client.put(f"/api/tasks/{task}/outline", json={"markdown": edited}).status_code == 200
    with SessionLocal() as db:
        before = [(point.id, point.content, point.source_type, point.source_pages)
                  for point in db.query(KnowledgePoint).filter_by(task_id=task).order_by(KnowledgePoint.id)]
        versions = [(outline.id, outline.version, outline.markdown)
                    for outline in db.query(Outline).filter_by(task_id=task).order_by(Outline.id)]

    def reject(current_stage, context, body, request):
        if current_stage != stage:
            return None
        if fault == "truncated":
            return _completion(body, finish="length")
        if fault == "foreign_id":
            return _completion({"modules": [{"title": "伪造模块", "point_ids": ["kp_999"]}]})
        if fault == "forged_source":
            body["points"][0].update(source_type="课堂原话", source_pages="P999")
            return _completion(body)
        if fault == "unavailable":
            return httpx.Response(503)
        return httpx.Response(200, content=b'{"choices":[')

    offline_provider["hook"] = reject
    failed = client.post(f"/api/tasks/{task}/regenerate")
    assert failed.status_code == 502, failed.text
    assert failed.json()["code"] == code
    assert client.get(f"/api/tasks/{task}").json()["status"] == "failed"
    assert client.get(f"/api/tasks/{task}/outline").json()["markdown"] == edited
    with SessionLocal() as db:
        after = [(point.id, point.content, point.source_type, point.source_pages)
                 for point in db.query(KnowledgePoint).filter_by(task_id=task).order_by(KnowledgePoint.id)]
        assert after == before
        assert [(outline.id, outline.version, outline.markdown)
                for outline in db.query(Outline).filter_by(task_id=task).order_by(Outline.id)] == versions
    exported = client.get(f"/api/tasks/{task}/export")
    assert exported.status_code == 200
    paragraphs = [paragraph.text for paragraph in Document(io.BytesIO(exported.content)).paragraphs]
    assert "教师手工修订，必须保留" in paragraphs
    assert "甲课：拓展适用边界（关联课堂页：P1） 【AI生成】" in paragraphs
    assert len(offline_provider["requests"]) == 3 + extra_calls
    assert json.loads(offline_provider["ledger"].read_text())["reserved_fen"] == (3 + extra_calls) * 10


def test_two_overlapping_independent_tasks_keep_contexts_and_records_separate(
        client, offline_provider):
    """A stalled model request must not hold SQLite's write transaction open.

    This is two controlled overlapping tasks, not an arbitrary-load claim.
    """
    first = _upload(client, _image("red"))
    second = _upload(client, _image("blue"))
    first_in_grouping = threading.Event()
    release_first = threading.Event()

    def overlap(stage, context, body, request):
        if stage == "cluster" and context["points"][0]["content"].startswith("甲课"):
            first_in_grouping.set()
            assert release_first.wait(timeout=10), "Other task did not complete while grouping was blocked"

    offline_provider["hook"] = overlap
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = workers.submit(client.post, f"/api/tasks/{first}/pipeline")
        try:
            assert first_in_grouping.wait(timeout=10), "First task never entered model grouping"
            assert client.get(f"/api/tasks/{first}").json()["status"] == "running"
            completed = client.post(f"/api/tasks/{second}/pipeline")
            assert completed.status_code == 200, completed.text
            assert completed.json()["status"] == "success"
            assert client.get(f"/api/tasks/{first}").json()["status"] == "running"
        finally:
            release_first.set()
        first_result = pending.result(timeout=10)
    assert first_result.status_code == 200, first_result.text
    assert first_result.json()["status"] == "success"
    for task, own, foreign in [(first, "甲课", "乙课"), (second, "乙课", "甲课")]:
        saved = client.get(f"/api/tasks/{task}/outline").json()["markdown"]
        assert own + "：第1页课堂概念" in saved and own + "：拓展适用边界" in saved
        assert foreign not in saved
        with SessionLocal() as db:
            page = db.query(Page).filter_by(task_id=task).one()
            points = db.query(KnowledgePoint).filter_by(task_id=task).all()
            assert len(points) == 2
            assert all(point.page_id == page.id and point.content.startswith(own) for point in points)
            assert Path(page.file_path).is_file()
    with SessionLocal() as db:
        assert len({page.file_path for page in db.query(Page).all()}) == 2
    contexts = [call["context"] for call in offline_provider["requests"] if call["stage"] != "recognize"]
    assert len(contexts) == 4
    for context in contexts:
        supplied = context["points"]
        assert len(supplied) == 1
        assert supplied[0]["source_type"] == "课堂原话"
        assert supplied[0]["source_pages"] == ["P1"]
    assert json.loads(offline_provider["ledger"].read_text())["reserved_fen"] == 60


def test_competing_budget_reservation_refuses_busy_then_never_overspends(
        tmp_path, monkeypatch):
    """Catch a missing exclusive lock or a cap check outside its transaction."""
    ledger = tmp_path / "shared-offline-budget.json"
    ledger.write_text('{"reserved_fen":490}', encoding="utf-8")
    acquired = threading.Event()
    release = threading.Event()
    read_text = Path.read_text

    def hold_locked_read(path, *args, **kwargs):
        if path == ledger and threading.current_thread().name.startswith("budget-holder"):
            acquired.set()
            assert release.wait(timeout=10), "Budget holder was not released"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", hold_locked_read)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="budget-holder") as workers:
        first = workers.submit(model_budget.reserve_budget, ledger)
        try:
            assert acquired.wait(timeout=10), "Reservation never acquired its lock"
            with pytest.raises(AppError) as busy:
                model_budget.reserve_budget(ledger)
            assert busy.value.code == "TEST_BUDGET_BUSY"
            assert json.loads(ledger.read_text())["reserved_fen"] == 490
        finally:
            release.set()
        assert first.result(timeout=10) == 500
    with pytest.raises(AppError) as exhausted:
        model_budget.reserve_budget(ledger)
    assert exhausted.value.code == "TEST_BUDGET_EXHAUSTED"
    assert json.loads(ledger.read_text())["reserved_fen"] == 500


@pytest.fixture
def interrupted_worker_snapshot(client, offline_provider):
    """Stop one test child after committing running status, then reopen the DB.

    The only process terminated is this test's child. A grouping transport exits
    after the actual pipeline has committed its running status and page state.
    """
    task = _upload(client, _image("red"))
    assert client.post(f"/api/tasks/{task}/pipeline").status_code == 200
    edited = client.get(f"/api/tasks/{task}/outline").json()["markdown"].replace(
        "# 课程大纲", "# 中断前保存的人工稿")
    assert client.put(f"/api/tasks/{task}/outline", json={"markdown": edited}).status_code == 200
    _run_child('''
import os, sys
from app.config import settings
from app.db import SessionLocal
from app.services.pipeline import pipeline
settings.model_provider = "deepseek"
settings.cluster_provider = "model"
settings.knowledge_expansion_enabled = True
settings.model_api_key = "offline-interruption-only"
settings.model_base_url = "https://api.deepseek.com"
settings.model_name = "deepseek-flash"
from app.services import model_budget
model_budget.BUDGET_PATH = model_budget.BUDGET_PATH.with_name("interrupted-child-offline-budget.json")
original_client = httpx.Client
def interrupted_model(request):
    os._exit(73)
httpx.Client = lambda **kwargs: original_client(transport=httpx.MockTransport(interrupted_model), **kwargs)
with SessionLocal() as db:
    pipeline.run_pipeline(db, int(sys.argv[1]))
''', task, expected_exit=73)
    reopened = _new_process_snapshot(task)
    return edited, reopened


def test_interrupted_worker_preserves_draft_for_a_separate_process_reopen(
        interrupted_worker_snapshot):
    """Prove durable draft/export only; restart recovery is a separate test."""
    edited, reopened = interrupted_worker_snapshot
    assert reopened["markdown"] == edited
    assert "中断前保存的人工稿" in reopened["paragraphs"]
    assert reopened["points"] == 2
    assert "甲课：拓展适用边界（关联课堂页：P1） 【AI生成】" in reopened["paragraphs"]


def test_interrupted_running_task_is_reconciled_after_restart(
        interrupted_worker_snapshot, record_property):
    """An exited execution owner must not leave a permanently running task."""
    _, reopened = interrupted_worker_snapshot
    record_property("observed_status_after_interruption_and_restart", reopened["status"])
    assert reopened["status"] == "failed"


def test_same_task_overlap_rejects_duplicate_model_execution(
        client, offline_provider, record_property):
    """A second active execution cannot consume another model allowance."""
    task = _upload(client, _image("red"))
    assert client.post(f"/api/tasks/{task}/pipeline").status_code == 200
    entered = threading.Event()
    release = threading.Event()
    grouping_lock = threading.Lock()
    grouping_entries = 0

    def stall_first_grouping(stage, context, body, request):
        nonlocal grouping_entries
        if stage == "cluster":
            with grouping_lock:
                grouping_entries += 1
                is_first = grouping_entries == 1
            if is_first:
                entered.set()
                assert release.wait(timeout=10), "Second execution never finished"

    offline_provider["hook"] = stall_first_grouping
    before = len(offline_provider["requests"])
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = workers.submit(client.post, f"/api/tasks/{task}/regenerate")
        try:
            assert entered.wait(timeout=10)
            second = client.post(f"/api/tasks/{task}/regenerate")
        finally:
            release.set()
        first = pending.result(timeout=10)
    assert first.status_code == 200, first.text
    stages = [call["stage"] for call in offline_provider["requests"][before:]]
    record_property("overlapping_same_task_http_statuses", f"{first.status_code},{second.status_code}")
    record_property("overlapping_same_task_model_stages", ",".join(stages))
    assert second.status_code == 409, second.text
    assert stages == ["cluster", "expand"]
    assert json.loads(offline_provider["ledger"].read_text())["reserved_fen"] == 50


def test_interrupted_budget_holder_recovers_without_losing_prior_reservations(tmp_path):
    """An exited owner must release exclusion while retaining the prior ledger."""
    ledger = tmp_path / "interrupted-offline-budget.json"
    ledger.write_text('{"reserved_fen":490}', encoding="utf-8")
    _run_child('''
import os, sys
from pathlib import Path
from app.services.model_budget import reserve_budget
ledger = Path(sys.argv[1])
original_read = Path.read_text
def exit_inside_locked_read(path, *args, **kwargs):
    if path == ledger:
        os._exit(74)
    return original_read(path, *args, **kwargs)
Path.read_text = exit_inside_locked_read
reserve_budget(ledger)
''', ledger, expected_exit=74)
    assert json.loads(ledger.read_text())["reserved_fen"] == 490
    assert model_budget.reserve_budget(ledger) == 500
    with pytest.raises(AppError) as exhausted:
        model_budget.reserve_budget(ledger)
    assert exhausted.value.code == "TEST_BUDGET_EXHAUSTED"
    assert json.loads(ledger.read_text())["reserved_fen"] == 500


def test_regeneration_refreshes_old_prompt_recognition_before_regrouping(client, offline_provider):
    """New quality rules must reach existing records when users regenerate."""
    task = _upload(client, _image('red'))
    assert client.post(f'/api/tasks/{task}/pipeline').status_code == 200
    with SessionLocal() as db:
        page = db.query(Page).filter_by(task_id=task).one()
        body = dict(page.recognized_structured)
        body['_meta'] = {**body['_meta'], 'prompt_version': '1.4.0'}
        page.recognized_structured = body
        db.commit()
    before = len(offline_provider['requests'])
    response = client.post(f'/api/tasks/{task}/regenerate')
    assert response.status_code == 200, response.text
    assert [request['stage'] for request in offline_provider['requests'][before:]] == ['recognize', 'cluster', 'expand']


def test_partial_stale_refresh_with_failed_diagram_review_preserves_manual_draft(client, offline_provider):
    task = _upload(client, _image('red'), pages=2)
    assert client.post(f'/api/tasks/{task}/pipeline').status_code == 200
    markdown = client.get(f'/api/tasks/{task}/outline').json()['markdown'].replace('# 课程大纲', '# 已校对的完整人工稿')
    assert client.put(f'/api/tasks/{task}/outline', json={'markdown': markdown}).status_code == 200
    with SessionLocal() as db:
        page = db.query(Page).filter_by(task_id=task, page_no=1).one()
        page.recognized_structured = {**page.recognized_structured,
            '_meta': {**page.recognized_structured['_meta'], 'prompt_version': '1.4.0'}}
        before_points = [(p.id, p.content) for p in db.query(KnowledgePoint).filter_by(task_id=task).all()]
        db.commit()
    recognition_calls = 0
    def fail_review(stage, context, body, request):
        nonlocal recognition_calls
        if stage == 'recognize':
            recognition_calls += 1
            if recognition_calls == 1:
                body['relations'] = [{'parent': '主题', 'children': ['分支']}]
                return _completion(body)
            return _completion(body, finish='length')
    offline_provider['hook'] = fail_review
    before_calls = len(offline_provider['requests'])
    failed = client.post(f'/api/tasks/{task}/regenerate')
    assert failed.status_code == 502, failed.text
    assert failed.json()['code'] == 'REGENERATION_RECOGNITION_FAILED'
    assert [r['stage'] for r in offline_provider['requests'][before_calls:]] == ['recognize', 'recognize']
    assert client.get(f'/api/tasks/{task}/outline').json()['markdown'] == markdown
    with SessionLocal() as db:
        assert [(p.id, p.content) for p in db.query(KnowledgePoint).filter_by(task_id=task).all()] == before_points
    paragraphs = [p.text for p in Document(io.BytesIO(client.get(f'/api/tasks/{task}/export').content)).paragraphs]
    assert '已校对的完整人工稿' in paragraphs
