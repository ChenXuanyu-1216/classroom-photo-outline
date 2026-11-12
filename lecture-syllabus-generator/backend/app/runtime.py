"""Explicit deployment profiles; cloud must never silently use local state."""
import os
from sqlalchemy.engine import make_url


def validate_runtime(config):
    if config.cos_credentials_source == 'wechat-cloudrun':
        if config.deployment_mode != 'cloud' or config.storage_backend != 'cos':
            raise ValueError('WeChat cloud credentials require cloud COS mode')
        if any((config.cos_credentials_file, config.cos_secret_id, config.cos_secret_key,
                config.cos_session_token)):
            raise ValueError('WeChat cloud credentials conflict with configured credentials')
    if config.deployment_mode != 'cloud':
        return
    if make_url(config.database_url).drivername != 'mysql+pymysql':
        raise ValueError('Cloud deployment requires MySQL (mysql+pymysql)')
    if config.storage_backend != 'cos' or not config.cos_bucket or not config.cos_region:
        raise ValueError('Cloud deployment requires private COS storage')
    if config.task_worker_mode != 'external':
        raise ValueError('Cloud API requires external worker mode')
    if config.model_provider == 'mock' and not config.cloud_allow_mock:
        raise ValueError('Cloud mock requires explicit testing opt-in')
    if config.model_provider != 'mock' and not config.model_api_key.strip():
        raise ValueError('Cloud model configuration is incomplete')
    if not config.wechat_app_id.strip() or not config.wechat_app_secret.strip():
        raise ValueError('Cloud WeChat configuration is incomplete')


def server_options(environ=None):
    from app.config import settings
    environ = os.environ if environ is None else environ
    raw = environ.get('PORT', '8000')
    if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 65535:
        raise ValueError('PORT must be an integer between 1 and 65535')
    host = '0.0.0.0' if settings.deployment_mode == 'cloud' else settings.server_host
    return {'host': host, 'port': int(raw), 'reload': False}
