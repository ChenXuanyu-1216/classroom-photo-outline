"""Explicit offline migration. Reports contain counts/digests, never login secrets.

Stop source writes and target API/workers before importing. This module never
loads a production source implicitly and never copies sessions or pending jobs.
"""
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from sqlalchemy import DateTime, Boolean, select, func
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import Page, Task, StoredObject, ObjectCleanup
from app.services import object_storage
from app.services.cloud_objects import storage_lock

CORE_TABLES = ('app_user', 'task', 'page', 'knowledge_point', 'outline', 'outline_node', 'export_log')
EVALUATION_TABLES = ('evaluation_snapshot', 'evaluation_review')
TABLES = CORE_TABLES + EVALUATION_TABLES


def _connect(path):
    path = Path(path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def _image(path, root):
    candidate = Path(path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    if not 0 < candidate.stat().st_size <= object_storage.MAX_READ:
        raise ValueError('Source object exceeds the bounded migration size')
    data = candidate.read_bytes()
    if len(data) > object_storage.MAX_READ:
        raise ValueError('Source object changed during inventory')
    return {'path': candidate, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def _snapshot(path, storage):
    root = Path(storage).resolve(strict=True)
    with closing(_connect(path)) as connection:
        connection.execute('BEGIN')
        available = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(CORE_TABLES) <= available:
            raise ValueError('Source is missing the supported application schema')
        if set(EVALUATION_TABLES) & available and not set(EVALUATION_TABLES) <= available:
            raise ValueError('Source evaluation schema is incomplete')
        rows = {name: [dict(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY id')]
                if name in available else [] for name in TABLES}
    files, missing = {}, []
    for page in rows['page']:
        found = _image(page['file_path'], root)
        if found is None:
            missing.append(page['id'])
        else:
            files[page['id']] = found
    digest = {'rows': rows, 'files': {str(key): {k: v for k, v in item.items() if k != 'path'}
                                    for key, item in files.items()}, 'missing': missing}
    fingerprint = hashlib.sha256(json.dumps(digest, sort_keys=True, ensure_ascii=False,
                                             separators=(',', ':')).encode()).hexdigest()
    return rows, files, {'fingerprint': fingerprint, 'counts': {key: len(value) for key, value in rows.items()},
                         'source_bytes': sum(item['size'] for item in files.values()), 'missing_page_ids': missing}


def inventory(source, storage):
    return _snapshot(source, storage)[2]


def backup_source(source, destination):
    destination = Path(destination)
    with destination.open('xb'):
        pass
    try:
        with closing(_connect(source)) as read:
            with closing(sqlite3.connect(destination)) as write:
                read.backup(write)
                if write.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Backup integrity check failed')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _values(name, row):
    table = Base.metadata.tables[name]
    result = {key: value for key, value in row.items() if key in table.c}
    for column in table.c:
        value = result.get(column.name)
        if value is not None and isinstance(column.type, DateTime) and isinstance(value, str):
            result[column.name] = datetime.fromisoformat(value)
        elif value is not None and isinstance(column.type, Boolean):
            result[column.name] = bool(value)
        elif value is not None and column.type.__class__.__name__ == 'JSON' and isinstance(value, str):
            result[column.name] = json.loads(value)
    if name == 'task' and result.get('status') in {'running', 'queued', 'deleting'}:
        result.update(status='failed', error_msg='迁移前任务未完成，已有资料已保留；请检查后手动重新提交。')
    return result


def _ordered_nodes(rows):
    pending = {row['id']: row for row in rows}
    ordered, emitted = [], set()
    while pending:
        ready = [row for row in pending.values() if row.get('parent_id') is None or row['parent_id'] in emitted]
        if not ready:
            raise ValueError('Invalid outline parent references')
        for row in ready:
            ordered.append(row)
            emitted.add(row['id'])
            del pending[row['id']]
    return ordered


def _empty(db):
    for name, table in Base.metadata.tables.items():
        if name not in {'schema_revision', 'coordination_fence', 'model_budget', 'model_reservation', 'runtime_probe'}:
            if db.scalar(select(func.count()).select_from(table)):
                raise ValueError('Target business tables must be empty; no records were overwritten')


def _checked_snapshot(source, storage, expected):
    rows, files, report = _snapshot(source, storage)
    if not expected or report['fingerprint'] != expected:
        raise ValueError('Source changed; review a fresh inventory before import')
    return rows, files, report


def import_data(source, storage, target, *, expected, allow_missing=False):
    rows, files, report = _checked_snapshot(source, storage, expected)
    if report['missing_page_ids'] and not allow_missing:
        raise ValueError('Original images are missing; review report and explicitly allow missing originals')
    if report['source_bytes'] > settings.cloud_max_total_bytes or len(rows['task']) > settings.cloud_max_total_tasks:
        raise ValueError('Migration exceeds configured cloud quota')
    tasks = {row['id']: row for row in rows['task']}
    for owner in {row['owner_id'] for row in rows['task']}:
        owned = {key for key, row in tasks.items() if row['owner_id'] == owner}
        size = sum(item['size'] for key, item in files.items()
                   if next(page for page in rows['page'] if page['id'] == key)['task_id'] in owned)
        if size > settings.cloud_max_user_bytes or len(owned) > settings.cloud_max_user_tasks:
            raise ValueError('Migration exceeds configured per-user quota')
    store = object_storage.get_storage()
    if not isinstance(store, object_storage.CosStorage):
        raise ValueError('Cloud import requires private COS storage')
    nonce, refs = uuid4().hex, {}
    with Session(target) as db:
        with storage_lock(db):
            _empty(db)
            for page in rows['page']:
                key = f'kezhao/migration/{nonce}/{page["id"]}' + Path(page['filename']).suffix.lower()
                refs[page['id']] = store.reference(key)
                if page['id'] in files:
                    item = files[page['id']]
                    db.add(StoredObject(object_ref=refs[page['id']], size=item['size'], kind='migration'))
                    db.add(ObjectCleanup(object_ref=refs[page['id']], not_before=datetime.utcnow() + timedelta(hours=24)))
            db.commit()  # Failures leave durable cleanup records, never untracked uploads.
            try:
                for page in rows['page']:
                    item = files.get(page['id'])
                    if item is None:
                        continue
                    data = item['path'].read_bytes()
                    if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                        raise ValueError('Source changed during private copy')
                    store.put(refs[page['id']].split('/', 3)[3], data)
                    if hashlib.sha256(store.read(refs[page['id']], limit=object_storage.MAX_READ)).hexdigest() != item['sha256']:
                        raise ValueError('Private object verification failed')
                for name in TABLES:
                    selected = _ordered_nodes(rows[name]) if name == 'outline_node' else rows[name]
                    for original in selected:
                        values = _values(name, original)
                        if name == 'page':
                            values['file_path'] = refs[original['id']]
                            if original['id'] not in files:
                                values.update(failed_flag=True, error_code='SOURCE_MISSING',
                                              error_retryable=False, error_msg='原图已缺失，已保留识别文字和大纲。')
                        db.execute(Base.metadata.tables[name].insert().values(**values))
                for page in rows['page']:
                    if page['id'] in files:
                        row = db.get(StoredObject, refs[page['id']])
                        row.owner_id, row.task_id = tasks[page['task_id']]['owner_id'], page['task_id']
                        db.delete(db.get(ObjectCleanup, row.object_ref))
                db.commit()
            except BaseException:
                db.rollback()
                # A lost commit acknowledgement must never delete committed originals.
                with Session(target) as check:
                    persisted = {row.id: row.file_path for row in check.query(Page).all()}
                if not refs or persisted != refs:
                    raise
    return report


def verify_import(source, storage, target, *, expected):
    rows, files, report = _checked_snapshot(source, storage, expected)
    store = object_storage.get_storage()
    with Session(target) as db:
        for name in TABLES:
            table = Base.metadata.tables[name]
            found = {row['id']: dict(row) for row in db.execute(select(table)).mappings()}
            if set(found) != {row['id'] for row in rows[name]}:
                raise ValueError('Target row identities do not match the snapshot')
            for row in rows[name]:
                expected_row = _values(name, row)
                ignored = {'file_path', 'failed_flag', 'error_code', 'error_retryable', 'error_msg'} if name == 'page' and row['id'] not in files else {'file_path'} if name == 'page' else set()
                if any(found[row['id']][key] != value for key, value in expected_row.items() if key not in ignored):
                    raise ValueError('Target contents do not match the snapshot')
        for page in db.query(Page):
            if page.id in files:
                content = store.read(page.file_path, limit=object_storage.MAX_READ)
                if hashlib.sha256(content).hexdigest() != files[page.id]['sha256']:
                    raise ValueError('Target private object checksum mismatch')
    return {'fingerprint': report['fingerprint'], 'verified_objects': len(files),
            'missing_page_ids': report['missing_page_ids']}
