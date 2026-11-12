"""User-visible repairs for flow provenance, expansion facts and model waste."""
import json
from io import BytesIO

import httpx
import pytest
from docx import Document
from PIL import Image

from app.config import settings
from app.errors import AppError
from app.services.extractor import ExtractedKnowledgePoint, Extractor
from app.services.knowledge_expander import KnowledgeExpander
from app.services.model_output import RecognizedPage
from app.services.recognizer import MultimodalRecognizer, RecognitionResult, recognition_is_current
from app.prompts_loader import get_prompt_version


HTTP_CLIENT = httpx.Client
VUE = 'Vue 原生表单 v-model：文本输入 input/textarea、checkbox、radio 和 select 的双向绑定。'
METRICS = '监督学习：概率模型 P(y|x) 与非概率模型 y=f(x)，分类预测及评价指标。'
WRONG_COMPONENT = 'Vue 3 的 v-model 参数配合 modelValue/update:modelValue 改写默认 prop 与事件。'


def point(content, pages='P1', page_id=42):
    return ExtractedKnowledgePoint(content, 'concept', '课堂原话', pages, page_id, content)


def flow_page():
    return {'title': '流程', 'points': [{'content': '可见说明', 'kp_type': 'concept', 'source_type': '课堂原话'}],
            'flows': [{'name': '主流程', 'steps': ['准备', '执行', '检查']}],
            'relations': [{'parent': '准备', 'children': ['执行']},
                          {'parent': '执行', 'children': ['检查']},
                          {'parent': '检查', 'children': ['准备']},
                          {'parent': '容器', 'children': ['数据源']}],
            'confidence': 0.9, 'readability': 'clear', 'uncertain_regions': []}


def response(body):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps(body, ensure_ascii=False)}}]})


@pytest.fixture
def mode(monkeypatch):
    monkeypatch.setattr(settings, 'model_provider', 'multimodal')
    monkeypatch.setattr(settings, 'model_base_url', 'https://model.invalid/v1')
    monkeypatch.setattr(settings, 'model_api_key', 'offline-only')
    monkeypatch.setattr(settings, 'model_name', 'test-model')
    monkeypatch.setattr(settings, 'model_max_retry', 0)
    monkeypatch.setattr(settings, 'knowledge_expansion_enabled', True)
    monkeypatch.setattr(settings, 'cluster_provider', 'rule')


def returned(monkeypatch, contents, ids=None):
    body = {'points': [{'content': content, 'kp_type': 'concept', 'based_on_ids': ids or ['kp_1']}
                       for content in contents]}
    monkeypatch.setattr(httpx, 'Client', lambda **kw: HTTP_CLIENT(
        transport=httpx.MockTransport(lambda request: response(body)), **kw))


def test_flow_cycle_is_preserved_as_uncertain_connections_not_classroom_containment():
    original = flow_page()
    validated = RecognizedPage.model_validate(original)
    assert validated.relations == []
    assert len(validated.unverified_relations) == 4
    assert original['relations'][0]['parent'] == '准备', 'Do not mutate caller evidence'
    extracted = Extractor().extract(42, 2, RecognitionResult('流程', structured=validated.model_dump()))
    assert any(item.content == '主流程：准备 → 执行 → 检查' and item.source_type == '课堂原话' for item in extracted)
    assert any('容器' in item.content and '数据源' in item.content and item.source_type == '来源待核对' for item in extracted)
    assert not any('包含' in item.content and item.source_type == '课堂原话' for item in extracted)
    assert all(item.source_pages == 'P2' for item in extracted)
    again = RecognizedPage.model_validate(validated.model_dump())
    assert again.model_dump() == validated.model_dump(), 'Cache re-read must not multiply pending edges'


