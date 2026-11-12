"""Explicit, non-destructive cloud schema bootstrap and revision checks."""
import hashlib

from sqlalchemy import inspect, select

from app.db import Base
from app.models import SchemaRevision

REVISION = 'cloud-0002'
PREVIOUS_REVISION = 'cloud-0001'
EVALUATION_TABLES = ('evaluation_snapshot', 'evaluation_review')

# Allowlisted diagnostics only: driver messages may contain passwords, SQL or URLs.
_DATABASE_FAILURES = {
    1045: ('MYSQL_AUTH_FAILED', 'Check the application account, password and allowed hosts.'),
    1044: ('MYSQL_DATABASE_DENIED', 'Check the application account database grants.'),
    1049: ('MYSQL_DATABASE_NOT_FOUND', 'Check the configured database name.'),
    1146: ('MYSQL_TABLE_NOT_FOUND', 'Check the selected database and explicit schema initialization.'),
    1142: ('MYSQL_PERMISSION_DENIED', 'Check the application account table permissions.'),
    2003: ('MYSQL_CONNECT_FAILED', 'Check the service/database environment, private network, host and port.'),
    2005: ('MYSQL_HOST_UNRESOLVED', 'Check the configured host and private DNS.'),
    2013: ('MYSQL_CONNECTION_LOST', 'Check database availability and the private network.'),
    2026: ('MYSQL_TLS_FAILED', 'Check the database TLS configuration.'),
}


def _check_tables(inspector, expected):
    actual = set(inspector.get_table_names())
    for table in expected:
        if table.name not in actual:
            raise ValueError('Cloud schema is incomplete')
        columns = {column['name'] for column in inspector.get_columns(table.name)}
        if not set(table.columns.keys()) <= columns:
            raise ValueError('Cloud schema is incomplete')



def _check_evaluation_tables(inspector, expected):
    """Only reuse added tables that preserve evidence identity and relationships."""
    from sqlalchemy import UniqueConstraint

    _check_tables(inspector, expected)
    for table in expected:
        columns = {column['name']: column for column in inspector.get_columns(table.name)}
        for column in table.columns:
            actual = columns[column.name]
            wanted_type = column.type.dialect_impl(inspector.bind.dialect)
            actual_type = actual['type']
            if (actual['nullable'] != column.nullable
                    or actual_type._type_affinity is not wanted_type._type_affinity
                    or getattr(actual_type, 'length', None) != getattr(wanted_type, 'length', None)
                    or getattr(actual_type, 'fsp', None) != getattr(wanted_type, 'fsp', None)):
                raise ValueError('Cloud evaluation schema has incompatible columns')
        primary = inspector.get_pk_constraint(table.name).get('constrained_columns') or []
        if primary != [column.name for column in table.primary_key.columns]:
            raise ValueError('Cloud evaluation schema has an incompatible primary key')
        unique = {frozenset(item['column_names']) for item in inspector.get_unique_constraints(table.name)}
        indexes = inspector.get_indexes(table.name)
        unique.update(frozenset(item['column_names']) for item in indexes if item.get('unique'))
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and frozenset(column.name for column in constraint.columns) not in unique:
                raise ValueError('Cloud evaluation schema is missing required uniqueness')
        actual_indexes = {tuple(item['column_names']) for item in indexes}
        if any(tuple(column.name for column in index.columns) not in actual_indexes for index in table.indexes):
            raise ValueError('Cloud evaluation schema is missing a required index')
        foreign = inspector.get_foreign_keys(table.name)
        for constraint in table.foreign_key_constraints:
            expected_columns = [column.name for column in constraint.columns]
            target_columns = [element.column.name for element in constraint.elements]
            target_table = next(iter(constraint.elements)).column.table.name
            if not any(item['constrained_columns'] == expected_columns
                       and item['referred_table'] == target_table
                       and item['referred_columns'] == target_columns
                       and item.get('referred_schema') in (None, inspector.default_schema_name)
                       and item.get('options', {}).get('ondelete') == constraint.ondelete
                       for item in foreign):
                raise ValueError('Cloud evaluation schema is missing a required foreign key')


def check_schema(engine, *, structure=True):
    try:
        with engine.connect() as connection:
            revisions = connection.execute(select(SchemaRevision.id, SchemaRevision.revision)).all()
        if revisions != [(1, REVISION)]:
            raise ValueError('Cloud schema revision does not match this application')
        if structure:
            _check_tables(inspect(engine), Base.metadata.sorted_tables)
            _check_evaluation_tables(inspect(engine), [Base.metadata.tables[name] for name in EVALUATION_TABLES])
    except ValueError:
        raise
    except Exception as error:
        arguments = getattr(getattr(error, 'orig', None), 'args', ())
        code = arguments[0] if arguments and type(arguments[0]) is int else None
        if code in _DATABASE_FAILURES:
            reason, suggestion = _DATABASE_FAILURES[code]
            message = f'Cloud schema check failed [{reason}:{code}]. {suggestion}'
        else:
            message = 'Cloud schema is unavailable [DB_CHECK_FAILED]; inspect database configuration and initialization.'
        raise ValueError(message) from None


def migrate_schema(engine):
    from sqlalchemy import text
    with engine.connect() as connection:
        mysql = engine.dialect.name == 'mysql'
        name = 'kezhao-schema-' + hashlib.sha256(str(engine.url.database).encode()).hexdigest()[:32]
        acquired = False
        try:
            if mysql:
                acquired = connection.scalar(text('SELECT GET_LOCK(:name, 10)'), {'name': name}) == 1
                if not acquired:
                    raise ValueError('Cloud schema migration is busy')
            if mysql:
                # Apply identical identity/collation rules to fresh and added tables.
                for table in Base.metadata.tables.values():
                    table.dialect_options['mysql']['engine'] = 'InnoDB'
                    table.dialect_options['mysql']['charset'] = 'utf8mb4'
                    table.dialect_options['mysql']['collate'] = 'utf8mb4_bin'
            existing = set(inspect(connection).get_table_names())
            if existing:
                if 'schema_revision' not in existing:
                    raise ValueError('Refusing to adopt a nonempty unversioned database')
                revisions = connection.execute(select(SchemaRevision.id, SchemaRevision.revision)).all()
                if revisions != [(1, PREVIOUS_REVISION)]:
                    check_schema(engine)
                    return
                legacy = [table for table in Base.metadata.sorted_tables if table.name not in EVALUATION_TABLES]
                added = [Base.metadata.tables[name] for name in EVALUATION_TABLES]
                _check_tables(inspect(connection), legacy)
                # MySQL DDL can survive an interrupted migration. Accept intact
                # added tables on retry; never adopt malformed pre-existing ones.
                _check_evaluation_tables(inspect(connection), [table for table in added if table.name in existing])
                Base.metadata.create_all(connection, tables=added)
                _check_evaluation_tables(inspect(connection), added)
                connection.execute(SchemaRevision.__table__.update().where(
                    SchemaRevision.id == 1, SchemaRevision.revision == PREVIOUS_REVISION
                ).values(revision=REVISION))
            else:
                Base.metadata.create_all(connection)
                connection.execute(SchemaRevision.__table__.insert().values(id=1, revision=REVISION))
            connection.commit()
        finally:
            if mysql and acquired:
                try:
                    connection.execute(text('SELECT RELEASE_LOCK(:name)'), {'name': name})
                    connection.commit()
                except Exception:
                    connection.invalidate()
    check_schema(engine)
