from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import ModelBudget
from app.services.process_lock import LockBusyError


def test_mysql_lock_excludes_other_containers_without_sharing_a_disk(mysql_engine):
    from app.services.coordination import coordination_lock
    def contender():
        with Session(mysql_engine) as db:
            with pytest.raises(LockBusyError):
                with coordination_lock(db, 'same-resource', Path('/different/container/path.lock')):
                    pytest.fail('Another container bypassed the database lock')
    with Session(mysql_engine) as db:
        with coordination_lock(db, 'same-resource', Path('/first/container/path.lock')) as first:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(contender).result(10)
            generation = first.generation
    with Session(mysql_engine) as db:
        with coordination_lock(db, 'same-resource', Path('/third/path.lock')) as second:
            assert second.generation > generation


@pytest.mark.parametrize('mutation', ['orm', 'statement'])
def test_lost_owner_cannot_write_after_takeover(mysql_engine, mutation):
    from app.services.coordination import coordination_lock
    with Session(mysql_engine) as db:
        db.add(ModelBudget(name='fixture', reserved_fen=0))
        db.commit()
    def takeover():
        deadline = time.monotonic() + 5
        while True:
            try:
                with Session(mysql_engine) as db:
                    with coordination_lock(db, 'fenced-resource', Path('/other.lock')):
                        db.get(ModelBudget, 'fixture').reserved_fen = 2
                        db.commit()
                return
            except LockBusyError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
    with Session(mysql_engine) as old:
        with coordination_lock(old, 'fenced-resource', Path('/old.lock')) as lease:
            record = old.get(ModelBudget, 'fixture')
            lease.connection.invalidate()  # Simulate only the coordination TCP session dying.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(takeover).result(10)
            with pytest.raises(AppError) as error:
                if mutation == 'orm':
                    record.reserved_fen = 999
                    old.commit()
                else:
                    old.execute(update(ModelBudget).where(ModelBudget.name == 'fixture').values(reserved_fen=999))
                    old.commit()
            assert error.value.code == 'COORDINATION_LOST'
            old.rollback()
    with Session(mysql_engine) as db:
        assert db.get(ModelBudget, 'fixture').reserved_fen == 2
