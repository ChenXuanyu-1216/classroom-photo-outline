"""Local OS locks or MySQL session locks with transactional write fencing.

A connection check alone has a check/commit race. The fence row is locked in
the data transaction, so takeover cannot advance its generation until an older
accepted write commits; writes after takeover are rejected before executing.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import CoordinationFence
from app.services.process_lock import exclusive_file_lock, LockBusyError

_guards = ContextVar('database_write_fences', default=())


def _identity(engine):
    url = engine.url
    return (url.host, url.port or 3306, url.database)


def _lost():
    return AppError('COORDINATION_LOST', '处理所有权已失效，已有资料保留，请重新检查任务状态。',
                    retryable=True, status_code=409)


@dataclass
class Lease:
    engine: object
    name: str
    generation: int
    connection: object
    connection_id: int

    def check(self, data_connection):
        if self.connection.closed or self.connection.invalidated:
            raise _lost()
        table = CoordinationFence.__table__
        generation = data_connection.scalar(select(table.c.generation).where(
            table.c.name == self.name).with_for_update())
        if generation != self.generation:
            raise _lost()
        try:
            owner = self.connection.scalar(text('SELECT IS_USED_LOCK(:name)'), {'name': self.name})
        except Exception:
            raise _lost() from None
        if owner != self.connection_id:
            raise _lost()


def _check_fences(session):
    guards = _guards.get()
    if not guards or session.get_bind().dialect.name != 'mysql':
        return
    relevant = [guard for guard in guards if _identity(guard.engine) == _identity(session.get_bind())]
    if relevant:
        connection = session.connection()
        for guard in sorted(relevant, key=lambda item: item.name):
            guard.check(connection)


@event.listens_for(Session, 'before_flush')
def _before_flush(session, *_):
    _check_fences(session)


@event.listens_for(Session, 'before_commit')
def _before_commit(session):
    _check_fences(session)


@event.listens_for(Session, 'do_orm_execute')
def _before_statement(state):
    if state.is_insert or state.is_update or state.is_delete:
        _check_fences(state.session)


@contextmanager
def coordination_lock(db, name, local_path):
    engine = db.get_bind()
    if engine.dialect.name != 'mysql':
        with exclusive_file_lock(local_path):
            yield None
        return
    # GET_LOCK names are server-wide. Do not include credentials, container
    # paths, or host aliases, which would let two actors bypass each other.
    lock_name = 'kezhao-' + hashlib.sha256((str(engine.url.database) + ':' + name).encode()).hexdigest()[:55]
    for guard in _guards.get():
        if guard.name == lock_name and _identity(guard.engine) == _identity(engine):
            guard.check(db.connection())
            yield guard
            return
    connection = engine.connect()
    acquired = False
    token = None
    try:
        acquired = connection.scalar(text('SELECT GET_LOCK(:name, 0)'), {'name': lock_name}) == 1
        if not acquired:
            raise LockBusyError()
        connection.commit()
        with connection.begin():
            table = CoordinationFence.__table__
            previous = connection.scalar(select(table.c.generation).where(table.c.name == lock_name).with_for_update())
            generation = (previous or 0) + 1
            if previous is None:
                connection.execute(table.insert().values(name=lock_name, generation=generation))
            else:
                connection.execute(table.update().where(table.c.name == lock_name).values(generation=generation))
            identifier = connection.scalar(text('SELECT CONNECTION_ID()'))
        lease = Lease(engine, lock_name, generation, connection, identifier)
        token = _guards.set(_guards.get() + (lease,))
        yield lease
    finally:
        if token is not None:
            _guards.reset(token)
        if acquired and not connection.closed and not connection.invalidated:
            try:
                connection.execute(text('SELECT RELEASE_LOCK(:name)'), {'name': lock_name})
                connection.commit()
            except Exception:
                connection.invalidate()
        connection.close()
