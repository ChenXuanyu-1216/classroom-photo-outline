import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from sqlalchemy import create_engine, inspect, text


def test_test_paths_are_separate_from_application_data():
    from app.config import DATA_DIR, STORAGE_DIR
    from app.db import engine
    database_path = Path(engine.url.database).resolve()
    assert DATA_DIR.resolve() == database_path.parent
    assert STORAGE_DIR.resolve().parent == database_path.parent.parent
    assert 'lecture-syllabus-tests-' in str(database_path.parent.parent)


def test_running_tests_preserves_normal_database_and_uploads(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    sandbox = tmp_path / 'checkout'
    shutil.copytree(backend / 'app', sandbox / 'app', ignore=shutil.ignore_patterns('__pycache__'))
    (sandbox / 'tests').mkdir()
    for name in ['conftest.py', 'test_smoke.py']:
        shutil.copy2(backend / 'tests' / name, sandbox / 'tests' / name)
    (sandbox / 'data').mkdir()
    (sandbox / 'storage').mkdir()
    user_database = sandbox / 'data' / 'app.db'
    with sqlite3.connect(user_database) as connection:
        connection.execute('CREATE TABLE personal_notes (value TEXT)')
        connection.execute("INSERT INTO personal_notes VALUES ('keep me')")
    original = user_database.read_bytes()
    user_upload = sandbox / 'storage' / 'keep.png'
    user_upload.write_bytes(b'personal-upload')
    env = os.environ.copy()
    env.update(APP_DATABASE_URL='sqlite:///' + user_database.as_posix(),
               APP_DATA_DIR=str(sandbox / 'data'), APP_STORAGE_DIR=str(sandbox / 'storage'),
               APP_MODEL_PROVIDER='multimodal', APP_MODEL_API_KEY='must-not-be-used')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests/test_smoke.py'],
                            cwd=sandbox, env=env, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')
    assert user_database.read_bytes() == original
    assert user_upload.read_bytes() == b'personal-upload'
    assert list((sandbox / 'storage').iterdir()) == [user_upload]


def test_old_database_upgrade_keeps_existing_page_and_is_repeatable(tmp_path, monkeypatch):
    import app.db as database
    legacy = create_engine('sqlite:///' + (tmp_path / 'old.db').as_posix())
    with legacy.begin() as connection:
        connection.execute(text('CREATE TABLE page (id INTEGER PRIMARY KEY, recognized_text TEXT)'))
        connection.execute(text("INSERT INTO page VALUES (7, 'old recognition')"))
    monkeypatch.setattr(database, 'engine', legacy)
    try:
        database.init_db()
        database.init_db()
        assert 'recognized_structured' in {column['name'] for column in inspect(legacy).get_columns('page')}
        with legacy.connect() as connection:
            row = connection.execute(text('SELECT id, recognized_text, recognized_structured FROM page')).one()
            assert tuple(row) == (7, 'old recognition', None)
    finally:
        legacy.dispose()
