from datetime import datetime, timedelta
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean,
    DateTime, ForeignKey, JSON, UniqueConstraint, BigInteger,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.mysql import LONGTEXT, DATETIME
from app.db import Base

# MySQL's plain DATETIME truncates microseconds from SQLite snapshots and TTLs.
DateTime = DateTime().with_variant(DATETIME(fsp=6), 'mysql')
FULL_TEXT = Text().with_variant(LONGTEXT(), 'mysql')


class AppUser(Base):
    __tablename__ = 'app_user'
    __table_args__ = (UniqueConstraint('provider', 'app_id', 'subject', name='uq_app_user_identity'), {'sqlite_autoincrement': True})
    id = Column(Integer, primary_key=True)
    provider = Column(String(20), nullable=False)
    app_id = Column(String(64), nullable=False)
    subject = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AuthSession(Base):
    __tablename__ = 'auth_session'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('app_user.id'), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WebLoginChallenge(Base):
    __tablename__ = 'web_login_challenge'
    scene_hash = Column(String(64), primary_key=True)
    proof_hash = Column(String(64), nullable=False)
    requester_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, default='waiting')
    user_id = Column(Integer, ForeignKey('app_user.id'), nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class Task(Base):
    __tablename__ = "task"
    __table_args__ = {'sqlite_autoincrement': True}

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey('app_user.id'), nullable=True, index=True)
    title = Column(String(255), default="未命名任务")
    status = Column(String(32), default="pending")  # pending/queued/running/success/failed/partial
    page_count = Column(Integer, default=0)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pages = relationship("Page", back_populates="task", cascade="all, delete-orphan")
    knowledge_points = relationship("KnowledgePoint", back_populates="task", cascade="all, delete-orphan")
    outlines = relationship("Outline", back_populates="task", cascade="all, delete-orphan")
    export_logs = relationship("ExportLog", back_populates="task", cascade="all, delete-orphan")
    jobs = relationship('TaskJob', foreign_keys='TaskJob.task_id', cascade='all, delete-orphan')
    evaluations = relationship('EvaluationSnapshot', back_populates='task', cascade='all, delete-orphan')


