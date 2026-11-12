"""Owned, expiring and idempotent JSON uploads with durable bounded chunks."""
import base64
from contextlib import contextmanager
import binascii
from datetime import datetime, timedelta
import hashlib
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.config import settings, DATA_DIR, STORAGE_DIR
from app.errors import AppError, TaskNotFoundError
from app.models import UploadIntent, UploadChunk, Task
from app.schemas import TaskCreateResponse
from app.services.coordination import coordination_lock
from app.services.process_lock import LockBusyError
from app.services import object_storage
from app.services.cloud_objects import storage_lock, check_quota, reserve_object, enqueue_cleanup

BATCH_LIMIT = 8 * 1024 * 1024


def _owned(db, identifier, owner_id):
    row = db.query(UploadIntent).filter_by(id=identifier, owner_id=owner_id).populate_existing().first()
    if row is None:
        raise AppError('UPLOAD_NOT_FOUND', '上传记录不存在。', status_code=404)
    return row


def _active(intent):
    if intent.expires_at <= datetime.utcnow():
        raise AppError('UPLOAD_EXPIRED', '上传已过期，请重新选择照片。', status_code=410)
    if intent.status != 'open':
        raise AppError('UPLOAD_CONFLICT', '此上传已完成，请检查整理记录。', status_code=409)


def _result(db, intent, owner_id):
    task = db.get(Task, intent.task_id) if intent.task_id is not None else None
    if task is None or task.owner_id != owner_id:
        raise TaskNotFoundError()
    return TaskCreateResponse(**intent.result)


def create_intent(db, owner_id, client_key, files):
    if not 1 <= len(files) <= min(settings.max_files_per_batch, 20):
        raise AppError('INVALID_UPLOAD', '请选择 1 至 20 张照片。')
    if any(type(file.get('size')) is not int or not 1 <= file['size'] <= BATCH_LIMIT or
           Path(file.get('name', '')).suffix.lower() not in {'.jpg', '.jpeg', '.png'} for file in files):
        raise AppError('INVALID_UPLOAD', '照片名称、格式或大小无效。', status_code=413)
    if sum(file['size'] for file in files) > BATCH_LIMIT:
        raise AppError('FILE_TOO_LARGE', '本次照片合计不能超过 8MiB。', status_code=413)
    manifest = {'files': files}
    with storage_lock(db):
        existing = db.query(UploadIntent).filter_by(owner_id=owner_id, client_key=client_key).first()
        if existing is not None:
            if existing.manifest != manifest:
                raise AppError('UPLOAD_CONFLICT', '相同上传标识不能用于不同照片。', status_code=409)
            if existing.status == 'complete':
                result = _result(db, existing, owner_id).model_dump()
            else:
                _active(existing)
                result = None
            return {'upload_id': existing.id, 'chunk_size': object_storage.CHUNK_SIZE, 'result': result}
        pending = db.query(UploadIntent).filter(UploadIntent.status == 'open', UploadIntent.expires_at > datetime.utcnow())
        if pending.filter(UploadIntent.owner_id == owner_id).count() >= settings.cloud_max_pending_uploads_user or pending.count() >= settings.cloud_max_pending_uploads_total:
            raise AppError('UPLOAD_LIMIT', '未完成上传数量已达上限，请完成已有上传或稍后重试。', status_code=429)
        check_quota(db, owner_id, additional=sum(file['size'] for file in files) * 2, new_task=True)
        row = UploadIntent(id=str(uuid4()), owner_id=owner_id, client_key=client_key, manifest=manifest,
                           status='open', expires_at=datetime.utcnow() + timedelta(hours=1))
        db.add(row)
        db.commit()
        return {'upload_id': row.id, 'chunk_size': object_storage.CHUNK_SIZE, 'result': None}


@contextmanager
def _lock(db, identifier):
    try:
        with coordination_lock(db, 'upload:' + identifier, DATA_DIR / ('.upload-intent-' + identifier + '.lock')):
            yield
    except LockBusyError:
        raise AppError('UPLOAD_BUSY', '照片正在上传，请稍后重试。', retryable=True, status_code=409) from None


