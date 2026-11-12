"""Browser-bound, expiring, one-use login confirmed by an existing WeChat user."""
import base64
from datetime import datetime, timedelta
import secrets

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import AppError
from app.models import AppUser, WebLoginChallenge
from app.routers.auth import user_view
from app.services import wechat_qrcode
from app.services.auth_service import get_current_user, issue_session, token_digest

router = APIRouter(prefix='/api/auth/web')
TTL = 300
COOKIE = 'web_login_proof_'


class ChallengeRequest(BaseModel):
    scene: str = Field(pattern=r'^[a-f0-9]{32}$')


class ConfirmRequest(ChallengeRequest):
    approve: bool


def _browser_origin(request):
    origin = request.headers.get('origin')
    if settings.deployment_mode == 'cloud':
        from urllib.parse import urlsplit
        if not settings.public_web_enabled:
            raise AppError('PUBLIC_WEB_DISABLED', '公网网页登录尚未开放。', status_code=503)
        expected = settings.public_origin
        parsed = urlsplit(expected)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
                parsed.path or parsed.query or parsed.fragment):
            raise AppError('PUBLIC_WEB_CONFIG', '公网登录来源尚未配置。', status_code=503)
        if origin != expected:
            raise AppError('WEB_LOGIN_ORIGIN', '请从本站登录页面操作。', status_code=403)
        return
    if origin and origin != str(request.base_url).rstrip('/'):
        raise AppError('WEB_LOGIN_ORIGIN', '请从本站登录页面操作。', status_code=403)


def _browser_challenge(request, body, db):
    _browser_origin(request)
    proof = request.cookies.get(COOKIE + body.scene, '')
    row = db.get(WebLoginChallenge, token_digest(body.scene))
    if not row or not proof or not secrets.compare_digest(row.proof_hash, token_digest(proof)):
        raise AppError('WEB_LOGIN_INVALID', '登录码无效，请在原网页刷新后重新扫码。', status_code=404)
    return row


def _wechat_user(user):
    if user is None or user.provider != 'wechat' or user.app_id != settings.wechat_app_id.strip():
        raise AppError('WECHAT_ACCOUNT_REQUIRED', '请使用此小程序的微信账号确认登录。', status_code=403)


def _live_challenge(db, scene):
    row = db.get(WebLoginChallenge, token_digest(scene))
    if not row or row.expires_at <= datetime.utcnow() or row.status not in ('waiting', 'scanned'):
        raise AppError('WEB_LOGIN_EXPIRED', '登录码已失效，请刷新网页后重新扫码。', status_code=410)
    return row


@router.post('/challenges', status_code=201)
def create(request: Request, response: Response, db: Session = Depends(get_db)):
    _browser_origin(request)
    now = datetime.utcnow()
    requester = token_digest(request.client.host if request.client else 'unknown')
    recent = db.query(WebLoginChallenge).filter(WebLoginChallenge.created_at > now - timedelta(minutes=1))
    if recent.filter(WebLoginChallenge.requester_hash == requester).count() >= 5 or recent.count() >= 120:
        raise AppError('WEB_LOGIN_RATE_LIMIT', '登录码刷新过于频繁，请一分钟后再试。', retryable=True, status_code=429)
    db.query(WebLoginChallenge).filter(WebLoginChallenge.expires_at < now - timedelta(days=1)).delete(synchronize_session=False)
    scene, proof = secrets.token_hex(16), secrets.token_urlsafe(32)
    row = WebLoginChallenge(scene_hash=token_digest(scene), proof_hash=token_digest(proof), requester_hash=requester,
                            status='waiting', expires_at=now + timedelta(seconds=TTL))
    db.add(row)
    db.commit()  # Count failed upstream attempts too, before making a network call.
    try:
        image = wechat_qrcode.generate(scene)
    except AppError:
        row.status = 'failed'
        db.commit()
        raise
    response.set_cookie(COOKIE + scene, proof, max_age=TTL, httponly=True, secure=settings.deployment_mode == 'cloud' or request.url.scheme == 'https', samesite='lax', path='/api/auth/web')
    response.headers['Cache-Control'] = 'no-store'
    mime = 'image/png' if image.startswith(b'\x89PNG') else 'image/jpeg'
    return {'scene': scene, 'qr_image': f'data:{mime};base64,' + base64.b64encode(image).decode('ascii'), 'expires_in': TTL, 'verification': scene[-6:].upper()}


