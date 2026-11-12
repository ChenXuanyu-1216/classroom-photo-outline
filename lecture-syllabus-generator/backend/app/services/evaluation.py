"""Immutable task evidence and append-only human reviews; never calls a model."""
from datetime import datetime
from hashlib import sha256
import json
import re
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import settings, STORAGE_DIR
from app.errors import AppError, TaskNotFoundError
from app.models import Task, Page, Outline, EvaluationSnapshot, EvaluationReview


PROTOCOL = 'exp05-web-v1'
APP_ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode('utf-8')).hexdigest()


def timestamp(value):
    return value.isoformat(timespec='microseconds') + 'Z' if value else None


def file_hash(path):
    hasher = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            hasher.update(block)
    return hasher.hexdigest()


def capture(db, task_id, job=None):
    """Stage a snapshot in the caller's terminal-state transaction (no commit)."""
    db.flush()  # Preserve staged terminal states before populate_existing reloads.
    if job:
        existing = db.query(EvaluationSnapshot).filter_by(job_id=job.id).first()
        if existing:
            return existing
    task = db.get(Task, task_id, populate_existing=True)
    if task is None:
        raise TaskNotFoundError()
    pages = db.query(Page).filter_by(task_id=task_id).order_by(Page.page_no).all()
    outline = db.query(Outline).filter_by(task_id=task_id).order_by(Outline.version.desc()).first()
    captured = datetime.utcnow()
    page_evidence = []
    for page in pages:
        input_hash = None
        try:
            if page.file_path.startswith('cos://'):
                from app.services import object_storage
                content = object_storage.get_storage().read(
                    page.file_path, limit=min(settings.max_file_mb * 1024 * 1024, object_storage.MAX_READ)
                )
                input_hash = sha256(content).hexdigest()
            else:
                path = Path(page.file_path).resolve()
                if path.is_relative_to(STORAGE_DIR.resolve()) and path.is_file():
                    input_hash = file_hash(path)
        except (OSError, AppError):
            # Missing/unavailable originals must not destroy terminal evidence.
            # No provider diagnostics or storage credentials enter the snapshot.
            pass
        # Persisted recognition is our validated schema, not raw HTTP completions.
        body = dict(page.recognized_structured or {})
        raw_meta = body.pop('_meta', {}) or {}
        allowed = ('provider', 'model', 'request_id', 'usage', 'prompt_version', 'input',
                   'context_version', 'output_schema_version', 'elapsed_seconds', 'attempts',
                   'confidence_basis', 'diagram_review_prompt_version')
        meta = {key: raw_meta[key] for key in allowed if key in raw_meta}
        page_evidence.append({'page_no': page.page_no, 'input_sha256': input_hash,
                              'failed': page.failed_flag, 'low_confidence': page.low_confidence_flag,
                              'error': {'code': page.error_code, 'message': page.error_msg,
                                        'retryable': page.error_retryable},
                              'text': page.recognized_text, 'structured': body, 'metadata': meta})
    prompt_root = Path(settings.prompts_dir)
    if not prompt_root.is_absolute():
        prompt_root = APP_ROOT.parent / prompt_root
    elapsed = (job.finished_at - job.started_at).total_seconds() if job and job.started_at and job.finished_at else None
    evidence = {
        'protocol': PROTOCOL, 'captured_at': timestamp(captured),
        'capture_kind': 'job_terminal' if job else 'manual_current_state',
        'task': {'id': task.id, 'status': task.status, 'page_count': task.page_count},
        'job': {'id': job.id, 'operation': job.operation, 'status': job.status,
                'started_at': timestamp(job.started_at), 'finished_at': timestamp(job.finished_at),
                'elapsed_seconds': elapsed, 'error': job.error} if job else None,
        'pages': page_evidence,
        'outline': {'version': outline.version, 'markdown': outline.markdown,
                    'recognition_provider': outline.recognition_provider,
                    'origin': ('retained_before_attempt' if job and job.started_at and
                               (outline.updated_at or outline.created_at) < job.started_at else
                               'saved_during_attempt' if job else 'current_saved_version')} if outline else None,
        'configuration': {'recognizer': settings.model_provider, 'clusterer': settings.cluster_provider,
                          'knowledge_expansion': settings.knowledge_expansion_enabled},
        'code_hashes': {p.relative_to(APP_ROOT).as_posix(): file_hash(p) for p in sorted(APP_ROOT.rglob('*.py'))},
        'prompt_hashes': {p.name: file_hash(p) for p in sorted(prompt_root.glob('*.yaml'))},
        'limits': ['配置不等于实际执行；实际识别元数据见各页。',
                   '代码和提示词哈希为取证时磁盘文件；历史记录不能反推原运行版本。',
                   '快照不包含每次网络重试事件；已有页内容可能来自缓存或旧识别。',
                   '手动快照不等于首次模型输出；删除任务会删除关联证据，请先导出。'],
    }
    snapshot = EvaluationSnapshot(id=str(uuid4()), task_id=task_id, job_id=job.id if job else None,
                                  created_at=captured, evidence=evidence, sha256=digest(evidence))
    db.add(snapshot)
    return snapshot


