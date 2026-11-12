from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from io import BytesIO
from app.db import get_db
from app.models import Task, Outline, ExportLog
from app.errors import TaskNotFoundError, PipelineError
from app.services.exporter import exporter
from app.services.task_execution import task_data_lock
from app.services.auth_service import require_task_owner

router = APIRouter(dependencies=[Depends(require_task_owner)])


@router.get("/api/tasks/{task_id}/export")
def export_word(task_id: int, db: Session = Depends(get_db)):
    """导出接口：取出任务最新版本的大纲，转换成 Word 文件供下载，并记录一次导出日志。"""
    with task_data_lock(db, task_id):
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise TaskNotFoundError()
        outline = db.query(Outline).filter(Outline.task_id == task_id).order_by(Outline.version.desc()).first()
        if not outline:
            raise PipelineError("大纲尚未生成，请先执行管道。")

        docx_bytes = exporter.export(outline.markdown)

        db.add(ExportLog(task_id=task_id, file_size=len(docx_bytes)))
        db.commit()

        buf = BytesIO(docx_bytes)
        filename = f"outline_{task_id}.docx"
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
