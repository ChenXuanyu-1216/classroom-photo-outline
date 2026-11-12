"""Regression tests for preserving visible graph structure in saved knowledge."""
import pytest
from pydantic import ValidationError

from app.services.extractor import Extractor
from app.services.model_output import RecognizedPage
from app.services.recognizer import RecognitionResult, MultimodalRecognizer
from app.config import settings
import httpx
import json


def page(**changes):
    return {'title': '图示', 'points': [], 'confidence': 0.9,
            'readability': 'clear', 'uncertain_regions': [], **changes}


def test_mixed_diagram_keeps_labels_and_flow_without_asserting_untyped_containment():
    structured = page(
        relations=[{'parent': '决策树', 'children': ['ID3', 'C4.5', 'CART']},
                   {'parent': 'CART', 'children': ['分类和回归树']},
                   {'parent': '人工神经网络', 'children': ['感知机', '梯度下降', '深度学习']}],
        flows=[{'name': '主流程', 'steps': ['数据收集', '数据清洗', '特征工程', '数据建模', '部署评估']}],
        uncertain_regions=['CART 下英文断行无法完整辨认'])
    validated = RecognizedPage.model_validate(structured)
    result = RecognitionResult('图示', structured=validated.model_dump())
    points = Extractor().extract(42, 2, result)
    by_content = {point.content: point for point in points}
    assert any('CART' in text and '分类和回归树' in text and item.source_type == '来源待核对'
               for text, item in by_content.items())
    assert any('人工神经网络' in text and '深度学习' in text and item.source_type == '来源待核对'
               for text, item in by_content.items())
    chain = '主流程：数据收集 → 数据清洗 → 特征工程 → 数据建模 → 部署评估'
    assert by_content[chain].source_type == '课堂原话'
    assert by_content[chain].source_pages == 'P2'
    warning = by_content['待核对：CART 下英文断行无法完整辨认']
    assert warning.source_type == '来源待核对'
    assert all(point.page_id == 42 for point in points)
    assert not any('人工神经网络' in point.content and '分类和回归树' in point.content for point in points)


def test_pure_tree_extraction_keeps_confirmed_parent_child_relations():
    validated = RecognizedPage.model_validate(page(relations=[
        {'parent': '决策树', 'children': ['CART']},
        {'parent': 'CART', 'children': ['分类和回归树']}]))
    points = Extractor().extract(42, 2, RecognitionResult('树图', structured=validated.model_dump()))
    assert any(p.content == 'CART 包含：分类和回归树' and p.source_type == '课堂原话' for p in points)
    assert validated.unverified_relations == []


@pytest.mark.parametrize('changes', [
    {'relations': [{'parent': 'A', 'children': ['A']}]},
    {'relations': [{'parent': 'A', 'children': ['B', 'B']}]},
    {'relations': [{'parent': 'A', 'children': ['B']}, {'parent': 'B', 'children': ['A']}]},
    {'flows': [{'name': '主流程', 'steps': ['A']}]},
    {'flows': [{'name': '主流程', 'steps': ['A', 'B', 'A']}]},
    {'relations': [{'parent': ' ', 'children': ['B']}]},
])
def test_inconsistent_diagram_structure_is_rejected(changes):
    with pytest.raises(ValidationError):
        RecognizedPage.model_validate(page(**changes))


def test_plain_legacy_recognition_remains_valid_and_does_not_invent_graphs():
    validated = RecognizedPage.model_validate(page(points=[{
        'content': '原文', 'kp_type': 'concept', 'source_type': '课堂原话'}]))
    extracted = Extractor().extract(1, 1, RecognitionResult('标题\n原文', structured=validated.model_dump()))
    assert [point.content for point in extracted] == ['原文']


