"""Validate and stage a batch before publishing files and committing its rows.

SQLite and the filesystem do not share a transaction. Publish complete files
before the single database commit, and compensate request failures by removing
only directories created by this request. Hard process termination may leave
unreferenced directories; it cannot expose a committed task with half a batch.
"""

import logging
import os
from pathlib import Path
import shutil
import tempfile

from fastapi import UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError, FileFormatError, FileTooLargeError, TooManyFilesError
from app.models import Page, Task
from app.schemas import TaskCreateResponse

logger = logging.getLogger(__name__)
READ_CHUNK_BYTES = 64 * 1024


def _parse_order(order: str, count: int) -> list[int]:
    if len(order) > 4096:
        raise AppError("INVALID_FILE_ORDER", "文件顺序参数过长。")
    if not order.strip():
        return list(range(count))
    tokens = [token.strip() for token in order.split(",")]
    if len(tokens) != count or any(not token.isascii() or not token.isdecimal() for token in tokens):
        raise AppError("INVALID_FILE_ORDER", "文件顺序必须完整包含每个上传文件的编号，且不能重复。")
    indices = [int(token) - 1 for token in tokens]
    if set(indices) != set(range(count)):
        raise AppError("INVALID_FILE_ORDER", "文件顺序必须完整包含每个上传文件的编号，且不能重复。")
    return indices


