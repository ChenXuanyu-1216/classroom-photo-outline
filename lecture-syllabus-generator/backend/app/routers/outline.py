from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import settings
from app.models import Task, Outline, Page
from app.schemas import OutlineResponse, OutlineUpdateRequest, OutlineUpdateResponse, PipelineResponse
from app.errors import AppError, TaskNotFoundError, PipelineError
from app.services.pipeline import pipeline
from app.services.task_execution import task_edit_lock
from app.services.task_jobs import reject_active_job, submit_job
from app.services.auth_service import require_task_owner

router = APIRouter(dependencies=[Depends(require_task_owner)])


@router.get("/api/tasks/{task_id}/outline", response_model=OutlineResponse)
def get_outline(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise TaskNotFoundError()
    outline = db.query(Outline).filter(Outline.task_id == task_id).order_by(Outline.version.desc()).first()
    if not outline:
        raise PipelineError("大纲尚未生成，请先执行管道。")
    pages = db.query(Page).filter(Page.task_id == task_id).order_by(Page.page_no).all()
    providers = {((page.recognized_structured or {}).get('_meta') or {}).get('provider') for page in pages}
    return OutlineResponse(
        task_id=task_id,
        markdown=outline.markdown,
        version=outline.version,
        warnings=[f'P{page.page_no}：{region}'
                  for page in pages
                  for region in ((page.recognized_structured or {}).get('uncertain_regions', [])
                                 or (['部分内容需对照原图核对'] if page.low_confidence_flag else []))],
        recognition_mode=outline.recognition_provider or 'unverified',
    )


@router.put("/api/tasks/{task_id}/outline", response_model=OutlineUpdateResponse)
def update_outline(task_id: int, body: OutlineUpdateRequest, db: Session = Depends(get_db)):
    with task_edit_lock(db, task_id):
        reject_active_job(db, task_id)
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise TaskNotFoundError()
        if task.status in {'queued', 'running', 'deleting'}:
            raise AppError('TASK_BUSY', '任务正在处理中，请完成后再保存。', retryable=True, status_code=409)
        latest = db.query(Outline).filter(Outline.task_id == task_id).order_by(Outline.version.desc()).first()
        new_version = (latest.version + 1) if latest else 1
        new_outline = Outline(task_id=task_id, markdown=body.markdown, version=new_version,
                              recognition_provider=latest.recognition_provider if latest else None)
        db.add(new_outline)
        db.commit()
        return OutlineUpdateResponse(task_id=task_id, version=new_version)


@router.post("/api/tasks/{task_id}/regenerate", response_model=PipelineResponse)
def regenerate(task_id: int, db: Session = Depends(get_db), prefer: str = Header(default='')):
    """重新生成接口：和生成接口走同一套流程，会先删除这个任务现有的全部大纲（包括手动修改过的版本），再重新生成一份；客户端声明 Prefer: respond-async 或云托管部署时转为后台作业，返回 202，稍后用作业接口查询结果。"""
    if settings.deployment_mode == 'cloud' or isinstance(prefer, str) and 'respond-async' in prefer.lower():
        return JSONResponse(status_code=202, content=submit_job(db, task_id, 'regenerate'))
    result = pipeline.run_pipeline(db, task_id)
    return PipelineResponse(**result)
