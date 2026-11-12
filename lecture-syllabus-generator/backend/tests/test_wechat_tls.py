"""Local-only TLS peer: fixture keys are synthetic and never used by the app."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import threading

import httpx
import pytest

from app.config import settings
from app.errors import AppError
from app.services import wechat_login

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def platform_ca(monkeypatch):
    path = FIXTURES / 'synthetic-cloudrun-ca.pem'
    monkeypatch.setattr(wechat_login, 'CLOUDRUN_CA_FILE', path, raising=False)
    return path


@pytest.fixture
def tls_peer():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"status":"tls-verified"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(FIXTURES / 'synthetic-cloudrun-ca.pem'),
                            str(FIXTURES / 'synthetic-cloudrun-server-key.pem'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cloud_wechat_https_trusts_platform_ca(platform_ca, tls_peer, monkeypatch):
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    with wechat_login._wechat_client() as client:
        response = client.get(f'https://localhost:{tls_peer}/')
    assert response.status_code == 200
    assert response.json() == {'status': 'tls-verified'}


def test_cloud_wechat_https_still_checks_hostname(platform_ca, tls_peer, monkeypatch):
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    with wechat_login._wechat_client() as client, pytest.raises(httpx.ConnectError):
        client.get(f'https://127.0.0.1:{tls_peer}/')


def test_local_wechat_does_not_trust_cloud_platform_ca(platform_ca, tls_peer, monkeypatch):
    monkeypatch.setattr(settings, 'deployment_mode', 'local')
    with wechat_login._wechat_client() as client, pytest.raises(httpx.ConnectError):
        client.get(f'https://localhost:{tls_peer}/')


def test_absent_platform_ca_keeps_normal_verified_tls(tmp_path, tls_peer, monkeypatch):
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    monkeypatch.setattr(wechat_login, 'CLOUDRUN_CA_FILE', tmp_path / 'missing.pem', raising=False)
    with wechat_login._wechat_client() as client, pytest.raises(httpx.ConnectError):
        client.get(f'https://localhost:{tls_peer}/')


def test_invalid_platform_ca_fails_closed_without_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    path = tmp_path / 'invalid.pem'
    path.write_text('not-a-certificate-sensitive-placeholder')
    monkeypatch.setattr(wechat_login, 'CLOUDRUN_CA_FILE', path, raising=False)
    with pytest.raises(AppError) as error:
        wechat_login._wechat_client()
    assert error.value.code == 'WECHAT_TLS_CONFIG_ERROR'
    assert 'sensitive-placeholder' not in str(error.value)
    assert str(path) not in str(error.value)
