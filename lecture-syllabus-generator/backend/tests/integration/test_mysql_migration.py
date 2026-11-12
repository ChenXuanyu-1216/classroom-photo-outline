from test_cloud_migration import source
from test_cloud_uploads import cloud_store


def test_real_mysql_migration_and_restore_verify_all_saved_fields(mysql_engine, source, cloud_store):
    from app.cloud_migration import inventory, import_data, verify_import
    path, root = source
    fingerprint = inventory(path, root)['fingerprint']
    import_data(path, root, mysql_engine, expected=fingerprint, allow_missing=True)
    assert verify_import(path, root, mysql_engine, expected=fingerprint)['verified_objects'] == 1


def test_real_mysql_migrates_long_saved_knowledge_without_truncation(mysql_engine, source, cloud_store):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models import Page, OutlineNode, KnowledgePoint
    from app.cloud_migration import inventory, import_data, verify_import
    path, root = source
    content = '🧠' * 20000
    original = create_engine('sqlite:///' + path.as_posix())
    with Session(original) as db:
        db.get(Page, 31).recognized_text = content
        db.add(KnowledgePoint(id=61, task_id=23, page_id=31, content=content, dedup_key=content, source_pages='P1'))
        db.add(OutlineNode(id=52, outline_id=41, parent_id=51, level=2, title=content, content=content))
        db.commit()
    original.dispose()
    fingerprint = inventory(path, root)['fingerprint']
    import_data(path, root, mysql_engine, expected=fingerprint, allow_missing=True)
    assert verify_import(path, root, mysql_engine, expected=fingerprint)['verified_objects'] == 1
