import json
import logging

import httpx
import pytest

from app.config import settings
from app.errors import AppError
from app.services import wechat_qrcode


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(settings, 'wechat_app_secret', 'private-secret')
    monkeypatch.setattr(settings, 'wechat_qrcode_env', 'develop')
    monkeypatch.setattr(wechat_qrcode, '_cache', {'identity': None, 'token': '', 'expires': 0})
    def install(handler):
        monkeypatch.setattr(wechat_qrcode, '_wechat_client', lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return install


def test_real_wechat_code_uses_scene_and_server_only_cached_credentials(transport, caplog):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('stable_token'):
            assert json.loads(request.content)['secret'] == 'private-secret'
            return httpx.Response(200, json={'access_token': 'private-access-token', 'expires_in': 7200})
        body = json.loads(request.content)
        assert body['scene'] == 'a' * 32 and body['env_version'] == 'develop'
        assert body['check_path'] is False and body['page'] == 'pages/web-login/web-login'
        return httpx.Response(200, content=b'\x89PNG\r\n\x1a\nimage')
    transport(handler)
    with caplog.at_level(logging.INFO, logger='httpx'):
        assert wechat_qrcode.generate('a' * 32).startswith(b'\x89PNG')
        wechat_qrcode.generate('a' * 32)
    assert len(calls) == 3
    assert 'private-access-token' not in caplog.text and 'private-secret' not in caplog.text


@pytest.mark.parametrize('code,expected', [(40164, 'WECHAT_IP_NOT_ALLOWED'), (40125, 'WECHAT_CONFIG_ERROR'), (41030, 'WECHAT_PAGE_UNAVAILABLE'), (999, 'WECHAT_QRCODE_UNAVAILABLE')])
def test_upstream_diagnostics_are_sanitized(transport, code, expected):
    transport(lambda request: httpx.Response(200, json={'errcode': code, 'errmsg': 'private-secret private-access-token'}))
    with pytest.raises(AppError) as result:
        wechat_qrcode.generate('a' * 32)
    assert result.value.code == expected
    assert 'private-' not in str(result.value)


def test_expired_wechat_token_refreshes_once(transport):
    count = {'token': 0, 'image': 0}
    def handler(request):
        if request.url.path.endswith('stable_token'):
            count['token'] += 1
            return httpx.Response(200, json={'access_token': 'server-token', 'expires_in': 7200})
        count['image'] += 1
        if count['image'] == 1:
            return httpx.Response(200, json={'errcode': 42001})
        return httpx.Response(200, content=b'\xff\xd8\xffimage')
    transport(handler)
    assert wechat_qrcode.generate('a' * 32).startswith(b'\xff\xd8\xff')
    assert count == {'token': 2, 'image': 2}
