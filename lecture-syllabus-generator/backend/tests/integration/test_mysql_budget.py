from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import time

from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.errors import AppError
from app.models import ModelBudget, ModelReservation


def test_mysql_concurrent_reservations_share_one_cap(mysql_engine, monkeypatch):
    from app.services import database_budget
    monkeypatch.setattr(database_budget, 'SessionLocal', sessionmaker(bind=mysql_engine))
    monkeypatch.setattr(settings, 'model_budget_enforced', True)
    monkeypatch.setattr(settings, 'model_budget_cny', Decimal('0.20'))
    database_budget.initialize_budget(0)
    def reserve(index):
        name = f'fixture-{index}'
        deadline = time.monotonic() + 5
        while True:
            try:
                database_budget.reserve_database_budget(10, name)
                return name
            except AppError as error:
                if error.code == 'TEST_BUDGET_EXHAUSTED':
                    return None
                if error.code != 'TEST_BUDGET_BUSY' or time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = [name for name in pool.map(reserve, range(4)) if name is not None]
    assert len(accepted) == 2
    assert database_budget.reserve_database_budget(10, accepted[0]) == 20
    with Session(mysql_engine) as db:
        assert db.get(ModelBudget, 'deepseek').reserved_fen == 20
        assert db.query(ModelReservation).count() == 2
