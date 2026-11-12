"""Read-only source-photo access for native clients; no filesystem paths in URLs."""
from pathlib import Path

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.config import STORAGE_DIR
from app.db import get_db
from app.errors import AppError, TaskNotFoundError
from app.models import Page, Task
from app.services.task_execution import task_data_lock
from app.services.auth_service import require_task_owner

router = APIRouter(dependencies=[Depends(require_task_owner)])


@router.get('/api/tasks/{task_id}/pages/{page_no}/image')
def source_image(task_id: int, page_no: int, db: Session = Depends(get_db)):
    with task_data_lock(db, task_id):
        if db.get(Task, task_id) is None:
            raise TaskNotFoundError()
        page = db.query(Page).filter_by(task_id=task_id, page_no=page_no).first()
        if page is None:
            raise AppError('PAGE_NOT_FOUND', '页面不存在。', status_code=404)
        if page.file_path.startswith('cos://'):
            from app.services import object_storage
            from app.config import settings
            content = object_storage.get_storage().read(page.file_path, limit=settings.max_file_mb * 1024 * 1024)
            media_type = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(Path(page.file_path).suffix.lower())
            if media_type is None:
                raise AppError('SOURCE_FORMAT_UNSUPPORTED', '该来源不是可预览的 JPG 或 PNG 照片。', status_code=415)
            return Response(content, media_type=media_type,
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        path = Path(page.file_path).resolve()
        if not path.is_relative_to(STORAGE_DIR.resolve()) or not path.is_file():
            raise AppError('SOURCE_NOT_FOUND', '原图文件不存在。', status_code=404)
        media_type = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(path.suffix.lower())
        if media_type is None:
            raise AppError('SOURCE_FORMAT_UNSUPPORTED', '该来源不是可预览的 JPG 或 PNG 照片。', status_code=415)
        # Read while locked so a concurrent deletion cannot truncate the response.
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise AppError('SOURCE_NOT_FOUND', '原图暂时无法读取。', status_code=404) from exc
        return Response(content, media_type=media_type,
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
