"""Knowledge expansion must add content without inventing classroom provenance."""
import io
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from docx import Document

from app.config import settings
from app.db import SessionLocal
from app.errors import AppError
from app.models import KnowledgePoint, OutlineNode, Page
from app.services.extractor import ExtractedKnowledgePoint
from app.services.outline_service import outline_service


HTTP_CLIENT = httpx.Client
DEFINITION = '机器学习是让计算机从数据中学习规律，并用于预测或决策的方法。'
SUPERVISED = '监督学习：利用带标签的数据学习输入到输出的映射，常见任务包括分类和回归。'
UNSUPERVISED = '无监督学习：从无标签数据中发现结构，常见任务包括聚类和降维。'
REINFORCEMENT = '强化学习：智能体通过与环境交互，根据奖励信号学习决策策略。'


def completion(result, finish='stop'):
    return httpx.Response(200, json={'choices': [{
        'finish_reason': finish, 'message': {'content': json.dumps(result, ensure_ascii=False)},
    }]})


def expansion_result():
    return {'points': [
        {'content': content, 'kp_type': 'concept', 'based_on_ids': ['kp_1']}
        for content in [SUPERVISED, UNSUPERVISED, REINFORCEMENT]
    ]}


@pytest.fixture
def expansion_mode(monkeypatch):
    # Older adapter tests opt out; this feature is on by default in the app.
    monkeypatch.setitem(settings.__dict__, 'knowledge_expansion_enabled', True)
    monkeypatch.setattr(settings, 'model_provider', 'multimodal')
    monkeypatch.setattr(settings, 'model_api_key', 'test-key')
    monkeypatch.setattr(settings, 'model_base_url', 'https://model.invalid/v1')
    monkeypatch.setattr(settings, 'model_name', 'test-model')
    monkeypatch.setattr(settings, 'model_max_retry', 0)
    monkeypatch.setattr(settings, 'cluster_provider', 'rule')


def transport(monkeypatch, handler):
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: HTTP_CLIENT(
        transport=httpx.MockTransport(handler), **kwargs))


def original_point(pages='P1'):
    return ExtractedKnowledgePoint(DEFINITION, 'concept', '课堂原话', pages, 42, DEFINITION)


def test_definition_produces_new_branches_with_server_owned_ai_label(expansion_mode, monkeypatch):
    from app.services.knowledge_expander import KnowledgeExpander
    requests = []
    transport(monkeypatch, lambda request: requests.append(json.loads(request.content)) or completion(expansion_result()))
    original = original_point('P1,P3')
    result = KnowledgeExpander().expand([original], {1: '机器学习定义', 3: '复习', 99: '其他任务的秘密'})
    assert [point.content for point in result] == [SUPERVISED, UNSUPERVISED, REINFORCEMENT]
    assert all(point.source_type == 'AI生成' and point.source_pages == 'P1,P3' and point.page_id == 42 for point in result)
    assert original.content == DEFINITION and original.source_type == '课堂原话'
    supplied = json.loads(requests[0]['messages'][1]['content'])
    assert supplied['scope'] == 'current_task_only'
    assert supplied['points'][0]['content'] == DEFINITION
    assert '其他任务的秘密' not in json.dumps(requests, ensure_ascii=False)
    assert requests[0]['response_format'] == {'type': 'json_object'}


