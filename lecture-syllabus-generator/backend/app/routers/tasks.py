from fastapi import APIRouter, Depends, UploadFile, File, Form, Response, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings, STORAGE_DIR
from app.models import Task, Page
from app.schemas import (
    TaskCreateResponse, TaskStatusResponse, PagesResponse,
    PageDetail, PipelineResponse, TaskListResponse, TaskListItem,
)
from app.errors import TaskNotFoundError
from app.services.pipeline import pipeline
from app.services.upload_service import create_task_from_uploads
from app.services.task_deletion import delete_task
from app.services.task_execution import reconcile_running_tasks
from app.services.task_jobs import submit_job, get_job, reconcile_jobs, task_job_state
from app.services.auth_service import get_current_user, require_task_owner
from app.models import AppUser

router = APIRouter(dependencies=[Depends(require_task_owner)])


@router.post("/api/tasks", response_model=TaskCreateResponse, status_code=201)
def create_task(
    files: list[UploadFile] = File(...),
    order: str = Form(""),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """上传接口：接收一批 PPT 照片和页码顺序，保存文件并为当前用户新建一个任务。"""
    return create_task_from_uploads(db, files, order, STORAGE_DIR, settings, owner_id=user.id)


@router.get("/api/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: int, db: Session = Depends(get_db)):
    reconcile_jobs(db)
    reconcile_running_tasks(db, task_id)
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise TaskNotFoundError()
    return TaskStatusResponse(
        task_id=task.id,
        status=task.status,
        page_count=task.page_count,
        error_msg=task.error_msg,
        created_at=task.created_at,
        **task_job_state(db, task_id),
    )


@router.delete('/api/tasks/{task_id}', status_code=204)
def remove_task(task_id: int, db: Session = Depends(get_db)):
    delete_task(db, task_id, STORAGE_DIR)
    return Response(status_code=204)


@router.post("/api/tasks/{task_id}/recognize")
def recognize_task(task_id: int, page_no: int = None, db: Session = Depends(get_db), prefer: str = Header(default='')):
    """识别接口：把任务里的照片（或只指定的一页）转成文字；客户端声明 Prefer: respond-async 或云托管部署时转为后台作业，返回 202，稍后用作业接口查询结果。"""
    if settings.deployment_mode == 'cloud' or isinstance(prefer, str) and 'respond-async' in prefer.lower():
        return JSONResponse(status_code=202, content=submit_job(db, task_id, 'recognize', page_no))
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise TaskNotFoundError()
    result = pipeline.run_recognize(db, task_id, page_no)
    return result


@router.post("/api/tasks/{task_id}/pipeline", response_model=PipelineResponse)
def run_pipeline(task_id: int, db: Session = Depends(get_db), prefer: str = Header(default='')):
    """生成接口：先识别任务里还没识别（或识别结果已过期）的照片，再整理知识点生成大纲；客户端声明 Prefer: respond-async 或云托管部署时转为后台作业，返回 202，稍后用作业接口查询结果。"""
    if settings.deployment_mode == 'cloud' or isinstance(prefer, str) and 'respond-async' in prefer.lower():
        return JSONResponse(status_code=202, content=submit_job(db, task_id, 'pipeline'))
    result = pipeline.run_pipeline(db, task_id)
    return PipelineResponse(**result)


@router.get("/api/tasks/{task_id}/pages", response_model=PagesResponse)
def get_pages(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise TaskNotFoundError()
    pages = db.query(Page).filter(Page.task_id == task_id).order_by(Page.page_no).all()
    return PagesResponse(
        task_id=task_id,
        pages=[
            PageDetail(
                page_no=p.page_no,
                filename=p.filename,
                confidence=p.confidence,
                low_confidence_flag=p.low_confidence_flag,
                failed_flag=p.failed_flag,
                error_msg=p.error_msg,
                error_code=p.error_code,
                error_retryable=p.error_retryable,
            )
            for p in pages
        ],
    )


@router.get("/api/tasks", response_model=TaskListResponse)
def list_tasks(db: Session = Depends(get_db), user: AppUser = Depends(get_current_user)):
    reconcile_jobs(db)
    reconcile_running_tasks(db)
    tasks = db.query(Task).filter(Task.owner_id == user.id).order_by(Task.created_at.desc()).all()
    return TaskListResponse(
        tasks=[
            TaskListItem(
                task_id=t.id,
                title=t.title,
                status=t.status,
                page_count=t.page_count,
                created_at=t.created_at,
            )
            for t in tasks
        ]
    )


@router.get('/api/tasks/{task_id}/jobs/{job_id}')
def get_task_job(task_id: int, job_id: str, db: Session = Depends(get_db)):
    return get_job(db, task_id, job_id)
