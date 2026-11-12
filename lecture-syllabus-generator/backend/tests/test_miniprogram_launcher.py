import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import socket
import subprocess
import threading

import pytest

SHELL = shutil.which('pwsh') or shutil.which('powershell')
pytestmark = pytest.mark.skipif(not SHELL, reason='PowerShell needed for launcher integration')
SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'start_miniprogram.ps1'


class Metadata(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/api/meta':
            self.send_error(404)
            return
        body = json.dumps({'model_provider': 'mock', 'model_name': 'isolated-launcher-test', 'model_ready': True}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def check(port):
    private = SCRIPT.parents[2] / 'miniprogram' / 'project.private.config.json'
    before = private.read_bytes() if private.exists() else None
    result = subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-File', str(SCRIPT), '-CheckOnly', '-Port', str(port)],
                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=25)
    assert (private.read_bytes() if private.exists() else None) == before
    return result


@pytest.mark.parametrize('host,expected', [('127.0.0.1', 'LOOPBACK_ONLY'), ('0.0.0.0', 'LAN_READY'), ('current-lan', 'LAN_READY')])
def test_check_only_distinguishes_loopback_from_lan_listener(host, expected):
    if host == 'current-lan':
        addresses = [row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                     if not row[4][0].startswith(('127.', '169.254.'))]
        if not addresses:
            pytest.skip('No current LAN IPv4 address')
        host = addresses[0]
    with ThreadingHTTPServer((host, 0), Metadata) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = check(server.server_port)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert expected in result.stdout + result.stderr, result.stdout + result.stderr
    assert (result.returncode == 0) == (expected == 'LAN_READY')


def test_check_only_never_starts_a_missing_backend():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    result = check(port)
    assert result.returncode != 0
    assert 'BACKEND_NOT_RUNNING' in result.stdout + result.stderr, result.stdout + result.stderr
