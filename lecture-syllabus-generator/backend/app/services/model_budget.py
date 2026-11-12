"""Conservative request accounting with an optional local spending cap."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from contextvars import ContextVar
from uuid import uuid4

from app.config import DATA_DIR, settings
from app.errors import AppError
from app.services.process_lock import LockBusyError, exclusive_file_lock

BUDGET_PATH = DATA_DIR / 'deepseek-test-budget.json'
_request_sequence = ContextVar('model_request_sequence', default=(None, 0))


def uses_deepseek(base_url: str, provider: str) -> bool:
    try:
        return provider.lower() == 'deepseek' or urlsplit(base_url.strip()).hostname == 'api.deepseek.com'
    except ValueError:
        return provider.lower() == 'deepseek'


def reserve_budget(path: Path, amount_fen: int = 10) -> int:
    """Serialize ledger replacement with an OS lock released on process exit.

    Upgrade with all old workers stopped: old versions only honor an O_EXCL
    ``.lock`` file and cannot safely run alongside this protocol. An existing
    legacy lock has no ownership metadata, so leave it untouched and fail
    closed. After stopping old workers, an operator can inspect the ledger
    and remove that legacy lock. The new ``.guard.lock`` file is persistent;
    its existence never indicates that a reservation is active.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with exclusive_file_lock(path.with_suffix('.guard.lock')):
            if path.with_suffix('.lock').exists():
                raise AppError('TEST_BUDGET_BUSY', '正在登记其他模型请求，请稍后再试。', status_code=409)
            try:
                reserved = json.loads(path.read_text(encoding='utf-8'))['reserved_fen'] if path.exists() else 0
                if type(reserved) is not int or reserved < 0:
                    raise ValueError('Invalid allowance')
            except (OSError, ValueError, TypeError, KeyError):
                raise AppError('TEST_BUDGET_INVALID', '模型额度记录无法读取，请先人工核对。', status_code=503) from None
            cap_fen = int(settings.model_budget_cny * 100) if settings.model_budget_enforced else None
            if cap_fen is not None and reserved + amount_fen > cap_fen:
                raise AppError('TEST_BUDGET_EXHAUSTED', f'本地模型调用额度已不足（累计上限{settings.model_budget_cny}元），已停止付费请求。', status_code=429)
            updated = reserved + amount_fen
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'reserved_fen': updated, 'cap_fen': cap_fen,
                                             'enforced': settings.model_budget_enforced}), encoding='utf-8')
            temporary.replace(path)
            return updated
    except LockBusyError:
        raise AppError('TEST_BUDGET_BUSY', '正在登记其他模型请求，请稍后再试。', status_code=409) from None


def reserve_request(payload: dict, path: Path | None = None) -> int:
    # UTF-8 bytes conservatively bound text tokens; image tokens <=1024 each
    # per the verified official Flash vision guide. Add framing headroom.
    input_bound = 2048
    for message in payload.get('messages', []):
        content = message.get('content', '')
        if isinstance(content, str):
            input_bound += len(content.encode('utf-8')) + 256
        else:
            for block in content:
                input_bound += 1024 if block.get('type') == 'image_url' else len(block.get('text', '').encode('utf-8'))
            input_bound += 256
    output_bound = payload.get('max_tokens', 4096)
    # Verified peak tariff: input 2 / output 8 CNY per million tokens.
    amount_fen = max(10, (input_bound * 2 + output_bound * 8 + 9999) // 10000)
    if settings.deployment_mode == 'cloud':
        from app.services.database_budget import reserve_database_budget
        from app.services.task_jobs import current_job
        job = current_job.get()
        previous, sequence = _request_sequence.get()
        sequence = sequence + 1 if job is not None and previous == job else 1
        _request_sequence.set((job, sequence))
        request_id = f'{job}:{sequence}' if job is not None else str(uuid4())
        return reserve_database_budget(amount_fen, request_id)
    return reserve_budget(path or BUDGET_PATH, amount_fen)


def budget_status() -> dict:
    if settings.deployment_mode == 'cloud':
        from app.services.database_budget import database_budget_status
        return database_budget_status()
    enforced = settings.model_budget_enforced
    cap = float(settings.model_budget_cny) if enforced else None
    try:
        reserved = json.loads(BUDGET_PATH.read_text(encoding='utf-8'))['reserved_fen'] if BUDGET_PATH.exists() else 0
        if type(reserved) is not int or reserved < 0:
            raise ValueError('Invalid allowance')
        return {'enforced': enforced, 'cap_cny': cap, 'reserved_cny': reserved / 100,
                'remaining_cny': max(0, cap - reserved / 100) if enforced else None,
                'available': not enforced or reserved + 10 <= int(settings.model_budget_cny * 100)}
    except (OSError, ValueError, TypeError, KeyError):
        return {'enforced': enforced, 'cap_cny': cap, 'available': False, 'error': '用量记录需要人工核对'}