@pytest.mark.parametrize('bad', [
    {'points': []},
    {'points': [{'content': SUPERVISED, 'kp_type': 'concept', 'based_on_ids': ['kp_99']}]},
    {'points': [{'content': SUPERVISED, 'kp_type': 'concept', 'based_on_ids': []}]},
    {'points': [{'content': SUPERVISED, 'kp_type': 'concept', 'based_on_ids': ['kp_1', 'kp_1']}]},
    {'points': [{'content': SUPERVISED, 'kp_type': 'concept', 'based_on_ids': ['kp_1'], 'source_type': '课堂原话'}]},
    {'points': [{'content': SUPERVISED, 'kp_type': 'concept', 'based_on_ids': ['kp_1'], 'source_pages': 'P99'}]},
    {'points': [{'content': '   ', 'kp_type': 'concept', 'based_on_ids': ['kp_1']}]},
    {'points': [{'content': DEFINITION, 'kp_type': 'concept', 'based_on_ids': ['kp_1']}]},
    {'points': [{'content': SUPERVISED, 'kp_type': 'unknown', 'based_on_ids': ['kp_1']}]},
])
def test_invalid_or_only_repeated_expansion_is_rejected(expansion_mode, monkeypatch, bad):
    from app.services.knowledge_expander import KnowledgeExpander
    transport(monkeypatch, lambda request: completion(bad))
    with pytest.raises(AppError) as error:
        KnowledgeExpander().expand([original_point()], {1: '机器学习'})
    assert error.value.code == 'KNOWLEDGE_EXPANSION_FAILED'


def test_disabled_or_demo_mode_never_makes_an_expansion_request(monkeypatch):
    from app.services.knowledge_expander import KnowledgeExpander
    requests = []
    transport(monkeypatch, lambda request: requests.append(request))
    monkeypatch.setattr(settings, 'knowledge_expansion_enabled', True)
    monkeypatch.setattr(settings, 'model_provider', 'mock')
    assert KnowledgeExpander().expand([original_point()], {}) == []
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    monkeypatch.setattr(settings, 'knowledge_expansion_enabled', False)
    assert KnowledgeExpander().expand([original_point()], {}) == []
    assert not requests


def test_expansion_uses_shared_budget_on_every_retry(expansion_mode, tmp_path, monkeypatch):
    from app.services import model_budget
    from app.services.knowledge_expander import KnowledgeExpander
    ledger = tmp_path / 'budget.json'
    monkeypatch.setattr(model_budget, 'BUDGET_PATH', ledger)
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    monkeypatch.setattr(settings, 'model_max_retry', 1)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(503) if len(requests) == 1 else completion(expansion_result())

    transport(monkeypatch, handler)
    assert len(KnowledgeExpander().expand([original_point()], {1: '机器学习'})) == 3
    assert len(requests) == 2
    assert all(request['thinking'] == {'type': 'disabled'} for request in requests)
    assert json.loads(ledger.read_text())['reserved_fen'] == 20
    ledger.write_text('{"reserved_fen":500}')
    with pytest.raises(AppError) as error:
        KnowledgeExpander().expand([original_point()], {1: '机器学习'})
    assert error.value.code == 'TEST_BUDGET_EXHAUSTED'
    assert len(requests) == 2


def test_cross_page_anchors_are_resolved_by_server(expansion_mode, monkeypatch):
    from app.services.knowledge_expander import KnowledgeExpander
    transport(monkeypatch, lambda request: completion({'points': [
        {'content': SUPERVISED, 'kp_type': 'concept', 'based_on_ids': ['kp_2', 'kp_1']},
    ]}))
    second = ExtractedKnowledgePoint('通过数据改善性能', 'concept', '课堂原话', 'P2,P3', 43, '通过数据改善性能')
    point = KnowledgeExpander().expand([original_point('P1,P3'), second], {})[0]
    assert point.source_pages == 'P1,P2,P3'
    assert point.source_type == 'AI生成'


def test_provider_insufficient_balance_is_actionable_and_not_retried(expansion_mode, monkeypatch):
    from app.services.knowledge_expander import KnowledgeExpander
    monkeypatch.setattr(settings, 'model_max_retry', 1)
    requests = []
    transport(monkeypatch, lambda request: requests.append(request) or httpx.Response(402))
    with pytest.raises(AppError) as error:
        KnowledgeExpander().expand([original_point()], {1: '机器学习'})
    assert error.value.code == 'MODEL_BALANCE_ERROR'
    assert '账户余额不足' in error.value.message
    assert error.value.retryable is False
    assert len(requests) == 1


