import pytest
from sqlalchemy import create_engine, text

from app import db as database


def legacy_engine(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'legacy.db').as_posix())
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE task (id INTEGER NOT NULL PRIMARY KEY, title TEXT)')
        connection.exec_driver_sql('CREATE INDEX custom_task_title ON task(title)')
        connection.exec_driver_sql('CREATE TABLE page (id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES task(id))')
        connection.exec_driver_sql('CREATE TABLE audit (title TEXT)')
        connection.exec_driver_sql('CREATE TRIGGER audit_task AFTER UPDATE ON task BEGIN INSERT INTO audit VALUES (NEW.title); END')
        connection.exec_driver_sql("INSERT INTO task VALUES (41, 'old outline')")
        connection.exec_driver_sql('INSERT INTO page VALUES (1, 41)')
    return engine


def test_legacy_id_upgrade_preserves_rows_references_indexes_and_triggers(tmp_path):
    engine = legacy_engine(tmp_path)
    try:
        with engine.connect() as connection:
            database.ensure_task_id_sequence(connection)
            database.ensure_task_id_sequence(connection)
            assert connection.scalar(text('SELECT title FROM task WHERE id=41')) == 'old outline'
            assert connection.scalar(text('SELECT task_id FROM page WHERE id=1')) == 41
            assert connection.scalar(text("SELECT name FROM sqlite_master WHERE type='index' AND name='custom_task_title'"))
            connection.exec_driver_sql("UPDATE task SET title='preserved' WHERE id=41")
            assert connection.scalar(text('SELECT title FROM audit')) == 'preserved'
            connection.exec_driver_sql('DELETE FROM task WHERE id=41')
            connection.exec_driver_sql("INSERT INTO task (title) VALUES ('new outline')")
            assert connection.scalar(text('SELECT id FROM task')) == 42
            connection.commit()
    finally:
        engine.dispose()


def test_id_upgrade_failure_rolls_back_original_table_and_data(tmp_path, monkeypatch):
    engine = legacy_engine(tmp_path)
    try:
        with engine.connect() as connection:
            original = connection.exec_driver_sql

            def fail_rename(statement, *args, **kwargs):
                if statement.startswith('ALTER TABLE'):
                    raise RuntimeError('injected migration interruption')
                return original(statement, *args, **kwargs)

            monkeypatch.setattr(connection, 'exec_driver_sql', fail_rename)
            with pytest.raises(RuntimeError, match='interruption'):
                database.ensure_task_id_sequence(connection)
            assert connection.scalar(text('SELECT title FROM task WHERE id=41')) == 'old outline'
            assert connection.scalar(text('SELECT task_id FROM page WHERE id=1')) == 41
            assert connection.scalar(text("SELECT name FROM sqlite_master WHERE name='custom_task_title'"))
    finally:
        engine.dispose()
