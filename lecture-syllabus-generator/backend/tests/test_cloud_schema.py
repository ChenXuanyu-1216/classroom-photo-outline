import pytest
from sqlalchemy import create_engine, inspect, text


def test_cloud_schema_is_explicit_and_idempotent(tmp_path):
    from app.schema_migrations import migrate_schema, check_schema
    engine = create_engine('sqlite:///' + (tmp_path / 'empty.db').as_posix())
    try:
        with pytest.raises(ValueError, match='schema'):
            check_schema(engine)
        migrate_schema(engine)
        migrate_schema(engine)
        check_schema(engine)
        assert {'app_user', 'task', 'coordination_fence', 'model_reservation', 'upload_intent',
                'upload_chunk', 'cloud_file_handle', 'object_cleanup'} <= set(inspect(engine).get_table_names())
        with engine.connect() as db:
            assert db.scalar(text('SELECT COUNT(*) FROM schema_revision')) == 1
    finally:
        engine.dispose()


def test_cloud_migration_will_not_adopt_an_unknown_nonempty_database(tmp_path):
    from app.schema_migrations import migrate_schema
    engine = create_engine('sqlite:///' + (tmp_path / 'unknown.db').as_posix())
    try:
        with engine.begin() as db:
            db.execute(text('CREATE TABLE private_record (content TEXT)'))
            db.execute(text("INSERT INTO private_record VALUES ('preserve me')"))
        with pytest.raises(ValueError, match='nonempty'):
            migrate_schema(engine)
        with engine.connect() as db:
            assert db.scalar(text('SELECT content FROM private_record')) == 'preserve me'
        assert inspect(engine).get_table_names() == ['private_record']
    finally:
        engine.dispose()


def test_schema_version_does_not_hide_missing_tables_or_columns(tmp_path):
    from app.schema_migrations import migrate_schema, check_schema
    engine = create_engine('sqlite:///' + (tmp_path / 'damaged.db').as_posix())
    try:
        migrate_schema(engine)
        with engine.begin() as db:
            db.execute(text('DROP TABLE upload_chunk'))
        with pytest.raises(ValueError, match='schema'):
            check_schema(engine)
    finally:
        engine.dispose()
def test_mysql_timestamps_preserve_snapshot_microseconds():
    from sqlalchemy.dialects import mysql
    from app.models import AppUser
    assert str(AppUser.__table__.c.created_at.type.compile(dialect=mysql.dialect())) == 'DATETIME(6)'


@pytest.mark.parametrize('mysql_code, reason', [
    (1045, 'MYSQL_AUTH_FAILED'),
    (1044, 'MYSQL_DATABASE_DENIED'),
    (1049, 'MYSQL_DATABASE_NOT_FOUND'),
    (1146, 'MYSQL_TABLE_NOT_FOUND'),
    (1142, 'MYSQL_PERMISSION_DENIED'),
    (2003, 'MYSQL_CONNECT_FAILED'),
    (2005, 'MYSQL_HOST_UNRESOLVED'),
    (2013, 'MYSQL_CONNECTION_LOST'),
    (2026, 'MYSQL_TLS_FAILED'),
])
def test_schema_check_reports_safe_database_failure_category(mysql_code, reason):
    import traceback
    from sqlalchemy.exc import OperationalError
    from app.schema_migrations import check_schema

    class UnavailableEngine:
        def connect(self):
            raise OperationalError('private-query', {'password': 'private-password'},
                                   Exception(mysql_code, 'private-host private-password private-provider-message'))

    with pytest.raises(ValueError, match=reason) as error:
        check_schema(UnavailableEngine())
    output = ''.join(traceback.format_exception(type(error.value), error.value, error.value.__traceback__))
    assert str(mysql_code) in str(error.value)
    for sensitive in ('private-query', 'private-password', 'private-host', 'private-provider-message'):
        assert sensitive not in output


def test_schema_check_never_renders_unknown_driver_error_contents():
    from sqlalchemy.exc import OperationalError
    from app.schema_migrations import check_schema

    class UnavailableEngine:
        def connect(self):
            raise OperationalError('private-query', {}, RuntimeError('private-connection-string'))

    with pytest.raises(ValueError, match='DB_CHECK_FAILED') as error:
        check_schema(UnavailableEngine())
    assert 'private-' not in str(error.value)