class TaskJob(Base):
    __tablename__ = 'task_job'
    id = Column(String(36), primary_key=True)
    task_id = Column(Integer, ForeignKey('task.id'), nullable=False, index=True)
    # NULL terminal rows permit history, one active row per task across processes.
    active_task_id = Column(Integer, unique=True, nullable=True)
    operation = Column(String(20), nullable=False)
    page_no = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default='queued', index=True)
    result = Column(JSON, nullable=True)
    error = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class Page(Base):
    __tablename__ = "page"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    page_no = Column(Integer, nullable=False)  # 逻辑页码
    filename = Column(String(512), nullable=False)
    file_path = Column(String(512), nullable=False)
    confidence = Column(Float, default=0.0)
    low_confidence_flag = Column(Boolean, default=False)
    failed_flag = Column(Boolean, default=False)
    recognized_text = Column(FULL_TEXT, nullable=True)
    recognized_structured = Column(JSON, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_retryable = Column(Boolean, nullable=True)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    task = relationship("Task", back_populates="pages")


class KnowledgePoint(Base):
    __tablename__ = "knowledge_point"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    page_id = Column(Integer, ForeignKey("page.id"), nullable=False)
    content = Column(FULL_TEXT, nullable=False)
    kp_type = Column(String(64), default="concept")  # concept/conclusion/method/example
    source_type = Column(String(32), default="课堂原话")  # 课堂原话 / AI推断 / AI生成 / 来源待核对
    source_pages = Column(String(128), nullable=False)  # 如 "P1" 或 "P1,P2"
    dedup_key = Column(FULL_TEXT, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    task = relationship("Task", back_populates="knowledge_points")


class Outline(Base):
    __tablename__ = "outline"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    markdown = Column(FULL_TEXT, nullable=False)
    version = Column(Integer, default=1)
    recognition_provider = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    task = relationship("Task", back_populates="outlines")
    nodes = relationship("OutlineNode", back_populates="outline", cascade="all, delete-orphan")


class OutlineNode(Base):
    __tablename__ = "outline_node"

    id = Column(Integer, primary_key=True, index=True)
    outline_id = Column(Integer, ForeignKey("outline.id"), nullable=False, index=True)
    level = Column(Integer, nullable=False)  # 1=模块, 2=知识点
    title = Column(FULL_TEXT, nullable=False)
    content = Column(FULL_TEXT, nullable=True)
    source_type = Column(String(32), nullable=True)
    source_pages = Column(String(128), nullable=True)
    parent_id = Column(Integer, ForeignKey("outline_node.id", ondelete='CASCADE'), nullable=True)
    sort_order = Column(Integer, default=0)

    outline = relationship("Outline", back_populates="nodes")
    children = relationship("OutlineNode", cascade="all, delete-orphan")


class ExportLog(Base):
    __tablename__ = "export_log"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    file_size = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    task = relationship("Task", back_populates="export_logs")


class SchemaRevision(Base):
    __tablename__ = 'schema_revision'
    id = Column(Integer, primary_key=True)
    revision = Column(String(32), nullable=False)


class CoordinationFence(Base):
    __tablename__ = 'coordination_fence'
    name = Column(String(64), primary_key=True)
    generation = Column(BigInteger, nullable=False)


class ModelBudget(Base):
    __tablename__ = 'model_budget'
    name = Column(String(32), primary_key=True)
    reserved_fen = Column(BigInteger, nullable=False, default=0)


class ModelReservation(Base):
    __tablename__ = 'model_reservation'
    id = Column(String(64), primary_key=True)
    budget_name = Column(String(32), ForeignKey('model_budget.name'), nullable=False)
    amount_fen = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class UploadIntent(Base):
    __tablename__ = 'upload_intent'
    __table_args__ = (UniqueConstraint('owner_id', 'client_key', name='uq_upload_owner_key'),)
    id = Column(String(36), primary_key=True)
    owner_id = Column(Integer, ForeignKey('app_user.id'), nullable=False, index=True)
    client_key = Column(String(64), nullable=False)
    manifest = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False, default='open')
    task_id = Column(Integer, ForeignKey('task.id', ondelete='SET NULL'), nullable=True)
    result = Column(JSON, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class UploadChunk(Base):
    __tablename__ = 'upload_chunk'
    intent_id = Column(String(36), ForeignKey('upload_intent.id', ondelete='CASCADE'), primary_key=True)
    file_index = Column(Integer, primary_key=True)
    chunk_index = Column(Integer, primary_key=True)
    object_ref = Column(String(512), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size = Column(Integer, nullable=False)


class CloudFileHandle(Base):
    __tablename__ = 'cloud_file_handle'
    id = Column(String(36), primary_key=True)
    owner_id = Column(Integer, ForeignKey('app_user.id'), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey('task.id', ondelete='CASCADE'), nullable=False, index=True)
    object_ref = Column(String(512), nullable=False)
    size = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    media_type = Column(String(100), nullable=False)
    temporary = Column(Boolean, nullable=False, default=False)
    expires_at = Column(DateTime, nullable=False, index=True)


class ObjectCleanup(Base):
    __tablename__ = 'object_cleanup'
    object_ref = Column(String(512), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    not_before = Column(DateTime, nullable=False, default=lambda: datetime.utcnow() + timedelta(hours=1))


class StoredObject(Base):
    __tablename__ = 'stored_object'
    object_ref = Column(String(512), primary_key=True)
    owner_id = Column(Integer, ForeignKey('app_user.id'), nullable=True, index=True)
    task_id = Column(Integer, ForeignKey('task.id', ondelete='SET NULL'), nullable=True, index=True)
    intent_id = Column(String(36), ForeignKey('upload_intent.id', ondelete='SET NULL'), nullable=True, index=True)
    size = Column(Integer, nullable=False)
    kind = Column(String(16), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RuntimeProbe(Base):
    __tablename__ = 'runtime_probe'
    id = Column(String(36), primary_key=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ticks = Column(Integer, default=0, nullable=False)
    max_gap_seconds = Column(Float, default=0, nullable=False)
    completed = Column(Boolean, default=False, nullable=False)


class EvaluationSnapshot(Base):
    __tablename__ = 'evaluation_snapshot'
    id = Column(String(36), primary_key=True)
    task_id = Column(Integer, ForeignKey('task.id'), nullable=False, index=True)
    job_id = Column(String(36), unique=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sha256 = Column(String(64), nullable=False)
    evidence = Column(JSON, nullable=False)
    task = relationship('Task', back_populates='evaluations')
    reviews = relationship('EvaluationReview', back_populates='snapshot', cascade='all, delete-orphan')


class EvaluationReview(Base):
    __tablename__ = 'evaluation_review'
    id = Column(String(36), primary_key=True)
    snapshot_id = Column(String(36), ForeignKey('evaluation_snapshot.id'), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    body = Column(JSON, nullable=False)
    snapshot = relationship('EvaluationSnapshot', back_populates='reviews')
