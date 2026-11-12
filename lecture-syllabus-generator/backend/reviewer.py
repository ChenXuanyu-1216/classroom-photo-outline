"""Loopback-only reviewer entry point. The normal run.py never imports this."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
import webbrowser


MODEL_ENV_NAMES = {
    'APP_MODEL_PROVIDER', 'APP_MODEL_API_KEY', 'APP_MODEL_BASE_URL', 'APP_MODEL_NAME',
    'APP_MODEL_TIMEOUT_SECONDS', 'APP_MODEL_TRUST_ENV', 'APP_MODEL_MAX_RETRY',
    'APP_MODEL_MAX_TOKENS', 'APP_DIAGRAM_REVIEW_MAX_TOKENS',
    'APP_MODEL_BUDGET_ENFORCED', 'APP_MODEL_BUDGET_CNY', 'APP_MODEL_CONTEXT_MAX_BYTES',
    'APP_IMAGE_MAX_EDGE', 'APP_IMAGE_MAX_PIXELS', 'APP_CLUSTER_PROVIDER',
    'APP_KNOWLEDGE_EXPANSION_ENABLED',
}


def create_review_app(root: Path):
    root = root.resolve()
    (root / 'data').mkdir(parents=True, exist_ok=True)
    # Model settings come only from the selected private local data directory.
    for name in list(os.environ):
        if name.startswith('APP_'):
            del os.environ[name]
    values = {
        'server_host': '127.0.0.1',
        'database_url': 'sqlite:///' + (root / 'data' / 'app.db').as_posix(),
        'data_dir': str(root / 'data'),
        'storage_dir': str(root / 'storage'),
        'model_provider': 'deepseek', 'cluster_provider': 'model',
        'model_base_url': 'https://api.deepseek.com', 'model_name': 'deepseek-flash',
        'model_api_key': '', 'wechat_app_id': '', 'wechat_app_secret': '',
        'knowledge_expansion_enabled': True, 'task_queue_enabled': True,
        'allowed_ext': '.jpg,.jpeg,.png',
    }
    model_file = root / '.env.reviewer'
    if not model_file.exists():
        raise ValueError('DeepSeek configuration is missing. Copy .env.reviewer.example to the selected data directory as .env.reviewer and set your own API key; no mock fallback is available.')
    if model_file.exists():
        from dotenv import dotenv_values
        supplied = dotenv_values(model_file, encoding='utf-8-sig', interpolate=False)
        if set(supplied) - MODEL_ENV_NAMES:
            raise ValueError('Unsupported reviewer model setting; see .env.reviewer.example. No values were logged.')
        values.update({key.removeprefix('APP_').lower(): value
                       for key, value in supplied.items() if value is not None})
    # config's initial directory creation must also stay inside review data.
    for key in ('data_dir', 'storage_dir', 'database_url'):
        os.environ['APP_' + key.upper()] = values[key]
    from app import config
    config.settings = config.Settings(_env_file=None, **values)
    config.DATA_DIR = root / 'data'
    config.STORAGE_DIR = root / 'storage'
    config.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    if (config.settings.model_provider != 'deepseek'
            or config.settings.model_base_url.strip().rstrip('/') not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}
            or config.settings.model_name != 'deepseek-flash'):
        raise ValueError('This reviewer entry point requires the official DeepSeek API and the configured deepseek-flash model; mock mode is not supported.')
    if not config.settings.model_api_key.strip():
        raise ValueError('DeepSeek review requires a configured API key in .env.reviewer; no mock fallback is available.')

    from app.main import app
    from app.db import SessionLocal, init_db
    from app.models import AppUser
    from app.services.auth_service import issue_session
    from fastapi import HTTPException
    from fastapi.responses import HTMLResponse

    init_db()
    entry = '/_review/start/' + secrets.token_urlsafe(32)
    used = False
    expires = time.monotonic() + 300
    gate = threading.Lock()

    @app.get(entry, include_in_schema=False)
    def reviewer_login():
        nonlocal used
        with gate:
            if used or time.monotonic() >= expires:
                raise HTTPException(status_code=404)
            used = True
        with SessionLocal() as db:
            user = db.query(AppUser).filter_by(provider='local', app_id='reviewer', subject='reviewer').first()
            if user is None:
                user = AppUser(provider='local', app_id='reviewer', subject='reviewer')
                db.add(user)
                db.flush()
            token = issue_session(db, user)
            session = {'token': token, 'user': {'id': user.id, 'provider': 'local'}}
            db.commit()
        serialized = json.dumps(session)
        body = ('<!doctype html><meta charset="utf-8"><title>Opening reviewer workspace</title>'
                '<p>Opening the local reviewer workspace...</p><script>\n'
                f'const session = {serialized};\n'
                'sessionStorage.setItem("classroom-wechat-session", JSON.stringify(session));\n'
                'location.replace("/");\n</script>')
        return HTMLResponse(body, headers={'Cache-Control': 'no-store',
                                          'Referrer-Policy': 'no-referrer',
                                          'X-Frame-Options': 'DENY'})

    return app, entry


def main():
    parser = argparse.ArgumentParser(description='Local source reviewer using your own official DeepSeek API key; no mock mode.')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent / 'data' / 'reviewer')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    root = args.data_dir.resolve()
    app, entry = create_review_app(root)
    from app.config import settings
    import uvicorn
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', args.port))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                               access_log=False, log_level='warning'))
        url = f'http://127.0.0.1:{port}{entry}'

        def open_when_ready():
            for _ in range(600):
                if server.started:
                    print('Reviewer ready: ' + url, flush=True)
                    print('Keep this window open. Press Ctrl+C to stop. Login link works once for 5 minutes.', flush=True)
                    if not args.no_browser:
                        webbrowser.open(url)
                    return
                time.sleep(.1)

        print('Starting local reviewer. Mode: ' + settings.model_provider, flush=True)
        print('Local data: ' + str(root), flush=True)
        threading.Thread(target=open_when_ready, daemon=True).start()
        server.run(sockets=[listener])


if __name__ == '__main__':
    main()
