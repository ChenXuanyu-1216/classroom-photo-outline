import os
import tempfile
from pathlib import Path

import pytest

os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["OTEL_PYTHON_DISABLED"] = "true"
os.environ["FASTAPI_TELEMETRY_DISABLE"] = "1"

# Set these BEFORE application imports, including test-module collection.
# Never use a developer's .env database, storage or live model credentials.
_test_directory = tempfile.TemporaryDirectory(prefix="lecture-syllabus-tests-")
TEST_ROOT = Path(_test_directory.name)
TEST_DATABASE = TEST_ROOT / "data" / "test.db"
TEST_DATABASE.parent.mkdir()
os.environ["APP_DATABASE_URL"] = "sqlite:///" + TEST_DATABASE.as_posix()
os.environ["APP_DATA_DIR"] = str(TEST_DATABASE.parent)
os.environ["APP_STORAGE_DIR"] = str(TEST_ROOT / "storage")
os.environ["APP_MODEL_PROVIDER"] = "mock"
os.environ['APP_DEPLOYMENT_MODE'] = 'local'
os.environ['APP_STORAGE_BACKEND'] = 'local'
os.environ['APP_TASK_WORKER_MODE'] = 'embedded'
os.environ['APP_COS_SECRET_ID'] = ''
os.environ['APP_COS_SECRET_KEY'] = ''
os.environ['APP_COS_SESSION_TOKEN'] = ''
os.environ['APP_COS_CREDENTIALS_SOURCE'] = 'configured'
os.environ['APP_COS_CREDENTIALS_FILE'] = ''
os.environ["APP_CLUSTER_PROVIDER"] = "rule"
os.environ["APP_KNOWLEDGE_EXPANSION_ENABLED"] = "false"
os.environ['APP_TASK_QUEUE_ENABLED'] = 'false'
os.environ["APP_MODEL_API_KEY"] = ""
os.environ['APP_WECHAT_APP_ID'] = 'wx-test-app'
os.environ['APP_WECHAT_APP_SECRET'] = ''
os.environ['APP_WECHAT_TRUST_ENV'] = 'false'
os.environ["APP_MODEL_BUDGET_ENFORCED"] = "true"
os.environ["APP_MODEL_BUDGET_CNY"] = "5"
os.environ["APP_ALLOWED_EXT"] = ".jpg,.jpeg,.png,.pdf"
os.environ["APP_MODEL_CONTEXT_MAX_BYTES"] = "96000"
os.environ["APP_IMAGE_MAX_EDGE"] = "2560"
os.environ["APP_IMAGE_MAX_PIXELS"] = "24000000"


@pytest.fixture
def valid_image_bytes():
    return (Path(__file__).parent / 'fixtures' / 'deepseek-synthetic-slide.png').read_bytes()


@pytest.fixture(autouse=True)
def isolated_database():
    from app.db import Base, engine, init_db

    # This guard must run before any destructive test cleanup.
    assert Path(engine.url.database).resolve() == TEST_DATABASE.resolve()
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def test_user(isolated_database, monkeypatch):
    from app.db import SessionLocal
    from app.models import AppUser
    from app.services.auth_service import issue_session

    # Legacy behavior tests now use an explicit real authenticated principal.
    # No dependency override or production auth bypass; raw auth tests use raw_client.
    with SessionLocal() as db:
        user = AppUser(provider='local', app_id='test', subject='legacy-suite')
        db.add(user)
        db.flush()
        assert user.id == 1
        token = issue_session(db, user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
    monkeypatch.setenv('TEST_AUTH_BEARER_TOKEN', token)
    return user


@pytest.fixture
def client(test_user):
    from fastapi.testclient import TestClient
    from app.main import app

    token = os.environ['TEST_AUTH_BEARER_TOKEN']
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.headers['Authorization'] = 'Bearer ' + token
        yield test_client


def pytest_unconfigure(config):
    # Close SQLite handles before TemporaryDirectory removes test-owned files.
    import sys
    if "app.db" in sys.modules:
        sys.modules["app.db"].engine.dispose()
    _test_directory.cleanup()


@pytest.fixture
def mysql_engine():
    """Disposable real MySQL only, never the application's production URL."""
    from sqlalchemy import inspect
    from sqlalchemy.engine import make_url
    value = os.environ.get('APP_TEST_MYSQL_URL')
    if not value:
        pytest.skip('APP_TEST_MYSQL_URL is absent; real MySQL was not tested')
    if make_url(value).database != 'kezhao_test_ci':
        pytest.fail('MySQL integration is restricted to the disposable kezhao_test_ci database')
    from app.database import create_db_engine
    from app.db import Base
    from app.schema_migrations import migrate_schema
    test_engine = create_db_engine(value)
    try:
        unexpected = set(inspect(test_engine).get_table_names()) - set(Base.metadata.tables)
        if unexpected:
            pytest.fail('Disposable test database contains unknown tables; refusing cleanup')
        Base.metadata.drop_all(test_engine)
        migrate_schema(test_engine)
        yield test_engine
    finally:
        test_engine.dispose()
