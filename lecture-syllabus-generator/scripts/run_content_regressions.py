"""Repeat frozen development cases; default is dry-run and never reads API keys."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from content_regressions import CASES, diagram_checks, expansion_checks
from run_quality_acceptance import DEFAULT_DATASET, BACKEND, prepare_manifest, write_json, file_hash, utc_now


def run_live(manifest, repeats, output_root, case_ids=None, model_retries=0):
    sys.path.insert(0, str(BACKEND))
    from app.config import PROMPTS_DIR, settings
    from app.services.recognizer import MultimodalRecognizer
    from app.services.knowledge_expander import KnowledgeExpander
    from app.services.extractor import ExtractedKnowledgePoint
    from app.services import model_budget
    if (settings.model_base_url.strip().rstrip('/') not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}
            or settings.model_name != 'deepseek-flash' or not settings.model_api_key.strip()):
        raise ValueError('仅使用本地配置的官方 DeepSeek 服务。')
    settings.model_provider = 'deepseek'
    settings.model_max_retry = model_retries
    settings.knowledge_expansion_enabled = True
    output = output_root.resolve() / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:8])
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    selected = [item for item in [*cases['diagram_cases'].items(), *cases['expansion_cases'].items()]
                if case_ids is None or item[0] in case_ids]
    by_id = {p['sample_id']: p for p in manifest['pages']}
    for sample in cases['diagram_cases']:
        if sample not in by_id:
            raise ValueError('冻结的图示反例不在授权数据包中。')
    entries, events = [], []
    report = {'purpose': 'development_content_regressions_not_long_term_availability',
              'status': 'started', 'started_at': utc_now(), 'cases_sha256': file_hash(CASES),
              'runner_sha256': file_hash(Path(__file__)), 'repeat_count': repeats,
              'model_retries': model_retries,
              'evaluator_sha256': file_hash(Path(__file__).with_name('content_regressions.py')),
              'selected_cases': [case_id for case_id, _ in selected],
              'prompt_hashes': {p.name: file_hash(p) for p in sorted(PROMPTS_DIR.glob('*.yaml'))},
              'code_hashes': {p.relative_to(BACKEND).as_posix(): file_hash(p) for p in (BACKEND / 'app').rglob('*.py')},
              'budget_before': model_budget.budget_status(), 'entries': entries,
              'formal_content_acceptance': 'pending_independent_human_review'}
    write_json(output / 'frozen-inputs.json', manifest)
    write_json(output / 'report.json', report)
    import httpx
    original_client = httpx.Client

    class RecordedClient(original_client):
        def send(self, request, *args, **kwargs):
            if request.url.host != 'api.deepseek.com':
                raise ValueError('未授权的模型服务。')
            entry = {'request_no': len(events)+1, 'started_at': utc_now(), 'status': 'started'}
            events.append(entry)
            write_json(output / 'network-events.json', {'events': events})
            try:
                response = super().send(request, *args, **kwargs)
                entry.update(status='completed', http_status=response.status_code)
                if response.is_success:
                    body = response.json()
                    entry.update(request_id=body.get('id'), usage=body.get('usage'), model=body.get('model'))
                    choices = body.get('choices') or []
                    if choices:
                        entry['finish_reason'] = choices[0].get('finish_reason')
                        write_json(output / f'completion-{entry["request_no"]}.json', {
                            'finish_reason': choices[0].get('finish_reason'),
                            'content': (choices[0].get('message') or {}).get('content')})
                return response
            except httpx.HTTPError as exc:
                entry.update(status='failed', error_type=type(exc).__name__)
                raise
            finally:
                entry['finished_at'] = utc_now()
                write_json(output / 'network-events.json', {'events': events})

    httpx.Client = RecordedClient
    blocked_by = None
    try:
        for repeat in range(1, repeats+1):
            for case_id, case in selected:
                entry = {'case_id': case_id, 'repeat': repeat}
                entries.append(entry)
                if blocked_by is not None:
                    entry.update(status='not_called', error_code=blocked_by['error_code'],
                                 reason=f"前序用例 {blocked_by['case_id']} 第 {blocked_by['repeat']} 轮失败，"
                                        f"已停止后续模型调用：{blocked_by['reason']}")
                    write_json(output / 'report.json', report)
                    continue
                entry.update(status='started', started_at=utc_now())
                write_json(output / 'report.json', report)
                try:
                    if case_id in cases['diagram_cases']:
                        result = MultimodalRecognizer().recognize(1, by_id[case_id]['absolute_image_path'])
                        observed = result.structured
                        checks = diagram_checks(observed, case)
                    else:
                        anchor = ExtractedKnowledgePoint(case['anchor'], 'concept', '课堂原话', 'P1', 1, case['anchor'])
                        result = KnowledgeExpander().expand([anchor], {1: case['anchor']})
                        observed = [{'content': p.content, 'source_type': p.source_type,
                                     'source_pages': p.source_pages} for p in result]
                        checks = expansion_checks(case_id, observed)
                    write_json(output / f'{repeat}-{case_id}.json', {'observed': observed, 'checks': checks})
                    entry.update(status='passed' if checks['passed'] else 'failed', checks=checks)
                except Exception as exc:
                    entry.update(status='failed', error_code=getattr(exc, 'code', type(exc).__name__))
                    if entry['error_code'] in {'MODEL_BALANCE_ERROR', 'TEST_BUDGET_EXHAUSTED'}:
                        entry['reason'] = exc.message
                        blocked_by = entry
                        report['status'] = 'blocked'
                finally:
                    entry['finished_at'] = utc_now()
                    write_json(output / 'report.json', report)
        report['status'] = 'blocked' if blocked_by is not None else 'completed'
    finally:
        httpx.Client = original_client
        report.update(finished_at=utc_now() if report['status'] in {'completed', 'blocked'} else None,
                      request_count=len(events), budget_after=model_budget.budget_status(),
                      passed_count=sum(entry['status'] == 'passed' for entry in entries),
                      attempt_count=len(entries))
        report['development_gate_passed'] = (len(selected) == 4 and report['status'] == 'completed'
                                             and len(entries) == 4*repeats and repeats >= 3
                                             and all(entry['status'] == 'passed' for entry in entries))
        write_json(output / 'report.json', report)
    return output, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--authorization-record', type=Path)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--model-retries', type=int, choices=range(3), default=0)
    parser.add_argument('--cases', nargs='+', choices=['CP003', 'CP008', 'vue_controls', 'label_metrics'])
    parser.add_argument('--output-root', type=Path, default=BACKEND / 'data/content-regressions')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats 必须至少为 1')
    manifest = prepare_manifest(args.dataset, args.authorization_record, live=args.live)
    if not args.live:
        selected = set(args.cases or ['CP003', 'CP008', 'vue_controls', 'label_metrics'])
        calls = sum(2 if case_id in {'CP003', 'CP008'} else 1 for case_id in selected)
        print(json.dumps({'status': 'not_called', 'case_count': len(selected), 'repeats': args.repeats,
                          'model_retries': args.model_retries,
                          'planned_max_requests': calls*args.repeats*(args.model_retries+1)}))
        return 0
    output, report = run_live(manifest, args.repeats, args.output_root, args.cases, args.model_retries)
    print(json.dumps({'output_directory': str(output), 'status': report['status'],
                      'passed_count': report['passed_count'], 'attempt_count': report['attempt_count'],
                      'request_count': report['request_count'], 'development_gate_passed': report['development_gate_passed']}, ensure_ascii=False))
    return 0 if report['passed_count'] == len(report['selected_cases'])*args.repeats else 1


if __name__ == '__main__':
    raise SystemExit(main())
