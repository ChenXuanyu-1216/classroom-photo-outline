"""Acceptance check for the actual ZIP, runtime, CMD launcher and HTTP workflow."""
import io
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import zipfile

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = Path(os.environ.get('REVIEWER_BUNDLE', ROOT / 'output' / 'ClassroomOutline-Review-DeepSeek-PRIVATE.zip'))


@pytest.mark.skipif(sys.platform != 'win32' or not ARCHIVE.exists(), reason='Needs a built Windows review ZIP')
def test_extracted_cmd_runs_without_system_python_and_preserves_review_data(tmp_path, request):
    extracted = tmp_path / 'review copy 中文'
    with zipfile.ZipFile(ARCHIVE) as archive:
        names = archive.namelist()
        assert not any('/.venv/' in name or '/storage/' in name or '/data/' in name for name in names)
        assert not any(name.endswith('/.env') or name.endswith('.db') or name.endswith('.log') for name in names)
        archive.extractall(extracted)
    bundle = next(path for path in extracted.iterdir() if path.is_dir())
    env = dict(os.environ)
    env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    env['PYTHONHOME'] = str(tmp_path / 'no-system-python')
    env['PYTHONPATH'] = str(tmp_path / 'no-system-packages')
    env['APP_MODEL_PROVIDER'] = 'mock'
    env['APP_MODEL_API_KEY'] = 'must-not-be-used'
    manifest = json.loads((bundle / 'manifest.json').read_text())
    assert 'pytest' not in manifest['dependencies']
    assert 'fastapi' in manifest['dependencies']
    assert manifest['contains_model_credentials'] is True
    private_config = bundle / 'reviewer-data' / '.env.reviewer'
    assert private_config.is_file()
    request.addfinalizer(lambda: private_config.unlink(missing_ok=True))

    def launch():
        launcher = bundle / 'Start Review.cmd'
        command = f'"{os.environ["COMSPEC"]}" /d /s /c ""{launcher}" --no-browser"'
        process = subprocess.Popen(command, env=env, cwd=tmp_path, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        lines = queue.Queue()

        def read_output():
            for line in process.stdout:
                lines.put(line)
            lines.put('PROCESS ENDED')

        threading.Thread(target=read_output, daemon=True).start()
        output = []
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                line = lines.get(timeout=max(.1, deadline - time.monotonic()))
                output.append(line)
                match = re.search(r'Reviewer ready: (http://127\.0\.0\.1:\d+)(/\S+)', line)
                if match:
                    return process, match.group(1), match.group(2)
                if 'PROCESS ENDED' in line:
                    break
            raise AssertionError('Launcher did not start:\n' + ''.join(output))
        except BaseException:
            stop(process)
            raise

    def stop(process):
        if process.poll() is None:
            # This is only the CMD process tree created by this test.
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
        process.wait(timeout=10)
        process.stdout.close()

    def session(client, entry):
        assert client.get('/api/tasks').status_code == 401
        response = client.get(entry)
        assert response.status_code == 200
        value = json.loads(re.search(r'const session = (.+);', response.text).group(1))
        client.headers['Authorization'] = 'Bearer ' + value['token']

    process, origin, entry = launch()
    try:
        with httpx.Client(base_url=origin, trust_env=False, timeout=15) as client:
            assert client.get('/api/health').json()['model_provider'] == 'deepseek'
            meta = client.get('/api/meta').json()
            assert meta['model_ready'] is True
            assert meta['model_name'] == 'deepseek-flash'
            session(client, entry)
            assert client.get('/').status_code == 200
            assert client.get('/js/auth.js').status_code == 200
            image = (bundle / 'samples' / 'synthetic-slide.png').read_bytes()
            response = client.post('/api/tasks', files={'files': ('slide.png', image, 'image/png')})
            assert response.status_code == 201, response.text
            task_id = response.json()['task_id']
            outline = '# Manual save check\n'
            if os.environ.get('REVIEWER_LIVE_GENERATION') == '1':
                response = client.post(f'/api/tasks/{task_id}/pipeline', headers={'Prefer': 'respond-async'})
                assert response.status_code == 202, response.text
                job_id = response.json()['job_id']
                for _ in range(360):
                    job = client.get(f'/api/tasks/{task_id}/jobs/{job_id}').json()
                    if job['status'] not in {'queued', 'running'}:
                        break
                    time.sleep(.5)
                assert job['status'] == 'success', job
                result = client.get(f'/api/tasks/{task_id}/outline').json()
                assert result['recognition_mode'] == 'deepseek'
                outline = result['markdown']
                assert 'P(y|x)' in outline
            edited = outline + '\n\nPortable reviewer saved edit\n'
            assert client.put(f'/api/tasks/{task_id}/outline', json={'markdown': edited}).status_code == 200
            assert client.get(f'/api/tasks/{task_id}/pages/1/image').content == image
            exported = client.get(f'/api/tasks/{task_id}/export')
            assert exported.status_code == 200
            with zipfile.ZipFile(io.BytesIO(exported.content)) as word:
                assert b'Portable reviewer saved edit' in word.read('word/document.xml')
    finally:
        stop(process)
    process, origin, entry = launch()
    try:
        with httpx.Client(base_url=origin, trust_env=False) as client:
            session(client, entry)
            assert client.get(f'/api/tasks/{task_id}/outline').json()['markdown'] == edited
    finally:
        stop(process)
