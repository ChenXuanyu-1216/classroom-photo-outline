"""Exchange one-use WeChat codes at a fixed trusted endpoint. Never return keys."""
import httpx
import certifi
import logging
from pathlib import Path
import re
import ssl

from app.config import settings
from app.errors import AppError


class _RedactWeChatCredentials(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if 'api.weixin.qq.com/' in message:
            record.msg = re.sub(r'([?&](?:secret|js_code|access_token)=)[^&\s\"]*', r'\1[REDACTED]', message)
            record.args = ()
        return True


# HTTPX logs full request URLs at INFO, including successful requests.
logging.getLogger('httpx').addFilter(_RedactWeChatCredentials())


# Fixed file injected by WeChat Cloud Hosting's open-interface sidecar.
# Extend trust only for this server-side WeChat client, never disable TLS checks.
CLOUDRUN_CA_FILE = Path('/app/cert/certificate.crt')


def _wechat_client():
    verify = True
    if settings.deployment_mode == 'cloud' and CLOUDRUN_CA_FILE.exists():
        try:
            verify = ssl.create_default_context(cafile=certifi.where())
            verify.load_verify_locations(cafile=str(CLOUDRUN_CA_FILE))
        except (OSError, ssl.SSLError):
            raise AppError('WECHAT_TLS_CONFIG_ERROR',
                           '微信云调用证书无法加载，请联系维护者检查容器证书。',
                           status_code=503) from None
    return httpx.Client(timeout=10, trust_env=settings.wechat_trust_env,
                        follow_redirects=False, verify=verify)


def exchange_code(code: str) -> str:
    if not settings.wechat_app_id.strip() or not settings.wechat_app_secret.strip():
        raise AppError('WECHAT_NOT_CONFIGURED', '后端尚未配置微信 AppID/AppSecret，请联系维护者。', status_code=503)
    try:
        with _wechat_client() as client:
            response = client.get('https://api.weixin.qq.com/sns/jscode2session', params={
                'appid': settings.wechat_app_id.strip(), 'secret': settings.wechat_app_secret.strip(),
                'js_code': code, 'grant_type': 'authorization_code',
            })
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        # URLs/exceptions may contain AppSecret and code: do not log or chain them.
        raise AppError('WECHAT_UNAVAILABLE', '微信登录服务暂时无法连接，请稍后重新登录。', retryable=True, status_code=502) from None
    if not isinstance(data, dict):
        raise AppError('WECHAT_UNAVAILABLE', '微信登录服务返回异常。', retryable=True, status_code=502)
    if data.get('errcode'):
        if data['errcode'] in (40029, 40163):
            raise AppError('WECHAT_CODE_INVALID', '微信登录凭证已失效，请重新登录。', retryable=True, status_code=400)
        if data['errcode'] in (40013, 40125, 40001):
            raise AppError('WECHAT_CONFIG_ERROR', '后端微信 AppID/AppSecret 配置不匹配，请联系维护者。', status_code=503)
        raise AppError('WECHAT_LOGIN_FAILED', '微信暂时未能验证登录，请稍后重试。', retryable=True, status_code=502)
    subject = data.get('openid')
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 128:
        raise AppError('WECHAT_LOGIN_FAILED', '微信未返回有效身份，无法登录。', retryable=True, status_code=502)
    return subject
