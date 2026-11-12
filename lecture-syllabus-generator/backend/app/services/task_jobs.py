"""Durable, bounded single-machine jobs for the existing model operations."""
from contextvars import ContextVar
from datetime import datetime
import logging
import threading
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.errors import AppError, TaskNotFoundError
from app.models import Task, Page, TaskJob
from app.services.process_lock import exclusive_file_lock, LockBusyError
from app.services.task_execution import execution_lock_path, execution_lock, task_data_lock, task_execution
from app.services.coordination import coordination_lock

current_job = ContextVar('current_task_job', default=None)
logger = logging.getLogger(__name__)


def job_view(job):
    return {'job_id': job.id, 'task_id': job.task_id, 'operation': job.operation,
            'page_no': job.page_no, 'status': job.status, 'result': job.result, 'error': job.error}


def active_job(db, task_id):
    return db.query(TaskJob).filter_by(active_task_id=task_id).first()


def reject_active_job(db, task_id):
    job = active_job(db, task_id)
    if job is not None and job.id != current_job.get():
        raise AppError('TASK_BUSY', '任务已排队或正在处理中，请稍后检查状态。', retryable=True, status_code=409)


def _queue_path(db, suffix):
    task_lock = execution_lock_path(db, 0)
    # Keep queue coordination beside SQLite, avoiding an extra directory level
    # in long Windows evaluation paths. Retain the database identity hash.
    return task_lock.parent.parent / ('.queue-' + task_lock.stem.split('-')[0] + suffix)


def queue_lock(db, suffix):
    return coordination_lock(db, 'queue:' + suffix, _queue_path(db, suffix))


def submit_job(db, task_id, operation, page_no=None):
    if settings.deployment_mode == 'cloud' and not settings.cloud_execution_verified and not (
        settings.model_provider == 'mock' and settings.cloud_allow_mock
    ):
        raise AppError('CLOUD_WORKER_UNVERIFIED', '云端任务执行尚未验收，暂未开放识别。', status_code=503)
    if not settings.task_queue_enabled:
        raise AppError('TASK_QUEUE_UNAVAILABLE', '后台处理未启用，请启用任务队列并重启服务。', status_code=503)
    if operation not in {'recognize', 'pipeline', 'regenerate'}:
        raise AppError('INVALID_JOB', '不支持的处理操作。')
    try:
        # Admission is separate from consumption: uploads can queue during a model call.
        with queue_lock(db, '.admission.lock'):
            with execution_lock(db, task_id):
                with task_data_lock(db, task_id):
                    task = db.get(Task, task_id, populate_existing=True)
                    if task is None:
                        raise TaskNotFoundError()
                    reject_active_job(db, task_id)
                    if task.status in {'running', 'deleting'}:
                        raise AppError('TASK_BUSY', '任务正在处理中。', retryable=True, status_code=409)
                    query = db.query(Page.id).filter_by(task_id=task_id)
                    if page_no is not None:
                        query = query.filter_by(page_no=page_no)
                    if query.first() is None:
                        raise AppError('PAGE_NOT_FOUND', '页面不存在。', status_code=404)
                    if db.query(TaskJob).filter_by(status='queued').count() >= settings.task_queue_capacity:
                        raise AppError('TASK_QUEUE_FULL', '等待任务已满，请稍后再提交。', retryable=True, status_code=429)
                    if settings.deployment_mode == 'cloud' and db.query(TaskJob).join(
                        Task, TaskJob.task_id == Task.id
                    ).filter(Task.owner_id == task.owner_id, TaskJob.active_task_id.is_not(None)).count() >= settings.cloud_max_active_jobs_user:
                        raise AppError('USER_QUEUE_FULL', '你的进行中任务已达上限，请完成后再提交。', retryable=True, status_code=429)
                    job = TaskJob(id=str(uuid4()), task_id=task_id, active_task_id=task_id,
                                  operation=operation, page_no=page_no, status='queued')
                    db.add(job)
                    task.status, task.error_msg = 'queued', None
                    db.commit()
                    return job_view(job)
    except (LockBusyError, IntegrityError):
        db.rollback()
        raise AppError('TASK_BUSY', '任务已排队或正在处理中，请检查状态。', retryable=True, status_code=409) from None


