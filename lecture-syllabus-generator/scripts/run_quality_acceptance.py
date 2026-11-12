"""Opt-in classroom-photo pilot. Draft comparisons are not human accuracy scores."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

APP_ROOT = Path(__file__).resolve().parents[1]
REPO = APP_ROOT.parent
BACKEND = APP_ROOT / 'backend'
DEFAULT_DATASET = REPO / 'evaluation/classroom-photos/pilot-20261019/dataset.json'


def write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def persist_attempt_finish(path, record):
    # Interrupts bypass except Exception. An unfinished attempt must remain a
    # valid started snapshot so the summary keeps it in the denominator.
    record['finished_at'] = utc_now() if record['status'] in {'success', 'failed'} else None
    write_json(path, record)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_manifest(dataset_path: Path, authorization_path: Path | None = None, *, live=False):
    dataset_path = dataset_path.resolve()
    root = dataset_path.parent
    data = json.loads(dataset_path.read_text(encoding='utf-8'))
    authorization = json.loads(authorization_path.read_text(encoding='utf-8')) if authorization_path else {}
    if live and (authorization.get('provider') != 'deepseek'
                 or authorization.get('dataset_id') != data.get('dataset_id')
                 or authorization.get('cloud_upload_allowed') is not True):
        raise ValueError('本次真实评测需要明确的照片与 DeepSeek 云端上传授权记录。')
    pages = []
    seen = set()
    for item in data['pages']:
        image = (root / item['file_path']).resolve()
        draft = (root / item['expectation_path']).resolve()
        if not image.is_relative_to(root) or not draft.is_relative_to(root):
            raise ValueError('样本路径超出数据包范围。')
        if item['sample_id'] in seen:
            raise ValueError('重复样本编号。')
        seen.add(item['sample_id'])
        digest = file_hash(image)
        if digest != item['sha256']:
            raise ValueError('照片哈希与登记值不符。')
        if live and authorization.get('sample_sha256', {}).get(item['sample_id']) != digest:
            raise ValueError('照片未包含在本次云端上传授权范围内。')
        pages.append({**item, 'absolute_image_path': str(image), 'absolute_draft_path': str(draft),
                      'draft_sha256': file_hash(draft)})
    for group in data['task_groups']:
        ids = group['ordered_sample_ids']
        if not ids or len(ids) != len(set(ids)) or any(item not in seen for item in ids):
            raise ValueError('任务页序无效。')
    return {'dataset_id': data['dataset_id'], 'dataset_version': data['version'],
            'annotation_status': 'draft_reference_not_human_gold',
            'pages': pages, 'task_groups': data['task_groups'],
            'dataset_sha256': file_hash(dataset_path),
            'authorization_sha256': file_hash(authorization_path) if authorization_path else None}


def token_coverage(expected_points: list[dict], observed_points: list[dict]):
    # Literal, case-sensitive lexical comparison only. Inferences/extensions do
    # not fill omissions in the classroom transcription.
    original = '\n'.join(point.get('content', '') for point in observed_points
                         if point.get('source_type') == '课堂原话')
    scored = []
    for expected in expected_points:
        tokens = expected.get('critical_tokens', [])
        missing = [token for token in tokens if token not in original]
        scored.append({'point_id': expected['point_id'], 'missing_tokens': missing,
                       'status': ('unscored_no_tokens' if not tokens else
                                  'all_tokens_present' if not missing else 'missing_tokens')})
    applicable = [point for point in scored if point['status'] != 'unscored_no_tokens']
    covered = sum(point['status'] == 'all_tokens_present' for point in applicable)
    return {'metric': 'draft_literal_critical_token_coverage_not_accuracy',
            'expected_point_count': len(expected_points), 'scored_point_count': len(applicable),
            'all_critical_tokens_present_count': covered,
            'coverage': covered / len(applicable) if applicable else None, 'points': scored}


def load_cached_recognition(manifest, previous: Path, refresh_samples):
    """Reuse exact-input real transcriptions without treating them as new calls."""
    previous = previous.resolve()
    frozen = json.loads((previous / 'frozen-inputs.json').read_text(encoding='utf-8'))
    expected = {p['sample_id']: p['sha256'] for p in manifest['pages']}
    prior = {p['sample_id']: p['sha256'] for p in frozen['pages']}
    if frozen['dataset_id'] != manifest['dataset_id'] or prior != expected:
        raise ValueError('复测缓存与本次照片范围或哈希不一致。')
    if not set(refresh_samples) <= expected.keys():
        raise ValueError('待重新识别的样本不在本次数据包中。')
    cached = {}
    for group in manifest['task_groups']:
        path = previous / group['task_group_id'] / 'recognition.json'
        if not path.resolve().is_relative_to(previous):
            raise ValueError('缓存路径超出试运行目录。')
        snapshots = json.loads(path.read_text(encoding='utf-8'))['pages']
        if [p['sample_id'] for p in snapshots] != group['ordered_sample_ids']:
            raise ValueError('缓存页面顺序与本次任务不一致。')
        for page_no, snapshot in enumerate(snapshots, 1):
            structured = snapshot.get('structured') or {}
            meta = structured.get('_meta') or {}
            if (snapshot['failed'] or not snapshot['recognized_text']
                    or not any(structured.get(key) for key in ('points', 'relations', 'flows'))
                    or meta.get('provider') != 'deepseek' or snapshot['page_no'] != page_no
                    or (meta.get('input') or {}).get('sha256') != expected[snapshot['sample_id']]
                    or not isinstance(meta.get('request_id'), str) or not meta['request_id'].strip()):
                raise ValueError('不能复用失败、模拟或来源未经验证的识别。')
            if snapshot['sample_id'] not in refresh_samples:
                cached[snapshot['sample_id']] = snapshot
    return cached


def shared_budget_preflight(settings, ledger: Path, minimum_calls: int, max_requests: int):
    """Read the original allowance, failing closed if it is missing or changed."""
    data = json.loads(ledger.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or type(data.get('reserved_fen')) is not int or data['reserved_fen'] < 0:
        raise ValueError('原有用量账本无效，不能清空或创建新账本。')
    if minimum_calls > max_requests:
        raise ValueError('计划调用次数超出本次请求上限。')
    if not getattr(settings, 'model_budget_enforced', True):
        return {'enforced': False, 'cap_cny': None, 'reserved_cny': data['reserved_fen'] / 100,
                'remaining_cny': None}
    cap = Decimal(str(settings.model_budget_cny))
    if not cap.is_finite() or cap <= 0 or cap * 100 != (cap * 100).to_integral_value():
        raise ValueError('本地额度上限必须是有效金额。')
    cap_fen = int(cap * 100)
    recorded_cap = data.get('cap_fen')
    if recorded_cap is None and data.get('enforced') is False:
        recorded_cap = cap_fen  # Explicitly opting into a cap retains all prior usage.
    if (not isinstance(data, dict) or type(data.get('reserved_fen')) is not int
            or data['reserved_fen'] < 0 or type(recorded_cap) is not int
            or recorded_cap <= 0 or cap_fen > recorded_cap):
        raise ValueError('原有共享额度账本无效，不能创建新额度或增加上限。')
    remaining = cap_fen - data['reserved_fen']
    if minimum_calls > max_requests or remaining < minimum_calls * 10:
        raise ValueError('现有共享额度或请求数上限不足；未发送照片。')
    return {'cap_cny': float(cap), 'reserved_cny': data['reserved_fen'] / 100,
            'remaining_cny': remaining / 100}


def validate_uploaded_inputs(upload, pages, inputs):
    if upload.get('rejected') or upload.get('page_count') != len(inputs) or len(pages) != len(inputs):
        raise ValueError('评测批次未完整接收，已停止；不能把部分上传映射为完整样本。')
    for index, (page, supplied) in enumerate(zip(pages, inputs), 1):
        if (page.page_no != index or page.filename != Path(supplied['absolute_image_path']).name
                or file_hash(Path(page.file_path)) != supplied['sha256']):
            raise ValueError('上传后的照片顺序、文件或哈希不符，已停止缓存注入及识别。')


def recognize_selected(client, task, page_numbers):
    for page_no in page_numbers:
        response = client.post(f'/api/tasks/{task}/recognize', params={'page_no': page_no})
        if not response.is_success or response.json().get('status') not in {'pending', 'success'}:
            raise ValueError('识别调用失败；保留页级记录并停止后续模型请求。')


def planned_requests(manifest, cached_recognition, model_retries=0):
    new_pages = len(manifest['pages']) - len(cached_recognition)
    minimum = new_pages + 2 * len(manifest['task_groups'])
    return minimum, (minimum + new_pages) * (model_retries + 1)


def run_live(manifest, output_root, max_requests=None, cached_recognition=None, previous_run=None, model_retries=0):
    cached_recognition = cached_recognition or {}
    sys.path.insert(0, str(BACKEND))
    from app import config
    from app.config import settings
    from app.errors import AppError
    from app.services import model_budget
    from app.services.recognizer import recognition_is_current

    if 'app.db' in sys.modules:
        raise ValueError('真实评测必须在独立进程启动，不能复用应用数据库。')
    endpoint = settings.model_base_url.strip().rstrip('/')
    if endpoint not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'} or not settings.model_api_key.strip():
        raise ValueError('本次照片授权仅适用于已配置的 DeepSeek 官方接口。')
    # The application refreshes old recognition when users regenerate. Plan
    # those calls before starting, instead of pretending old caches avoid them.
    cached_recognition = {sample: snapshot for sample, snapshot in cached_recognition.items()
                          if recognition_is_current(snapshot.get('structured'), 'deepseek')}
    minimum_calls, maximum_calls = planned_requests(manifest, cached_recognition, model_retries)
    ledger = model_budget.BUDGET_PATH
    max_requests = maximum_calls if max_requests is None else max_requests
    budget_before = shared_budget_preflight(settings, ledger, maximum_calls, max_requests)
    # Each newly recognized diagram may need one bounded visual detail pass.
    # Actual HTTP requests are still recorded; unused capacity is not a call.
    max_requests = min(max_requests, maximum_calls)
    output = output_root.resolve() / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:8])
    output.mkdir(parents=True)
    config.DATA_DIR = output / 'runtime'
    config.STORAGE_DIR = output / 'storage'
    settings.database_url = 'sqlite:///' + (output / 'evaluation.db').as_posix()
    settings.model_provider = 'deepseek'
    settings.cluster_provider = 'model'
    settings.knowledge_expansion_enabled = True
    settings.model_max_retry = model_retries
    write_json(output / 'frozen-inputs.json', manifest)
    prompt_hashes = {p.name: file_hash(p) for p in sorted(config.PROMPTS_DIR.glob('*.yaml'))}
    code_hashes = {p.relative_to(BACKEND).as_posix(): file_hash(p) for p in (BACKEND / 'app').rglob('*.py')}
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()

    import httpx
    from docx import Document
    from fastapi.testclient import TestClient
    from app.db import SessionLocal, engine
    from app.main import app
    from app.models import KnowledgePoint, Page
    from acceptance_metrics import reliability_summary

    assert Path(engine.url.database).resolve() == (output / 'evaluation.db').resolve()
    assert model_budget.BUDGET_PATH == ledger
    network_events = []
    original_client = httpx.Client

    class RecordedClient(original_client):
        def send(self, request, *args, **kwargs):
            if request.url.host != 'api.deepseek.com':
                raise ValueError('本轮不允许向其他模型服务发送照片。')
            if len(network_events) >= max_requests:
                raise ValueError('本轮请求数上限已到达。')
            entry = {'request_no': len(network_events) + 1, 'started_at': utc_now(), 'status': 'started'}
            network_events.append(entry)
            write_json(output / 'network-events.json', {'events': network_events})
            try:
                response = super().send(request, *args, **kwargs)
                entry.update({'status': 'completed', 'http_status': response.status_code,
                              'finished_at': utc_now()})
                if response.is_success:
                    body = response.json()
                    entry.update({'request_id': body.get('id'), 'model': body.get('model'), 'usage': body.get('usage')})
                    choices = body.get('choices') or []
                    if choices:
                        entry['finish_reason'] = choices[0].get('finish_reason')
                        write_json(output / f'completion-{entry["request_no"]}.json', {
                            'finish_reason': choices[0].get('finish_reason'),
                            'content': (choices[0].get('message') or {}).get('content')})
                return response
            except httpx.HTTPError as exc:
                entry.update({'status': 'failed', 'error_type': type(exc).__name__, 'finished_at': utc_now()})
                raise
            finally:
                write_json(output / 'network-events.json', {'events': network_events})

    # Starlette's TestClient was imported before replacing cloud HTTP clients.
    httpx.Client = RecordedClient
    attempts, groups = [], []
    report = {'dataset': manifest['dataset_id'], 'annotation_status': manifest['annotation_status'],
              'code_commit': commit, 'code_file_hashes': code_hashes, 'prompt_hashes': prompt_hashes,
              'runner_sha256': file_hash(Path(__file__)), 'budget_before': budget_before,
              'output_directory': str(output), 'execution_status': 'started', 'groups': groups,
              'previous_run': str(previous_run) if previous_run else None,
              'reused_recognition_samples': sorted(cached_recognition),
              'comparison_scope': 'development_recheck_not_holdout' if previous_run else 'calibration_pilot'}
    by_id = {p['sample_id']: p for p in manifest['pages']}

    def checked(response):
        if not response.is_success:
            body = response.json()
            raise AppError(body.get('code', 'ACCEPTANCE_API_ERROR'), body.get('message', '评测接口失败。'))
        return response

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            for group in manifest['task_groups']:
                group_dir = output / group['task_group_id']
                record = {'run_id': uuid4().hex, 'mode': 'live', 'purpose': 'quality_pilot',
                          'status': 'started', 'started_at': utc_now(), 'finished_at': None,
                          'group_id': group['task_group_id'], 'max_retry': model_retries}
                attempts.append(record)
                write_json(group_dir / 'attempt.json', record)
                result = {'group_id': group['task_group_id'], 'pages': []}
                groups.append(result)
                try:
                    inputs = [by_id[sample] for sample in group['ordered_sample_ids']]
                    upload = checked(client.post('/api/tasks', files=[
                        ('files', (Path(p['absolute_image_path']).name, Path(p['absolute_image_path']).read_bytes(), 'image/jpeg'))
                        for p in inputs])).json()
                    task = upload['task_id']
                    record['task_id'] = task
                    # The upload still contains all original inputs in the same order.
                    # Reused metadata retain their original prompt version and request ID.
                    with SessionLocal() as db:
                        pages = db.query(Page).filter_by(task_id=task).order_by(Page.page_no).all()
                        validate_uploaded_inputs(upload, pages, inputs)
                        for page, supplied in zip(pages, inputs):
                            cached = cached_recognition.get(supplied['sample_id'])
                            if cached:
                                page.recognized_text = cached['recognized_text']
                                page.recognized_structured = cached['structured']
                                page.confidence = cached['confidence']
                                page.low_confidence_flag = cached['low_confidence']
                                page.failed_flag = False
                                page.error_msg = None
                        db.commit()
                    recognition_failure = None
                    try:
                        recognize_selected(client, task, [page_no for page_no, supplied in enumerate(inputs, 1)
                                                          if supplied['sample_id'] not in cached_recognition])
                    except Exception as exc:
                        recognition_failure = exc
                    with SessionLocal() as db:
                        pages = db.query(Page).filter_by(task_id=task).order_by(Page.page_no).all()
                        snapshots = []
                        for page, supplied in zip(pages, inputs):
                            structured = page.recognized_structured or {}
                            draft = json.loads(Path(supplied['absolute_draft_path']).read_text(encoding='utf-8'))
                            coverage = token_coverage(draft['expected_points'], structured.get('points', []))
                            result['pages'].append({'sample_id': supplied['sample_id'], 'logical_page': page.page_no,
                                                    'failed': page.failed_flag, 'error_msg': page.error_msg,
                                                    'coverage': coverage})
                            snapshots.append({'sample_id': supplied['sample_id'], 'page_no': page.page_no,
                                              'recognized_text': page.recognized_text, 'structured': structured,
                                              'recognition_state': ('failed' if page.failed_flag else
                                                                    'recognized' if page.recognized_text else 'not_attempted'),
                                              'confidence': page.confidence, 'low_confidence': page.low_confidence_flag,
                                              'failed': page.failed_flag, 'error_msg': page.error_msg})
                        write_json(group_dir / 'recognition.json', {'pages': snapshots})
                        if recognition_failure is not None:
                            raise recognition_failure
                        if any(page.failed_flag for page in pages):
                            raise AppError('ACCEPTANCE_RECOGNITION_FAILED', '至少一页失败，已保存页级证据并停止后续模型调用。')
                    generated = checked(client.post(f'/api/tasks/{task}/pipeline')).json()
                    saved = checked(client.get(f'/api/tasks/{task}/outline')).json()
                    word = checked(client.get(f'/api/tasks/{task}/export')).content
                    group_dir.joinpath('outline.md').write_text(saved['markdown'], encoding='utf-8')
                    group_dir.joinpath('outline.docx').write_bytes(word)
                    paragraphs = [p.text for p in Document(BytesIO(word)).paragraphs]
                    with SessionLocal() as db:
                        points = db.query(KnowledgePoint).filter_by(task_id=task).order_by(KnowledgePoint.id).all()
                        data = [{'content': p.content, 'source_type': p.source_type, 'source_pages': p.source_pages,
                                 'kp_type': p.kp_type} for p in points]
                    write_json(group_dir / 'knowledge-points.json', {'points': data})
                    generated_count = sum(p['source_type'] == 'AI生成' for p in data)
                    structural = {
                        'all_pages_recognized': not any(p['failed'] for p in result['pages']),
                        'ai_expansion_present': generated_count > 0,
                        'markdown_ai_label_count_matches': saved['markdown'].count('【AI生成】') == generated_count,
                        'word_ai_label_count_matches': sum(p.count('【AI生成】') for p in paragraphs) == generated_count,
                        'all_contents_in_word': all(any(p['content'] in paragraph for paragraph in paragraphs) for p in data),
                        'associated_pages_labeled': saved['markdown'].count('（关联课堂页：') == generated_count,
                        'all_page_references_valid': all(set(p['source_pages'].split(',')) <= {f'P{i}' for i in range(1, len(inputs)+1)} for p in data),
                    }
                    result.update({'pipeline': generated, 'structural_checks': structural,
                                   'original_count': sum(p['source_type'] == '课堂原话' for p in data),
                                   'ai_generated_count': generated_count})
                    if not all(structural.values()):
                        raise AppError('ACCEPTANCE_STRUCTURE_FAILED', '生成或导出结构检查失败，详见原始记录。')
                    record.update({'status': 'success', 'actual_provider': 'deepseek'})
                except Exception as exc:
                    record.update({'status': 'failed', 'error_code': exc.code if isinstance(exc, AppError) else type(exc).__name__})
                    raise
                finally:
                    persist_attempt_finish(group_dir / 'attempt.json', record)
                write_json(output / 'report.json', report)
        report['execution_status'] = 'completed'
    except Exception as exc:
        report.update({'execution_status': 'failed', 'error_code': exc.code if isinstance(exc, AppError) else type(exc).__name__})
    finally:
        httpx.Client = original_client
        engine.dispose()
        report.update({'attempts': attempts, 'reliability': reliability_summary(attempts),
                       'budget_after': model_budget.budget_status(), 'request_count': len(network_events),
                       'quality_acceptance': 'pilot_only_pending_independent_human_review',
                       'long_term_paid_campaign': 'not_started_per_user_instruction'})
        write_json(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--authorization-record', type=Path)
    parser.add_argument('--output-root', type=Path, default=BACKEND / 'data/acceptance')
    parser.add_argument('--reuse-recognition-from', type=Path)
    parser.add_argument('--refresh-samples', nargs='*', default=[])
    parser.add_argument('--model-retries', type=int, choices=range(3), default=0,
                        help='每个请求的有界重试次数；默认0，另行记录，不能混算为零重试结果')
    args = parser.parse_args()
    try:
        manifest = prepare_manifest(args.dataset, args.authorization_record, live=args.live)
        cached = (load_cached_recognition(manifest, args.reuse_recognition_from, args.refresh_samples)
                  if args.reuse_recognition_from else {})
        if args.refresh_samples and not args.reuse_recognition_from:
            raise ValueError('--refresh-samples 需要指定前一次完整试运行目录。')
        if not args.live:
            print(json.dumps({'status': 'not_called', 'pages': len(manifest['pages']),
                              'groups': len(manifest['task_groups']), 'annotation_status': manifest['annotation_status'],
                              'reused_recognition_count': len(cached),
                              'model_retries': args.model_retries,
                              'planned_request_count': planned_requests(manifest, cached, args.model_retries)[0],
                              'planned_max_request_count': planned_requests(manifest, cached, args.model_retries)[1]}, ensure_ascii=False))
            return 0
        report = run_live(manifest, args.output_root, cached_recognition=cached,
                          previous_run=args.reuse_recognition_from, model_retries=args.model_retries)
        print(json.dumps({k: report[k] for k in ['execution_status', 'output_directory', 'request_count',
                                               'budget_before', 'budget_after', 'quality_acceptance',
                                               'long_term_paid_campaign']}, ensure_ascii=False, indent=2))
        return 0 if report['execution_status'] == 'completed' else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'not_called', 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
