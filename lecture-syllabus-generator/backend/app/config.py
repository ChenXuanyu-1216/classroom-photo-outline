from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Literal
from pydantic import Field
from decimal import Decimal


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    baseline_version: str = "MVP Design v1.1 / PRD v1.1"
    app_name: str = "课堂PPT照片智能大纲生成器"
    # Authenticated local phone testing needs LAN access; proxy deployments can override.
    server_host: str = "0.0.0.0"
    deployment_mode: Literal['local', 'cloud'] = 'local'
    cloud_allow_mock: bool = False
    cloud_execution_verified: bool = False
    task_worker_mode: Literal['embedded', 'external'] = 'embedded'
    storage_backend: Literal['local', 'cos'] = 'local'
    cos_bucket: str = ''
    cos_region: str = ''
    cos_secret_id: str = Field(default='', repr=False)
    cos_secret_key: str = Field(default='', repr=False)
    cos_session_token: str = Field(default='', repr=False)
    cos_credentials_file: str = ''
    cos_credentials_source: Literal['configured', 'wechat-cloudrun'] = 'configured'
    database_tls_ca: str = ''
    cloud_max_user_bytes: int = Field(default=512 * 1024 * 1024, ge=1)
    cloud_max_total_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1)
    cloud_max_user_tasks: int = Field(default=200, ge=1)
    cloud_max_total_tasks: int = Field(default=2000, ge=1)
    cloud_max_pending_uploads_user: int = Field(default=3, ge=1)
    cloud_max_pending_uploads_total: int = Field(default=128, ge=1)
    cloud_max_active_jobs_user: int = Field(default=3, ge=1, le=20)
    public_web_enabled: bool = False
    public_origin: str = ''

    wechat_app_id: str = ''
    wechat_app_secret: str = Field(default='', repr=False)
    wechat_trust_env: bool = False
    wechat_qrcode_env: str = Field(default='release', pattern='^(release|trial|develop)$')
    auth_session_ttl_seconds: int = Field(default=604800, ge=300, le=2592000)

    model_provider: str = "mock"
    model_api_key: str = Field(default="", repr=False)
    model_base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-flash"
    model_timeout_seconds: int = Field(default=60, ge=1, le=300)
    model_trust_env: bool = False
    model_max_retry: int = Field(default=1, ge=0, le=3)
    model_max_tokens: int = Field(default=4096, ge=128, le=8192)
    diagram_review_max_tokens: int = Field(default=16384, ge=128, le=16384)
    model_budget_enforced: bool = False
    model_budget_cny: Decimal = Field(default=Decimal('5.00'), ge=Decimal('0.01'), decimal_places=2)
    model_context_max_bytes: int = Field(default=96000, ge=1024, le=524288)
    image_max_edge: int = Field(default=2560, ge=512, le=8192)
    image_max_pixels: int = Field(default=24000000, ge=1000000, le=50000000)
    cluster_provider: Literal["rule", "model"] = "rule"
    knowledge_expansion_enabled: bool = True
    task_queue_enabled: bool = True
    task_queue_capacity: int = Field(default=20, ge=1, le=20)

    allowed_ext: str = ".jpg,.jpeg,.png,.pdf"
    max_file_mb: int = 10
    max_files_per_batch: int = 20
    recommended_batch: int = 10
    low_confidence_threshold: float = 0.60

    database_url: str = Field(default="sqlite:///./data/app.db", repr=False)
    data_dir: str = "data"
    storage_dir: str = "storage"
    prompts_dir: str = "prompts"

    class Config:
        env_prefix = "APP_"
        env_file = BASE_DIR / ".env"

    @property
    def allowed_ext_list(self) -> list[str]:
        return [e.strip().lower() for e in self.allowed_ext.split(",") if e.strip()]


settings = Settings()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / settings.data_dir
STORAGE_DIR = BASE_DIR / settings.storage_dir
PROMPTS_DIR = BASE_DIR / settings.prompts_dir
FRONTEND_DIR = BASE_DIR.parent / "frontend"

DATA_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