def test_flow_without_cycle_still_does_not_assert_source_container_containment():
    raw = flow_page()
    raw['relations'] = [{'parent': '容器', 'children': ['数据源']}]
    validated = RecognizedPage.model_validate(raw)
    rendered = validated.visible_points()
    assert not any(item['source_type'] == '课堂原话' and '容器 包含' in item['content'] for item in rendered)
    assert any(item['source_type'] == '来源待核对' and '数据源' in item['content'] for item in rendered)


def test_graph_review_uses_low_reasoning_and_keeps_real_detail_images(monkeypatch, tmp_path, mode):
    from app.services import model_budget
    monkeypatch.setattr(model_budget, 'BUDGET_PATH', tmp_path / 'budget.json')
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    monkeypatch.setattr(settings, 'diagram_review_max_tokens', 4096)
    raw = flow_page()
    raw['relations'] = []
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return response(raw)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(handle), **kw))
    photo = tmp_path / 'flow.png'
    Image.new('RGB', (40, 40), 'white').save(photo)
    result = MultimodalRecognizer().recognize(1, str(photo))
    assert len(requests) == 2
    assert requests[0]['thinking'] == {'type': 'disabled'}
    assert 'reasoning_effort' not in requests[0]
    assert requests[1]['thinking'] == {'type': 'enabled'}
    assert requests[1]['reasoning_effort'] == 'low'
    assert requests[1]['max_tokens'] == 4096
    assert sum(block['type'] == 'image_url' for block in requests[1]['messages'][1]['content']) == 6
    assert result.structured['_meta']['diagram_review']['performed'] is True
    assert result.structured['_meta']['diagram_review_thinking'] is True
    assert result.structured['_meta']['diagram_review_reasoning_effort'] == 'low'
    assert recognition_is_current(result.structured, 'deepseek')
    result.structured['_meta']['diagram_review_thinking'] = False
    assert not recognition_is_current(result.structured, 'deepseek')


def test_named_vue_component_protocol_is_corrected_in_its_own_topic(mode, monkeypatch):
    returned(monkeypatch, [WRONG_COMPONENT])
    original = point(VUE, 'P1,P3')
    result = KnowledgeExpander().expand([original], {})
    text = '\n'.join(item.content for item in result)
    assert 'v-model:title' in text and 'update:title' in text
    assert '默认' in text and 'modelValue' in text and 'update:modelValue' in text
    assert WRONG_COMPONENT not in text
    assert all(item.source_type == 'AI生成' and item.source_pages == 'P1,P3' for item in result)
    assert original.content == VUE and original.source_type == '课堂原话'


def test_pending_only_graph_is_reviewed_and_requires_review_cache_metadata(mode, monkeypatch, tmp_path):
    raw = flow_page()
    raw['relations'] = []
    raw['flows'] = []
    raw['unverified_relations'] = [{'parent': '容器', 'children': ['数据源']}]
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return response(raw)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(handle), **kw))
    photo = tmp_path / 'pending.png'
    Image.new('RGB', (40, 40), 'white').save(photo)
    result = MultimodalRecognizer().recognize(1, str(photo))
    assert len(requests) == 2, 'Uncertain connectors still require independent visual review'
    assert len(result.structured['unverified_relations']) == 1
    assert recognition_is_current(result.structured, 'multimodal')
    result.structured['_meta'].pop('diagram_review')
    assert not recognition_is_current(result.structured, 'multimodal')


def test_default_vue_component_event_parameter_example_remains_intact(mode, monkeypatch):
    correct = ('Vue 3 组件默认 v-model 使用 modelValue 和 update:modelValue。'
               "emit('update:modelValue', $event.target.value) 的第二个参数是新值。")
    returned(monkeypatch, [correct])
    result = KnowledgeExpander().expand([point('Vue 3 v-model')], {})
    assert [item.content for item in result] == [correct]


