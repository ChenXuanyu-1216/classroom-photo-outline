import threading

from app.db import engine, SessionLocal


def test_probe_persists_heartbeat_without_model_or_queue_work():
    from app.runtime_probe import run_probe, probe_status
    from app.models import TaskJob, ModelReservation
    elapsed = [0]
    def wait(seconds):
        elapsed[0] += seconds
        return False
    identifier = run_probe(engine, seconds=3, interval=1, clock=lambda: elapsed[0], wait=wait)
    status = probe_status(engine, identifier)
    assert status['completed'] and status['ticks'] == 3 and status['max_gap_seconds'] == 1
    with SessionLocal() as db:
        assert db.query(TaskJob).count() == 0 and db.query(ModelReservation).count() == 0


def test_interrupted_probe_is_not_reported_complete():
    from app.runtime_probe import run_probe, probe_status
    identifier = run_probe(engine, seconds=10, interval=1, wait=lambda _: True)
    assert probe_status(engine, identifier)['completed'] is False
