"""Retryable cleanup after durable DB changes, never removal by client-supplied key."""
from datetime import datetime, timedelta

from app.db import SessionLocal
from app.errors import AppError
from app.models import CloudFileHandle, UploadIntent, UploadChunk, ObjectCleanup, StoredObject, Page
from app.services import object_storage
from app.services.cloud_objects import storage_lock, enqueue_cleanup
from app.services.process_lock import LockBusyError


def cleanup_objects(limit=20):
    completed = 0
    try:
        with SessionLocal() as db:
            with storage_lock(db):
                now = datetime.utcnow()
                for row in db.query(CloudFileHandle).filter(CloudFileHandle.expires_at <= now).limit(limit).all():
                    if row.temporary:
                        enqueue_cleanup(db, row.object_ref)
                    db.delete(row)
                for intent in db.query(UploadIntent).filter(UploadIntent.status == 'open', UploadIntent.expires_at <= now).limit(limit).all():
                    intent.status = 'expired'
                    for chunk in db.query(UploadChunk).filter_by(intent_id=intent.id).all():
                        enqueue_cleanup(db, chunk.object_ref)
                        db.delete(chunk)
                db.commit()
                candidates = [row[0] for row in db.query(ObjectCleanup.object_ref).filter(ObjectCleanup.not_before <= now)
                              .order_by(ObjectCleanup.created_at).limit(limit).all()]
        for reference in candidates:
            with SessionLocal() as db:
                with storage_lock(db):
                    pending = db.get(ObjectCleanup, reference)
                    if pending is None:
                        continue
                    now = datetime.utcnow()
                    referenced = db.query(Page.id).filter_by(file_path=reference).first() is not None
                    referenced = referenced or db.query(CloudFileHandle.id).filter(
                        CloudFileHandle.object_ref == reference, CloudFileHandle.expires_at > now).first() is not None
                    referenced = referenced or db.query(UploadChunk.intent_id).join(UploadIntent).filter(
                        UploadChunk.object_ref == reference, UploadIntent.status == 'open', UploadIntent.expires_at > now).first() is not None
                    if referenced:
                        pending.not_before = now + timedelta(hours=1)
                        db.commit()
                        continue
                    try:
                        object_storage.get_storage().delete(reference)
                    except Exception:
                        pending.attempts += 1
                        pending.not_before = now + timedelta(seconds=30)
                        db.commit()
                        continue
                    db.query(UploadChunk).filter_by(object_ref=reference).delete(synchronize_session=False)
                    record = db.get(StoredObject, reference)
                    if record is not None:
                        db.delete(record)
                    db.delete(pending)
                    db.commit()
                    completed += 1
    except LockBusyError:
        pass  # Active writers retain ownership; housekeeping can wait.
    except AppError as error:
        if error.code != 'STORAGE_BUSY':
            raise
    return completed
