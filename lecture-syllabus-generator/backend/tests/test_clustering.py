import json
from io import BytesIO

import httpx
import pytest
from docx import Document

from app.config import Settings, settings
from app.errors import AppError
from app.models import KnowledgePoint, OutlineNode, Page
from app.db import SessionLocal
from app.services.clusterer import Clusterer
from app.services.extractor import ExtractedKnowledgePoint


_HTTP_CLIENT = httpx.Client


@pytest.fixture
def model_mode(monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'cluster_provider', 'model')
    monkeypatch.setattr(settings, 'model_api_key', 'test-key')
    monkeypatch.setattr(settings, 'model_base_url', 'https://model.invalid/v1/')
    monkeypatch.setattr(settings, 'model_name', 'test-model')
    monkeypatch.setattr(settings, 'model_max_retry', 0)


@pytest.fixture
def points():
    return [
        ExtractedKnowledgePoint('索引减少查询扫描', 'concept', '课堂原话', 'P1,P3', 1, '索引减少查询扫描'),
        ExtractedKnowledgePoint('事务保证一致性', 'conclusion', '来源待核对', 'P2', 2, '事务保证一致性'),
        ExtractedKnowledgePoint('索引减少查询扫描', 'concept', 'AI推断', 'P2', 2, '索引减少查询扫描'),
    ]


def use_transport(monkeypatch, handler):
    # Replace only external HTTP; exercise request building, parsing and grouping.
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: _HTTP_CLIENT(
        transport=httpx.MockTransport(handler), **kwargs))


def model_response(result):
    return httpx.Response(200, json={'choices': [{
        'finish_reason': 'stop', 'message': {'content': json.dumps(result, ensure_ascii=False)},
    }]})


