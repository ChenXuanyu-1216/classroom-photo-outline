"""Only evaluation binding defects; no live model calls."""
from datetime import datetime
from types import SimpleNamespace

from app.services.evaluation import ReviewInput, review_view, detail


def snapshot(markdown='# 课程\n## 真实模块\n- 可复核正文\n## 同名模块\n- 甲\n## 同名模块\n- 乙\n## 空模块\n'):
    return SimpleNamespace(id='snapshot-a', job_id=None, created_at=datetime(2026, 11, 3),
                           sha256='unchanged-evidence-hash', reviews=[], evidence={
        'task': {'status': 'success'}, 'capture_kind': 'manual_current_state',
        'outline': {'origin': 'current_saved_version', 'markdown': markdown}})


def body(**changes):
    values = {'reviewer': 'audit', 'review_kind': 'self', 'scope': 'module:不存在的模块',
              'expected': '只评价输出中存在的模块', 'observed': '复核模块绑定', 'structure': 3, 'usability': 3}
    values.update(changes)
    return ReviewInput(**values).model_dump()


def test_legacy_unbound_module_cannot_receive_pass():
    review = SimpleNamespace(id='legacy', created_at=datetime(2026, 11, 3), body=body())
    result = review_view(review, snapshot())
    assert result['module_pass'] is None
    assert result['module_binding'] == 'unbound'


def test_detail_identifies_distinct_modules_and_excludes_headings_inside_code():
    snap = snapshot('# 课程\n## 同名模块\n- 甲\n## 同名模块\n- 乙\n```text\n## 假标题\n```\n## 空模块\n### 仅有小标题\n```python\n```\n')
    result = detail(snap)
    modules = result['modules']
    assert [item['heading'] for item in modules] == ['同名模块', '同名模块', '空模块']
    assert modules[0]['id'] != modules[1]['id']
    assert modules[2]['has_content'] is False
    assert result['sha256'] == 'unchanged-evidence-hash'


def test_api_rejects_missing_forged_or_foreign_module_binding(client, valid_image_bytes):
    from app.db import SessionLocal
    from app.models import Outline
    task = client.post('/api/tasks', files=[('files', ('input.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    with SessionLocal() as db:
        db.add(Outline(task_id=task, version=1, markdown='# 课程\n## 真实模块\n- 正文\n## 空模块\n'))
        db.commit()
    snapshot_id = client.post(f'/api/tasks/{task}/evaluations').json()['id']
    url = f'/api/tasks/{task}/evaluations/{snapshot_id}'
    assert client.post(url + '/reviews', json=body(scope='module:真实模块')).status_code == 422
    modules = client.get(url).json()['modules']
    valid = body(scope='module:真实模块', module_id=modules[0]['id'])
    saved = client.post(url + '/reviews', json=valid)
    assert saved.status_code == 201 and saved.json()['module_pass'] is True
    assert saved.json()['module_binding'] == 'bound'
    forged = {**valid, 'scope': 'module:不存在的模块'}
    assert client.post(url + '/reviews', json=forged).status_code == 422
    forged = {**valid, 'module_id': 'module_99_' + 'a' * 16}
    assert client.post(url + '/reviews', json=forged).status_code == 422
    empty = body(scope='module:空模块', module_id=modules[1]['id'])
    saved_empty = client.post(url + '/reviews', json=empty)
    assert saved_empty.status_code == 201 and saved_empty.json()['module_pass'] is False
    assert len(client.get(url).json()['reviews']) == 2
    identical_id = client.post(f'/api/tasks/{task}/evaluations').json()['id']
    assert client.post(f'/api/tasks/{task}/evaluations/{identical_id}/reviews', json=valid).status_code == 422
    with SessionLocal() as db:
        db.query(Outline).filter_by(task_id=task).update({'markdown': '# 另一版本\n## 别的模块\n- 内容'})
        db.commit()
    other_id = client.post(f'/api/tasks/{task}/evaluations').json()['id']
    assert client.post(f'/api/tasks/{task}/evaluations/{other_id}/reviews', json=valid).status_code == 422
    assert client.get(url).json()['modules'][0]['id'] == valid['module_id']
