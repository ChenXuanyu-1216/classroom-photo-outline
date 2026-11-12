"""Local operator commands. No HTTP admin endpoint or implicit legacy claiming."""
import argparse
from pathlib import Path

from app.config import DATA_DIR, settings
from app.db import SessionLocal, init_db
from app.models import AppUser, Task
from app.services.auth_service import issue_session


def claim_unassigned(db, user_id: int, task_ids=None) -> int:
    if db.get(AppUser, user_id) is None:
        raise ValueError('Target user does not exist.')
    if task_ids is None:
        task_ids = [row[0] for row in db.query(Task.id).filter(Task.owner_id.is_(None)).all()]
    task_ids = list(set(task_ids))
    if not task_ids:
        return 0
    if any(not isinstance(value, int) or value < 1 for value in task_ids):
        raise ValueError('Invalid task IDs.')
    available = db.query(Task).filter(Task.id.in_(task_ids), Task.owner_id.is_(None)).count()
    if available != len(task_ids):
        raise ValueError('Task missing or already owned; no ownership changed.')
    changed = db.query(Task).filter(Task.id.in_(task_ids), Task.owner_id.is_(None)).update({Task.owner_id: user_id}, synchronize_session=False)
    if changed != len(task_ids):
        db.rollback()
        raise ValueError('Ownership changed concurrently; no tasks claimed.')
    db.commit()
    return changed


def create_local_session(db, name: str, token_file: Path):
    path = token_file.resolve()
    if not path.is_relative_to(DATA_DIR.resolve()):
        raise ValueError('Token file must be inside the backend data directory, which is never served over HTTP.')
    if not name or len(name) > 128:
        raise ValueError('Name must contain 1-128 characters.')
    user = db.query(AppUser).filter_by(provider='local', app_id='desktop', subject=name).first()
    if user is None:
        user = AppUser(provider='local', app_id='desktop', subject=name)
        db.add(user)
        db.flush()
    token = issue_session(db, user)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create avoids overwriting a still-needed credential.
    with path.open('x', encoding='utf-8') as output:
        output.write(token + '\n')
    try:
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return user.id, path


def main():
    parser = argparse.ArgumentParser(description='Local authentication management; never prints keys or openids.')
    commands = parser.add_subparsers(dest='command', required=True)
    session = commands.add_parser('create-local-session')
    session.add_argument('--name', default='desktop')
    session.add_argument('--token-file', required=True, type=Path)
    commands.add_parser('list-users')
    commands.add_parser('migrate-cloud')
    commands.add_parser('check-private-bucket')
    probe = commands.add_parser('probe-status')
    probe.add_argument('--id', required=True)
    budget = commands.add_parser('initialize-cloud-budget')
    budget.add_argument('--reserved-fen', type=int, required=True)
    for operation in ('cloud-inventory', 'cloud-import', 'cloud-verify', 'backup-local'):
        command = commands.add_parser(operation)
        command.add_argument('--source', required=True, type=Path)
        if operation == 'backup-local':
            command.add_argument('--output', required=True, type=Path)
        else:
            command.add_argument('--source-storage', required=True, type=Path)
        if operation in {'cloud-import', 'cloud-verify'}:
            command.add_argument('--expected-fingerprint', required=True)
        if operation == 'cloud-import':
            command.add_argument('--allow-missing-originals', action='store_true')
    claim = commands.add_parser('claim-legacy')
    claim.add_argument('--user-id', required=True, type=int)
    selection = claim.add_mutually_exclusive_group(required=True)
    selection.add_argument('--task-ids', nargs='+', type=int)
    selection.add_argument('--all-unassigned', action='store_true')
    arguments = parser.parse_args()
    if arguments.command in {'cloud-inventory', 'cloud-import', 'cloud-verify', 'backup-local'}:
        import json
        from app.cloud_migration import inventory, import_data, verify_import, backup_source
        if arguments.command == 'backup-local':
            backup_source(arguments.source, arguments.output)
            print('Verified SQLite backup created. Store its originals separately and protect both.')
        elif arguments.command == 'cloud-inventory':
            print(json.dumps(inventory(arguments.source, arguments.source_storage), ensure_ascii=False))
        else:
            from app.db import engine
            from app.schema_migrations import check_schema
            if settings.deployment_mode != 'cloud' or engine.dialect.name != 'mysql':
                raise ValueError('Explicit cloud MySQL configuration is required')
            check_schema(engine)
            if arguments.command == 'cloud-import':
                result = import_data(arguments.source, arguments.source_storage, engine,
                    expected=arguments.expected_fingerprint, allow_missing=arguments.allow_missing_originals)
            else:
                result = verify_import(arguments.source, arguments.source_storage, engine,
                                       expected=arguments.expected_fingerprint)
            print(json.dumps(result, ensure_ascii=False))
        return
    if arguments.command == 'migrate-cloud':
        from app.db import engine
        from app.schema_migrations import migrate_schema
        if engine.dialect.name != 'mysql':
            raise ValueError('migrate-cloud requires an explicitly configured MySQL database')
        migrate_schema(engine)
        print('Cloud schema is ready. No legacy data was imported.')
        return
    if settings.deployment_mode == 'cloud':
        from app.db import engine
        from app.schema_migrations import check_schema
        check_schema(engine)
    else:
        init_db()
    if arguments.command == 'initialize-cloud-budget':
        from app.services.database_budget import initialize_budget
        if settings.deployment_mode != 'cloud':
            raise ValueError('initialize-cloud-budget requires explicit cloud configuration')
        initialize_budget(arguments.reserved_fen)
        print('Shared budget initialized without overwriting an existing ledger.')
        return
    if arguments.command == 'check-private-bucket':
        from app.services.object_storage import get_storage, CosStorage
        from app.services.bucket_privacy import check_bucket_privacy
        import json
        store = get_storage()
        if not isinstance(store, CosStorage):
            raise ValueError('Configure COS explicitly before inspecting bucket privacy')
        print(json.dumps(check_bucket_privacy(store.client, store.bucket)))
        return
    if arguments.command == 'probe-status':
        from app.db import engine
        from app.runtime_probe import probe_status
        import json
        print(json.dumps(probe_status(engine, arguments.id)))
        return
    with SessionLocal() as db:
        if arguments.command == 'create-local-session':
            user_id, path = create_local_session(db, arguments.name, arguments.token_file)
            print(f'Local user {user_id}; session saved only to {path}. No legacy tasks were claimed.')
        elif arguments.command == 'list-users':
            for user in db.query(AppUser).order_by(AppUser.id):
                count = db.query(Task).filter_by(owner_id=user.id).count()
                print(f'User {user.id}; provider={user.provider}; tasks={count}; created={user.created_at}')
            print('Unassigned tasks:', db.query(Task).filter(Task.owner_id.is_(None)).count())
        else:
            changed = claim_unassigned(db, arguments.user_id, None if arguments.all_unassigned else arguments.task_ids)
            print(f'Assigned {changed} previously unassigned tasks to user {arguments.user_id}.')


if __name__ == '__main__':
    main()
