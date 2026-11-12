import io
import json

import httpx
import pytest
from docx import Document

from app.config import settings
from app.errors import AppError
from app.services.extractor import Extractor
from app.services.recognizer import RecognitionResult
from app.services.clusterer import ClusterModule
from app.services.outline_service import outline_service
from app.services.exporter import exporter


def test_code_case_spaces_and_indentation_cannot_be_deduplicated_away():
    result = RecognitionResult('', structured={'points': [
        {'content': text, 'kp_type': 'example', 'source_type': '课堂原话'}
        for text in ['Value', 'value', '"a b"', '"ab"', 'if x:\n    run()', 'if x:\nrun()', '    run()', 'run()']
    ]})
    points = Extractor().extract(1, 1, result)
    assert len(Extractor().dedup_cross_page(points)) == 8


def test_multiline_code_survives_outline_parsing_and_word_export():
    code = '    if Value:\n        print("a b")\n        # keep this\n        return Value'
    points = Extractor().extract(1, 1, RecognitionResult('', structured={'points': [
        {'content': code, 'kp_type': 'example', 'source_type': '课堂原话'}]}))
    markdown = outline_service.render_markdown([ClusterModule('代码', points, 'P1')])
    parsed = outline_service.parse_markdown(markdown)[0]['points'][0]
    assert parsed['content'] == code
    assert parsed['source_pages'] == 'P1'
    doc = Document(io.BytesIO(exporter.export(markdown)))
    assert any(code in paragraph.text for paragraph in doc.paragraphs)


def test_web_recognition_refuses_exhausted_budget_before_sending(tmp_path, monkeypatch, valid_image_bytes):
    from app.services import model_budget
    from app.services.recognizer import MultimodalRecognizer
    path = tmp_path / 'budget.json'
    path.write_text('{"reserved_fen":500}', encoding='utf-8')
    monkeypatch.setattr(model_budget, 'BUDGET_PATH', path)
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    monkeypatch.setattr(settings, 'model_api_key', 'test-key')
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    image = tmp_path / 'page.png'
    image.write_bytes(valid_image_bytes)
    requests = []
    original_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(
        transport=httpx.MockTransport(lambda request: requests.append(request)), **kwargs))
    with pytest.raises(AppError) as error:
        MultimodalRecognizer().recognize(1, str(image))
    assert error.value.code == 'TEST_BUDGET_EXHAUSTED'
    assert requests == []


def test_deepseek_grouping_disables_thinking_and_shares_budget(tmp_path, monkeypatch):
    from app.services import model_budget
    from app.services.model_clusterer import ModelClusterer
    path = tmp_path / 'budget.json'
    monkeypatch.setattr(model_budget, 'BUDGET_PATH', path)
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    monkeypatch.setattr(settings, 'model_api_key', 'test-key')
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        assert json.loads(path.read_text())['reserved_fen'] == 10
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': '{"modules":[{"title":"概率模型","point_ids":["kp_1"]}]}'}}]})
    original_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    points = Extractor().extract(1, 1, RecognitionResult('', structured={'points': [
        {'content': 'P(y|x)', 'kp_type': 'concept', 'source_type': '课堂原话'}]}))
    modules = ModelClusterer().cluster(points, {1: '模型'})
    assert modules[0].knowledge_points[0].content == 'P(y|x)'
    assert requests[0]['thinking'] == {'type': 'disabled'}


def test_large_request_cannot_exceed_allowance_in_one_attempt(tmp_path):
    from app.services.model_budget import reserve_request
    path = tmp_path / 'budget.json'
    with pytest.raises(AppError) as error:
        reserve_request({'messages':[{'role':'user','content':'x' * 3_000_000}], 'max_tokens':4096}, path)
    assert error.value.code == 'TEST_BUDGET_EXHAUSTED'


