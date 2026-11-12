"""Durable object reservations, quotas and publication; no public object URLs."""
from datetime import datetime, timedelta
from contextlib import contextmanager
import logging
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings, DATA_DIR
from app.errors import AppError
from app.models import ObjectCleanup, StoredObject, UploadIntent, Task, Page
from app.schemas import TaskCreateResponse
from app.services.coordination import coordination_lock
from app.services.process_lock import LockBusyError
from app.services import object_storage

logger = logging.getLogger(__name__)


@contextmanager
def storage_lock(db):
    try:
        with coordination_lock(db, 'storage-quota', DATA_DIR / '.storage-quota.lock'):
            yield
    except LockBusyError:
        raise AppError('STORAGE_BUSY', '文件正在保存或清理，请稍后重试。', retryable=True, status_code=409) from None


def enqueue_cleanup(db, reference, *, not_before=None):
    when = not_before or datetime.utcnow()
    pending = db.get(ObjectCleanup, reference)
    if pending is None:
        db.add(ObjectCleanup(object_ref=reference, not_before=when))
    elif pending.not_before > when:
        pending.not_before = when


def usage(db, owner_id=None):
    objects = db.query(func.coalesce(func.sum(StoredObject.size), 0))
    intents = db.query(UploadIntent).filter(UploadIntent.status == 'open', UploadIntent.expires_at > datetime.utcnow())
    if owner_id is not None:
        objects = objects.filter(StoredObject.owner_id == owner_id)
        intents = intents.filter(UploadIntent.owner_id == owner_id)
    total = int(objects.scalar())
    for intent in intents.limit(settings.cloud_max_pending_uploads_total + 1):
        reserved = sum(file['size'] for file in intent.manifest['files']) * 2
        allocated = db.query(func.coalesce(func.sum(StoredObject.size), 0)).filter(StoredObject.intent_id == intent.id).scalar()
        total += max(0, reserved - int(allocated))
    return total


def check_quota(db, owner_id, *, additional=0, new_task=False):
    if usage(db, owner_id) + additional > settings.cloud_max_user_bytes or usage(db) + additional > settings.cloud_max_total_bytes:
        raise AppError('STORAGE_QUOTA_EXCEEDED', '文件存储额度不足，请删除不再需要的记录或联系维护者。', status_code=429)
    if new_task and (db.query(Task).filter_by(owner_id=owner_id).count() >= settings.cloud_max_user_tasks or
                     db.query(Task).count() >= settings.cloud_max_total_tasks):
        raise AppError('TASK_QUOTA_EXCEEDED', '整理记录数量已达上限。', status_code=429)


def reserve_object(db, owner_id, key, size, kind, *, intent_id=None, task_id=None, expires_at=None):
    store = object_storage.get_storage()
    reference = store.reference(key)
    row = db.get(StoredObject, reference)
    if row is None:
        db.add(StoredObject(object_ref=reference, owner_id=owner_id, task_id=task_id,
                            intent_id=intent_id, size=size, kind=kind))
    elif (row.owner_id, row.size, row.kind, row.intent_id) != (owner_id, size, kind, intent_id):
        raise AppError('STORAGE_CONFLICT', '文件预留记录不一致。', status_code=409)
    enqueue_cleanup(db, reference, not_before=expires_at or datetime.utcnow() + timedelta(hours=1))
    return reference


def _rollback(db):
    try:
        db.rollback()
    except Exception:
        db.close()


def publish_staged_task(db, staging, accepted, rejected, owner_id, *, intent_id=None, on_publish=None):
    if owner_id is None:
        raise AppError('AUTH_REQUIRED', '请先登录后再上传。', status_code=401)
    store = object_storage.get_storage()
    response = None
    references = []
    with storage_lock(db):
        check_quota(db, owner_id, additional=0 if intent_id else sum((staging / saved).stat().st_size for _, _, saved in accepted), new_task=True)
        batch = uuid4().hex
        plans = []
        for page_no, filename, saved in accepted:
            key = f'kezhao/source/{owner_id}/{batch}/{saved}'
            size = (staging / saved).stat().st_size
            reference = reserve_object(db, owner_id, key, size, 'source', intent_id=intent_id)
            plans.append((page_no, filename, saved, key, reference))
            references.append(reference)
        # An earlier uncertain source PUT may already have consumed this
        # intent's virtual reservation. Count the actual new allocations too.
        db.flush()
        check_quota(db, owner_id, new_task=True)
        db.commit()  # Cleanup and quota reservations survive termination during COS writes.
        try:
            for _, _, saved, key, _ in plans:
                store.put(key, staging / saved)
            task = Task(owner_id=owner_id, title='未命名任务', status='pending', page_count=len(plans))
            db.add(task)
            db.flush()
            for page_no, filename, _, _, reference in plans:
                db.add(Page(task_id=task.id, page_no=page_no, filename=filename, file_path=reference,
                            confidence=0.0, low_confidence_flag=False, failed_flag=False))
                db.get(StoredObject, reference).task_id = task.id
            response = TaskCreateResponse(task_id=task.id, page_count=len(plans), rejected=rejected)
            if on_publish:
                on_publish(task, response)
            db.query(ObjectCleanup).filter(ObjectCleanup.object_ref.in_(references)).delete(synchronize_session=False)
            db.commit()
            return response
        except BaseException as error:
            _rollback(db)
            if response is not None:
                try:
                    with Session(bind=db.get_bind()) as verification:
                        committed = verification.query(Page.id).filter(Page.task_id == response.task_id,
                                                                       Page.file_path.in_(references)).first() is not None
                except Exception:
                    raise AppError('UPLOAD_STATE_UNCERTAIN', '上传结果暂时无法确认，请检查整理记录后再操作。', status_code=503) from None
                if committed and isinstance(error, Exception):
                    return response
            # Do not delete here: delayed cleanup rechecks durable references,
            # including commits whose network acknowledgment was lost.
            if isinstance(error, AppError):
                raise
            raise AppError('UPLOAD_PERSISTENCE_ERROR', '上传保存未完成，资料尚未发布，请稍后重试。', retryable=True, status_code=503) from None