def test_multiline_visual_labels_are_preserved_and_share_graph_identity():
    validated = RecognizedPage.model_validate(page(relations=[
        {'parent': '主题', 'children': ['缩写A\nEnglish name']},
        {'parent': '缩写A English name', 'children': ['中文解释\nEnglish gloss']}]))
    extracted = Extractor().extract(1, 1, RecognitionResult('图', structured=validated.model_dump()))
    assert any('中文解释\nEnglish gloss' in point.content for point in extracted)
    with pytest.raises(ValidationError):
        RecognizedPage.model_validate(page(relations=[
            {'parent': 'A\nB', 'children': ['C']}, {'parent': 'C', 'children': ['A B']}]))


def test_diagram_only_completion_is_readable_and_exposes_rendered_text(monkeypatch, tmp_path):
    from PIL import Image
    image = tmp_path / 'flow.png'
    Image.new('RGB', (24, 24), 'white').save(image)
    from app.services import model_budget
    monkeypatch.setattr(model_budget, 'BUDGET_PATH', tmp_path / 'budget.json')
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    monkeypatch.setattr(settings, 'model_api_key', 'offline')
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    monkeypatch.setattr(settings, 'diagram_review_max_tokens', 8192, raising=False)
    body = page(flows=[{'name': '主流程', 'steps': ['收集', '清洗', '建模']}])
    original_client = httpx.Client
    requests = []
    def response(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(body, ensure_ascii=False)}}]})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(
        transport=httpx.MockTransport(response), **kwargs))
    result = MultimodalRecognizer().recognize(1, str(image))
    assert '收集 → 清洗 → 建模' in result.text
    assert result.structured['_meta']['output_schema_version'] == '1.4.0'
    assert len(requests) == 2
    assert requests[0]['thinking'] == {'type': 'disabled'}
    assert requests[1]['thinking'] == {'type': 'enabled'}
    assert requests[1]['reasoning_effort'] == 'low'
    assert requests[1]['max_tokens'] == 8192
    detail_blocks = requests[1]['messages'][1]['content']
    assert sum(block['type'] == 'image_url' for block in detail_blocks) == 6
    assert result.structured['_meta']['diagram_review']['performed'] is True


@pytest.mark.parametrize('pending_only', [False, True])
def test_diagram_only_cached_page_retains_source_and_warning_through_word(client, valid_image_bytes, pending_only):
    from app.db import SessionLocal
    from app.models import Page
    from docx import Document
    from io import BytesIO
    task = client.post('/api/tasks', files=[('files', ('flow.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    structured = page(flows=[{'name': '主流程', 'steps': ['收集', '清洗', '建模']}],
                      uncertain_regions=['反馈虚线终点无法辨认'])
    expected = '主流程：收集 → 清洗 → 建模（来源：P1）【课堂原话】'
    if pending_only:
        structured = page(unverified_relations=[{'parent': '容器', 'children': ['数据源']}],
                          uncertain_regions=['反馈虚线终点无法辨认'])
        expected = '图示关联待核对：容器；相关标注：数据源（关系类型与方向未确认）（来源：P1）【来源待核对】'
    with SessionLocal() as db:
        saved_page = db.query(Page).filter_by(task_id=task).one()
        saved_page.recognized_structured = structured
        saved_page.recognized_text = '图示\n' + '\n'.join(
            p['content'] for p in RecognizedPage.model_validate(structured).visible_points())
        saved_page.confidence = 0.5
        db.commit()
    generated = client.post(f'/api/tasks/{task}/pipeline')
    assert generated.status_code == 200, generated.text
    markdown = client.get(f'/api/tasks/{task}/outline').json()['markdown']
    assert expected in markdown
    assert '待核对：反馈虚线终点无法辨认（来源：P1）【来源待核对】' in markdown
    paragraphs = [paragraph.text for paragraph in Document(BytesIO(client.get(f'/api/tasks/{task}/export').content)).paragraphs]
    assert any('反馈虚线终点无法辨认' in text and '来源待核对' in text for text in paragraphs)