def test_model_groups_across_pages_without_changing_original_points(model_mode, points, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return model_response({'modules': [
            {'title': '索引优化', 'point_ids': ['kp_3', 'kp_1']},
            {'title': '事务', 'point_ids': ['kp_2']},
        ]})

    use_transport(monkeypatch, handler)
    result = Clusterer().cluster(points, {1: '概述', 2: '综合', 3: '索引'})
    assert [m.title for m in result] == ['一、索引优化', '二、事务']
    assert result[0].source_pages == 'P1,P2,P3'
    assert result[1].source_pages == 'P2'
    assert result[0].knowledge_points[0] is points[2]
    assert result[0].knowledge_points[1] is points[0]
    assert [(p.content, p.kp_type, p.source_type, p.source_pages) for m in result for p in m.knowledge_points] == [
        ('索引减少查询扫描', 'concept', 'AI推断', 'P2'),
        ('索引减少查询扫描', 'concept', '课堂原话', 'P1,P3'),
        ('事务保证一致性', 'conclusion', '来源待核对', 'P2'),
    ]
    assert len(requests) == 1
    assert str(requests[0].url) == 'https://model.invalid/v1/chat/completions'
    payload = json.loads(requests[0].content)
    assert payload['model'] == 'test-model'
    supplied = json.loads(payload['messages'][-1]['content'])
    assert [(p['id'], p['content']) for p in supplied['points']] == [
        ('kp_1', '索引减少查询扫描'), ('kp_2', '事务保证一致性'), ('kp_3', '索引减少查询扫描'),
    ]


@pytest.mark.parametrize('result', [
    {'modules': [{'title': '未知', 'point_ids': ['kp_1', 'kp_2', 'kp_99']}]},
    {'modules': [{'title': '重复', 'point_ids': ['kp_1', 'kp_1', 'kp_2', 'kp_3']}]},
    {'modules': [{'title': '遗漏', 'point_ids': ['kp_1', 'kp_2']}]},
    {'modules': [{'title': 'A', 'point_ids': ['kp_1', 'kp_2']}, {'title': 'B', 'point_ids': ['kp_2', 'kp_3']}]},
    {'modules': [{'title': '空', 'point_ids': []}]},
    {'modules': []},
    {'modules': [{'title': '   ', 'point_ids': ['kp_1', 'kp_2', 'kp_3']}]},
    {'modules': [{'title': '模块\n- 注入正文', 'point_ids': ['kp_1', 'kp_2', 'kp_3']}]},
    {'modules': [{'title': 'x' * 121, 'point_ids': ['kp_1', 'kp_2', 'kp_3']}]},
    {'modules': [{'title': '类型错误', 'point_ids': [1, 2, 3]}]},
    {'modules': [{'title': '改写', 'point_ids': ['kp_1', 'kp_2', 'kp_3'], 'content': '篡改正文'}]},
    {'modules': [{'title': '来源', 'point_ids': ['kp_1', 'kp_2', 'kp_3'], 'source_type': '课堂原话'}]},
    {'modules': [{'title': '额外结构', 'point_ids': ['kp_1', 'kp_2', 'kp_3']}], 'extra': True},
    {'modules': '不是数组'},
    None,
])
def test_invalid_assignments_are_rejected_without_mutating_input(model_mode, points, monkeypatch, result):
    use_transport(monkeypatch, lambda request: model_response(result))
    with pytest.raises(AppError) as error:
        Clusterer().cluster(points, {})
    assert error.value.code == 'CLUSTERING_FAILED'
    assert error.value.retryable is True
    assert [(p.content, p.source_type, p.source_pages) for p in points] == [
        ('索引减少查询扫描', '课堂原话', 'P1,P3'),
        ('事务保证一致性', '来源待核对', 'P2'),
        ('索引减少查询扫描', 'AI推断', 'P2'),
    ]


@pytest.mark.parametrize('body', [
    {'choices': [{'message': {'content': 'not json'}}]},
    {'choices': []},
    {'choices': [{'message': {'content': None}}]},
    {'choices': [{'finish_reason': 'length', 'message': {'content': '{"modules": []}'}}]},
])
def test_invalid_model_envelope_has_clear_error(model_mode, points, monkeypatch, body):
    use_transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(AppError) as error:
        Clusterer().cluster(points, {})
    assert error.value.code == 'CLUSTERING_FAILED'


def test_model_mode_without_key_does_not_silently_use_rules(model_mode, points, monkeypatch):
    monkeypatch.setattr(settings, 'model_api_key', '')

    def no_request(request):
        pytest.fail('Missing key must be rejected before any HTTP request')

    use_transport(monkeypatch, no_request)
    with pytest.raises(AppError) as error:
        Clusterer().cluster(points, {})
    assert error.value.code == 'CLUSTER_CONFIG_ERROR'
    assert error.value.retryable is False


@pytest.mark.parametrize('yaml_text', [None, '', 'prompt: [', 'prompt: 42', 'prompt: null', '[]', 'prompt: ""'])
def test_invalid_prompt_files_report_configuration_error_before_http(model_mode, points, monkeypatch, tmp_path, yaml_text):
    from app import prompts_loader

    monkeypatch.setattr(prompts_loader, 'PROMPTS_DIR', tmp_path)
    monkeypatch.setattr(prompts_loader, '_prompts_cache', {})
    if yaml_text is not None:
        (tmp_path / 'cluster.yaml').write_text(yaml_text, encoding='utf-8')

    def no_request(request):
        pytest.fail('Invalid prompt configuration must fail before calling model')

    use_transport(monkeypatch, no_request)
    with pytest.raises(AppError) as error:
        Clusterer().cluster(points, {})
    assert error.value.code == 'CLUSTER_CONFIG_ERROR'
    assert error.value.retryable is False


@pytest.mark.parametrize('failure,code', [
    (httpx.ReadTimeout, 'MODEL_TIMEOUT'), (httpx.ConnectError, 'CLUSTERING_FAILED'),
])
def test_transport_retries_are_bounded(model_mode, points, monkeypatch, failure, code):
    monkeypatch.setattr(settings, 'model_max_retry', 1)
    requests = []

    def handler(request):
        requests.append(request)
        raise failure('private transport detail', request=request)

    use_transport(monkeypatch, handler)
    with pytest.raises(AppError) as error:
        Clusterer().cluster(points, {})
    assert error.value.code == code
    assert len(requests) == 2
    assert 'private transport detail' not in error.value.message


def test_transient_timeout_can_recover(model_mode, points, monkeypatch):
    monkeypatch.setattr(settings, 'model_max_retry', 1)
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout('temporary', request=request)
        return model_response({'modules': [{'title': '综合', 'point_ids': ['kp_1', 'kp_2', 'kp_3']}]})

    use_transport(monkeypatch, handler)
    assert Clusterer().cluster(points, {})[0].source_pages == 'P1,P2,P3'
    assert len(requests) == 2


@pytest.mark.parametrize('status,attempts', [(401, 1), (429, 2), (503, 2)])
def test_http_failures_are_clear_and_only_transient_errors_retry(model_mode, points, monkeypatch, status, attempts):
    monkeypatch.setattr(settings, 'model_max_retry', 1)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text='private provider response')

    use_transport(monkeypatch, handler)
    with pytest.raises(AppError) as error:
        Clusterer().cluster(points, {})
    assert error.value.code == ('MODEL_AUTH_ERROR' if status == 401 else 'CLUSTERING_FAILED')
    assert len(requests) == attempts
    assert error.value.retryable is (status != 401)
    assert 'private provider response' not in error.value.message


