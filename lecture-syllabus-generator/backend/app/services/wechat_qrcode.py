"""Server-only WeChat access token cache and real mini-program codes."""
import threading
import time

import httpx

from app.config import settings
from app.errors import AppError
from app.services.wechat_login import _wechat_client

_lock = threading.Lock()
_cache = {'identity': None, 'token': '', 'expires': 0}


def _failure(data):
    code = data.get('errcode') if isinstance(data, dict) else None
    if code == 40164:
        return AppError('WECHAT_IP_NOT_ALLOWED', '微信拒绝了服务器 IP，请在小程序后台将后端出口 IP 加入白名单。', status_code=503)
    if code in (40013, 40125, 40001):
        return AppError('WECHAT_CONFIG_ERROR', '后端微信配置无效，请检查 AppID 和 AppSecret。', status_code=503)
    if code in (41030, 85096):
        return AppError('WECHAT_PAGE_UNAVAILABLE', '小程序登录确认页尚不可用，请上传包含该页面的开发版或体验版，并检查二维码版本配置。', status_code=503)
    return AppError('WECHAT_QRCODE_UNAVAILABLE', '暂时无法生成微信登录码，请稍后重试；持续失败请联系维护者检查微信接口权限。', retryable=True, status_code=502)


def _access_token(client):
    identity = (settings.wechat_app_id.strip(), settings.wechat_app_secret.strip())
    if not all(identity):
        raise AppError('WECHAT_NOT_CONFIGURED', '后端尚未配置微信 AppID/AppSecret，请联系维护者。', status_code=503)
    with _lock:
        if _cache['identity'] == identity and _cache['expires'] > time.monotonic():
            return _cache['token']
        # POST keeps AppSecret out of HTTPX request URL logs.
        response = client.post('https://api.weixin.qq.com/cgi-bin/stable_token', json={
            'grant_type': 'client_credential', 'appid': identity[0], 'secret': identity[1], 'force_refresh': False,
        })
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('access_token'), str) or not isinstance(data.get('expires_in'), int) or data['expires_in'] <= 120:
            raise _failure(data)
        _cache.update(identity=identity, token=data['access_token'], expires=time.monotonic() + data['expires_in'] - 120)
        return _cache['token']


def generate(scene: str) -> bytes:
    try:
        with _wechat_client() as client:
            for attempt in range(2):
                token = _access_token(client)
                response = client.post('https://api.weixin.qq.com/wxa/getwxacodeunlimit', params={'access_token': token}, json={
                    'scene': scene, 'page': 'pages/web-login/web-login', 'width': 430,
                    'check_path': settings.wechat_qrcode_env == 'release', 'env_version': settings.wechat_qrcode_env,
                })
                response.raise_for_status()
                content = response.content
                if content.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')) and len(content) <= 1024 * 1024:
                    return content
                data = response.json()
                if isinstance(data, dict) and data.get('errcode') in (40001, 40014, 42001) and attempt == 0:
                    with _lock:
                        _cache['expires'] = 0
                    continue
                raise _failure(data)
    except (httpx.HTTPError, ValueError):
        raise _failure(None) from None
