"""Native Cloud Hosting credentials: real parser/cache, simulated HTTP boundary."""
import importlib
import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.errors import AppError

HTTP_CLIENT = httpx.Client
ENDPOINT = 'http://api.weixin.qq.com/_/cos/getauth'


def provider():
    return importlib.import_module('app.services.wechat_storage_credentials').CloudRunCredentialProvider()


@pytest.fixture
def upstream(monkeypatch):
    def install(handler):
        options = []
        def client(**kwargs):
            options.append(kwargs)
            return HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
        monkeypatch.setattr(httpx, 'Client', client)
        return options
    return install


def credentials(token='first-token', expiry=1600):
    return {'TmpSecretId': 'temporary-id', 'TmpSecretKey': 'temporary-key',
            'Token': token, 'ExpiredTime': expiry}


def clock(monkeypatch, wall=1000, monotonic=50):
    import time
    values = {'wall': wall, 'monotonic': monotonic}
    monkeypatch.setattr(time, 'time', lambda: values['wall'])
    monkeypatch.setattr(time, 'monotonic', lambda: values['monotonic'])
    return values


def test_refreshes_before_expiry_and_never_sends_application_secrets(monkeypatch, upstream):
    now = clock(monkeypatch)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=credentials('first-token' if len(calls) == 1 else 'new-token',
                                                   1600 if len(calls) == 1 else 2200))
    options = upstream(handle)
    source = provider()
    assert source.get() == ('temporary-id', 'temporary-key', 'first-token')
    now.update(wall=1539, monotonic=589)
    assert source.get() == ('temporary-id', 'temporary-key', 'first-token')
    assert len(calls) == 1
    now.update(wall=1540, monotonic=590)
    assert source.get() == ('temporary-id', 'temporary-key', 'new-token')
    assert len(calls) == 2
    for request in calls:
        assert request.method == 'GET'
        assert str(request.url) == ENDPOINT
        assert request.content == b''
        assert 'authorization' not in request.headers
        assert 'cookie' not in request.headers
    assert all(o['trust_env'] is False and o['follow_redirects'] is False and o['timeout'] == 10
               for o in options)


@pytest.mark.parametrize('wall,monotonic', [(900, 590), (1600, 51)])
def test_clock_jump_cannot_extend_cached_credential_lifetime(monkeypatch, upstream, wall, monotonic):
    now = clock(monkeypatch)
    calls = []
    # The first response expires at 1600; subsequent response stays valid after a forward jump.
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=credentials('first-token' if len(calls) == 1 else 'new-token',
                                                   1600 if len(calls) == 1 else 2200))
    upstream(handle)
    source = provider()
    assert source.get()[2] == 'first-token'
    now.update(wall=wall, monotonic=monotonic)
    assert source.get()[2] == 'new-token'
    assert len(calls) == 2


def test_concurrent_storage_requests_share_one_refresh(monkeypatch, upstream):
    clock(monkeypatch)
    calls = []
    barrier = threading.Barrier(8)
    upstream(lambda request: calls.append(request) or httpx.Response(200, json=credentials()))
    source = provider()
    def get():
        barrier.wait(timeout=5)
        return source.get()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: get(), range(8)))
    assert results == [('temporary-id', 'temporary-key', 'first-token')] * 8
    assert len(calls) == 1


@pytest.mark.parametrize('body', [
    {}, [], {'errcode': 48001, 'errmsg': 'private-upstream-data'},
    credentials(expiry=1060), credentials(expiry='NaN'), credentials(expiry='Infinity'),
    credentials(expiry=True), credentials(expiry=None),
    {**credentials(), 'TmpSecretId': ''}, {**credentials(), 'TmpSecretKey': 42},
    {**credentials(), 'Token': '   '}, {**credentials(), 'errcode': 48001},
])
def test_invalid_credentials_fail_closed_without_exposing_body(monkeypatch, upstream, caplog, body):
    clock(monkeypatch)
    upstream(lambda request: httpx.Response(200, json=body))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AppError) as error:
            provider().get()
    assert error.value.code == 'COS_CREDENTIALS_UNAVAILABLE'
    assert error.value.status_code == 503
    assert error.value.retryable is True
    output = ''.join(traceback.format_exception(type(error.value), error.value, error.value.__traceback__))
    for secret in ('private-upstream-data', 'temporary-key', 'first-token'):
        assert secret not in output + caplog.text


