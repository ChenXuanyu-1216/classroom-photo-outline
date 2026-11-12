"""Delete one saved task atomically, then clean up its unshared upload files."""
import logging
from pathlib import Path

from sqlalchemy import or_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Page, Task, CloudFileHandle, StoredObject, UploadIntent
from app.services.process_lock import LockBusyError, exclusive_file_lock
from app.services.task_execution import execution_lock_path, execution_lock, task_data_lock


logger = logging.getLogger(__name__)


def delete_task(db: Session, task_id: int, storage_root: Path) -> None:
    # Always take model ownership before the data lock. Both are nonblocking;
    # exports need only the latter, so old drafts remain exportable during a
    # regeneration while no deletion can race any of these writers.
    busy = '任务正在处理、保存或导出中，请稍后再删除。'
    try:
        with execution_lock(db, task_id):
            with task_data_lock(db, task_id, busy_message=busy):
                _delete_task_locked(db, task_id, storage_root)
    except LockBusyError:
        raise AppError('TASK_BUSY', busy, retryable=True, status_code=409) from None


def _delete_task_locked(db: Session, task_id: int, storage_root: Path) -> None:
    from app.services.task_jobs import reject_active_job
    reject_active_job(db, task_id)
    task = db.get(Task, task_id, populate_existing=True)
    if task is None:
        return  # Retrying after a lost successful response is harmless.
    try:
        # Claim the row with a conditional write, rather than relying on a
        # stale status read. This transaction holds the SQLite write lock until
        # all related rows are removed; running generation must keep its data.
        claimed = db.execute(update(Task).where(
            Task.id == task_id, or_(Task.status != 'running', Task.status.is_(None)),
        ).values(status='deleting'), execution_options={'synchronize_session': False})
        if claimed.rowcount != 1:
            db.rollback()
            if db.get(Task, task_id) is None:
                return
            raise AppError('TASK_BUSY', '任务正在处理中，请完成后再删除。', status_code=409)
        references = {path for path, in db.query(Page.file_path).filter_by(task_id=task_id)}
        from app.services.cloud_objects import enqueue_cleanup
        objects = {path for path, in db.query(StoredObject.object_ref).filter_by(task_id=task_id)}
        objects.update(path for path, in db.query(CloudFileHandle.object_ref).filter_by(task_id=task_id, temporary=True))
        objects.update(path for path in references if path.startswith('cos://'))
        for reference in objects:
            enqueue_cleanup(db, reference)
        db.query(CloudFileHandle).filter_by(task_id=task_id).delete(synchronize_session=False)
        db.query(StoredObject).filter_by(task_id=task_id).update({'task_id': None}, synchronize_session=False)
        db.query(UploadIntent).filter_by(task_id=task_id).update({'task_id': None}, synchronize_session=False)
        paths = {Path(path).resolve() for path in references if not path.startswith('cos://')}
        shared = {Path(path).resolve() for path, in db.query(Page.file_path).filter(Page.task_id != task_id)
                  if not path.startswith('cos://')}
        db.delete(task)  # ORM cascades remove pages, points, outlines/nodes and export logs.
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception('Task %s deletion failed; saved data retained', task_id)
        raise AppError('TASK_DELETE_FAILED', '删除失败，记录已保留，请稍后重试。',
                       retryable=True, status_code=500) from None

    # Files are touched only after a durable DB deletion. An I/O failure must
    # not turn a completed deletion into an apparent failed/repeated mutation.
    root = storage_root.resolve()
    for path in paths - shared:
        if path == root or not path.is_relative_to(root):
            logger.warning('Skipped upload outside storage while deleting task %s', task_id)
            continue
        try:
            if path.is_file():
                path.unlink()
            parent = path.parent
            if parent != root and parent.is_relative_to(root):
                try:
                    parent.rmdir()  # Empty task directory only; never recursive.
                except OSError:
                    pass
        except OSError:
            logger.warning('Task %s deleted; upload cleanup pending: %s', task_id, path, exc_info=True)