def _stage_file(upload: UploadFile, destination: Path, limit: int) -> None:
    upload.file.seek(0)
    size = 0
    with destination.open("xb") as output:
        while True:
            chunk = upload.file.read(min(READ_CHUNK_BYTES, limit - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise FileTooLargeError()
            if output.write(chunk) != len(chunk):
                raise OSError("Incomplete upload write")
        if size == 0:
            raise FileFormatError("文件内容为空。")
        output.flush()
        os.fsync(output.fileno())


def _remove_owned_directory(root: Path, directory: Path | None) -> None:
    if directory is None or not directory.exists():
        return
    # Never recursively remove a computed path until its absolute target has
    # been verified. User filenames are not used in directory or saved names.
    if directory.is_symlink() or directory.resolve().parent != root:
        raise OSError("Refusing cleanup outside the upload root")
    if not directory.name.startswith((".upload-", "task-")):
        raise OSError("Refusing cleanup of an unowned directory")
    shutil.rmtree(directory)


def _committed_upload_exists(bind, response: TaskCreateResponse, file_paths: list[str]) -> bool:
    # Use a separate transaction: a commit exception or an after_commit callback
    # can leave the original Session unusable even though its rows are durable.
    with Session(bind=bind) as verification:
        return verification.query(Page.id).filter(
            Page.task_id == response.task_id,
            Page.file_path.in_(file_paths),
        ).first() is not None


def create_task_from_uploads(
    db: Session,
    files: list[UploadFile],
    order: str,
    storage_root: Path,
    config: Settings,
    owner_id: int | None = None,
    *, intent_id=None, on_publish=None,
) -> TaskCreateResponse:
    if not files:
        raise FileFormatError("请至少选择一个文件。")
    if len(files) > config.max_files_per_batch:
        raise TooManyFilesError()
    indices = _parse_order(order, len(files))
    rejected = {}
    extensions = {}
    for index, upload in enumerate(files):
        ext = os.path.splitext(upload.filename or "")[1].lower()
        if ext not in config.allowed_ext_list:
            rejected[index] = {"filename": upload.filename or "", "reason": f"格式不支持：{ext}"}
        else:
            extensions[index] = ext
    if not extensions:
        raise FileFormatError("所有文件格式均不支持。")

    root = storage_root.resolve()
    staging = None
    published = None
    response = None
    file_paths = []
    bind = db.get_bind()
    try:
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".upload-", dir=root))
        accepted = []
        oversized = False
        for index in indices:
            if index not in extensions:
                continue
            page_no = len(accepted) + 1
            saved_name = f"page_{page_no}{extensions[index]}"
            path = staging / saved_name
            try:
                _stage_file(files[index], path, config.max_file_mb * 1024 * 1024)
                if config.storage_backend == 'cos' and extensions[index] in {'.jpg', '.jpeg', '.png'}:
                    from app.services.image_preprocessor import prepare_image
                    prepare_image(str(path))  # Validate actual bytes before publishing a cloud source.
            except (FileFormatError, FileTooLargeError) as exc:
                path.unlink(missing_ok=True)
                oversized = oversized or isinstance(exc, FileTooLargeError)
                rejected[index] = {"filename": files[index].filename, "reason": exc.message}
                continue
            accepted.append((page_no, files[index].filename, saved_name))
        if not accepted:
            if oversized:
                raise FileTooLargeError()
            raise FileFormatError("没有可接收的文件，请检查文件格式和内容。")

        if config.storage_backend == 'cos':
            from app.services.cloud_objects import publish_staged_task
            result = publish_staged_task(db, staging, accepted,
                                         [rejected[index] for index in sorted(rejected)], owner_id,
                                         intent_id=intent_id, on_publish=on_publish)
            try:
                _remove_owned_directory(root, staging)
            except OSError:
                logger.warning('Committed cloud upload retained; scratch cleanup pending')
            return result

        task = Task(title="未命名任务", status="pending", page_count=len(accepted), owner_id=owner_id)
        db.add(task)
        db.flush()
        # Unique final directories avoid collisions with legacy data or a
        # previous interrupted upload, including when SQLite reuses a task ID.
        destination = root / f"task-{task.id}-{staging.name.removeprefix('.upload-')}"
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Upload destination already exists")
        for page_no, filename, saved_name in accepted:
            file_paths.append(str(destination / saved_name))
            db.add(Page(
                task_id=task.id,
                page_no=page_no,
                filename=filename,
                file_path=file_paths[-1],
                confidence=0.0,
                low_confidence_flag=False,
                failed_flag=False,
            ))
        db.flush()
        response = TaskCreateResponse(
            task_id=task.id,
            page_count=len(accepted),
            rejected=[rejected[index] for index in sorted(rejected)],
        )
        if on_publish:
            on_publish(task, response)
        os.rename(staging, destination)
        published = destination
        # Construct the response before commit: no refresh/query/cleanup may
        # fail after a successful commit and turn success into an apparent retry.
        db.commit()
        return response
    except BaseException as exc:
        try:
            db.rollback()
        except Exception:
            # In particular, rollback is forbidden in a COMMITTED Session when
            # an after_commit listener raised. Closing releases that Session.
            logger.warning("Upload session rollback failed; closing it before reconciliation", exc_info=True)
            try:
                db.close()
            except Exception:
                logger.warning("Upload session close failed", exc_info=True)
        if published is not None:
            try:
                committed = _committed_upload_exists(bind, response, file_paths)
            except Exception as verification_error:
                # Failure to confirm is not proof of rollback. Preserve files
                # rather than corrupting a potentially committed task.
                logger.exception("Upload commit outcome uncertain; published files retained at %s", published)
                raise AppError("UPLOAD_STATE_UNCERTAIN", "任务保存状态暂时无法确认，请先检查历史记录，避免重复上传。", status_code=503) from verification_error
            if committed:
                logger.warning("Upload commit succeeded despite a subsequent exception", exc_info=True)
                if isinstance(exc, Exception):
                    return response
                raise
        try:
            _remove_owned_directory(root, staging)
            _remove_owned_directory(root, published)
        except OSError as cleanup_error:
            logger.exception("Upload cleanup failed; manual storage reconciliation required")
            raise AppError("UPLOAD_CLEANUP_ERROR", "上传未完成，文件清理失败，请检查存储空间后联系维护者。", status_code=500) from cleanup_error
        if isinstance(exc, (OSError, SQLAlchemyError)):
            logger.exception("Upload persistence failed")
            raise AppError("UPLOAD_PERSISTENCE_ERROR", "上传保存失败，请稍后重试。", retryable=True, status_code=503) from exc
        raise
