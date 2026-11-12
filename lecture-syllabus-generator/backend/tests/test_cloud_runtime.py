import runpy
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import Settings


def launch(monkeypatch, port):
    import uvicorn
    monkeypatch.setenv('PORT', port)
    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: calls.append(kwargs))
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'run.py'), run_name='__main__')
    return calls[0]


def test_platform_port_changes_the_actual_listener(monkeypatch):
    assert launch(monkeypatch, '8080')['port'] == 8080


@pytest.mark.parametrize('port', ['0', '65536', '8000;echo secret', 'invalid'])
def test_invalid_platform_port_does_not_start_a_listener(monkeypatch, port):
    with pytest.raises(ValueError, match='PORT'):
        launch(monkeypatch, port)


def test_local_factory_can_commit_and_read_an_isolated_sqlite_database(tmp_path):
    from app.database import create_db_engine
    engine = create_db_engine('sqlite:///' + (tmp_path / 'factory.db').as_posix())
    try:
        with engine.begin() as db:
            db.execute(text('CREATE TABLE item (value INTEGER NOT NULL)'))
            db.execute(text('INSERT INTO item VALUES (42)'))
        with engine.connect() as db:
            assert db.scalar(text('SELECT value FROM item')) == 42
    finally:
        engine.dispose()


def test_cloud_refuses_ephemeral_default_storage():
    from app.runtime import validate_runtime
    config = Settings(_env_file=None, deployment_mode='cloud')
    with pytest.raises(ValueError, match='MySQL'):
        validate_runtime(config)


def cloud_config(**values):
    defaults = dict(deployment_mode='cloud', database_url='mysql+pymysql://user:password@db/app',
                    storage_backend='cos', cos_bucket='test-bucket-1234', cos_region='ap-guangzhou',
                    model_provider='mock', cloud_allow_mock=True, task_worker_mode='external',
                    wechat_app_id='wx-test-app', wechat_app_secret='test-secret')
    return Settings(_env_file=None, **dict(defaults, **values))


def test_cloud_mock_requires_explicit_testing_opt_in():
    from app.runtime import validate_runtime
    with pytest.raises(ValueError, match='mock'):
        validate_runtime(cloud_config(cloud_allow_mock=False))
    validate_runtime(cloud_config())


def test_cloud_refuses_embedded_api_consumer():
    from app.runtime import validate_runtime
    with pytest.raises(ValueError, match='external'):
        validate_runtime(cloud_config(task_worker_mode='embedded'))


def test_health_endpoints_are_small_and_credential_free(client):
    response = client.get('/health/live')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
    ready = client.get('/health/ready')
    assert ready.status_code == 200
    assert ready.json() == {'status': 'ready'}
    assert 'password' not in ready.text and 'database' not in ready.text


def test_native_storage_source_is_explicit_and_rejects_local_runtime():
    from app.runtime import validate_runtime
    config = cloud_config(cos_credentials_source='wechat-cloudrun')
    validate_runtime(config)
    with pytest.raises(ValueError, match='cloud'):
        validate_runtime(config.model_copy(update={'deployment_mode': 'local'}))


@pytest.mark.parametrize('field', ['cos_secret_id', 'cos_secret_key', 'cos_session_token', 'cos_credentials_file'])
def test_native_storage_source_refuses_conflicting_credential_configuration(field):
    from app.runtime import validate_runtime
    config = cloud_config(cos_credentials_source='wechat-cloudrun', **{field: 'synthetic-config'})
    with pytest.raises(ValueError, match='credential'):
        validate_runtime(config)