@pytest.mark.parametrize('response', [
    httpx.Response(403, text='private-upstream-data'),
    httpx.Response(302, headers={'Location': 'https://untrusted.invalid'}),
    httpx.Response(200, text='not json private-upstream-data'),
    httpx.Response(200, content=b'x' * (16 * 1024 + 1)),
])
def test_http_failures_redirects_and_oversized_bodies_are_not_credentials(monkeypatch, upstream, response):
    clock(monkeypatch)
    calls = []
    upstream(lambda request: calls.append(request) or response)
    with pytest.raises(AppError) as error:
        provider().get()
    assert error.value.code == 'COS_CREDENTIALS_UNAVAILABLE'
    assert len(calls) == 1
    assert 'private-upstream-data' not in str(error.value)


def test_failed_refresh_does_not_return_stale_token_and_can_recover(monkeypatch, upstream):
    now = clock(monkeypatch)
    calls = []
    def handle(request):
        calls.append(request)
        if len(calls) == 2:
            raise httpx.ReadTimeout('private-upstream-data', request=request)
        return httpx.Response(200, json=credentials('first-token' if len(calls) == 1 else 'new-token',
                                                   1600 if len(calls) == 1 else 2200))
    upstream(handle)
    source = provider()
    assert source.get()[2] == 'first-token'
    now.update(wall=1540, monotonic=590)
    with pytest.raises(AppError) as error:
        source.get()
    assert 'private-upstream-data' not in str(error.value)
    assert source.get()[2] == 'new-token'


def test_deeply_nested_json_is_a_sanitized_credential_failure(monkeypatch, upstream):
    clock(monkeypatch)
    upstream(lambda request: httpx.Response(200, content=b'[' * 2000 + b']' * 2000))
    with pytest.raises(AppError) as error:
        provider().get()
    assert error.value.code == 'COS_CREDENTIALS_UNAVAILABLE'
    assert error.value.status_code == 503


def test_cos_consumer_uses_native_rotating_credentials_without_fallback(monkeypatch, upstream):
    from app.config import settings
    from app.services import object_storage
    now = clock(monkeypatch)
    calls = []
    upstream(lambda request: calls.append(request) or httpx.Response(
        200, json=credentials('first-token' if len(calls) == 1 else 'new-token',
                              1600 if len(calls) == 1 else 2200)))
    monkeypatch.setattr(settings, 'cos_credentials_source', 'wechat-cloudrun', raising=False)
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    monkeypatch.setattr(settings, 'cos_secret_id', 'static-must-not-win')
    monkeypatch.setattr(settings, 'cos_secret_key', 'static-key')
    monkeypatch.setattr(object_storage, '_cloudrun_credentials', provider(), raising=False)
    monkeypatch.setattr(object_storage, '_client', lambda region, *values: (region, *values))
    store = object_storage.CosStorage('test-bucket', 'ap-shanghai')
    assert store.client == ('ap-shanghai', 'temporary-id', 'temporary-key', 'first-token')
    now.update(wall=1540, monotonic=590)
    assert store.client == ('ap-shanghai', 'temporary-id', 'temporary-key', 'new-token')
    now.update(wall=2200, monotonic=1250)
    upstream(lambda request: httpx.Response(403))
    with pytest.raises(AppError):
        _ = store.client


def test_native_source_cannot_make_internal_http_in_local_mode(monkeypatch):
    from app.config import settings
    from app.services.object_storage import _credentials
    monkeypatch.setattr(settings, 'cos_credentials_source', 'wechat-cloudrun', raising=False)
    monkeypatch.setattr(settings, 'deployment_mode', 'local')
    monkeypatch.setattr(settings, 'cos_secret_id', 'synthetic-id')
    monkeypatch.setattr(settings, 'cos_secret_key', 'synthetic-key')
    with pytest.raises(AppError) as error:
        _credentials()
    assert error.value.code == 'COS_CREDENTIALS_UNAVAILABLE'
