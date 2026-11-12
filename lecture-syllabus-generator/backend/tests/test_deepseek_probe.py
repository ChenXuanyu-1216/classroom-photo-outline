import json
from pathlib import Path
import runpy

import httpx
import pytest

from app.config import settings
from app.errors import AppError


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'check_deepseek.py'
HTTP_CLIENT = httpx.Client


def probe():
    return runpy.run_path(str(SCRIPT))


def test_default_probe_does_not_make_paid_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'model_api_key', 'private-test-key')
    requests = []
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: requests.append(kwargs))
    result = probe()['run_probe'](budget_path=tmp_path / 'budget.json')
    assert result['status'] == 'configured_not_called'
    assert requests == []
    assert 'private-test-key' not in json.dumps(result)


def test_budget_refuses_more_than_five_yuan_and_rejects_corruption(tmp_path):
    reserve = probe()['reserve_budget']
    budget = tmp_path / 'budget.json'
    budget.write_text('{"reserved_fen":495}', encoding='utf-8')
    with pytest.raises(AppError) as error:
        reserve(budget)
    assert error.value.code == 'TEST_BUDGET_EXHAUSTED'
    budget.write_text('invalid', encoding='utf-8')
    with pytest.raises(AppError) as error:
        reserve(budget)
    assert error.value.code == 'TEST_BUDGET_INVALID'


def test_live_text_probe_is_bounded_and_reserves_before_request(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'model_api_key', 'private-test-key')
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    budget = tmp_path / 'budget.json'
    requests = []

    def handler(request):
        assert json.loads(budget.read_text(encoding='utf-8'))['reserved_fen'] == 10
        requests.append(request)
        return httpx.Response(200, json={
            'id': 'synthetic-test', 'model': 'deepseek-flash',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                'status': 'ok', 'formula': 'P(y|x)', 'code': '<input v-model="name">',
            })}}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 40},
        })

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    result = probe()['run_probe'](live=True, budget_path=budget)
    assert result['status'] == 'passed'
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload['max_tokens'] == 128
    assert payload['thinking'] == {'type': 'disabled'}
    assert result['checks'][0]['estimated_cost_cny_upper_rate'] == 0.00052
    assert 'private-test-key' not in json.dumps(result)


def test_probe_without_key_stops_before_reserving_or_sending(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'model_api_key', '')
    budget = tmp_path / 'budget.json'
    with pytest.raises(AppError) as error:
        probe()['run_probe'](live=True, budget_path=budget)
    assert error.value.code == 'MODEL_CONFIG_ERROR'
    assert not budget.exists()


def test_vision_probe_works_while_application_stays_in_mock_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'model_provider', 'mock')
    monkeypatch.setattr(settings, 'model_api_key', 'private-test-key')
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        content = {'status': 'ok', 'formula': 'P(y|x)', 'code': '<input v-model="name">'} if len(requests) == 1 else {
            'title': 'Synthetic sample', 'points': [{'content': '<input v-model="name"> P(y|x)',
                'kp_type': 'example', 'source_type': '课堂原话'}],
            'confidence': 0.9, 'readability': 'clear', 'uncertain_regions': [],
        }
        return httpx.Response(200, json={'id': 'synthetic', 'model': 'deepseek-flash',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}],
            'usage': {'prompt_tokens': 1000, 'completion_tokens': 100}})

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    result = probe()['run_probe'](live=True, vision=True, budget_path=tmp_path / 'budget.json')
    assert result['status'] == 'passed'
    assert len(result['checks']) == 2
    assert result['reserved_allowance_cny'] == 0.2
    assert settings.model_provider == 'mock'
    assert requests[1]['thinking'] == {'type': 'disabled'}
