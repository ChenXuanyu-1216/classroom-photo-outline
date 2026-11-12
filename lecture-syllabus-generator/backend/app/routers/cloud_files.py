from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from typing import Literal
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppUser
from app.services.auth_service import get_current_user, require_task_owner
from app.services.cloud_uploads import create_intent, put_chunk, complete_intent

router = APIRouter()


class FileManifest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: StrictStr = Field(min_length=1, max_length=128)
    size: StrictInt = Field(ge=1, le=8 * 1024 * 1024)


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_key: StrictStr = Field(pattern=r'^[A-Za-z0-9_-]{8,64}$')
    files: list[FileManifest] = Field(min_length=1, max_length=20)


class ChunkRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: StrictStr = Field(max_length=350000)


@router.post('/api/files/uploads', status_code=201)
def begin(body: UploadRequest, db: Session = Depends(get_db), user: AppUser = Depends(get_current_user)):
    return create_intent(db, user.id, body.client_key, [file.model_dump() for file in body.files])


@router.put('/api/files/uploads/{upload_id}/{file_index}/{chunk_index}')
def chunk(upload_id: str, file_index: int, chunk_index: int, body: ChunkRequest,
          db: Session = Depends(get_db), user: AppUser = Depends(get_current_user)):
    return put_chunk(db, user.id, upload_id, file_index, chunk_index, body.data)


@router.post('/api/files/uploads/{upload_id}/complete', status_code=201)
def complete(upload_id: str, db: Session = Depends(get_db), user: AppUser = Depends(get_current_user)):
    return complete_intent(db, user.id, upload_id)


class FileRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['source', 'export']
    page_no: StrictInt | None = Field(default=None, ge=1)


@router.post('/api/tasks/{task_id}/files', status_code=201, dependencies=[Depends(require_task_owner)])
def create_file(task_id: int, body: FileRequest, db: Session = Depends(get_db),
                user: AppUser = Depends(get_current_user)):
    from app.services.cloud_files import create_handle
    return create_handle(db, user.id, task_id, body.kind, body.page_no)


@router.get('/api/tasks/{task_id}/files/{file_id}/chunks/{index}', dependencies=[Depends(require_task_owner)])
def read_file(task_id: int, file_id: str, index: int, db: Session = Depends(get_db),
              user: AppUser = Depends(get_current_user)):
    from app.services.cloud_files import file_chunk
    return file_chunk(db, user.id, task_id, file_id, index)