def _recover_locked(db):
    # Caller owns the consumer lock. A running job without it lost its worker.
    for job in db.query(TaskJob).filter_by(status='running').all():
        try:
            with execution_lock(db, job.task_id):
                job.status, job.active_task_id = 'failed', None
                job.finished_at = datetime.utcnow()
                job.error = {'code': 'TASK_INTERRUPTED', 'message': '后台处理已中断，已有大纲保留，请手动重试。', 'retryable': True}
                task = db.get(Task, job.task_id)
                if task is not None:
                    task.status, task.error_msg = 'failed', job.error['message']
                    from app.services.evaluation import capture
                    capture(db, job.task_id, job)
        except LockBusyError:
            continue
    db.commit()


def reconcile_jobs(db):
    try:
        with queue_lock(db, '.consumer.lock'):
            _recover_locked(db)
    except LockBusyError:
        pass


def task_job_state(db, task_id):
    jobs = db.query(TaskJob).filter_by(task_id=task_id).order_by(TaskJob.created_at.desc(), TaskJob.id.desc()).all()
    active = next((job for job in jobs if job.active_task_id is not None), None)
    return {'active_job': job_view(active) if active else None,
            'last_job': job_view(jobs[0]) if jobs else None}


def get_job(db, task_id, job_id):
    if db.get(Task, task_id) is None:
        raise TaskNotFoundError()
    reconcile_jobs(db)
    job = db.query(TaskJob).filter_by(id=job_id, task_id=task_id).first()
    if job is None:
        raise AppError('JOB_NOT_FOUND', '处理记录不存在。', status_code=404)
    return job_view(job)


class TaskJobQueue:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()

    def run_once(self):
        with SessionLocal() as db:
            try:
                with queue_lock(db, '.consumer.lock'):
                    _recover_locked(db)
                    job = db.query(TaskJob).filter_by(status='queued').order_by(TaskJob.created_at, TaskJob.id).first()
                    if job is None:
                        return False
                    return self._execute(db, job)
            except LockBusyError:
                return False

    def _execute(self, db, job):
        from app.services.pipeline import pipeline
        token = current_job.set(job.id)
        try:
            with task_execution(db, job.task_id):
                job.status, job.started_at = 'running', datetime.utcnow()
                db.commit()
                if job.operation == 'recognize':
                    result = pipeline._run_recognize(db, job.task_id, job.page_no)
                else:
                    result = pipeline._run_pipeline(db, job.task_id)
                job.result = result
                job.status = result['status'] if result['status'] in {'success', 'partial', 'failed'} else 'success'
                if job.status == 'failed':
                    pages = db.query(Page).filter_by(task_id=job.task_id, failed_flag=True).all()
                    job.error = {'code': 'RECOGNITION_FAILED', 'message': '页面识别失败，请查看页级原因。',
                                 'retryable': any(page.error_retryable is not False for page in pages)}
                job.active_task_id, job.finished_at = None, datetime.utcnow()
                from app.services.evaluation import capture
                capture(db, job.task_id, job)
                db.commit()
                return True
        except Exception as exc:
            db.rollback()
            job = db.get(TaskJob, job.id)
            if isinstance(exc, AppError) and exc.code == 'TASK_BUSY' and job is not None and job.status == 'queued':
                # Rejected duplicate requests can briefly hold the execution
                # lock. No model work started: retain the accepted queue entry.
                return False
            if job is not None:
                job.status, job.active_task_id = 'failed', None
                job.finished_at = datetime.utcnow()
                job.error = {'code': exc.code, 'message': exc.message, 'retryable': exc.retryable} if isinstance(exc, AppError) else {
                    'code': 'INTERNAL_ERROR', 'message': '后台处理失败，已有大纲已保留。', 'retryable': False}
                with task_data_lock(db, job.task_id):
                    from app.services.evaluation import capture
                    capture(db, job.task_id, job)
                    db.commit()
            return True
        finally:
            current_job.reset(token)

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name='task-job-consumer')
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                logger.error('Background consumer failed; durable job state retained')
                worked = False
            if not worked:
                self._stop.wait(.25)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)


task_queue = TaskJobQueue()
