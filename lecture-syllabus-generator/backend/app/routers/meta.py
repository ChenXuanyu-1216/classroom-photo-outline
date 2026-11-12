from fastapi import APIRouter
from app.config import settings
from app.schemas import HealthResponse, MetaResponse
from app.services.model_budget import budget_status

router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        baseline_version=settings.baseline_version,
        model_provider=settings.model_provider,
    )


@router.get("/api/meta", response_model=MetaResponse)
def meta():
    budget = budget_status() if settings.model_provider == 'deepseek' or settings.deployment_mode == 'cloud' else {}
    if settings.deployment_mode == 'cloud':
        budget = {key: value for key, value in budget.items() if key in {'available', 'enforced'}}
    return MetaResponse(
        model_provider=settings.model_provider,
        model_name=settings.model_name,
        allowed_ext=settings.allowed_ext_list,
        max_file_mb=settings.max_file_mb,
        max_files_per_batch=settings.max_files_per_batch,
        recommended_batch=settings.recommended_batch,
        low_confidence_threshold=settings.low_confidence_threshold,
        error_contract={
            "fields": ["code", "message", "retryable"],
            "retryable_true": "前端须显式提供重试按钮",
        },
        cluster_provider=settings.cluster_provider,
        model_ready=bool(settings.model_api_key.strip()) if settings.model_provider in {'deepseek', 'multimodal'} else settings.model_provider == 'mock',
        model_budget=budget,
    )
