import pytest

from app.config import settings
from app.db import SessionLocal
from app.services.process_lock import exclusive_file_lock, LockBusyError


def test_coordination_preserves_the_existing_local_lock_protocol(tmp_path):
    from app.services.coordination import coordination_lock
    path = tmp_path / 'shared.lock'
    with SessionLocal() as db:
        with exclusive_file_lock(path):
            with pytest.raises(LockBusyError):
                with coordination_lock(db, 'test', path):
                    pytest.fail('Conflicting local operation was admitted')
        with coordination_lock(db, 'test', path):
            with pytest.raises(LockBusyError):
                with exclusive_file_lock(path):
                    pytest.fail('The existing lock protocol was bypassed')


@pytest.mark.parametrize('headers', [{'Prefer': 'respond-async'}, {}])
def test_cloud_api_cannot_accept_jobs_without_an_accepted_worker(client, monkeypatch, valid_image_bytes, headers):
    monkeypatch.setattr(settings, 'task_queue_enabled', True)
    task = client.post('/api/tasks', files=[('files', ('photo.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    monkeypatch.setattr(settings, 'cloud_allow_mock', False)
    monkeypatch.setattr(settings, 'cloud_execution_verified', False)
    response = client.post(f'/api/tasks/{task}/pipeline', headers=headers)
    assert response.status_code == 503
    assert response.json()['code'] == 'CLOUD_WORKER_UNVERIFIED'
    assert client.get(f'/api/tasks/{task}').json()['status'] == 'pending'


def test_paid_worker_requires_platform_execution_acceptance():
    from app.worker import validate_worker
    from app.config import Settings
    config = Settings(_env_file=None, deployment_mode='cloud',
                      database_url='mysql+pymysql://user:fake@db/app', storage_backend='cos',
                      cos_bucket='test-bucket', cos_region='ap-guangzhou', task_worker_mode='external',
                      model_provider='deepseek', model_api_key='synthetic-test-key',
                      wechat_app_id='wx-test', wechat_app_secret='synthetic-test-secret')
    with pytest.raises(ValueError, match='acceptance'):
        validate_worker(config)
