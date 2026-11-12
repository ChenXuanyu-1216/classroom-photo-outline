from sqlalchemy import create_engine, inspect, text, MetaData, Table, select
from sqlalchemy.schema import CreateTable
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings, DATA_DIR
from app.database import create_db_engine

DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_db_engine(settings.database_url, tls_ca=settings.database_tls_ca)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_task_id_sequence(connection):
    """Upgrade legacy SQLite IDs without reusing deleted record identities."""
    definition = connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='task'"))
    if not definition or 'AUTOINCREMENT' in definition.upper():
        return
    foreign_keys = connection.scalar(text('PRAGMA foreign_keys'))
    connection.commit()
    if foreign_keys:
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        connection.commit()
    try:
        # An explicit SQLite transaction makes DDL rollback with the copied data.
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        definition = connection.scalar(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='task'"))
        if 'AUTOINCREMENT' in definition.upper():
            connection.commit()
            return
        schema_objects = connection.scalars(text(
            "SELECT sql FROM sqlite_master WHERE tbl_name='task' AND type IN ('index','trigger') AND sql IS NOT NULL"
        )).all()
        original = Table('task', MetaData(), autoload_with=connection)
        upgrade_metadata = MetaData()
        for referenced in list(original.metadata.tables.values()):
            if referenced is not original:
                referenced.to_metadata(upgrade_metadata)
        upgraded = original.to_metadata(upgrade_metadata, name='_task_id_upgrade')
        upgraded.dialect_options['sqlite']['autoincrement'] = True
        connection.execute(CreateTable(upgraded))
        connection.execute(upgraded.insert().from_select(list(original.columns.keys()), select(original)))
        connection.exec_driver_sql('DROP TABLE task')
        connection.exec_driver_sql('ALTER TABLE _task_id_upgrade RENAME TO task')
        for statement in schema_objects:
            connection.exec_driver_sql(statement)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        if foreign_keys:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')


def init_db():
    if settings.deployment_mode == 'cloud':
        raise ValueError('Cloud schema requires the explicit migrate-cloud command')
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    # Upgrade existing SQLite databases without deleting their records.
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            ensure_task_id_sequence(connection)
        with engine.begin() as connection:
            task_columns = {column['name'] for column in inspect(connection).get_columns('task')}
            if 'owner_id' not in task_columns:
                connection.execute(text('ALTER TABLE task ADD COLUMN owner_id INTEGER REFERENCES app_user(id)'))
            connection.execute(text('CREATE INDEX IF NOT EXISTS ix_task_owner_id ON task(owner_id)'))
            columns = {column["name"] for column in inspect(connection).get_columns("page")}
            if "recognized_structured" not in columns:
                connection.execute(text("ALTER TABLE page ADD COLUMN recognized_structured JSON"))
            if 'error_code' not in columns:
                connection.execute(text('ALTER TABLE page ADD COLUMN error_code VARCHAR(64)'))
            if 'error_retryable' not in columns:
                connection.execute(text('ALTER TABLE page ADD COLUMN error_retryable BOOLEAN'))
            outline_columns = {column['name'] for column in inspect(connection).get_columns('outline')}
            if 'recognition_provider' not in outline_columns:
                connection.execute(text('ALTER TABLE outline ADD COLUMN recognition_provider VARCHAR(32)'))
