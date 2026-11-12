import json

import httpx
import pytest

from app.config import settings
from app.errors import AppError
from app.services.extractor import ExtractedKnowledgePoint
from app.services.recognizer import MultimodalRecognizer
from app.services.model_clusterer import ModelClusterer
from app.services.knowledge_expander import KnowledgeExpander


HTTP_CLIENT = httpx.Client
PAGE = {'title': '测试课件', 'points': [{'content': '课堂定义', 'kp_type': 'concept', 'source_type': '课堂原话'}],
        'confidence': .9, 'readability': 'clear', 'uncertain_regions': []}
GROUP = {'modules': [{'title': '原模块', 'point_ids': ['kp_1']}]}
EXPANSION = {'points': [{'content': '课件之外的补充知识', 'kp_type': 'concept', 'based_on_ids': ['kp_1']}]}


def completion(value):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value, ensure_ascii=False)}}]}


def bad_response(kind):
    if kind == 'empty_body':
        return httpx.Response(200, content=b' \r\n')
    content = '' if kind == 'empty_content' else '{"private-provider-body":'
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})


def configure(monkeypatch):
    monkeypatch.setattr(settings, 'model_provider', 'multimodal')
    monkeypatch.setattr(settings, 'model_api_key', 'offline-only')
    monkeypatch.setattr(settings, 'model_base_url', 'https://model.invalid/v1')
    monkeypatch.setattr(settings, 'model_max_retry', 1)
    monkeypatch.setattr(settings, 'knowledge_expansion_enabled', True)
    monkeypatch.setattr(settings, 'cluster_provider', 'model')


@pytest.mark.parametrize('stage', ['recognition', 'grouping', 'expansion'])
@pytest.mark.parametrize('kind,expected', [
    ('empty_body', 'MODEL_EMPTY_RESPONSE'), ('empty_content', 'MODEL_EMPTY_CONTENT'),
    ('malformed', None),
])
def test_every_adapter_preserves_response_kind_without_network_retry(monkeypatch, tmp_path, valid_image_bytes, stage, kind, expected):
    configure(monkeypatch)
    calls = []
    def handler(request):
        calls.append(request)
        return bad_response(kind)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(handler), **kw))
    point = ExtractedKnowledgePoint('课堂定义', 'concept', '课堂原话', 'P1', 1, '课堂定义')
    with pytest.raises(AppError) as error:
        if stage == 'recognition':
            path = tmp_path/'page.png'
            path.write_bytes(valid_image_bytes)
            MultimodalRecognizer().recognize(1, str(path))
        elif stage == 'grouping':
            ModelClusterer().cluster([point], {1: '课件'})
        else:
            KnowledgeExpander().expand([point], {1: '课件'})
    legacy = {'recognition': 'RECOGNITION_FAILED', 'grouping': 'CLUSTERING_FAILED', 'expansion': 'KNOWLEDGE_EXPANSION_FAILED'}
    assert error.value.code == (expected or legacy[stage])
    assert error.value.retryable is True
    assert 'private-provider-body' not in error.value.message
    assert len(calls) == 1


@pytest.mark.parametrize('stage', ['grouping', 'expansion'])
@pytest.mark.parametrize('kind', ['empty_body', 'empty_content', 'malformed'])
def test_async_job_records_output_error_and_preserves_edited_draft(client, monkeypatch, valid_image_bytes, stage, kind):
    from app.services.pipeline import pipeline
    from app.services.task_jobs import TaskJobQueue
    configure(monkeypatch)
    monkeypatch.setattr(settings, 'task_queue_enabled', True)
    monkeypatch.setattr(pipeline, 'recognizer', MultimodalRecognizer())
    calls = []
    def handler(request):
        calls.append(request)
        count = len(calls)
        if count <= 3:
            return httpx.Response(200, json=completion([PAGE, GROUP, EXPANSION][count-1]))
        if stage == 'expansion' and count == 4:
            return httpx.Response(200, json=completion(GROUP))
        return bad_response(kind)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(handler), **kw))
    task = client.post('/api/tasks', files=[('files', ('page.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    initial = client.post(f'/api/tasks/{task}/pipeline')
    assert initial.status_code == 200, initial.text
    saved = '# 已人工校对的大纲\n- 必须保留这条内容（来源：P1）【课堂原话】'
    assert client.put(f'/api/tasks/{task}/outline', json={'markdown': saved}).status_code == 200
    accepted = client.post(f'/api/tasks/{task}/regenerate', headers={'Prefer': 'respond-async'})
    assert accepted.status_code == 202
    assert TaskJobQueue().run_once()
    result = client.get(f'/api/tasks/{task}/jobs/{accepted.json()["job_id"]}').json()
    code = {'empty_body': 'MODEL_EMPTY_RESPONSE', 'empty_content': 'MODEL_EMPTY_CONTENT',
            'malformed': 'CLUSTERING_FAILED' if stage == 'grouping' else 'KNOWLEDGE_EXPANSION_FAILED'}[kind]
    assert result['status'] == 'failed' and result['error']['code'] == code
    assert client.get(f'/api/tasks/{task}/outline').json()['markdown'] == saved
    assert len(calls) == (4 if stage == 'grouping' else 5)
