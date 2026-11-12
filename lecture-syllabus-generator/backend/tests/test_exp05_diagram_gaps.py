"""Offline regressions for diagram omissions identified in EXP05 feedback."""
import json
from io import BytesIO

import httpx
import pytest
from docx import Document

from app.config import settings
from app.prompts_loader import get_prompt_version
from app.services.model_output import RecognizedPage
from app.services.recognizer import MultimodalRecognizer, recognition_is_current


HTTP_CLIENT = httpx.Client


def diagram(**changes):
    return {
        'title': '条件循环', 'points': [], 'has_diagram': True,
        'relations': [], 'flows': [], 'confidence': 0.95,
        'readability': 'clear', 'uncertain_regions': [], **changes,
    }


def branching_flow():
    return {'name': 'While', 'steps': ['条件', '执行', '结束'], 'edges': [
        {'source': '条件', 'target': '执行', 'label': '真'},
        {'source': '条件', 'target': '结束', 'label': '假'},
        {'source': '执行', 'target': '条件', 'label': '回到判断'},
    ]}


@pytest.fixture
def visual_model(monkeypatch):
    monkeypatch.setattr(settings, 'model_provider', 'multimodal')
    monkeypatch.setattr(settings, 'model_api_key', 'offline-only')
    monkeypatch.setattr(settings, 'model_base_url', 'https://model.invalid/v1')
    monkeypatch.setattr(settings, 'model_name', 'offline-model')
    monkeypatch.setattr(settings, 'model_max_retry', 0)
    monkeypatch.setattr(settings, 'knowledge_expansion_enabled', False)
    monkeypatch.setattr(settings, 'cluster_provider', 'rule')


def mock_responses(monkeypatch, bodies):
    responses = iter(bodies)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(next(responses), ensure_ascii=False)}}]})

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: HTTP_CLIENT(
        transport=httpx.MockTransport(respond), **kwargs))
    return requests


def test_diagram_presence_triggers_detail_review_even_when_initial_edges_are_missing(
        visual_model, monkeypatch, tmp_path, valid_image_bytes):
    initial = diagram(points=[{'content': 'While', 'kp_type': 'concept', 'source_type': '课堂原话'}])
    requests = mock_responses(monkeypatch, [initial, diagram(flows=[branching_flow()])])
    photo = tmp_path / 'loop.png'
    photo.write_bytes(valid_image_bytes)
    result = MultimodalRecognizer().recognize(1, str(photo))
    assert len(requests) == 2
    assert result.structured['_meta']['diagram_review']['performed'] is True
    assert result.structured['flows'][0]['edges'][2]['target'] == '条件'


def test_detected_diagram_without_any_recovered_structure_fails_explicitly(
        visual_model, monkeypatch, tmp_path, valid_image_bytes):
    from app.errors import RecognitionFailedError
    only_name = diagram(points=[{'content': 'While', 'kp_type': 'concept', 'source_type': '课堂原话'}])
    mock_responses(monkeypatch, [only_name, only_name])
    photo = tmp_path / 'missing.png'
    photo.write_bytes(valid_image_bytes)
    with pytest.raises(RecognitionFailedError) as error:
        MultimodalRecognizer().recognize(1, str(photo))
    assert error.value.reason_code == 'MODEL_OUTPUT_INVALID'


def test_diagram_with_only_a_title_can_recover_from_detail_images(
        visual_model, monkeypatch, tmp_path, valid_image_bytes):
    requests = mock_responses(monkeypatch, [diagram(), diagram(flows=[branching_flow()])])
    photo = tmp_path / 'title-only.png'
    photo.write_bytes(valid_image_bytes)
    result = MultimodalRecognizer().recognize(1, str(photo))
    assert len(requests) == 2
    assert '回到判断' in result.text


def test_declared_diagram_cache_requires_review_even_without_edges():
    cached = diagram(points=[{'content': 'While', 'kp_type': 'concept', 'source_type': '课堂原话'}])
    cached['_meta'] = {'provider': 'multimodal', 'prompt_version': get_prompt_version('recognize'),
                       'output_schema_version': '1.4.0'}
    assert not recognition_is_current(cached, 'multimodal')


def test_branch_conditions_and_loop_back_edge_survive_saved_outline_and_word(valid_image_bytes, tmp_path):
    from app.db import SessionLocal
    from app.models import Page, Task, Outline
    from app.services.pipeline import Pipeline
    from app.services.exporter import exporter
    body = diagram(flows=[branching_flow()])
    body['flows'][0]['edges'][2]['source_type'] = '来源待核对'
    photo = tmp_path / 'loop.png'
    photo.write_bytes(valid_image_bytes)
    # Store the model-shaped result, rather than manually writing the final outline.
    with SessionLocal() as db:
        task = Task(title='条件循环', page_count=1)
        db.add(task)
        db.flush()
        saved = Page(task_id=task.id, page_no=1, filename=photo.name, file_path=str(photo),
                     recognized_structured=RecognizedPage.model_validate(body).model_dump(),
                     recognized_text='条件循环')
        db.add(saved)
        db.commit()
        result = Pipeline().run_pipeline(db, task.id)
        assert result['status'] == 'success'
        markdown = db.query(Outline).filter_by(task_id=task.id).one().markdown
    document = Document(BytesIO(exporter.export(markdown)))
    word_text = '\n'.join(paragraph.text for paragraph in document.paragraphs)
    for text in [markdown, word_text]:
        assert '条件 —[真]→ 执行' in text
        assert '条件 —[假]→ 结束' in text
        assert '执行 —[回到判断]→ 条件' in text
        loop_line = next(line for line in text.splitlines() if '执行 —[回到判断]→ 条件' in line)
        assert '（来源：P1）' in loop_line and '【来源待核对】' in loop_line
        assert '包含' not in text
        assert '条件 → 执行 → 结束' not in text, 'A branch is not a linear chain'


def test_unverified_edge_lowers_confidence(visual_model, monkeypatch, tmp_path, valid_image_bytes):
    flow = branching_flow()
    flow['edges'][2]['source_type'] = '来源待核对'
    mock_responses(monkeypatch, [diagram(flows=[flow]), diagram(flows=[flow])])
    photo = tmp_path / 'uncertain.png'
    photo.write_bytes(valid_image_bytes)
    result = MultimodalRecognizer().recognize(1, str(photo))
    assert result.low_confidence is True
    assert result.confidence <= 0.5


def test_plain_page_does_not_trigger_paid_diagram_review(
        visual_model, monkeypatch, tmp_path, valid_image_bytes):
    requests = mock_responses(monkeypatch, [diagram(has_diagram=False, points=[{
        'content': '正文', 'kp_type': 'concept', 'source_type': '课堂原话'}])])
    photo = tmp_path / 'text.png'
    photo.write_bytes(valid_image_bytes)
    result = MultimodalRecognizer().recognize(1, str(photo))
    assert len(requests) == 1
    assert result.structured['_meta'].get('diagram_review') is None