def test_rule_mode_and_empty_input_never_call_model(points, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'cluster_provider', 'rule')

    def no_request(request):
        pytest.fail('Rule mode and empty input must not call the model')

    use_transport(monkeypatch, no_request)
    modules = Clusterer().cluster(points, {1: '概述', 2: '综合'})
    assert [m.title for m in modules] == ['一、概述', '二、综合']
    monkeypatch.setitem(settings.__dict__, 'cluster_provider', 'model')
    assert Clusterer().cluster([], {}) == []


def test_cluster_provider_can_be_configured_independently(monkeypatch):
    monkeypatch.setenv('APP_MODEL_PROVIDER', 'mock')
    monkeypatch.setenv('APP_CLUSTER_PROVIDER', 'model')
    configured = Settings(_env_file=None)
    assert getattr(configured, 'cluster_provider', None) == 'model'
    assert configured.model_provider == 'mock'


def test_model_grouping_survives_save_reopen_and_word_export(client, model_mode, monkeypatch):
    response = client.post('/api/tasks', files=[
        ('files', (f'page{i}.png', b'\x89PNG\r\n\x1a\n', 'image/png')) for i in range(2)
    ])
    task_id = response.json()['task_id']
    with SessionLocal() as db:
        pages = db.query(Page).filter_by(task_id=task_id).order_by(Page.page_no).all()
        for page, title, items in [
            (pages[0], '第一讲', [('索引基础', 'concept', '课堂原话'), ('事务定义', 'concept', '来源待核对')]),
            (pages[1], '第二讲', [('索引优化', 'method', 'AI推断')]),
        ]:
            page.recognized_text = title
            page.recognized_structured = {'title': title, 'points': [
                {'content': text, 'kp_type': kind, 'source_type': source} for text, kind, source in items
            ]}
        db.commit()

    use_transport(monkeypatch, lambda request: model_response({'modules': [
        {'title': '索引专题', 'point_ids': ['kp_1', 'kp_3']},
        {'title': '事务专题', 'point_ids': ['kp_2']},
    ]}))
    result = client.post(f'/api/tasks/{task_id}/pipeline')
    assert result.status_code == 200
    assert result.json()['module_count'] == 2
    expected = '# 课程大纲\n\n## 一、索引专题\n- 索引基础（来源：P1）【课堂原话】\n- 索引优化（来源：P2）【AI推断】\n\n## 二、事务专题\n- 事务定义（来源：P1）【来源待核对】'
    assert client.get(f'/api/tasks/{task_id}/outline').json()['markdown'] == expected
    exported = client.get(f'/api/tasks/{task_id}/export')
    assert exported.status_code == 200
    paragraphs = Document(BytesIO(exported.content)).paragraphs
    assert [p.text for p in paragraphs] == [
        '课程大纲', '一、索引专题', '索引基础（来源：P1） 【课堂原话】',
        '索引优化（来源：P2） 【AI推断】', '二、事务专题', '事务定义（来源：P1） 【来源待核对】',
    ]
    assert str(paragraphs[3].runs[0].font.color.rgb) == '0066CC'
    with SessionLocal() as db:
        nodes = db.query(OutlineNode).filter_by(level=1).order_by(OutlineNode.sort_order).all()
        assert [(n.title, n.source_pages) for n in nodes] == [('一、索引专题', 'P1,P2'), ('二、事务专题', 'P1')]
        before = [(p.id, p.content, p.kp_type, p.source_type, p.source_pages)
                  for p in db.query(KnowledgePoint).filter_by(task_id=task_id).order_by(KnowledgePoint.id)]

    for handler in [
        lambda request: model_response({'modules': [{'title': '遗漏条目', 'point_ids': ['kp_1']}]}),
        lambda request: httpx.Response(503, text='unavailable'),
    ]:
        use_transport(monkeypatch, handler)
        failed = client.post(f'/api/tasks/{task_id}/regenerate')
        assert failed.status_code == 502
        assert failed.json()['code'] == 'CLUSTERING_FAILED'
        assert client.get(f'/api/tasks/{task_id}/outline').json()['markdown'] == expected
        with SessionLocal() as db:
            after = [(p.id, p.content, p.kp_type, p.source_type, p.source_pages)
                     for p in db.query(KnowledgePoint).filter_by(task_id=task_id).order_by(KnowledgePoint.id)]
            assert after == before
