"""One upload-to-Word check on new images. No repeats; no calls without --live."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import time

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(BACKEND))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', nargs='+', type=Path, help='JPG/PNG in upload order')
    parser.add_argument('--budget-ledger', required=True, type=Path, help='Existing ledger; never reset')
    parser.add_argument('--cap-cny', type=Decimal, default=Decimal('3.00'))
    parser.add_argument('--output', required=True, type=Path, help='New result directory')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--resume-from', type=Path, help='Resume a failed run, reusing its successful pages')
    args = parser.parse_args()

    from app import config
    from app.config import settings
    from app.services import model_budget
    from app.services.image_preprocessor import prepare_image

    ledger = args.budget_ledger.resolve(strict=True)
    previous = json.loads(ledger.read_text(encoding='utf-8'))
    if not args.cap_cny.is_finite() or args.cap_cny <= 0 or args.cap_cny > Decimal('3.00'):
        parser.error('This V1 check permits a cumulative ceiling of at most CNY 3.')
    cap_fen = int(args.cap_cny * 100)
    if previous.get('cap_fen') is not None and cap_fen > previous['cap_fen']:
        parser.error('Do not increase the existing allowance.')
    settings.model_budget_enforced = True
    settings.model_budget_cny = args.cap_cny
    model_budget.BUDGET_PATH = ledger
    if not model_budget.budget_status()['available']:
        parser.error('Existing allowance is unavailable; no request sent.')
    if settings.model_provider != 'deepseek' or settings.model_name != 'deepseek-flash':
        parser.error('Configure the real DeepSeek model first; mock results cannot pass V1.')
    if settings.model_base_url.rstrip('/') not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}:
        parser.error('Only the configured official DeepSeek endpoint is supported.')
    if not settings.model_api_key.strip() or not settings.knowledge_expansion_enabled:
        parser.error('A local API key and knowledge expansion are required.')
    if not 1 <= len(args.images) <= min(5, settings.max_files_per_batch):
        parser.error('Use 1–5 new images for this small check.')
    images = [p.resolve(strict=True) for p in args.images]
    for path in images:
        prepare_image(str(path))
    output = args.output.resolve()
    if output.exists():
        parser.error('Use a new result directory; previous results must not be overwritten.')
    inputs = [{'page': f'P{i}', 'file': p.name, 'sha256': sha256(p.read_bytes()).hexdigest()}
              for i, p in enumerate(images, 1)]
    prior = None
    if args.resume_from:
        prior = json.loads((args.resume_from / 'report.json').read_text(encoding='utf-8'))
        if prior['inputs'] != inputs or prior['status'] != 'failed' or not prior.get('task_id'):
            parser.error('Resume requires the identical ordered inputs and a failed saved task.')
    print(json.dumps({'status': 'ready', 'pages': len(images),
                      'budget': model_budget.budget_status(), 'live': args.live}, ensure_ascii=False), flush=True)
    if not args.live:
        return 0

    import httpx
    from docx import Document
    from fastapi.testclient import TestClient

    output.mkdir(parents=True)
    if prior:
        import sqlite3
        source_path = (args.resume_from / 'app.db').resolve(strict=True)
        with sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(output / 'app.db') as destination:
                source.backup(destination)
    config.DATA_DIR = output / 'data'
    config.STORAGE_DIR = output / 'storage'
    settings.database_url = 'sqlite:///' + (output / 'app.db').as_posix()
    # Only the test process is affected. A failed call is recorded, not auto-repeated.
    settings.model_max_retry = 0
    from app.main import app
    from app.db import SessionLocal, engine
    from app.models import Page, KnowledgePoint
    assert Path(engine.url.database).resolve() == output / 'app.db'
    assert model_budget.BUDGET_PATH == ledger

    started = time.monotonic()
    report = {'status': 'started', 'started_at': datetime.now(timezone.utc).isoformat(),
              'budget_before': model_budget.budget_status(), 'retries': 0,
              'inputs': inputs,
              'requests': [], 'content_review': 'pending_source_comparison'}
    if prior:
        report['resumed_from'] = str(args.resume_from.resolve())

    def save_report():
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def balance():
        response = httpx.get(settings.model_base_url.rstrip('/') + '/user/balance',
                            headers={'Authorization': 'Bearer ' + settings.model_api_key.strip()}, timeout=20,
                            trust_env=settings.model_trust_env)
        response.raise_for_status()
        return response.json()

    original_send = httpx.Client.send

    def recorded_send(client, request, *a, **kw):
        if request.url.host != 'api.deepseek.com' or not request.url.path.endswith('/chat/completions'):
            return original_send(client, request, *a, **kw)
        event = {'number': len(report['requests']) + 1, 'status': 'started'}
        report['requests'].append(event)
        save_report()
        print(f"Model request {event['number']} started", flush=True)
        try:
            response = original_send(client, request, *a, **kw)
            event['http_status'] = response.status_code
            event['status'] = 'returned'
            if response.is_success:
                body = response.json()
                event['usage'] = body.get('usage', {})
                event['request_id'] = body.get('id')
                # Save visible output and usage, never credentials or reasoning content.
                visible = {'id': body.get('id'), 'usage': body.get('usage'), 'choices': [
                    {'finish_reason': c.get('finish_reason'), 'content': c.get('message', {}).get('content')}
                    for c in body.get('choices', [])]}
                (output / f"response-{event['number']}.json").write_text(
                    json.dumps(visible, ensure_ascii=False, indent=2), encoding='utf-8')
            return response
        except Exception as exc:
            event.update(status='failed', error=type(exc).__name__)
            raise
        finally:
            save_report()

    def checked(response):
        if not response.is_success:
            data = response.json()
            raise RuntimeError(f"{data.get('code', response.status_code)}: {data.get('message', 'Request failed')}")
        return response

    save_report()
    httpx.Client.send = recorded_send
    try:
        report['balance_before'] = balance()
        if not report['balance_before'].get('is_available'):
            raise RuntimeError('Provider balance unavailable.')
        with TestClient(app, raise_server_exceptions=False) as client:
            if prior:
                task = checked(client.get(f"/api/tasks/{prior['task_id']}")).json()
                task['rejected'] = []
                failed_pages = checked(client.get(f"/api/tasks/{task['task_id']}/pages")).json()['pages']
                for page in failed_pages:
                    if page['failed_flag']:
                        checked(client.post(f"/api/tasks/{task['task_id']}/recognize", params={'page_no': page['page_no']}))
            else:
                task = checked(client.post('/api/tasks', files=[
                    ('files', (p.name, p.read_bytes(), 'image/png' if p.suffix.lower() == '.png' else 'image/jpeg'))
                    for p in images])).json()
            report['task_id'] = task_id = task['task_id']
            assert task['page_count'] == len(images) and not task['rejected'], 'Upload incomplete'
            if not prior:
                checked(client.post(f'/api/tasks/{task_id}/recognize'))
            page_status = checked(client.get(f'/api/tasks/{task_id}/pages')).json()['pages']
            if any(p['failed_flag'] for p in page_status):
                report['page_errors'] = page_status
                raise RuntimeError('Some pages failed; stopped before paid grouping and expansion.')
            generated = checked(client.post(f'/api/tasks/{task_id}/pipeline')).json()
            report['pipeline'] = generated
            with SessionLocal() as db:
                pages = db.query(Page).filter_by(task_id=task_id).order_by(Page.page_no).all()
                report['pages'] = [{'page': p.page_no, 'file': p.filename, 'failed': p.failed_flag,
                                    'error': p.error_msg, 'recognition': p.recognized_structured} for p in pages]
                save_report()
                assert generated['status'] == 'success' and all(not p.failed_flag for p in pages), 'Some pages failed'
                assert all(p.recognized_structured['_meta']['provider'] == 'deepseek' for p in pages)
                points = db.query(KnowledgePoint).filter_by(task_id=task_id).all()
                additions = [p for p in points if p.source_type == 'AI生成']
                report['points'] = [{'content': p.content, 'source_type': p.source_type,
                                     'source_pages': p.source_pages} for p in points]
                assert additions, 'AI expansion missing'
                valid_pages = {f'P{i}' for i in range(1, len(images) + 1)}
                assert all(p.source_pages and set(p.source_pages.split(',')) <= valid_pages for p in points)
                original_pages = {s for p in points if p.source_type != 'AI生成' for s in p.source_pages.split(',')}
                assert original_pages == valid_pages, 'A source page has no outline content'
            markdown = checked(client.get(f'/api/tasks/{task_id}/outline')).json()['markdown']
            (output / 'outline.md').write_text(markdown, encoding='utf-8')
            # Saving the returned draft exercises the editor API without rewriting model content.
            checked(client.put(f'/api/tasks/{task_id}/outline', json={'markdown': markdown}))
            assert checked(client.get(f'/api/tasks/{task_id}/outline')).json()['markdown'] == markdown
            assert any(t['task_id'] == task_id for t in checked(client.get('/api/tasks')).json()['tasks'])
            word = checked(client.get(f'/api/tasks/{task_id}/export')).content
            (output / 'outline.docx').write_bytes(word)
            paragraphs = [p.text for p in Document(io.BytesIO(word)).paragraphs]
            for point in report['points']:
                assert any(point['content'] in p for p in paragraphs), 'Word lost outline content'
            assert sum('【AI生成】' in p for p in paragraphs) == len(additions), 'Word lost AI labels'
            report.update(status='flow_passed', save_reopen_export='passed',
                          original_points=len(points) - len(additions), ai_points=len(additions))
    except Exception as exc:
        report.update(status='failed', error=str(exc) if isinstance(exc, (RuntimeError, AssertionError)) else type(exc).__name__)
    finally:
        httpx.Client.send = original_send
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        report['budget_after'] = model_budget.budget_status()
        try:
            report['balance_after'] = balance()
        except Exception as exc:
            report['balance_after_error'] = type(exc).__name__
        save_report()
        engine.dispose()
    print(json.dumps({k: v for k, v in report.items() if k not in {'pages', 'points', 'inputs', 'requests'}},
                     ensure_ascii=False, indent=2), flush=True)
    return 0 if report['status'] == 'flow_passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
