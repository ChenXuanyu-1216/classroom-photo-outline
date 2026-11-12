import json
from pathlib import Path
import runpy

import httpx
import pytest

from app.config import settings
from app.errors import AppError
from app.services import model_network
from app.services.recognizer import MultimodalRecognizer
from app.services.model_clusterer import ModelClusterer
from app.services.knowledge_expander import KnowledgeExpander
from app.services.extractor import ExtractedKnowledgePoint


@pytest.mark.parametrize('stage', ['recognition', 'grouping', 'expansion'])
@pytest.mark.parametrize('status', [429, 503, 'connect'])
def test_transient_failure_waits_before_recovery(monkeypatch, tmp_path, valid_image_bytes, stage, status):
    monkeypatch.setattr(settings, 'model_provider', 'multimodal')
    monkeypatch.setattr(settings, 'model_base_url', 'https://model.invalid/v1')
    monkeypatch.setattr(settings, 'model_api_key', 'offline-only')
    monkeypatch.setattr(settings, 'model_max_retry', 1)
    monkeypatch.setattr(settings, 'knowledge_expansion_enabled', True)
    waits, calls = [], []
    monkeypatch.setattr(model_network.time, 'sleep', waits.append)
    bodies = {
        'recognition': {'title': '课件', 'points': [{'content': '课堂知识', 'kp_type': 'concept', 'source_type': '课堂原话'}],
                        'confidence': .9, 'readability': 'clear', 'uncertain_regions': []},
        'grouping': {'modules': [{'title': '主题', 'point_ids': ['kp_1']}]},
        'expansion': {'points': [{'content': '补充知识', 'kp_type': 'concept', 'based_on_ids': ['kp_1']}]},
    }
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            if status == 'connect':
                raise httpx.ConnectError('offline', request=request)
            return httpx.Response(status, headers={'Retry-After': '2'})
        assert waits == ([.5] if status == 'connect' else [2.0])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(bodies[stage], ensure_ascii=False)}}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    point = ExtractedKnowledgePoint('课堂知识', 'concept', '课堂原话', 'P1', 1, '课堂知识')
    if stage == 'recognition':
        path = tmp_path / 'page.png'
        path.write_bytes(valid_image_bytes)
        MultimodalRecognizer().recognize(1, str(path))
    elif stage == 'grouping':
        ModelClusterer().cluster([point], {1: '课件'})
    else:
        KnowledgeExpander().expand([point], {1: '课件'})
    assert len(calls) == 2


@pytest.mark.parametrize('header', ['300', '999999999999999999999', 'Wed, 01 Jan 2099 00:00:00 GMT'])
def test_retry_after_above_bound_stops_without_sleep(monkeypatch, header):
    waits = []
    monkeypatch.setattr(model_network.time, 'sleep', waits.append)
    assert model_network.wait_before_retry(0, httpx.Response(429, headers={'Retry-After': header})) is False
    assert waits == []


@pytest.mark.parametrize('route', [False, True])
def test_fixed_probe_obeys_application_route(monkeypatch, tmp_path, route):
    monkeypatch.setattr(settings, 'model_api_key', 'offline-only')
    monkeypatch.setattr(settings, 'model_trust_env', route)
    original = httpx.Client
    def client(**kw):
        assert kw.get('trust_env') is route
        return original(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                'status': 'ok', 'formula': 'P(y|x)', 'code': '<input v-model="name">'})}}]})), **kw)
    monkeypatch.setattr(httpx, 'Client', client)
    script = Path(__file__).resolve().parents[2] / 'scripts/check_deepseek.py'
    assert runpy.run_path(str(script))['run_probe'](live=True, budget_path=tmp_path / 'budget.json')['status'] == 'passed'
