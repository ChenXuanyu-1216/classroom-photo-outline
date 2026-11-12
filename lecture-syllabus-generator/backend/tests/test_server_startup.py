import runpy
from pathlib import Path

from app.config import Settings


def run_launcher(monkeypatch, host=None):
    from app import config
    import uvicorn
    if host is None:
        monkeypatch.delenv('APP_SERVER_HOST', raising=False)
    else:
        monkeypatch.setenv('APP_SERVER_HOST', host)
    monkeypatch.setattr(config, 'settings', Settings(_env_file=None))
    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'run.py'), run_name='__main__')
    return calls[0][1]


def test_default_launcher_is_reachable_from_ipv4_lan(monkeypatch):
    assert run_launcher(monkeypatch)['host'] == '0.0.0.0'


def test_launcher_keeps_explicit_loopback_override_for_reverse_proxy(monkeypatch):
    assert run_launcher(monkeypatch, '127.0.0.1')['host'] == '127.0.0.1'


def test_launcher_respects_an_explicit_bind_address(monkeypatch):
    assert run_launcher(monkeypatch, '192.0.2.25')['host'] == '192.0.2.25'
