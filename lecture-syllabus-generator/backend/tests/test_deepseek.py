"""Exercise real request building and validation with external HTTP replaced."""

import json

import httpx
import pytest

from app.config import settings
from app.errors import AppError
from app.services import recognizer as recognition


HTTP_CLIENT = httpx.Client
CONTENT = '<input v-model="name">；P(y|x)，y=f(x)'


def page_payload(**overrides):
    return {
        'title': '数据绑定与模型',
        'points': [{'content': CONTENT, 'kp_type': 'example', 'source_type': '课堂原话'}],
        'confidence': 0.85,
        'readability': 'clear',
        'uncertain_regions': [],
        **overrides,
    }


def response(content=None, finish_reason='stop'):
    return httpx.Response(200, json={
        'id': 'test-request-1', 'model': 'deepseek-flash',
        'choices': [{'finish_reason': finish_reason, 'message': {
            'content': json.dumps(page_payload() if content is None else content, ensure_ascii=False),
        }}],
        'usage': {'prompt_tokens': 1200, 'completion_tokens': 140, 'total_tokens': 1340},
    })


@pytest.fixture
def deepseek(monkeypatch, tmp_path, valid_image_bytes):
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    monkeypatch.setattr(settings, 'model_api_key', 'test-secret-do-not-expose')
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com/')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    monkeypatch.setattr(settings, 'model_max_retry', 0)
    image = tmp_path / 'page.png'
    image.write_bytes(valid_image_bytes)
    return image


def transport(monkeypatch, handler):
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: HTTP_CLIENT(
        transport=httpx.MockTransport(handler), **kwargs))


def test_selected_deepseek_uses_image_json_and_preserves_code_formula(deepseek, monkeypatch):
    requests = []
    transport(monkeypatch, lambda request: requests.append(request) or response())
    result = recognition.get_recognizer().recognize(7, str(deepseek))
    assert result.structured['points'][0]['content'] == CONTENT
    assert result.structured['title'] == '数据绑定与模型'
    assert result.confidence == 0.85
    assert result.low_confidence is False
    assert len(requests) == 1
    assert str(requests[0].url) == 'https://api.deepseek.com/chat/completions'
    payload = json.loads(requests[0].content)
    assert payload['model'] == 'deepseek-flash'
    assert payload['thinking'] == {'type': 'disabled'}
    assert payload['response_format'] == {'type': 'json_object'}
    assert payload['max_tokens'] <= 4096
    assert 'JSON' in payload['messages'][0]['content']
    assert payload['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert requests[0].headers['Authorization'] == 'Bearer test-secret-do-not-expose'
    assert result.structured['_meta']['usage']['total_tokens'] == 1340
    assert result.structured['_meta']['request_id'] == 'test-request-1'
    assert result.structured['_meta']['confidence_basis'] == 'model_self_report_not_calibrated_accuracy'


def test_real_provider_without_key_fails_instead_of_mock(deepseek, monkeypatch):
    monkeypatch.setattr(settings, 'model_api_key', '  ')
    with pytest.raises(AppError) as error:
        recognition.get_recognizer().recognize(1, str(deepseek))
    assert error.value.code == 'MODEL_CONFIG_ERROR'
    assert error.value.retryable is False


@pytest.mark.parametrize('status,code', [(401, 'MODEL_AUTH_ERROR'), (402, 'MODEL_BALANCE_ERROR'), (400, 'RECOGNITION_FAILED')])
def test_permanent_provider_error_is_safe_and_never_retried(deepseek, monkeypatch, status, code):
    monkeypatch.setattr(settings, 'model_max_retry', 2)
    requests = []
    transport(monkeypatch, lambda request: requests.append(request) or httpx.Response(
        status, text='test-secret-do-not-expose and provider private details'))
    with pytest.raises(AppError) as error:
        recognition.get_recognizer().recognize(1, str(deepseek))
    assert error.value.code == code
    assert error.value.retryable is False
    assert 'test-secret' not in str(error.value)
    assert 'private details' not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize('content,finish', [
    ({'title': '缺少知识点'}, 'stop'),
    (page_payload(confidence=1.5), 'stop'),
    (page_payload(points=[{'content': '未知类型', 'kp_type': 'unknown', 'source_type': '课堂原话'}]), 'stop'),
    (page_payload(points=[{'content': '注入来源', 'kp_type': 'concept', 'source_type': '伪造来源'}]), 'stop'),
    (page_payload(), 'length'),
    (page_payload(readability='unreadable'), 'stop'),
    (page_payload(points=[]), 'stop'),
])
def test_invalid_or_truncated_recognition_does_not_enter_pipeline(deepseek, monkeypatch, content, finish):
    transport(monkeypatch, lambda request: response(content, finish))
    with pytest.raises(AppError) as error:
        recognition.get_recognizer().recognize(1, str(deepseek))
    assert error.value.code == 'RECOGNITION_FAILED'


def test_partial_readability_is_flagged_even_when_model_claims_high_confidence(deepseek, monkeypatch):
    transport(monkeypatch, lambda request: response(page_payload(
        confidence=0.99, readability='partial', uncertain_regions=['右下角小字无法辨认'])))
    result = recognition.get_recognizer().recognize(1, str(deepseek))
    assert result.low_confidence is True
    assert result.confidence <= 0.5
    assert result.structured['uncertain_regions'] == ['右下角小字无法辨认']


def test_deepseek_rejects_pdf_before_network(deepseek, monkeypatch):
    pdf = deepseek.with_suffix('.pdf')
    pdf.write_bytes(b'%PDF-1.4')
    requests = []
    transport(monkeypatch, lambda request: requests.append(request) or response())
    with pytest.raises(AppError) as error:
        recognition.get_recognizer().recognize(1, str(pdf))
    assert error.value.code == 'FILE_FORMAT_ERROR'
    assert requests == []


def test_unknown_provider_cannot_silently_produce_demo_content(monkeypatch):
    monkeypatch.setattr(settings, 'model_provider', 'deepseke')
    with pytest.raises(AppError) as error:
        recognition.get_recognizer()
    assert error.value.code == 'MODEL_CONFIG_ERROR'


def test_prompt_load_failure_does_not_send_image(deepseek, monkeypatch):
    monkeypatch.setattr(recognition, 'get_prompt_text', lambda name: '')
    requests = []
    transport(monkeypatch, lambda request: requests.append(request) or response())
    with pytest.raises(AppError) as error:
        recognition.get_recognizer().recognize(1, str(deepseek))
    assert error.value.code == 'MODEL_CONFIG_ERROR'
    assert requests == []