def test_native_modifier_contradiction_and_missing_update_timing_are_grounded(mode, monkeypatch):
    wrong = ('checkbox 与 select 的常见误区：多选 select 的 v-model 应绑定数组。'
             'v-model 的修饰符 .lazy 由 input 改为 change，但不改变属性、事件对应关系。')
    returned(monkeypatch, ['radio 使用 checked/change。', wrong])
    result = KnowledgeExpander().expand([point(VUE)], {})
    text = '\n'.join(item.content for item in result)
    assert wrong not in text
    assert 'DOM' in text and 'nextTick' in text and '同步' in text
    assert '多选' in text and '数组' in text and '.lazy' in text
    assert all(item.source_type == 'AI生成' and item.source_pages == 'P1' for item in result)


def test_two_visual_reads_disagreeing_on_parent_are_not_asserted_as_original(mode, monkeypatch, tmp_path):
    initial = flow_page()
    initial['flows'] = []
    initial['relations'] = [{'parent': '根', 'children': ['分支']},
                            {'parent': '分支', 'children': ['叶甲', '叶乙']}]
    reviewed = {**initial, 'relations': [{'parent': '根', 'children': ['分支', '叶甲']},
                                        {'parent': '分支', 'children': ['叶乙']}]}
    responses = iter([initial, reviewed])
    monkeypatch.setattr(httpx, 'Client', lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(
        lambda request: response(next(responses))), **kw))
    photo = tmp_path / 'tree.png'
    Image.new('RGB', (40, 40), 'white').save(photo)
    result = MultimodalRecognizer().recognize(1, str(photo))
    rendered = RecognizedPage.model_validate({k: v for k, v in result.structured.items() if k != '_meta'}).visible_points()
    assert not any(item['source_type'] == '课堂原话' and '叶甲' in item['content'] for item in rendered)
    assert any(item['source_type'] == '来源待核对' and '叶甲' in item['content'] for item in rendered)
    assert any(item['source_type'] == '课堂原话' and '叶乙' in item['content'] for item in rendered)
    assert result.structured['_meta']['diagram_review']['disputed_edge_count'] == 1


def test_evaluation_missing_log_loss_is_completed_without_an_extra_model_request(mode, monkeypatch):
    returned(monkeypatch, ['类别标签可计算准确率、精确率、召回率、F1；非概率模型也能分类。'])
    result = KnowledgeExpander().expand([point(METRICS)], {})
    text = '\n'.join(item.content for item in result)
    assert '对数损失' in text and '不要求' in text and '校准' in text
    assert '二分类' in text and 'ROC-AUC' in text and '分数' in text
    assert '联合' in text and '逻辑回归' in text and '朴素贝叶斯' in text
    assert all(item.source_type == 'AI生成' and item.source_pages == 'P1' for item in result)


def test_evaluation_grounding_does_not_relabel_other_anchor_pages(mode, monkeypatch):
    returned(monkeypatch, ['评估指标与条件概率。'], ['kp_1', 'kp_2'])
    result = KnowledgeExpander().expand([point(METRICS), point('Vue 组件说明', 'P2', 43)], {})
    assert all(item.source_pages == 'P1' for item in result)


def test_saved_flow_outline_and_word_keep_unverified_edges(client, mode, monkeypatch, valid_image_bytes):
    from app.services.pipeline import pipeline
    monkeypatch.setattr(pipeline, 'recognizer', MultimodalRecognizer())
    monkeypatch.setattr(settings, 'knowledge_expansion_enabled', False)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: HTTP_CLIENT(
        transport=httpx.MockTransport(lambda request: response(flow_page())), **kw))
    task = client.post('/api/tasks', files=[('files', ('flow.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    generated = client.post(f'/api/tasks/{task}/pipeline')
    assert generated.status_code == 200, generated.text
    markdown = client.get(f'/api/tasks/{task}/outline').json()['markdown']
    assert '准备 → 执行 → 检查' in markdown and '【来源待核对】' in markdown
    assert '容器 包含：数据源' not in markdown
    document = Document(BytesIO(client.get(f'/api/tasks/{task}/export').content))
    text = '\n'.join(p.text for p in document.paragraphs)
    assert '数据源' in text and '【来源待核对】' in text
