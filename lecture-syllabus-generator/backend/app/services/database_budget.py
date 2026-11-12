"""Shared conservative reservations, not provider billing or automatic refunds."""
import hashlib

from sqlalchemy.exc import SQLAlchemyError

from app.config import DATA_DIR, settings
from app.db import SessionLocal
from app.errors import AppError
from app.models import ModelBudget, ModelReservation
from app.services.coordination import coordination_lock
from app.services.process_lock import LockBusyError

NAME = 'deepseek'


def _invalid():
    return AppError('TEST_BUDGET_INVALID', '共享模型额度记录无法读取，请先核对。', status_code=503)


def _amount(value):
    if type(value) is not int or not 0 <= value <= 2 ** 53 - 1:
        raise _invalid()
    return value


def initialize_budget(reserved_fen):
    _amount(reserved_fen)
    try:
        with SessionLocal() as db:
            with coordination_lock(db, 'model-budget:' + NAME, DATA_DIR / '.shared-model-budget.lock'):
                row = db.get(ModelBudget, NAME)
                if row is not None and row.reserved_fen != reserved_fen:
                    raise AppError('TEST_BUDGET_CONFLICT', '现有额度账本不能被初始化操作覆盖。', status_code=409)
                if row is None:
                    db.add(ModelBudget(name=NAME, reserved_fen=reserved_fen))
                    db.commit()
    except LockBusyError:
        raise AppError('TEST_BUDGET_BUSY', '正在登记其他模型请求，请稍后再试。', status_code=409) from None
    except SQLAlchemyError:
        raise _invalid() from None


def reserve_database_budget(amount_fen, request_id):
    if _amount(amount_fen) == 0 or not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
        raise _invalid()
    identifier = hashlib.sha256(request_id.encode()).hexdigest()
    try:
        with SessionLocal() as db:
            with coordination_lock(db, 'model-budget:' + NAME, DATA_DIR / '.shared-model-budget.lock'):
                account = db.query(ModelBudget).filter_by(name=NAME).with_for_update().first()
                if account is None:
                    raise AppError('TEST_BUDGET_UNINITIALIZED', '共享模型额度尚未初始化，请先核对迁移账本。', status_code=503)
                total = _amount(account.reserved_fen)
                previous = db.get(ModelReservation, identifier)
                if previous is not None:
                    if previous.amount_fen != amount_fen:
                        raise AppError('TEST_BUDGET_CONFLICT', '此模型请求的额度记录不一致。', status_code=409)
                    return total
                if settings.model_budget_enforced and total + amount_fen > int(settings.model_budget_cny * 100):
                    raise AppError('TEST_BUDGET_EXHAUSTED', '模型调用额度已不足，已停止付费请求。', status_code=429)
                account.reserved_fen = total + amount_fen
                db.add(ModelReservation(id=identifier, budget_name=NAME, amount_fen=amount_fen))
                db.commit()
                return total + amount_fen
    except LockBusyError:
        raise AppError('TEST_BUDGET_BUSY', '正在登记其他模型请求，请稍后再试。', status_code=409) from None
    except SQLAlchemyError:
        raise _invalid() from None


def database_budget_status():
    enforced = settings.model_budget_enforced
    cap = float(settings.model_budget_cny) if enforced else None
    try:
        with SessionLocal() as db:
            account = db.get(ModelBudget, NAME)
            if account is None:
                raise ValueError()
            reserved = _amount(account.reserved_fen)
            return {'enforced': enforced, 'cap_cny': cap, 'reserved_cny': reserved / 100,
                    'remaining_cny': max(0, cap - reserved / 100) if enforced else None,
                    'available': not enforced or reserved + 10 <= int(settings.model_budget_cny * 100)}
    except (SQLAlchemyError, ValueError, AppError):
        return {'enforced': enforced, 'cap_cny': cap, 'available': False,
                'error': '共享模型额度需要初始化或人工核对'}
