"""The app must not silently depend on a desktop proxy when reopened."""
import json

import httpx
import pytest

from app.config import settings
from app.services.extractor import ExtractedKnowledgePoint
from app.services.knowledge_expander import KnowledgeExpander
from app.services.model_clusterer import ModelClusterer
from app.services.recognizer import MultimodalRecognizer


@pytest.mark.parametrize('stage', ['recognition', 'grouping', 'expansion'])
@pytest.mark.parametrize('use_environment', [False, True])
def test_all_model_stages_choose_the_configured_network_route(
        monkeypatch, tmp_path, valid_image_bytes, stage, use_environment):
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setattr(settings, 'model_provider', 'multimodal')
    monkeypatch.setattr(settings, 'model_base_url', 'https://model.invalid/v1')
    monkeypatch.setattr(settings, 'model_api_key', 'offline-only')
    monkeypatch.setattr(settings, 'model_max_retry', 0)
    monkeypatch.setattr(settings, 'knowledge_expansion_enabled', True)
    monkeypatch.setattr(settings, 'model_trust_env', use_environment, raising=False)
    bodies = {
        'recognition': {'title': '课件', 'points': [{'content': '课堂概念', 'kp_type': 'concept',
                        'source_type': '课堂原话'}], 'confidence': 0.95,
                        'readability': 'clear', 'uncertain_regions': []},
        'grouping': {'modules': [{'title': '主题', 'point_ids': ['kp_1']}]},
        'expansion': {'points': [{'content': '相关的补充知识', 'kp_type': 'concept', 'based_on_ids': ['kp_1']}]},
    }
    clients = []
    original = httpx.Client

    def client(**kwargs):
        clients.append(kwargs)
        assert kwargs.get('trust_env') is use_environment
        return original(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop', 'message': {
                'content': json.dumps(bodies[stage], ensure_ascii=False)}}]})), **kwargs)

    monkeypatch.setattr(httpx, 'Client', client)
    point = ExtractedKnowledgePoint('课堂概念', 'concept', '课堂原话', 'P1', 1, '课堂概念')
    if stage == 'recognition':
        image = tmp_path / 'page.png'
        image.write_bytes(valid_image_bytes)
        assert MultimodalRecognizer().recognize(1, str(image)).structured['title'] == '课件'
    elif stage == 'grouping':
        assert ModelClusterer().cluster([point], {1: '课件'})[0].knowledge_points == [point]
    else:
        assert KnowledgeExpander().expand([point], {1: '课件'})[0].source_type == 'AI生成'
    assert len(clients) == 1


def test_fresh_settings_default_to_direct_connection(monkeypatch):
    from app.config import Settings
    monkeypatch.delenv('APP_MODEL_TRUST_ENV', raising=False)
    assert Settings(_env_file=None).model_trust_env is False


def test_transport_diagnostics_keep_causes_without_secret_text(caplog):
    from app.services.model_network import report_transport_failure
    error = httpx.ConnectError('secret-key-and-image-content')
    error.__cause__ = ConnectionRefusedError(10061, 'sensitive proxy address')
    with caplog.at_level('WARNING'):
        report_transport_failure('recognition', error)
    assert 'stage=recognition' in caplog.text and 'ConnectError' in caplog.text
    assert '10061' in caplog.text
    assert 'secret-key' not in caplog.text and 'sensitive proxy address' not in caplog.text