@router.post('/poll')
def poll(body: ChallengeRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    row = _browser_challenge(request, body, db)
    response.headers['Cache-Control'] = 'no-store'
    if row.expires_at <= datetime.utcnow():
        return {'status': 'expired'}
    if row.status != 'approved':
        return {'status': 'used' if row.status == 'consumed' else row.status}
    changed = db.query(WebLoginChallenge).filter(WebLoginChallenge.scene_hash == row.scene_hash,
        WebLoginChallenge.status == 'approved', WebLoginChallenge.expires_at > datetime.utcnow()).update({'status': 'consumed'}, synchronize_session=False)
    if changed != 1:
        db.rollback()
        return {'status': 'used'}
    user = db.get(AppUser, row.user_id)
    _wechat_user(user)
    token = issue_session(db, user)
    db.commit()
    return {'status': 'confirmed', 'access_token': token, 'expires_in': settings.auth_session_ttl_seconds, 'user': user_view(user)}


@router.post('/cancel')
def cancel(body: ChallengeRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    row = _browser_challenge(request, body, db)
    db.query(WebLoginChallenge).filter(WebLoginChallenge.scene_hash == row.scene_hash,
        WebLoginChallenge.status.in_(['waiting', 'scanned', 'approved'])).update({'status': 'cancelled'}, synchronize_session=False)
    db.commit()
    response.headers['Cache-Control'] = 'no-store'
    return {'status': 'cancelled'}


@router.post('/scan')
def scan(body: ChallengeRequest, response: Response, user: AppUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _wechat_user(user)
    row = _live_challenge(db, body.scene)
    if row.status == 'waiting':
        db.query(WebLoginChallenge).filter(WebLoginChallenge.scene_hash == row.scene_hash, WebLoginChallenge.status == 'waiting',
            WebLoginChallenge.expires_at > datetime.utcnow()).update({'status': 'scanned', 'user_id': user.id}, synchronize_session=False)
        db.commit()
        db.refresh(row)
    if row.user_id != user.id or row.status != 'scanned':
        raise AppError('WEB_LOGIN_INVALID', '此登录码已由其他账号扫码，请刷新网页后重试。', status_code=404)
    response.headers['Cache-Control'] = 'no-store'
    return {'status': 'scanned', 'verification': body.scene[-6:].upper(), 'expires_in': max(0, int((row.expires_at - datetime.utcnow()).total_seconds()))}


@router.post('/confirm')
def confirm(body: ConfirmRequest, response: Response, user: AppUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _wechat_user(user)
    row = _live_challenge(db, body.scene)
    if row.status != 'scanned':
        raise AppError('WEB_LOGIN_NOT_SCANNED', '请先扫描网页上的登录码。', status_code=409)
    if row.user_id != user.id:
        raise AppError('WEB_LOGIN_INVALID', '此登录码由其他账号扫码。', status_code=404)
    status = 'approved' if body.approve else 'denied'
    count = db.query(WebLoginChallenge).filter(WebLoginChallenge.scene_hash == row.scene_hash, WebLoginChallenge.status == 'scanned',
        WebLoginChallenge.user_id == user.id, WebLoginChallenge.expires_at > datetime.utcnow()).update({'status': status}, synchronize_session=False)
    if count != 1:
        db.rollback()
        raise AppError('WEB_LOGIN_EXPIRED', '登录码已失效，请重新扫码。', status_code=410)
    db.commit()
    response.headers['Cache-Control'] = 'no-store'
    return {'status': status}