def test_incomplete_or_oversized_expansion_never_enters_outline(expansion_mode, monkeypatch):
    from app.services.knowledge_expander import KnowledgeExpander
    transport(monkeypatch, lambda request: completion(expansion_result(), finish='length'))
    with pytest.raises(AppError) as error:
        KnowledgeExpander().expand([original_point()], {})
    assert error.value.code == 'KNOWLEDGE_EXPANSION_FAILED'
    monkeypatch.setitem(settings.__dict__, 'model_context_max_bytes', 1024)
    requests = []
    transport(monkeypatch, lambda request: requests.append(request))
    with pytest.raises(AppError) as error:
        KnowledgeExpander().expand([original_point()], {})
    assert error.value.code == 'MODEL_CONTEXT_TOO_LARGE'
    assert not requests


def test_pipeline_persists_separate_expansion_and_exports_ai_provenance(client, expansion_mode, monkeypatch, valid_image_bytes):
    from app.services.pipeline import pipeline
    from app.services.recognizer import MultimodalRecognizer
    monkeypatch.setattr(pipeline, 'recognizer', MultimodalRecognizer())
    requests = []
    fail_expansion = False

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if isinstance(payload['messages'][1]['content'], list):
            return completion({'title': '机器学习定义', 'points': [
                {'content': DEFINITION, 'kp_type': 'concept', 'source_type': '课堂原话'},
            ], 'confidence': 0.95, 'readability': 'clear', 'uncertain_regions': []})
        return completion({'points': []} if fail_expansion else expansion_result())

    transport(monkeypatch, handler)
    task = client.post('/api/tasks', files=[('files', ('definition.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    response = client.post(f'/api/tasks/{task}/pipeline')
    assert response.status_code == 200, response.text
    assert response.json()['knowledge_point_count'] == 4
    saved = client.get(f'/api/tasks/{task}/outline').json()['markdown']
    assert f'{DEFINITION}（来源：P1）【课堂原话】' in saved
    assert 'AI 知识拓展' in saved
    assert all(f'{text}（关联课堂页：P1）【AI生成】' in saved for text in [SUPERVISED, UNSUPERVISED, REINFORCEMENT])
    parsed = outline_service.parse_markdown(saved)
    assert parsed[-1]['points'][0] == {'content': SUPERVISED, 'source_pages': 'P1', 'source_type': 'AI生成'}
    with SessionLocal() as db:
        assert db.query(KnowledgePoint).filter_by(task_id=task, source_type='AI生成').count() == 3
        assert db.query(OutlineNode).filter_by(source_type='AI生成').count() == 3
        page = db.query(Page).filter_by(task_id=task).one()
        assert len(page.recognized_structured['points']) == 1
    edited = saved.replace('# 课程大纲', '# 已校对课程大纲')
    assert client.put(f'/api/tasks/{task}/outline', json={'markdown': edited}).status_code == 200
    export = client.get(f'/api/tasks/{task}/export')
    assert export.status_code == 200
    paragraphs = [p.text for p in Document(io.BytesIO(export.content)).paragraphs]
    assert any(SUPERVISED + '（关联课堂页：P1） 【AI生成】' == text for text in paragraphs)
    fail_expansion = True
    failed = client.post(f'/api/tasks/{task}/regenerate')
    assert failed.status_code == 502
    assert failed.json()['code'] == 'KNOWLEDGE_EXPANSION_FAILED'
    assert client.get(f'/api/tasks/{task}/outline').json()['markdown'] == edited
    with SessionLocal() as db:
        assert db.query(KnowledgePoint).filter_by(task_id=task, source_type='AI生成').count() == 3
    # Recognition is reused; saved AI additions must not be fed back as originals.
    assert len(requests) == 3
    assert len(json.loads(requests[-1]['messages'][1]['content'])['points']) == 1


def test_switch_to_compatible_live_provider_refreshes_demo_before_expanding(client, expansion_mode, monkeypatch, valid_image_bytes):
    from app.services.pipeline import pipeline
    from app.services.recognizer import MultimodalRecognizer
    monkeypatch.setattr(settings, 'model_provider', 'mock')
    task = client.post('/api/tasks', files=[('files', ('definition.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    assert client.post(f'/api/tasks/{task}/pipeline').status_code == 200
    assert '关系数据库' in client.get(f'/api/tasks/{task}/outline').json()['markdown']
    monkeypatch.setattr(settings, 'model_provider', 'multimodal')
    monkeypatch.setattr(pipeline, 'recognizer', MultimodalRecognizer())
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if isinstance(payload['messages'][1]['content'], list):
            return completion({'title': '机器学习', 'points': [
                {'content': DEFINITION, 'kp_type': 'concept', 'source_type': '课堂原话'},
            ], 'confidence': 0.95, 'readability': 'clear', 'uncertain_regions': []})
        return completion(expansion_result())

    transport(monkeypatch, handler)
    assert client.post(f'/api/tasks/{task}/regenerate').status_code == 200
    saved = client.get(f'/api/tasks/{task}/outline').json()['markdown']
    assert DEFINITION in saved and '关系数据库' not in saved
    assert len(requests) == 2


def test_literal_source_mentions_in_ai_body_do_not_replace_related_page_metadata():
    markdown = '# 大纲\n## AI 知识拓展\n- 可参考“（来源：教材）”的表述，但仍需核对。（关联课堂页：P1,P3）【AI生成】'
    assert outline_service.parse_markdown(markdown)[0]['points'][0] == {
        'content': '可参考“（来源：教材）”的表述，但仍需核对。',
        'source_pages': 'P1,P3', 'source_type': 'AI生成',
    }


@pytest.mark.parametrize('text', ['无监督学习与强化学习', 'unsupervised learning and reinforcement learning'])
def test_live_probe_does_not_count_unsupervised_as_supervised(text):
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'check_knowledge_expansion.py'
    spec = importlib.util.spec_from_file_location('knowledge_expansion_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.branch_presence(text) == {'supervised': False, 'unsupervised': True, 'reinforcement': True}


VUE_NATIVE = 'Vue 原生表单 v-model：文本 input/textarea、checkbox、radio 和 select 的双向绑定。'
# The contradictory pair was observed in one real completion, not invented by
# the evaluator. A later correct sentence must not excuse the earlier error.
RADIO_WRONG = ('不同控件上 v-model 绑定的属性与监听事件不同：文本 input/textarea 通常绑定 value 并监听 input；'
               'checkbox 使用 checked 与 change；radio 绑定 value 与 change，选中时把该 radio 的 value 写入模型；'
               'select 绑定 value 并监听 change。')
RADIO_CORRECT_LATER = 'radio 的值来自 value 属性，但是否选中对应 checked 属性，交互事件为 change。'
MODIFIERS = '文本输入使用 .lazy 后改在 change 时更新模型，.trim 用于去除首尾空白。'


def vue_point(content=VUE_NATIVE, pages='P1', page_id=42):
    return ExtractedKnowledgePoint(content, 'concept', '课堂原话', pages, page_id, content)


def test_vue_radio_contradiction_is_grounded_once_without_rewriting_classroom(expansion_mode, monkeypatch):
    from app.services.knowledge_expander import KnowledgeExpander
    transport(monkeypatch, lambda request: completion({'points': [
        {'content': text, 'kp_type': 'concept', 'based_on_ids': ['kp_1']}
        for text in [RADIO_WRONG, MODIFIERS, RADIO_CORRECT_LATER]
    ]}))
    original = vue_point(pages='P1,P3')
    result = KnowledgeExpander().expand([original], {1: 'Vue 表单', 3: '复习'})
    radio = [point for point in result if 'radio' in point.content]
    assert len(radio) == 1
    assert 'radio 使用 checked/change' in radio[0].content
    assert 'radio 绑定 value 与 change' not in '\n'.join(p.content for p in result)
    assert 'value 是选项值' in radio[0].content and 'checked 是选中状态' in radio[0].content
    assert 'https://vuejs.org/guide/essentials/forms.html#basic-usage' in radio[0].content
    assert MODIFIERS in [p.content for p in result]
    assert all(p.source_type == 'AI生成' and p.source_pages == 'P1,P3' and p.page_id == 42 for p in result)
    assert original.content == VUE_NATIVE and original.source_type == '课堂原话'


@pytest.mark.parametrize('content', [
    'radio 使用 value/change。',
    'radio绑定 value 与 change，选中时将 value 写入模型。',
    'radio 使用 value/change；自定义组件 radio-picker 使用 modelValue 与 update:modelValue。',
    'radio 使用 value/change，自定义组件 radio-picker 使用 modelValue 与 update:modelValue。',
    '单选按钮的 v-model 使用 value 属性与 change 事件。',
    'RADIO 的 DOM 绑定为 value，监听 change。',
])
def test_vue_native_radio_mapping_cannot_bypass_grounding_by_wording(expansion_mode, monkeypatch, content):
    from app.services.knowledge_expander import KnowledgeExpander
    transport(monkeypatch, lambda request: completion({'points': [
        {'content': content, 'kp_type': 'example', 'based_on_ids': ['kp_1']},
    ]}))
    result = KnowledgeExpander().expand([vue_point()], {})
    assert len(result) == 1 and 'radio 使用 checked/change' in result[0].content
    assert result[0].kp_type == 'concept'


@pytest.mark.parametrize(('anchor', 'addition'), [
    ('Vue 2 自定义组件 RadioPicker 的 v-model 接口。',
     'RadioPicker 自定义组件使用 value prop 和 input 事件作为 v-model 默认协议。'),
    ('Vue 3 自定义组件 radio-picker 的 v-model 接口。',
     'radio-picker 的 v-model 可使用 modelValue 与 update:modelValue。'),
    ('无线电通信 radio 的基本原理。', 'radio 信号可通过调制携带信息。'),
])
def test_vue_radio_grounding_does_not_replace_component_protocols_or_other_topics(expansion_mode, monkeypatch, anchor, addition):
    from app.services.knowledge_expander import KnowledgeExpander
    transport(monkeypatch, lambda request: completion({'points': [
        {'content': addition, 'kp_type': 'example', 'based_on_ids': ['kp_1']},
    ]}))
    result = KnowledgeExpander().expand([vue_point(anchor)], {1: anchor})
    assert [(p.content, p.kp_type) for p in result] == [(addition, 'example')]


def test_vue_radio_grounding_merges_only_valid_related_pages(expansion_mode, monkeypatch):
    from app.services.knowledge_expander import KnowledgeExpander
    transport(monkeypatch, lambda request: completion({'points': [
        {'content': RADIO_WRONG, 'kp_type': 'concept', 'based_on_ids': ['kp_1']},
        {'content': RADIO_CORRECT_LATER, 'kp_type': 'concept', 'based_on_ids': ['kp_2']},
    ]}))
    result = KnowledgeExpander().expand([vue_point(), vue_point(pages='P2', page_id=43)], {})
    assert len(result) == 1 and result[0].source_pages == 'P1,P2'
    assert result[0].source_type == 'AI生成'


def test_native_form_page_title_does_not_override_a_component_only_anchor(expansion_mode, monkeypatch):
    from app.services.knowledge_expander import KnowledgeExpander
    addition = 'radio-picker 自定义组件使用 value prop 和 input 事件作为 v-model 默认协议。'
    transport(monkeypatch, lambda request: completion({'points': [
        {'content': addition, 'kp_type': 'example', 'based_on_ids': ['kp_1']},
    ]}))
    result = KnowledgeExpander().expand([vue_point('Vue 2 自定义组件 radio-picker 的 v-model 接口。')],
                                       {1: 'Vue 原生表单 v-model'})
    assert result[0].content == addition and result[0].kp_type == 'example'


@pytest.mark.parametrize('addition', [
    'radio-picker 自定义组件的 v-model 使用 modelValue 与 update:modelValue。',
    '自定义 radio 组件的 v-model 使用 modelValue 与 update:modelValue。',
    '自定义 radio 组件封装原生 input，对外使用 modelValue 与 update:modelValue。',
])
def test_mixed_native_and_component_anchors_preserve_component_protocol(expansion_mode, monkeypatch, addition):
    from app.services.knowledge_expander import KnowledgeExpander
    transport(monkeypatch, lambda request: completion({'points': [
        {'content': addition, 'kp_type': 'example', 'based_on_ids': ['kp_1', 'kp_2']},
    ]}))
    result = KnowledgeExpander().expand([vue_point(), vue_point('Vue 自定义组件 v-model 协议。')], {})
    assert [(p.content, p.kp_type) for p in result] == [(addition, 'example')]


def test_vue_radio_grounding_cannot_launder_an_invalid_reference(expansion_mode, monkeypatch):
    from app.services.knowledge_expander import KnowledgeExpander
    transport(monkeypatch, lambda request: completion({'points': [
        {'content': RADIO_WRONG, 'kp_type': 'concept', 'based_on_ids': ['kp_1']},
        {'content': RADIO_CORRECT_LATER, 'kp_type': 'concept', 'based_on_ids': ['kp_99']},
    ]}))
    with pytest.raises(AppError) as error:
        KnowledgeExpander().expand([vue_point()], {})
    assert error.value.code == 'KNOWLEDGE_EXPANSION_FAILED'


def test_vue_radio_correction_reaches_saved_outline_and_word(client, expansion_mode, monkeypatch, valid_image_bytes):
    from app.services.pipeline import pipeline
    from app.services.recognizer import MultimodalRecognizer
    monkeypatch.setattr(pipeline, 'recognizer', MultimodalRecognizer())

    def handler(request):
        payload = json.loads(request.content)
        if isinstance(payload['messages'][1]['content'], list):
            return completion({'title': 'Vue 原生表单', 'points': [
                {'content': VUE_NATIVE, 'kp_type': 'concept', 'source_type': '课堂原话'},
            ], 'confidence': 0.95, 'readability': 'clear', 'uncertain_regions': []})
        return completion({'points': [
            {'content': RADIO_WRONG, 'kp_type': 'concept', 'based_on_ids': ['kp_1']},
        ]})

    transport(monkeypatch, handler)
    task_id = client.post('/api/tasks', files=[('files', ('vue.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    response = client.post(f'/api/tasks/{task_id}/pipeline')
    assert response.status_code == 200, response.text
    saved = client.get(f'/api/tasks/{task_id}/outline').json()['markdown']
    assert 'radio 使用 checked/change' in saved and 'radio 绑定 value 与 change' not in saved
    assert f'{VUE_NATIVE}（来源：P1）【课堂原话】' in saved
    with SessionLocal() as db:
        points = db.query(KnowledgePoint).filter_by(task_id=task_id, source_type='AI生成').all()
        assert len(points) == 1 and 'radio 使用 checked/change' in points[0].content
    exported = client.get(f'/api/tasks/{task_id}/export')
    assert exported.status_code == 200
    paragraphs = '\n'.join(p.text for p in Document(io.BytesIO(exported.content)).paragraphs)
    assert 'radio 使用 checked/change' in paragraphs
    assert 'radio 绑定 value 与 change' not in paragraphs
    assert '【AI生成】' in paragraphs and '关联课堂页：P1' in paragraphs
