from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.db import engine

router = APIRouter()


@router.get('/health/live')
def live():
    return {'status': 'ok'}


@router.get('/health/ready')
def ready():
    try:
        with engine.connect() as db:
            db.execute(text('SELECT 1'))
        if settings.deployment_mode == 'cloud':
            from app.schema_migrations import check_schema
            check_schema(engine, structure=False)
    except Exception:
        return JSONResponse(status_code=503, content={'status': 'unavailable'})
    return {'status': 'ready'}
