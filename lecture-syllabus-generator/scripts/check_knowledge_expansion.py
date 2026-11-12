"""Opt-in real upload-to-Word check using a synthetic definition-only slide.

Run as a separate process. The database/storage are isolated; the existing
DeepSeek allowance is shared. Default mode checks configuration without calls.
"""
import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(BACKEND))


def branch_presence(text):
    return {
        'supervised': bool(re.search(r'(?<!无)监督学习|\bsupervised learning\b', text, re.IGNORECASE)),
        'unsupervised': bool(re.search(r'无监督学习|\bunsupervised learning\b', text, re.IGNORECASE)),
        'reinforcement': bool(re.search(r'强化学习|\breinforcement learning\b', text, re.IGNORECASE)),
    }


def run(live=False):
    from app import config
    from app.config import settings
    from app.errors import AppError
    from app.services import model_budget

    if not live:
        return {'status': 'not_called', 'provider': settings.model_provider,
                'key_present': bool(settings.model_api_key.strip()),
                'expansion_enabled': settings.knowledge_expansion_enabled,
                'budget': model_budget.budget_status()}
    if settings.model_provider not in {'deepseek', 'multimodal'} or not settings.knowledge_expansion_enabled:
        raise AppError('TEST_CONFIG_ERROR', '请启用真实模型及 APP_KNOWLEDGE_EXPANSION_ENABLED。')
    if not settings.model_api_key.strip():
        raise AppError('TEST_CONFIG_ERROR', '请先配置本地模型密钥。')
    if 'app.db' in sys.modules:
        raise AppError('TEST_CONFIG_ERROR', '必须在独立进程运行，不能复用已加载的数据库。')

    # Import the budget module BEFORE redirecting config.DATA_DIR. Its existing
    # ledger remains authoritative; never start a fresh paid allowance here.
    ledger = model_budget.BUDGET_PATH
    run_dir = config.DATA_DIR / 'knowledge-expansion-check' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:8])
    run_dir.mkdir(parents=True)
    config.DATA_DIR = run_dir / 'data'
    config.STORAGE_DIR = run_dir / 'storage'
    settings.database_url = 'sqlite:///' + (run_dir / 'check.db').as_posix()
    settings.model_max_retry = 0
    assert model_budget.BUDGET_PATH == ledger

    from PIL import Image, ImageDraw, ImageFont
    from docx import Document
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal, engine
    from app.models import KnowledgePoint, Page
    from app.services.outline_service import outline_service

    assert Path(engine.url.database).resolve() == (run_dir / 'check.db').resolve()
    image = Image.new('RGB', (1600, 900), '#ffffff')
    draw = ImageDraw.Draw(image)
    font_file = Path('C:/Windows/Fonts/arial.ttf')
    title_font = ImageFont.truetype(str(font_file), 64) if font_file.exists() else ImageFont.load_default(size=64)
    body_font = ImageFont.truetype(str(font_file), 40) if font_file.exists() else ImageFont.load_default(size=40)
    draw.text((100, 110), 'Machine Learning', font=title_font, fill='#111111')
    draw.multiline_text((100, 300),
        'Machine learning is a way for computers to learn\n'
        'patterns from data and use them to make\n'
        'predictions or decisions.', font=body_font, fill='#222222', spacing=28)
    image_path = run_dir / 'definition-only.png'
    image.save(image_path)
    started = time.monotonic()
    report = {'status': 'started', 'input': 'synthetic_definition_only', 'run_directory': str(run_dir)}
    report_path = run_dir / 'report.json'

    def checked(response):
        if not response.is_success:
            data = response.json()
            raise AppError(data.get('code', 'TEST_API_ERROR'), data.get('message', '测试接口失败。'))
        return response

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            task = checked(client.post('/api/tasks', files=[
                ('files', ('definition-only.png', image_path.read_bytes(), 'image/png')),
            ])).json()['task_id']
            report['task_id'] = task
            generated = checked(client.post(f'/api/tasks/{task}/pipeline')).json()
            saved = checked(client.get(f'/api/tasks/{task}/outline')).json()
            markdown = saved['markdown']
            (run_dir / 'outline.md').write_text(markdown, encoding='utf-8')
            with SessionLocal() as db:
                originals = db.query(KnowledgePoint).filter_by(task_id=task, source_type='课堂原话').all()
                additions = db.query(KnowledgePoint).filter_by(task_id=task, source_type='AI生成').all()
                page = db.query(Page).filter_by(task_id=task).one()
                recognition_meta = page.recognized_structured.get('_meta', {})
                original_text = '\n'.join(point.content for point in originals)
                added_text = '\n'.join(point.content for point in additions)
                assert originals and additions, 'Originals or generated additions are missing'
                assert all(point.source_pages == 'P1' for point in additions)
                assert not any(term in original_text.lower() for term in ('supervised', 'reinforcement', '监督学习', '强化学习'))
                branches = branch_presence(added_text)
                assert all(branches.values()), 'Expected machine learning branches are missing'
                report.update({'original_count': len(originals), 'ai_generated_count': len(additions),
                               'branches_present': branches,
                               'recognition_request_id': recognition_meta.get('request_id')})
            parsed = outline_service.parse_markdown(markdown)
            ai_points = [point for module in parsed for point in module['points'] if point['source_type'] == 'AI生成']
            assert len(ai_points) == report['ai_generated_count']
            assert all(point['source_pages'] == 'P1' for point in ai_points)
            edited = markdown.replace('# 课程大纲', '# 测试校对大纲', 1)
            checked(client.put(f'/api/tasks/{task}/outline', json={'markdown': edited}))
            assert checked(client.get(f'/api/tasks/{task}/outline')).json()['markdown'] == edited
            word = checked(client.get(f'/api/tasks/{task}/export')).content
            (run_dir / 'outline.docx').write_bytes(word)
            paragraphs = [paragraph.text for paragraph in Document(io.BytesIO(word)).paragraphs]
            assert sum('【AI生成】' in text for text in paragraphs) == report['ai_generated_count']
            assert all(any(point['content'] in text and '（关联课堂页：P1）' in text for text in paragraphs) for point in ai_points)
            report.update({'status': 'passed', 'pipeline': generated, 'edit_save_export': 'passed',
                           'elapsed_seconds': round(time.monotonic() - started, 3),
                           'budget_after': model_budget.budget_status()})
    except (AppError, AssertionError) as exc:
        report.update({'status': 'failed', 'code': exc.code if isinstance(exc, AppError) else 'TEST_ASSERTION_FAILED',
                       'message': str(exc), 'elapsed_seconds': round(time.monotonic() - started, 3),
                       'budget_after': model_budget.budget_status()})
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        engine.dispose()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='真实调用模型；默认不发送请求')
    args = parser.parse_args()
    from app.errors import AppError
    try:
        report = run(args.live)
    except AppError as exc:
        report = {'status': 'failed', 'code': exc.code, 'message': exc.message}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
