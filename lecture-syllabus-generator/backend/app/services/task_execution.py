"""Coordinate task writers and reconcile model work whose worker has exited."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.errors import AppError, TaskNotFoundError
from app.models import Task
from app.services.process_lock import LockBusyError, exclusive_file_lock
from app.services.coordination import coordination_lock


INTERRUPTED_MESSAGE = "任务处理进程已中断，请重试；已保存的大纲仍然保留。"


def execution_lock_path(db: Session, task_id: int) -> Path:
    """Workers using the same SQLite database must share the same lock path."""
    url = db.get_bind().url
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        database = Path(url.database).resolve()
        root = database.parent
        identity = os.path.normcase(str(database))
    else:
        root = DATA_DIR
        identity = url.render_as_string(hide_password=True)
    database_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return root / ".task-execution-locks" / f"{database_key}-{task_id}.lock"


def _busy_error(message=None) -> AppError:
    return AppError("TASK_BUSY", message or "任务正在处理中，请完成后再重试。", retryable=True, status_code=409)


def execution_lock(db, task_id):
    return coordination_lock(db, f'task-execution:{task_id}', execution_lock_path(db, task_id))


@contextmanager
def task_data_lock(db: Session, task_id: int, *, busy_message=None):
    """Acquire before reading a task; retain through commit or rollback.

    Saving, export logs and deletion share this short data lock. Deletion also
    takes model ownership, while exports can still read saved drafts during
    model work. Short writes do not change the task lifecycle.
    The caller commits successful work; failures roll back while still owned.
    """
    try:
        with coordination_lock(db, f'task-data:{task_id}', execution_lock_path(db, task_id).with_suffix('.data.lock')):
            try:
                yield
            except BaseException:
                db.rollback()
                raise
    except LockBusyError:
        raise _busy_error(busy_message) from None


@contextmanager
def task_edit_lock(db: Session, task_id: int):
    """Exclude model writers while saving, without changing lifecycle status."""
    try:
        with execution_lock(db, task_id):
            with task_data_lock(db, task_id):
                yield
    except LockBusyError:
        raise _busy_error('任务正在处理中，请完成后再保存。') from None


@contextmanager
def task_execution(db: Session, task_id: int):
    """Claim before any model call and retain ownership through final commit."""
    try:
        with execution_lock(db, task_id):
            from app.services.task_jobs import reject_active_job
            reject_active_job(db, task_id)
            # Match deletion's conditional write. A stale ORM status cannot
            # resurrect a row another transaction is deleting or has removed.
            claimed = db.execute(update(Task).where(
                Task.id == task_id,
                or_(Task.status != "deleting", Task.status.is_(None)),
            ).values(status="running", error_msg=None),
                execution_options={"synchronize_session": False})
            if claimed.rowcount != 1:
                db.rollback()
                if db.get(Task, task_id) is None:
                    raise TaskNotFoundError()
                raise _busy_error()
            db.commit()
            try:
                yield
            except BaseException as exc:
                db.rollback()
                message = exc.message if isinstance(exc, AppError) else INTERRUPTED_MESSAGE
                db.execute(update(Task).where(
                    Task.id == task_id, Task.status == "running",
                ).values(status="failed", error_msg=message),
                    execution_options={"synchronize_session": False})
                db.commit()
                raise
    except LockBusyError:
        raise _busy_error() from None


def reconcile_running_tasks(db: Session, task_id: int | None = None) -> None:
    """Repair orphan statuses without retrying work or touching saved drafts.

    Acquiring the same ownership lock is the proof of interruption. A startup
    in another worker cannot mistake a live operation for an orphan.
    """
    query = select(Task.id).where(Task.status == "running")
    if task_id is not None:
        query = query.where(Task.id == task_id)
    candidates = db.scalars(query).all()
    for candidate in candidates:
        try:
            with execution_lock(db, candidate):
                db.execute(update(Task).where(
                    Task.id == candidate, Task.status == "running",
                ).values(status="failed", error_msg=INTERRUPTED_MESSAGE),
                    execution_options={"synchronize_session": False})
                db.commit()
        except LockBusyError:
            continue
