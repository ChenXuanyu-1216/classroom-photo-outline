"""Independent queue consumer. Cloud CPU/lifecycle acceptance is mandatory.

The HTTP listener provides health checks only. No client polling or HTTP
response is responsible for keeping this worker's processing loop alive.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import signal
import threading
import time

from sqlalchemy import text

from app.config import settings
from app.db import engine, init_db
from app.runtime import server_options, validate_runtime
from app.services.task_jobs import TaskJobQueue


def validate_worker(config):
    validate_runtime(config)
    if config.deployment_mode == 'cloud' and not config.cloud_execution_verified and not (
        config.model_provider == 'mock' and config.cloud_allow_mock
    ):
        raise ValueError('Cloud worker requires platform execution acceptance before paid work')
    if not config.task_queue_enabled:
        raise ValueError('Task queue is disabled')


def run(*, once=False, probe_seconds=0):
    validate_worker(settings)
    if probe_seconds and not (settings.model_provider == 'mock' and settings.cloud_allow_mock):
        raise ValueError('Background probe requires explicit mock mode')
    if settings.deployment_mode == 'cloud':
        from app.schema_migrations import check_schema
        check_schema(engine)
    else:
        init_db()
    queue = TaskJobQueue()
    if once:
        return queue.run_once()
    stop = threading.Event()
    for event in (signal.SIGTERM, signal.SIGINT):
        signal.signal(event, lambda *_: stop.set())

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            status = 200
            content = {'status': 'ok'}
            if self.path == '/health/ready':
                try:
                    if stop.is_set():
                        raise RuntimeError()
                    with engine.connect() as db:
                        db.execute(text('SELECT 1'))
                    content = {'status': 'ready'}
                except Exception:
                    status, content = 503, {'status': 'unavailable'}
            elif self.path != '/health/live':
                status, content = 404, {'status': 'not_found'}
            body = json.dumps(content).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass  # Do not log arbitrary request paths or headers.

    options = server_options()
    server = ThreadingHTTPServer((options['host'], options['port']), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if probe_seconds:
            from app.runtime_probe import run_probe, probe_status
            identifier = run_probe(engine, seconds=probe_seconds, wait=stop.wait)
            print(json.dumps(probe_status(engine, identifier)), flush=True)
            return
        cleaned_at = 0
        while not stop.is_set():
            try:
                worked = queue.run_once()
                if time.monotonic() - cleaned_at >= 10:
                    from app.services.cloud_cleanup import cleanup_objects
                    cleanup_objects(limit=5)
                    cleaned_at = time.monotonic()
            except Exception:
                logging.error('Worker iteration failed; durable job state retained')
                worked = False
            if not worked:
                stop.wait(.25)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run the private durable task worker')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--probe-seconds', type=int, default=0)
    arguments = parser.parse_args()
    if arguments.once and arguments.probe_seconds:
        parser.error('--once and --probe-seconds cannot be combined')
    run(once=arguments.once, probe_seconds=arguments.probe_seconds)
