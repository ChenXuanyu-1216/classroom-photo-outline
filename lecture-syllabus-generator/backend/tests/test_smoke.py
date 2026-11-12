import io
import pytest
from app.models import AppUser

pytestmark = pytest.mark.usefixtures("test_user")
from fastapi import UploadFile
from app.routers import meta, tasks, outline, export
from app.errors import (
    AppError, TaskNotFoundError, FileFormatError, app_error_handler,
)
from app.schemas import OutlineUpdateRequest

from app.db import SessionLocal


def _make_upload(name: str = "test.png", content: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100):
    return UploadFile(file=io.BytesIO(content), filename=name)


def _new_db():
    return SessionLocal()


def test_health():
    result = meta.health()
    data = result.model_dump()
    assert data["status"] == "ok"
    assert data["app"] == "课堂PPT照片智能大纲生成器"
    assert "MVP Design" in data["baseline_version"]
    assert data["model_provider"] == "mock"


def test_upload_rejects_bad_format():
    db = _new_db()
    bad_file = _make_upload("test.txt", b"not an image")
    with pytest.raises(FileFormatError) as exc_info:
        tasks.create_task(files=[bad_file], order="", db=db, user=AppUser(id=1))
    assert exc_info.value.code == "FILE_FORMAT_ERROR"
    db.rollback()
    db.close()

    db = _new_db()
    good_file = _make_upload("page1.png")
    result = tasks.create_task(files=[good_file], order="", db=db, user=AppUser(id=1))
    data = result.model_dump()
    assert data["task_id"] > 0
    assert data["page_count"] == 1
    assert data["rejected"] == []
    db.close()


def _run_async(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_error_contract_unknown_task():
    with _new_db() as db, pytest.raises(TaskNotFoundError) as exc_info:
        tasks.get_task(task_id=999999, db=db)
    exc = exc_info.value
    assert exc.code == "TASK_NOT_FOUND"
    assert exc.message == "任务不存在。"
    assert exc.retryable is False

    from fastapi.responses import JSONResponse
    response = JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
    )
    assert response.status_code == 404
    import json
    body = json.loads(bytes(response.body).decode("utf-8"))
    assert body["code"] == "TASK_NOT_FOUND"
    assert body["message"] == "任务不存在。"
    assert body["retryable"] is False


def test_end_to_end_pipeline_and_edit_export():
    db = _new_db()
    files = [
        _make_upload("slide1.png"),
        _make_upload("slide2.png"),
        _make_upload("slide3.png"),
    ]
    result = tasks.create_task(files=files, order="3,1,2", db=db, user=AppUser(id=1))
    task_data = result.model_dump()
    task_id = task_data["task_id"]
    assert task_data["page_count"] == 3
    assert task_data["rejected"] == []
    db.close()

    db = _new_db()
    pages_resp = tasks.get_pages(task_id=task_id, db=db)
    pages = pages_resp.model_dump()["pages"]
    assert len(pages) == 3
    page_nos = [p["page_no"] for p in pages]
    assert page_nos == [1, 2, 3]
    db.close()

    db = _new_db()
    pipe_resp = tasks.run_pipeline(task_id=task_id, db=db)
    pipe_data = pipe_resp.model_dump()
    assert pipe_data["status"] == "success"
    assert pipe_data["knowledge_point_count"] == 12
    assert pipe_data["module_count"] == 3
    db.close()

    db = _new_db()
    outline_resp = outline.get_outline(task_id=task_id, db=db)
    outline_data = outline_resp.model_dump()
    markdown = outline_data["markdown"]
    assert "设计概述" in markdown
    assert "范式与函数依赖" in markdown
    assert "索引与查询优化" in markdown
    assert "来源：" in markdown
    assert "课堂原话" in markdown
    assert "AI推断" in markdown
    original_version = outline_data["version"]
    db.close()

    db = _new_db()
    edited = markdown.replace("关系数据库基本概念", "关系数据库基本概念（重点）")
    update_resp = outline.update_outline(task_id=task_id, body=OutlineUpdateRequest(markdown=edited), db=db)
    assert update_resp.model_dump()["version"] == original_version + 1
    db.close()

    db = _new_db()
    reopen_resp = outline.get_outline(task_id=task_id, db=db)
    reopen_data = reopen_resp.model_dump()
    assert reopen_data["markdown"] == edited
    assert reopen_data["version"] == original_version + 1
    db.close()

    db = _new_db()
    export_resp = export.export_word(task_id=task_id, db=db)
    assert export_resp.media_type.startswith("application/vnd.openxmlformats")
    from app.models import Outline as OutlineModel
    from app.services.exporter import exporter as docx_exporter
    latest_outline = db.query(OutlineModel).filter(OutlineModel.task_id == task_id).order_by(OutlineModel.version.desc()).first()
    docx_bytes = docx_exporter.export(latest_outline.markdown)
    assert docx_bytes[:2] == b"PK"
    assert len(docx_bytes) > 1000
    db.close()

    db = _new_db()
    list_resp = tasks.list_tasks(db=db, user=AppUser(id=1))
    tasks_list = list_resp.model_dump()["tasks"]
    assert any(t["task_id"] == task_id for t in tasks_list)
    db.close()