def test_outline_reports_uncertain_regions_without_inventing_points(client):
    from app.db import SessionLocal
    from app.models import Page
    task_id = client.post('/api/tasks', files=[('files', ('page.png', b'\x89PNG\r\n\x1a\n' + b'\0'*100, 'image/png'))]).json()['task_id']
    client.post(f'/api/tasks/{task_id}/pipeline')
    with SessionLocal() as db:
        page = db.query(Page).filter(Page.task_id == task_id).first()
        page.low_confidence_flag = True
        page.recognized_structured = {**page.recognized_structured, 'uncertain_regions':['右下角公式不可辨认'], '_meta':{'provider':'deepseek'}}
        db.commit()
    data = client.get(f'/api/tasks/{task_id}/outline').json()
    assert data['warnings'] == ['P1：右下角公式不可辨认']
    assert '右下角公式不可辨认' not in data['markdown']


def test_regeneration_after_switch_to_live_replaces_demo_recognition(client, tmp_path, monkeypatch, valid_image_bytes):
    from app.services import model_budget
    from app.services.pipeline import pipeline
    from app.services.recognizer import MultimodalRecognizer
    task_id = client.post('/api/tasks', files=[('files', ('page.png', valid_image_bytes, 'image/png'))]).json()['task_id']
    assert client.post(f'/api/tasks/{task_id}/pipeline').status_code == 200
    monkeypatch.setattr(model_budget, 'BUDGET_PATH', tmp_path / 'budget.json')
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    monkeypatch.setattr(settings, 'model_api_key', 'test-key')
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    monkeypatch.setattr(pipeline, 'recognizer', MultimodalRecognizer())
    original_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'title':'Vue3', 'points':[{'content':'Vue 数据绑定','kp_type':'concept','source_type':'课堂原话'}],
            'readability':'clear','confidence':0.9,'uncertain_regions':[]},ensure_ascii=False)}}]})), **kwargs))
    assert client.post(f'/api/tasks/{task_id}/regenerate').status_code == 200
    data = client.get(f'/api/tasks/{task_id}/outline').json()
    assert 'Vue 数据绑定' in data['markdown']
    assert '关系数据库' not in data['markdown']
    assert data['recognition_mode'] == 'deepseek'


def test_page_rerecognition_cannot_relabel_saved_demo_outline(client):
    from app.db import SessionLocal
    from app.models import Page
    task_id=client.post('/api/tasks',files=[('files',('page.png',b'\x89PNG\r\n\x1a\n','image/png'))]).json()['task_id']
    client.post(f'/api/tasks/{task_id}/pipeline')
    with SessionLocal() as db:
        page=db.query(Page).filter_by(task_id=task_id).one()
        page.recognized_structured={**page.recognized_structured,'_meta':{'provider':'deepseek'}}
        db.commit()
    assert client.get(f'/api/tasks/{task_id}/outline').json()['recognition_mode'] != 'deepseek'


def test_compatible_provider_cannot_bypass_deepseek_budget(tmp_path, monkeypatch, valid_image_bytes):
    from app.services import model_budget
    from app.services.recognizer import MultimodalRecognizer
    path=tmp_path/'budget.json'
    path.write_text('{"reserved_fen":500}')
    monkeypatch.setattr(model_budget,'BUDGET_PATH',path)
    monkeypatch.setattr(settings,'model_provider','multimodal')
    monkeypatch.setattr(settings,'model_api_key','test-key')
    monkeypatch.setattr(settings,'model_base_url','https://api.deepseek.com')
    monkeypatch.setattr(settings,'model_name','deepseek-flash')
    original_client=httpx.Client
    requests=[]
    monkeypatch.setattr(httpx,'Client',lambda **kwargs: original_client(transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(401)),**kwargs))
    image=tmp_path/'sample.png'; image.write_bytes(valid_image_bytes)
    with pytest.raises(AppError) as error:
        MultimodalRecognizer().recognize(1,str(image))
    assert error.value.code == 'TEST_BUDGET_EXHAUSTED'
    assert requests == []
