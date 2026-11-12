"""A fatal funding error must stop dispatch without shrinking the test batch."""
import importlib
import json
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.services import model_budget
from app.services.knowledge_expander import KnowledgeExpander
from app.services.recognizer import MultimodalRecognizer


@pytest.fixture
def regression_batch(monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[2] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    runner = importlib.import_module('run_content_regressions')
    for name, value in {
        'model_provider': 'deepseek', 'model_api_key': 'synthetic-unused-key',
        'model_base_url': 'https://api.deepseek.com', 'model_name': 'deepseek-flash',
        'model_max_retry': 0, 'model_max_tokens': 64, 'knowledge_expansion_enabled': True,
        'model_budget_enforced': True, 'model_budget_cny': 3,
    }.items():
        monkeypatch.setattr(settings, name, value)
    ledger = tmp_path / 'budget.json'
    monkeypatch.setattr(model_budget, 'BUDGET_PATH', ledger)
    image = Path(__file__).parent / 'fixtures/deepseek-synthetic-slide.png'
    manifest = {'pages': [
        {'sample_id': 'CP003', 'absolute_image_path': str(image)},
        {'sample_id': 'CP008', 'absolute_image_path': str(image)},
    ]}

    # Observe adapter dispatch while retaining their real budget and HTTP paths.
    dispatches = []
    recognize = MultimodalRecognizer.recognize
    expand = KnowledgeExpander.expand

    def recorded_recognize(self, *args, **kwargs):
        dispatches.append('recognize')
        return recognize(self, *args, **kwargs)

    def recorded_expand(self, *args, **kwargs):
        dispatches.append('expand')
        return expand(self, *args, **kwargs)

    monkeypatch.setattr(MultimodalRecognizer, 'recognize', recorded_recognize)
    monkeypatch.setattr(KnowledgeExpander, 'expand', recorded_expand)
    return runner, manifest, dispatches, ledger


@pytest.mark.parametrize('fatal_request', [1, 3])
def test_balance_error_stops_dispatch_and_preserves_planned_denominator(
        regression_batch, monkeypatch, tmp_path, fatal_request):
    runner, manifest, dispatches, _ = regression_batch
    requests = []

    def provider_response(self, request):
        requests.append(request)
        status = 402 if len(requests) >= fatal_request else 500
        return httpx.Response(status, json={'error': {'message': 'synthetic provider failure'}})

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', provider_response)
    original_client = httpx.Client
    output, report = runner.run_live(manifest, 3, tmp_path / 'reports')

    assert dispatches == ['recognize', 'recognize', 'expand'][:fatal_request]
    assert len(requests) == report['request_count'] == fatal_request
    assert httpx.Client is original_client
    assert report['status'] == 'blocked'
    assert report['attempt_count'] == 12
    assert report['passed_count'] == 0
    assert report['development_gate_passed'] is False
    assert [(entry['case_id'], entry['repeat']) for entry in report['entries']] == [
        ('CP003', 1), ('CP008', 1), ('vue_controls', 1), ('label_metrics', 1),
        ('CP003', 2), ('CP008', 2), ('vue_controls', 2), ('label_metrics', 2),
        ('CP003', 3), ('CP008', 3), ('vue_controls', 3), ('label_metrics', 3),
    ]
    failed = report['entries'][fatal_request - 1]
    assert failed['status'] == 'failed'
    assert failed['error_code'] == 'MODEL_BALANCE_ERROR'
    assert failed['reason']
    for entry in report['entries'][fatal_request:]:
        assert entry['status'] == 'not_called'
        assert entry['error_code'] == 'MODEL_BALANCE_ERROR'
        assert entry['reason']
        assert 'started_at' not in entry
    assert report['finished_at']
    assert json.loads((output / 'report.json').read_text(encoding='utf-8')) == report
    events = json.loads((output / 'network-events.json').read_text(encoding='utf-8'))['events']
    assert len(events) == fatal_request
    assert events[-1]['http_status'] == 402


def test_exhausted_budget_stops_adapters_without_a_physical_request(
        regression_batch, monkeypatch, tmp_path):
    runner, manifest, dispatches, ledger = regression_batch
    ledger.write_text('{"reserved_fen":300}', encoding='utf-8')
    requests = []

    def forbid_network(self, request):
        requests.append(request)
        raise AssertionError('An exhausted budget must not reach the network')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbid_network)
    output, report = runner.run_live(manifest, 3, tmp_path / 'reports')

    assert dispatches == ['recognize']
    assert requests == []
    assert report['request_count'] == 0
    assert report['status'] == 'blocked'
    assert report['attempt_count'] == len(report['entries']) == 12
    assert report['passed_count'] == 0
    assert report['development_gate_passed'] is False
    assert report['entries'][0]['status'] == 'failed'
    assert all(entry['status'] == 'not_called' for entry in report['entries'][1:])
    assert all(entry['error_code'] == 'TEST_BUDGET_EXHAUSTED' and entry['reason']
               for entry in report['entries'])
    assert report['budget_after']['reserved_cny'] == 3
    assert json.loads((output / 'report.json').read_text(encoding='utf-8')) == report


def test_non_funding_failure_continues_the_planned_batch(regression_batch, monkeypatch, tmp_path):
    runner, manifest, dispatches, _ = regression_batch

    def provider_failure(self, request):
        return httpx.Response(500, json={'error': {'message': 'synthetic service failure'}})

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', provider_failure)
    _, report = runner.run_live(manifest, 3, tmp_path / 'reports')

    assert dispatches == ['recognize', 'recognize', 'expand', 'expand'] * 3
    assert report['request_count'] == report['attempt_count'] == 12
    assert report['status'] == 'completed'
    assert all(entry['status'] == 'failed' for entry in report['entries'])
    assert report['development_gate_passed'] is False
