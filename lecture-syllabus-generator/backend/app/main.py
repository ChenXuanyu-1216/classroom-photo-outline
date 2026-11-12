from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from app.config import settings, FRONTEND_DIR
from app.db import init_db, SessionLocal
from app.services.task_execution import reconcile_running_tasks
from app.services.task_jobs import task_queue, reconcile_jobs
from app.errors import AppError, app_error_handler, generic_error_handler
from app.routers import meta, tasks, outline, export, sources, auth, web_login, evaluations
from app.routers import health
from app.runtime import validate_runtime
from app.routers import cloud_files
from app.body_limits import UploadBodyLimit

app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, generic_error_handler)
app.add_middleware(UploadBodyLimit)


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/api/meta" or path.startswith('/api/auth/'):
        response.headers["Cache-Control"] = "no-store"
    elif path == "/" or path.endswith(".html") or path.startswith(("/js/", "/css/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.include_router(meta.router)
app.include_router(health.router)
app.include_router(cloud_files.router)
app.include_router(auth.router)
app.include_router(web_login.router)
app.include_router(tasks.router)
app.include_router(outline.router)
app.include_router(export.router)
app.include_router(sources.router)
app.include_router(evaluations.router)


@app.on_event("startup")
def startup():
    validate_runtime(settings)
    if settings.deployment_mode == 'cloud':
        from app.db import engine
        from app.schema_migrations import check_schema
        check_schema(engine)
    else:
        init_db()
        with SessionLocal() as db:
            reconcile_jobs(db)
            reconcile_running_tasks(db)
    if settings.task_queue_enabled and settings.task_worker_mode == 'embedded' and settings.deployment_mode == 'local':
        task_queue.start()


@app.on_event('shutdown')
def shutdown():
    task_queue.stop()


if FRONTEND_DIR.exists():
    app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")

    @app.get("/")
    async def index():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/{page}.html")
    async def frontend_page(page: str):
        file_path = FRONTEND_DIR / f"{page}.html"
        if file_path.exists():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
