"""Short-lived, owner-bound file handles transported as bounded JSON chunks."""
import base64
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.errors import AppError, TaskNotFoundError, PipelineError
from app.models import CloudFileHandle, Task, Page, Outline, ExportLog
from app.services import object_storage
from app.services.cloud_objects import storage_lock, check_quota, reserve_object
from app.services.task_execution import task_data_lock
from app.services.exporter import exporter


def _descriptor(row):
    return {'file_id': row.id, 'size': row.size, 'name': row.name, 'media_type': row.media_type,
            'chunk_size': object_storage.CHUNK_SIZE}


def create_handle(db, owner_id, task_id, kind, page_no=None):
    with task_data_lock(db, task_id):
        task = db.get(Task, task_id, populate_existing=True)
        if task is None or task.owner_id != owner_id:
            raise TaskNotFoundError()
        active = db.query(CloudFileHandle).filter(CloudFileHandle.owner_id == owner_id,
                                                 CloudFileHandle.expires_at > datetime.utcnow())
        store = object_storage.get_storage()
        expires = datetime.utcnow() + timedelta(minutes=15)
        if kind == 'source':
            page = db.query(Page).filter_by(task_id=task_id, page_no=page_no).first()
            if page is None:
                raise AppError('PAGE_NOT_FOUND', '页面不存在。', status_code=404)
            media = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(Path(page.file_path).suffix.lower())
            if media is None:
                raise AppError('SOURCE_FORMAT_UNSUPPORTED', '此来源不支持照片预览。', status_code=415)
            existing = active.filter_by(task_id=task_id, object_ref=page.file_path, temporary=False).first()
            if existing is not None:
                return _descriptor(existing)
            size = store.size(page.file_path)
            reference = page.file_path
            name = f'page_{page_no}' + Path(page.file_path).suffix.lower()
        elif kind == 'export':
            outline = db.query(Outline).filter_by(task_id=task_id).order_by(Outline.version.desc()).first()
            if outline is None:
                raise PipelineError('大纲尚未生成，请先执行管道。')
            content = exporter.export(outline.markdown)
            size = len(content)
            media = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            name = f'outline_{task_id}.docx'
            reference = None
        else:
            raise AppError('INVALID_FILE_KIND', '不支持的文件类型。')
        if size <= 0 or size > object_storage.MAX_READ:
            raise AppError('FILE_TOO_LARGE', '文件超过允许的下载大小。', status_code=413)
        if active.count() >= 20:
            raise AppError('FILE_LIMIT', '进行中的文件下载数量已达上限，请稍后重试。', status_code=429)
        if kind == 'export':
            with storage_lock(db):
                check_quota(db, owner_id, additional=size)
                key = f'kezhao/exports/{owner_id}/{uuid4().hex}.docx'
                reference = reserve_object(db, owner_id, key, size, 'export', task_id=task_id, expires_at=expires)
                db.commit()
                store.put(key, content)
                row = CloudFileHandle(id=str(uuid4()), owner_id=owner_id, task_id=task_id,
                                      object_ref=reference, size=size, name=name, media_type=media,
                                      temporary=True, expires_at=expires)
                db.add(row)
                db.add(ExportLog(task_id=task_id, file_size=size))
                db.commit()
                return _descriptor(row)
        row = CloudFileHandle(id=str(uuid4()), owner_id=owner_id, task_id=task_id, object_ref=reference,
                              size=size, name=name, media_type=media, temporary=False, expires_at=expires)
        db.add(row)
        db.commit()
        return _descriptor(row)


def file_chunk(db, owner_id, task_id, identifier, index):
    with task_data_lock(db, task_id):
        task = db.get(Task, task_id, populate_existing=True)
        if task is None or task.owner_id != owner_id:
            raise TaskNotFoundError()
        row = db.query(CloudFileHandle).filter_by(id=identifier, owner_id=owner_id, task_id=task_id).first()
        if row is None:
            raise AppError('FILE_NOT_FOUND', '文件不存在。', status_code=404)
        if row.expires_at <= datetime.utcnow():
            raise AppError('FILE_EXPIRED', '文件下载已过期，请重新打开。', status_code=410)
        offset = index * object_storage.CHUNK_SIZE
        if index < 0 or offset >= row.size:
            raise AppError('INVALID_CHUNK', '文件分块编号无效。')
        expected = min(object_storage.CHUNK_SIZE, row.size - offset)
        content = object_storage.get_storage().read_range(row.object_ref, offset, expected)
        if len(content) != expected:
            raise AppError('FILE_INCOMPLETE', '文件读取不完整，请重试下载。', retryable=True, status_code=503)
        return {'data': base64.b64encode(content).decode('ascii'), 'size': len(content), 'index': index}
