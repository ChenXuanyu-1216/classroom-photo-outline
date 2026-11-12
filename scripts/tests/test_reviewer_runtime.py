"""Run review-mode checks in separate interpreters, away from everyday data."""
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'lecture-syllabus-generator' / 'backend'


def test_review_session_uses_deepseek_adapter_and_preserves_work(tmp_path):
    # Catches accidental production credentials, missing auth, broken bootstrap,
    # non-persistent ownership, and packaging-specific frontend/resource paths.
    code = r'''
import io, json, os, re, sys
import httpx
from pathlib import Path
from fastapi.testclient import TestClient
from docx import Document
from reviewer import create_review_app

root = Path(sys.argv[1])
root.mkdir(exist_ok=True)
(root / '.env.reviewer').write_text('APP_MODEL_API_KEY=test-review-key\nAPP_KNOWLEDGE_EXPANSION_ENABLED=false\n', encoding='utf-8')
os.environ['APP_MODEL_PROVIDER'] = 'mock'
os.environ['APP_CLUSTER_PROVIDER'] = 'rule'
os.environ['APP_MODEL_API_KEY'] = 'must-not-be-used'
os.environ['APP_DATABASE_URL'] = 'sqlite:///must-not-be-created.db'
app, entry = create_review_app(root)
from app.config import settings
assert settings.model_provider == 'deepseek'
assert settings.model_api_key == 'test-review-key'
assert settings.model_base_url == 'https://api.deepseek.com'
assert settings.cluster_provider == 'model'
assert settings.server_host == '127.0.0.1'
assert Path(settings.database_url.removeprefix('sqlite:///')).resolve() == root / 'data' / 'app.db'
original_client = httpx.Client
requests = []
def upstream(request):
    assert str(request.url) == 'https://api.deepseek.com/chat/completions'
    assert request.headers['Authorization'] == 'Bearer test-review-key'
    payload = json.loads(request.content)
    assert payload['model'] == 'deepseek-flash'
    requests.append(payload)
    content = payload['messages'][1]['content']
    if isinstance(content, list):
        assert content[1]['image_url']['url'].startswith('data:image/png;base64,')
        result = {'title': 'API transport fixture', 'points': [{'content': 'API adapter example', 'kp_type': 'concept', 'source_type': '\u8bfe\u5802\u539f\u8bdd'}], 'confidence': .95, 'readability': 'clear', 'uncertain_regions': []}
    else:
        supplied = json.loads(content)['points']
        result = {'modules': [{'title': 'API module', 'point_ids': [point['id'] for point in supplied]}]}
    return httpx.Response(200, json={'id': 'transport-fixture', 'model': 'deepseek-flash', 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}]})
# Only this test replaces upstream HTTP. The delivered app uses the real adapter.
httpx.Client = lambda **kwargs: original_client(transport=httpx.MockTransport(upstream), **kwargs)
with TestClient(app) as client:
    assert client.get('/').status_code == 200
    assert client.get('/css/app.css').status_code == 200
    assert client.get('/api/tasks').status_code == 401
    assert client.get('/_review/start/wrong').status_code == 404
    start = client.get(entry)
    assert start.status_code == 200
    assert start.headers['cache-control'] == 'no-store'
    assert client.get(entry).status_code == 404
    session = json.loads(re.search(r'const session = (.+);', start.text).group(1))
    assert session['user']['provider'] == 'local'
    headers = {'Authorization': 'Bearer ' + session['token']}
    assert client.get('/api/auth/me', headers=headers).status_code == 200
    fixture = Path('tests/fixtures/deepseek-synthetic-slide.png').read_bytes()
    response = client.post('/api/tasks', headers=headers, files={'files': ('slide.png', fixture, 'image/png')})
    assert response.status_code == 201, response.text
    task_id = response.json()['task_id']
    response = client.post(f'/api/tasks/{task_id}/pipeline', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'success'
    outline = client.get(f'/api/tasks/{task_id}/outline', headers=headers).json()
    assert client.get('/api/health').json()['model_provider'] == 'deepseek'
    assert outline['recognition_mode'] == 'deepseek'
    assert len(requests) == 2
    edited = outline['markdown'] + '\n\nReviewer saved edit\n'
    assert client.put(f'/api/tasks/{task_id}/outline', headers=headers, json={'markdown': edited}).status_code == 200
    exported = client.get(f'/api/tasks/{task_id}/export', headers=headers)
    assert exported.status_code == 200
    document = Document(io.BytesIO(exported.content))
    assert 'Reviewer saved edit' in '\n'.join(p.text for p in document.paragraphs)
app2, entry2 = create_review_app(root)
with TestClient(app2) as client:
    session2 = json.loads(re.search(r'const session = (.+);', client.get(entry2).text).group(1))
    headers2 = {'Authorization': 'Bearer ' + session2['token']}
    assert session2['user']['id'] == session['user']['id']
    assert client.get(f'/api/tasks/{task_id}/outline', headers=headers2).json()['markdown'] == edited
assert not Path('must-not-be-created.db').exists()
print('review workflow passed')
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], cwd=BACKEND,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'review workflow passed' in result.stdout


def test_review_config_rejects_missing_keys_mock_fallback_and_database_override(tmp_path):
    code = r'''
import sys
from pathlib import Path
from reviewer import create_review_app
root = Path(sys.argv[1])
root.mkdir(exist_ok=True)
config = root / '.env.reviewer'
try:
    create_review_app(root)
except ValueError as error:
    assert 'DeepSeek configuration is missing' in str(error)
else:
    raise AssertionError('Missing config silently fell back')
config.write_text('APP_DATABASE_URL=sqlite:///unexpected.db\n', encoding='utf-8')
try:
    create_review_app(root)
except ValueError as error:
    assert 'Unsupported reviewer model setting' in str(error)
else:
    raise AssertionError('Database override was accepted')
config.write_text('APP_MODEL_PROVIDER=deepseek\nAPP_MODEL_API_KEY=\n', encoding='utf-8')
try:
    create_review_app(root)
except ValueError as error:
    assert 'requires a configured API key' in str(error)
else:
    raise AssertionError('Live mode without a key was accepted')
config.write_text('APP_MODEL_PROVIDER=mock\nAPP_MODEL_API_KEY=test-key\n', encoding='utf-8')
try:
    create_review_app(root)
except ValueError as error:
    assert 'requires the official DeepSeek API' in str(error)
else:
    raise AssertionError('Mock mode was accepted')
assert not Path('unexpected.db').exists()
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], cwd=BACKEND,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
