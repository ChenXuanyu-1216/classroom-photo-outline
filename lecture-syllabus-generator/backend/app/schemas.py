from pydantic import BaseModel, Field
from datetime import datetime


class HealthResponse(BaseModel):
    status: str
    app: str
    baseline_version: str
    model_provider: str


class MetaResponse(BaseModel):
    model_provider: str
    model_name: str
    allowed_ext: list[str]
    max_file_mb: int
    max_files_per_batch: int
    recommended_batch: int
    low_confidence_threshold: float
    error_contract: dict
    cluster_provider: str = 'rule'
    model_ready: bool = False
    model_budget: dict = Field(default_factory=dict)


class TaskCreateResponse(BaseModel):
    task_id: int
    page_count: int
    rejected: list[dict]


class PageDetail(BaseModel):
    page_no: int
    filename: str
    confidence: float
    low_confidence_flag: bool
    failed_flag: bool
    error_msg: str | None = None
    error_code: str | None = None
    error_retryable: bool | None = None


class TaskStatusResponse(BaseModel):
    task_id: int
    status: str
    page_count: int
    error_msg: str | None = None
    created_at: datetime
    active_job: dict | None = None
    last_job: dict | None = None


class PagesResponse(BaseModel):
    task_id: int
    pages: list[PageDetail]


class PipelineResponse(BaseModel):
    task_id: int
    status: str
    knowledge_point_count: int
    module_count: int


class OutlineResponse(BaseModel):
    task_id: int
    markdown: str
    version: int
    warnings: list[str] = Field(default_factory=list)
    recognition_mode: str = 'unverified'


class OutlineUpdateRequest(BaseModel):
    markdown: str


class OutlineUpdateResponse(BaseModel):
    task_id: int
    version: int


class TaskListItem(BaseModel):
    task_id: int
    title: str
    status: str
    page_count: int
    created_at: datetime


class TaskListResponse(BaseModel):
    tasks: list[TaskListItem]


class ErrorResponse(BaseModel):
    code: str
    message: str
    retryable: bool
