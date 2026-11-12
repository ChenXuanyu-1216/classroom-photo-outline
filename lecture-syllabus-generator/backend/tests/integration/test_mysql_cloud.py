"""Real MySQL contract tests. Never obtain a URL from the app's production config."""
import os
from sqlalchemy import text
from sqlalchemy.orm import Session


def test_real_mysql_schema_round_trips_identity_emoji_and_json(mysql_engine):
    from app.models import AppUser, Task, Page, Outline
    from app.schema_migrations import check_schema, migrate_schema
    with Session(mysql_engine) as db:
        first = AppUser(provider='wechat', app_id='wx-test', subject='CaseSensitive')
        second = AppUser(provider='wechat', app_id='wx-test', subject='casesensitive')
        db.add_all([first, second])
        db.flush()
        task = Task(owner_id=first.id, title='课堂 📚', status='pending', page_count=1)
        db.add(task)
        db.flush()
        db.add(Page(task_id=task.id, page_no=1, filename='课堂.png',
                    file_path='cos://fixture-bucket/immutable/page.png',
                    recognized_structured={'文本': ['示例', '📚']}))
        db.add(Outline(task_id=task.id, markdown='# 同一份资料 📚'))
        db.commit()
        identity, identifier = first.id, task.id
    migrate_schema(mysql_engine)
    check_schema(mysql_engine)
    with Session(mysql_engine) as db:
        assert db.get(Task, identifier).owner_id == identity
        assert db.query(Page).one().recognized_structured == {'文本': ['示例', '📚']}
        assert db.query(Outline).one().markdown == '# 同一份资料 📚'
        assert db.query(AppUser).count() == 2
    with mysql_engine.connect() as db:
        assert db.scalar(text('SELECT COUNT(*) FROM schema_revision')) == 1
        expected_case = os.environ.get('APP_TEST_MYSQL_TABLE_CASE')
        if expected_case is not None:
            assert db.scalar(text('SELECT @@lower_case_table_names')) == int(expected_case)
            if expected_case == '1':
                assert db.scalar(text('SELECT COUNT(*) FROM APP_USER')) == 2


def test_real_mysql_preserves_large_outline_without_truncation(mysql_engine):
    from app.models import AppUser, Task, Outline
    markdown = '# 大纲\n' + '知识点与来源。' * 8000
    with Session(mysql_engine) as db:
        user = AppUser(provider='wechat', app_id='wx-test', subject='large-outline')
        db.add(user)
        db.flush()
        task = Task(owner_id=user.id, title='长课堂', page_count=0)
        db.add(task)
        db.flush()
        db.add(Outline(task_id=task.id, markdown=markdown))
        db.commit()
    with Session(mysql_engine) as db:
        assert db.query(Outline).one().markdown == markdown


def test_real_mysql_preserves_full_valid_knowledge_and_node_text(mysql_engine):
    from app.models import AppUser, Task, Page, Outline, OutlineNode, KnowledgePoint
    from app.services.model_output import RecognizedPoint
    from app.services.pipeline import Pipeline
    content = '🧠' * 20000
    point = RecognizedPoint.model_validate({'content': content, 'kp_type': 'concept', 'source_type': '课堂原话'})
    with Session(mysql_engine) as db:
        user = AppUser(provider='wechat', app_id='wx-test', subject='long-knowledge')
        db.add(user)
        db.flush()
        task = Task(owner_id=user.id, title='长知识点', page_count=1)
        db.add(task)
        db.flush()
        page = Page(task_id=task.id, page_no=1, filename='page.png',
                    file_path='cos://fixture/kezhao/page.png', recognized_text=point.content,
                    recognized_structured={'title': '长知识点', 'points': [point.model_dump()]})
        db.add(page)
        db.commit()
        Pipeline().run_pipeline(db, task.id)
    with Session(mysql_engine) as db:
        assert db.query(Page).one().recognized_text == content
        assert db.query(KnowledgePoint).one().dedup_key == content
        assert db.query(KnowledgePoint).one().content == content
        child = db.query(OutlineNode).filter_by(level=2).one()
        assert child.title == content
        assert child.content == content


def test_real_mysql_regeneration_replaces_parent_child_tree(mysql_engine):
    from app.models import AppUser, Task, Page, Outline, OutlineNode
    from app.services.pipeline import Pipeline
    with Session(mysql_engine) as db:
        user = AppUser(provider='wechat', app_id='wx-test', subject='regeneration')
        db.add(user)
        db.flush()
        task = Task(owner_id=user.id, title='重新生成', page_count=1)
        db.add(task)
        db.flush()
        page = Page(task_id=task.id, page_no=1, filename='page.png', file_path='fixture',
                    recognized_text='原知识点', recognized_structured={'title': '课堂', 'points': [
                        {'content': '原知识点', 'kp_type': 'concept', 'source_type': '课堂原话'}]})
        db.add(page)
        db.commit()
        identifier = task.id
        Pipeline().run_pipeline(db, identifier)
        assert db.query(OutlineNode).filter(OutlineNode.parent_id.is_not(None)).count() == 1
    with Session(mysql_engine) as db:
        page = db.query(Page).filter_by(task_id=identifier).one()
        page.recognized_text = '更新的知识点'
        page.recognized_structured = {'title': '课堂', 'points': [
            {'content': '更新的知识点', 'kp_type': 'concept', 'source_type': '课堂原话'}]}
        db.commit()
        Pipeline().run_pipeline(db, identifier)
        assert db.query(Outline).filter_by(task_id=identifier).count() == 1
        assert '更新的知识点' in db.query(Outline).one().markdown
        assert db.query(OutlineNode).filter_by(level=2).one().title == '更新的知识点'