class Counts(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected: int = Field(ge=0, le=100000)
    matched: int = Field(ge=0, le=100000)
    output: int = Field(ge=0, le=100000)
    supported: int = Field(ge=0, le=100000)
    source_correct: int = Field(ge=0, le=100000)
    source_total: int = Field(ge=0, le=100000)

    @model_validator(mode='after')
    def consistent(self):
        if self.matched > self.expected or self.supported > self.output or self.source_correct > self.source_total:
            raise ValueError('正确数不能超过对应总数')
        if self.matched > self.supported:
            raise ValueError('正确恢复的期望单元不能超过有证据支持的输出单元')
        return self


class CriticalError(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    category: str = Field(min_length=1, max_length=50)
    expected: str = Field(min_length=1, max_length=4000)
    actual: str = Field(min_length=1, max_length=4000)
    impact: str = Field(min_length=1, max_length=2000)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reviewer: str = Field(min_length=1, max_length=80)
    review_kind: str = Field(pattern='^(self|independent|adjudication)$')
    scope: str = Field(min_length=1, max_length=1024)
    module_id: str | None = Field(default=None, pattern=r'^module_[1-9]\d*_[a-f0-9]{16}$')
    expected: str = Field(min_length=1, max_length=12000)
    observed: str = Field(min_length=1, max_length=12000)
    counts: Counts | None = None
    structure: int | None = Field(default=None, ge=0, le=4, strict=True)
    usability: int | None = Field(default=None, ge=0, le=4, strict=True)
    critical_errors: list[CriticalError] = Field(default_factory=list, max_length=30)
    notes: str = Field(default='', max_length=12000)
    user_feedback: str = Field(default='', max_length=12000)
    core_flow: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_flow(self):
        allowed = {'upload', 'generate', 'source', 'edit_save', 'reopen', 'word', 'source_labels'}
        if set(self.core_flow) - allowed or any(v not in {'passed', 'failed', 'not_run', 'blocked'} for v in self.core_flow.values()):
            raise ValueError('流程检查项或状态无效')
        if (self.structure is not None or self.usability is not None) and not self.scope.startswith('module:'):
            raise ValueError('模块评分的范围须以 module: 开头')
        if self.module_id is not None and not self.scope.startswith('module:'):
            raise ValueError('只有单模块评价可以绑定模块')
        return self


def get_snapshot(db, task_id, snapshot_id):
    snapshot = db.query(EvaluationSnapshot).filter_by(task_id=task_id, id=snapshot_id).first()
    if snapshot is None:
        raise AppError('EVIDENCE_NOT_FOUND', '评价快照不存在。', status_code=404)
    return snapshot


def summary(snapshot):
    job = snapshot.evidence.get('job') or {}
    return {'id': snapshot.id, 'job_id': snapshot.job_id, 'created_at': timestamp(snapshot.created_at),
            'status': job.get('status', snapshot.evidence['task']['status']), 'sha256': snapshot.sha256,
            'capture_kind': snapshot.evidence['capture_kind']}


def snapshot_modules(snapshot):
    """Derive stable module identities from immutable saved Markdown, not names from a client."""
    markdown = (snapshot.evidence.get('outline') or {}).get('markdown', '')
    modules, heading, content, fence = [], None, [], None

    def finish():
        if heading is None:
            return
        identity = digest({'snapshot_id': snapshot.id, 'heading': heading, 'content': '\n'.join(content)})[:16]
        has_content = any(line.strip() and not re.fullmatch(r'[\s`~#*+\-]+', line)
                          and not re.match(r'^ {0,3}(`{3,}|~{3,}|#{1,6}(?:\s|$))', line) for line in content)
        modules.append({'id': f'module_{len(modules) + 1}_{identity}',
                        'heading': heading, 'has_content': has_content})

    for line in markdown.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence is not None:
            if heading is not None:
                content.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            if heading is not None:
                content.append(line)
        elif line.startswith('## ') and line[3:].strip():
            finish()
            heading, content = line[3:].strip(), []
        elif line.startswith('# '):
            finish()
            heading, content = None, []
        elif heading is not None:
            content.append(line)
    finish()
    return modules


def bound_module(snapshot, body):
    return next((module for module in snapshot_modules(snapshot)
                 if module['id'] == body.get('module_id')
                 and body['scope'] == 'module:' + module['heading']), None)


def validate_module_binding(snapshot, body):
    if body.scope.startswith('module:') and bound_module(snapshot, body.model_dump()) is None:
        raise AppError('EVALUATION_MODULE_INVALID', '请选择这份快照中实际存在的模块；模块名称与绑定记录必须一致。', status_code=422)


def review_view(review, snapshot):
    body = review.body
    counts = body.get('counts')
    metrics = None if counts is None else {
        'fidelity': counts['supported'] / counts['output'] if counts['output'] else None,
        'coverage': counts['matched'] / counts['expected'] if counts['expected'] else None,
        'source': counts['source_correct'] / counts['source_total'] if counts['source_total'] else None,
    }
    module_pass = None
    module = bound_module(snapshot, body) if body['scope'].startswith('module:') else None
    binding = ('bound' if module else 'unbound') if body['scope'].startswith('module:') else 'not_applicable'
    if body['scope'].startswith('module:') and body.get('structure') is not None and body.get('usability') is not None:
        outline = snapshot.evidence.get('outline')
        if module and outline and outline['origin'] != 'retained_before_attempt':
            module_pass = module['has_content'] and body['structure'] >= 3 and body['usability'] >= 3 and not body['critical_errors']
    return {'id': review.id, 'created_at': timestamp(review.created_at), **body,
            'metrics': metrics, 'module_pass': module_pass, 'module_binding': binding}


def detail(snapshot):
    return {**summary(snapshot), 'evidence': snapshot.evidence, 'modules': snapshot_modules(snapshot),
            'reviews': [review_view(r, snapshot) for r in sorted(snapshot.reviews, key=lambda r: (r.created_at, r.id))]}
