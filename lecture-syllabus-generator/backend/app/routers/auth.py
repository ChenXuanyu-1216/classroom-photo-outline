from fastapi import APIRouter, Depends, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import AppUser, AuthSession
from app.services import wechat_login
from app.services.auth_service import bearer, get_current_user, issue_session, token_digest

router = APIRouter(prefix='/api/auth')


class WeChatLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256, pattern=r'^\S+$')


def user_view(user):
    return {'id': user.id, 'provider': user.provider}


@router.post('/wechat')
def login(body: WeChatLoginRequest, response: Response, db: Session = Depends(get_db)):
    subject = wechat_login.exchange_code(body.code)
    identity = {'provider': 'wechat', 'app_id': settings.wechat_app_id.strip(), 'subject': subject}
    user = db.query(AppUser).filter_by(**identity).first()
    if user is None:
        user = AppUser(**identity)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            user = db.query(AppUser).filter_by(**identity).one()
    token = issue_session(db, user)
    db.commit()
    response.headers['Cache-Control'] = 'no-store'
    return {'access_token': token, 'token_type': 'Bearer', 'expires_in': settings.auth_session_ttl_seconds, 'user': user_view(user)}


@router.get('/me')
def me(response: Response, user: AppUser = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    return {'user': user_view(user)}


@router.post('/logout', status_code=204)
def logout(user: AppUser = Depends(get_current_user), credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)):
    session = db.get(AuthSession, token_digest(credentials.credentials))
    if session is not None:
        db.delete(session)
        db.commit()
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})
