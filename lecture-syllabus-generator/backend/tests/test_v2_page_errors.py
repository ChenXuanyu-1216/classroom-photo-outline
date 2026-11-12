from sqlalchemy import create_engine, inspect, text

from app.errors import AppError


def test_page_error_contract_survives_failure_and_clears_after_recovery(client, monkeypatch, valid_image_bytes):
    from app.services.pipeline import pipeline
    original = pipeline.recognizer.recognize
    def fail(*args):
        raise AppError('MODEL_AUTH_ERROR', '请检查模型密钥。', retryable=False, status_code=503)
    monkeypatch.setattr(pipeline.recognizer, 'recognize', fail)
    task = client.post('/api/tasks', files=[('files', ('p.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    assert client.post(f'/api/tasks/{task}/recognize').status_code == 200
    page = client.get(f'/api/tasks/{task}/pages').json()['pages'][0]
    assert page['error_code'] == 'MODEL_AUTH_ERROR'
    assert page['error_retryable'] is False
    monkeypatch.setattr(pipeline.recognizer, 'recognize', original)
    assert client.post(f'/api/tasks/{task}/recognize?page_no=1').status_code == 200
    page = client.get(f'/api/tasks/{task}/pages').json()['pages'][0]
    assert page['error_code'] is None and page['error_retryable'] is None


def test_page_error_migration_is_additive_and_repeatable(tmp_path, monkeypatch):
    import app.db as database
    engine = create_engine('sqlite:///' + (tmp_path / 'legacy.db').as_posix())
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE page (id INTEGER PRIMARY KEY, recognized_text TEXT)'))
        connection.execute(text("INSERT INTO page VALUES (3, '保留识别')"))
    monkeypatch.setattr(database, 'engine', engine)
    try:
        database.init_db()
        database.init_db()
        assert {'error_code', 'error_retryable'} <= {col['name'] for col in inspect(engine).get_columns('page')}
        with engine.connect() as connection:
            assert tuple(connection.execute(text('SELECT recognized_text, error_code, error_retryable FROM page')).one()) == ('保留识别', None, None)
    finally:
        engine.dispose()
