import json
import httpx
import pytest

from app.errors import ModelTimeoutError, RecognitionFailedError
from app.services.recognizer import MultimodalRecognizer


@pytest.mark.parametrize('failure,expected_error', [
    (httpx.ReadTimeout, ModelTimeoutError),
    (httpx.ConnectError, RecognitionFailedError),
])
def test_model_transport_failures_have_bounded_retries(tmp_path, monkeypatch, failure, expected_error, valid_image_bytes):
    image = tmp_path / 'page.png'
    image.write_bytes(valid_image_bytes)
    requests = []

    def transport(request):
        requests.append(request)
        raise failure('simulated unavailable model', request=request)

    original_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(
        transport=httpx.MockTransport(transport), **kwargs))
    recognizer = MultimodalRecognizer()
    recognizer.api_key = 'test-only-not-a-real-key'
    recognizer.base_url = 'https://model.invalid/v1'
    recognizer.max_retry = 1
    with pytest.raises(expected_error) as error:
        recognizer.recognize(1, str(image))
    assert error.value.retryable is True
    assert len(requests) == 2


def test_model_timeout_then_recovery_returns_recognition(tmp_path, monkeypatch, valid_image_bytes):
    image = tmp_path / 'page.png'
    image.write_bytes(valid_image_bytes)
    requests = []

    def transport(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout('temporary timeout', request=request)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
            'title': '页面标题', 'points': [{'content': '正常识别内容', 'kp_type': 'concept', 'source_type': '课堂原话'}],
            'confidence': 0.8, 'readability': 'clear', 'uncertain_regions': [],
        }, ensure_ascii=False)}}]})

    original_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(
        transport=httpx.MockTransport(transport), **kwargs))
    recognizer = MultimodalRecognizer()
    recognizer.api_key = 'test-only-not-a-real-key'
    recognizer.base_url = 'https://model.invalid/v1'
    recognizer.max_retry = 1
    assert recognizer.recognize(1, str(image)).text == '页面标题\n- 正常识别内容'
    assert len(requests) == 2
