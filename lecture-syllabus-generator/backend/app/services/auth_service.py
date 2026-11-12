"""Opaque sessions. There is no anonymous/localhost authentication bypass."""
from datetime import datetime, timedelta
import hashlib
import secrets

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import AppError, TaskNotFoundError
from app.models import AppUser, AuthSession, Task

bearer = HTTPBearer(auto_error=False)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def issue_session(db: Session, user: AppUser) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=token_digest(token), user_id=user.id,
                       expires_at=datetime.utcnow() + timedelta(seconds=settings.auth_session_ttl_seconds)))
    db.flush()
    return token


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> AppUser:
    if not credentials or credentials.scheme.lower() != 'bearer' or len(credentials.credentials) > 256:
        raise AppError('AUTH_REQUIRED', '请先登录后再访问课堂资料。', status_code=401)
    session = db.get(AuthSession, token_digest(credentials.credentials))
    if session is None or session.expires_at <= datetime.utcnow():
        raise AppError('AUTH_EXPIRED', '登录已失效，请重新登录。', status_code=401)
    user = db.get(AppUser, session.user_id)
    if user is None:
        raise AppError('AUTH_EXPIRED', '登录已失效，请重新登录。', status_code=401)
    return user


def require_task_owner(request: Request, user: AppUser = Depends(get_current_user), db: Session = Depends(get_db)):
    value = request.path_params.get('task_id')
    if value is None:
        return
    try:
        task_id = int(value)
    except (TypeError, ValueError):
        raise TaskNotFoundError() from None
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == user.id).first()
    if task is None:
        # Same response for missing, unowned, and someone else's task.
        raise TaskNotFoundError()