def put_chunk(db, owner_id, identifier, file_index, chunk_index, encoded):
    _owned(db, identifier, owner_id)  # Reject cross-account IDs before coordination or storage work.
    with _lock(db, identifier):
        intent = _owned(db, identifier, owner_id)
        _active(intent)
        files = intent.manifest['files']
        if not 0 <= file_index < len(files) or chunk_index < 0:
            raise AppError('INVALID_CHUNK', '照片分块编号无效。')
        expected = min(object_storage.CHUNK_SIZE, files[file_index]['size'] - chunk_index * object_storage.CHUNK_SIZE)
        if expected <= 0 or len(encoded) > 4 * ((object_storage.CHUNK_SIZE + 2) // 3):
            raise AppError('INVALID_CHUNK', '照片分块大小无效。')
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise AppError('INVALID_CHUNK', '照片分块无法读取。') from None
        if len(content) != expected:
            raise AppError('INVALID_CHUNK', '照片分块大小与声明不一致。')
        digest = hashlib.sha256(content).hexdigest()
        identity = (identifier, file_index, chunk_index)
        existing = db.get(UploadChunk, identity)
        if existing is not None:
            if existing.sha256 != digest or existing.size != expected:
                raise AppError('UPLOAD_CONFLICT', '重复上传的照片分块不一致。', status_code=409)
            return {'received': True}
        key = f'kezhao/staged/{owner_id}/{identifier}/{file_index}/{chunk_index}-{digest}.bin'
        with storage_lock(db):
            reference = reserve_object(db, owner_id, key, expected, 'chunk', intent_id=identifier,
                                       expires_at=intent.expires_at)
            db.flush()
            check_quota(db, owner_id)
            db.commit()
        object_storage.get_storage().put(key, content)
        _active(intent)
        db.add(UploadChunk(intent_id=identifier, file_index=file_index, chunk_index=chunk_index,
                           object_ref=reference, sha256=digest, size=expected))
        db.commit()
        return {'received': True}


def complete_intent(db, owner_id, identifier):
    _owned(db, identifier, owner_id)
    with _lock(db, identifier):
        intent = _owned(db, identifier, owner_id)
        if intent.status == 'complete':
            return _result(db, intent, owner_id)
        _active(intent)
        store = object_storage.get_storage()
        uploads = []
        try:
            for index, file in enumerate(intent.manifest['files']):
                chunks = db.query(UploadChunk).filter_by(intent_id=identifier, file_index=index).order_by(UploadChunk.chunk_index).all()
                count = (file['size'] + object_storage.CHUNK_SIZE - 1) // object_storage.CHUNK_SIZE
                if len(chunks) != count or [chunk.chunk_index for chunk in chunks] != list(range(count)):
                    raise AppError('UPLOAD_INCOMPLETE', '照片尚未上传完整，请重试上传。', retryable=True, status_code=409)
                parts = []
                for chunk in chunks:
                    content = store.read(chunk.object_ref, limit=object_storage.CHUNK_SIZE)
                    if len(content) != chunk.size or hashlib.sha256(content).hexdigest() != chunk.sha256:
                        raise AppError('UPLOAD_INCOMPLETE', '照片分块校验失败，请重新上传。', status_code=409)
                    parts.append(content)
                content = b''.join(parts)
                if len(content) != file['size']:
                    raise AppError('UPLOAD_INCOMPLETE', '照片大小校验失败。', status_code=409)
                uploads.append(UploadFile(filename=file['name'], file=BytesIO(content)))

            def published(task, response):
                _active(intent)
                intent.task_id, intent.status, intent.result = task.id, 'complete', response.model_dump()
                for chunk in db.query(UploadChunk).filter_by(intent_id=identifier).all():
                    enqueue_cleanup(db, chunk.object_ref)
                    db.delete(chunk)

            from app.services.upload_service import create_task_from_uploads
            return create_task_from_uploads(db, uploads, '', STORAGE_DIR, settings, owner_id=owner_id,
                                            intent_id=identifier, on_publish=published)
        finally:
            for upload in uploads:
                upload.file.close()
