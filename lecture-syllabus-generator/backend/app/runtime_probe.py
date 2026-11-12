"""Mock-only background CPU probe. It cannot mark paid execution as accepted."""
from datetime import datetime
import json
import time
from uuid import uuid4
from sqlalchemy.orm import Session
from app.models import RuntimeProbe


def run_probe(engine, *, seconds, interval=10, clock=time.monotonic, wait=time.sleep):
    if not 1 <= seconds <= 1800 or not 1 <= interval <= 30:
        raise ValueError('Probe duration or interval is outside its allowed range')
    identifier = str(uuid4())
    with Session(engine) as db:
        db.add(RuntimeProbe(id=identifier))
        db.commit()
    print(json.dumps({'probe_id': identifier, 'state': 'started'}), flush=True)
    started = previous = clock()
    interrupted = False
    while clock() - started < seconds:
        if wait(min(interval, seconds - (clock() - started))):
            interrupted = True
            break
        now = clock()
        with Session(engine) as db:
            row = db.get(RuntimeProbe, identifier)
            row.last_at = datetime.utcnow()
            row.ticks += 1
            row.max_gap_seconds = max(row.max_gap_seconds, now - previous)
            db.commit()
        previous = now
    if not interrupted:
        with Session(engine) as db:
            db.get(RuntimeProbe, identifier).completed = True
            db.commit()
    return identifier


def probe_status(engine, identifier):
    with Session(engine) as db:
        row = db.get(RuntimeProbe, identifier)
        if row is None:
            raise ValueError('Unknown probe ID')
        return {'probe_id': row.id, 'started_at': row.started_at.isoformat(),
                'last_at': row.last_at.isoformat(), 'ticks': row.ticks,
                'max_gap_seconds': row.max_gap_seconds, 'completed': row.completed}
