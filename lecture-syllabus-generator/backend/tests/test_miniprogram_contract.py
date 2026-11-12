"""Mini-program wire contracts, with isolated DB/storage and mock recognition."""
import base64
from pathlib import Path
import subprocess

def test_binary_batch_from_miniprogram_and_image_source(client, valid_image_bytes):
    repo = Path(__file__).resolve().parents[3]
    script = """
const { multipart } = require('./miniprogram/utils/multipart');
const data = Buffer.from(require('node:fs').readFileSync(0, 'utf8'), 'base64');
const files = [{ path: 'second', name: 'second.png' }, { path: 'first', name: 'first.png' }];
multipart(files, async () => Uint8Array.from(data).buffer, 'contract-boundary').then(result =>
  process.stdout.write(Buffer.from(result.body).toString('base64')));
"""
    result = subprocess.run(['node', '-e', script], input=base64.b64encode(valid_image_bytes).decode(),
                            cwd=repo, capture_output=True, text=True, check=True, timeout=15)
    uploaded = client.post('/api/tasks', content=base64.b64decode(result.stdout),
                           headers={'Content-Type': 'multipart/form-data; boundary=contract-boundary'})
    assert uploaded.status_code == 201, uploaded.text
    task_id = uploaded.json()['task_id']
    assert uploaded.json()['page_count'] == 2
    pages = client.get(f'/api/tasks/{task_id}/pages').json()['pages']
    assert [p['filename'] for p in pages] == ['page_1.png', 'page_2.png']
    original = client.get(f'/api/tasks/{task_id}/pages/1/image')
    assert original.status_code == 200
    assert original.content == valid_image_bytes
    assert original.headers['content-type'] == 'image/png'
    assert original.headers['cache-control'] == 'no-store'
    generated = client.post(f'/api/tasks/{task_id}/pipeline')
    assert generated.status_code == 200, generated.text
    outline = client.get(f'/api/tasks/{task_id}/outline').json()
    # Existing backend explicitly marks non-DeepSeek provenance unverified.
    assert outline['recognition_mode'] == 'unverified'
    assert client.get('/api/meta').json()['model_provider'] == 'mock'
    saved = client.put(f'/api/tasks/{task_id}/outline', json={'markdown': '# 小程序校对\n\n来源 P1'})
    assert saved.status_code == 200
    word = client.get(f'/api/tasks/{task_id}/export')
    assert word.status_code == 200
    assert word.content[:2] == b'PK'


def test_image_source_missing_page_and_task_are_not_exposed(client, valid_image_bytes):
    first = client.post('/api/tasks', files=[('files', ('a.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    assert client.get(f'/api/tasks/{first}/pages/99/image').status_code == 404
    assert client.get('/api/tasks/99999/pages/1/image').status_code == 404


def test_image_source_rejects_outside_storage_and_missing_file(client, valid_image_bytes, tmp_path):
    from app.db import SessionLocal
    from app.models import Page
    task_id = client.post('/api/tasks', files=[('files', ('a.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    outside = tmp_path / 'private.png'
    outside.write_bytes(b'private-data')
    with SessionLocal() as db:
        page = db.query(Page).filter_by(task_id=task_id).one()
        page.file_path = str(outside)
        db.commit()
    result = client.get(f'/api/tasks/{task_id}/pages/1/image')
    assert result.status_code == 404
    assert b'private-data' not in result.content


def test_image_source_disallows_non_image_files(client):
    task_id = client.post('/api/tasks', files=[('files', ('slides.pdf', b'%PDF-sample', 'application/pdf'))]).json()['task_id']
    assert client.get(f'/api/tasks/{task_id}/pages/1/image').status_code == 415
