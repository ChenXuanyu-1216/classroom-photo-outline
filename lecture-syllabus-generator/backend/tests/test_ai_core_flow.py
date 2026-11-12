"""Exercise preprocessing/context/parsing through the actual model adapters.

Only external HTTP is replaced. No test sends data to a cloud model.
"""
import base64
import io
import json

import httpx
import pytest
from PIL import Image, PngImagePlugin

from app.config import settings
from app.errors import AppError
from app.services.extractor import ExtractedKnowledgePoint
from app.services.model_clusterer import ModelClusterer
from app.services.recognizer import MultimodalRecognizer


HTTP_CLIENT = httpx.Client
CODE = '    if Value:\n        print("a b")\nP(y|x)'


def page_result(**changes):
    return {'title': 'Vue', 'points': [{'content': CODE, 'kp_type': 'example', 'source_type': '课堂原话'}],
            'confidence': 0.9, 'readability': 'clear', 'uncertain_regions': [], **changes}


def completion(content, finish='stop', **message_changes):
    return {'id': 'offline-test', 'model': 'deepseek-flash', 'choices': [
        {'finish_reason': finish, 'message': {'content': content, **message_changes}}]}


def point(content=CODE, pages='P1,P3', source='课堂原话'):
    return ExtractedKnowledgePoint(content, 'example', source, pages, 1, content)


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    from app.services import model_budget
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    monkeypatch.setattr(settings, 'model_api_key', 'offline-test-key')
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    monkeypatch.setattr(settings, 'model_max_retry', 0)
    ledger = tmp_path / 'budget.json'
    monkeypatch.setattr(model_budget, 'BUDGET_PATH', ledger)
    path = tmp_path / 'source.png'
    Image.new('RGB', (32, 16), 'white').save(path)
    requests = []
    reply = {'body': completion(json.dumps(page_result(), ensure_ascii=False))}

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if 'responder' in reply:
            return httpx.Response(200, json=reply['responder'](payload))
        return httpx.Response(200, content=reply['raw']) if 'raw' in reply else httpx.Response(200, json=reply['body'])

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    return path, ledger, requests, reply


def sent_image(requests):
    url = requests[-1]['messages'][1]['content'][1]['image_url']['url']
    return Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1])))


@pytest.mark.parametrize('kind', ['renamed-text', 'truncated-png', 'mismatched-extension'])
def test_bad_images_stop_before_http_or_budget_reservation(adapter, kind):
    path, ledger, requests, _ = adapter
    if kind == 'renamed-text':
        path.write_bytes(b'<html>not an image</html>')
    elif kind == 'truncated-png':
        path.write_bytes(path.read_bytes()[:-20])
    else:
        renamed = path.with_suffix('.jpg')
        renamed.write_bytes(path.read_bytes())
        path = renamed
    with pytest.raises(AppError) as error:
        MultimodalRecognizer().recognize(1, str(path))
    assert error.value.code == 'FILE_FORMAT_ERROR'
    assert not requests
    assert not ledger.exists()


def test_phone_orientation_is_applied_and_original_file_is_untouched(adapter):
    path, _, requests, _ = adapter
    path = path.with_suffix('.jpg')
    exif = Image.Exif()
    exif[274] = 6
    Image.new('RGB', (32, 16), 'red').save(path, exif=exif)
    original = path.read_bytes()
    result = MultimodalRecognizer().recognize(2, str(path))
    with sent_image(requests) as image:
        assert image.size == (16, 32)
        assert image.getexif().get(274) is None
    assert path.read_bytes() == original
    assert result.structured['_meta']['input']['orientation_corrected'] is True


def test_oversized_image_is_scaled_without_cropping_and_small_images_are_not_enlarged(adapter, monkeypatch):
    path, _, requests, _ = adapter
    monkeypatch.setitem(settings.__dict__, 'image_max_edge', 64)
    source = Image.new('RGB', (128, 64), 'white')
    source.putpixel((0, 0), (255, 0, 0))
    source.putpixel((127, 63), (0, 0, 255))
    source.save(path)
    original = path.read_bytes()
    MultimodalRecognizer().recognize(1, str(path))
    with sent_image(requests) as image:
        assert image.size == (64, 32)
        assert image.getpixel((0, 0))[0] > image.getpixel((0, 0))[2]
        assert image.getpixel((63, 31))[2] > image.getpixel((63, 31))[0]
    assert path.read_bytes() == original
    Image.new('RGB', (8, 4)).save(path)
    MultimodalRecognizer().recognize(1, str(path))
    with sent_image(requests) as image:
        assert image.size == (8, 4)


def test_transparency_and_private_image_metadata_are_removed_from_sent_copy(adapter):
    path, _, requests, _ = adapter
    info = PngImagePlugin.PngInfo()
    info.add_text('private-note', 'must not leave the original file')
    Image.new('RGBA', (32, 16), (0, 0, 0, 0)).save(path, pnginfo=info)
    MultimodalRecognizer().recognize(1, str(path))
    with sent_image(requests) as image:
        assert image.mode == 'RGB'
        assert image.getpixel((0, 0)) == (255, 255, 255)
        assert 'private-note' not in image.info


def test_16bit_gray_png_preserves_distinct_gray_levels_instead_of_whitening_text(adapter):
    path, _, requests, _ = adapter
    raw = b''.join(value.to_bytes(2, 'little') for value in [0, 32768, 65535, 16384])
    Image.frombytes('I;16', (4, 1), raw).save(path)
    MultimodalRecognizer().recognize(1, str(path))
    with sent_image(requests) as image:
        assert [image.getpixel((x, 0)) for x in range(4)] == [(0, 0, 0), (127, 127, 127), (255, 255, 255), (63, 63, 63)]


@pytest.mark.parametrize('values,transparent,expected_gray', [
    ([0, 32767, 32768, 32769, 65535], 32768, [0, 127, 255, 127, 255]),
    ([0, 1, 256, 257, 65535], 0, [255, 0, 0, 1, 255]),
    ([0, 65278, 65534, 65535], 65535, [0, 254, 254, 255]),
])
def test_16bit_transparency_uses_original_values_without_gray_collision(adapter, values, transparent, expected_gray):
    path, _, requests, _ = adapter
    raw = b''.join(value.to_bytes(2, 'little') for value in values)
    Image.frombytes('I;16', (len(values), 1), raw).save(path, transparency=transparent)
    original = path.read_bytes()
    MultimodalRecognizer().recognize(1, str(path))
    with sent_image(requests) as image:
        assert [image.getpixel((x, 0)) for x in range(len(values))] == [(value, value, value) for value in expected_gray]
        assert 'transparency' not in image.info
    assert path.read_bytes() == original


def test_16bit_transparency_stays_aligned_after_scaling(adapter, monkeypatch):
    path, _, requests, _ = adapter
    monkeypatch.setitem(settings.__dict__, 'image_max_edge', 64)
    raw = (b'\x00\x00' * 64 + b'\x00\x80' * 64) * 64
    Image.frombytes('I;16', (128, 64), raw).save(path, transparency=32768)
    MultimodalRecognizer().recognize(1, str(path))
    with sent_image(requests) as image:
        assert image.size == (64, 32)
        assert image.getpixel((5, 16)) == (0, 0, 0)
        assert image.getpixel((60, 16)) == (255, 255, 255)


def test_16bit_transparency_stays_aligned_after_exif_rotation(adapter):
    path, _, requests, _ = adapter
    raw = b''.join(value.to_bytes(2, 'little') for value in [0, 32768, 65535])
    exif = Image.Exif()
    exif[274] = 6
    Image.frombytes('I;16', (3, 1), raw).save(path, transparency=32768, exif=exif)
    MultimodalRecognizer().recognize(1, str(path))
    with sent_image(requests) as image:
        assert image.size == (1, 3)
        assert [image.getpixel((0, y)) for y in range(3)] == [(0, 0, 0), (255, 255, 255), (255, 255, 255)]


def test_decoded_pixel_limit_prevents_a_request_even_for_small_compressed_files(adapter, monkeypatch):
    path, ledger, requests, _ = adapter
    monkeypatch.setitem(settings.__dict__, 'image_max_pixels', 100)
    with pytest.raises(AppError) as error:
        MultimodalRecognizer().recognize(1, str(path))
    assert error.value.code == 'IMAGE_DIMENSIONS_EXCEEDED'
    assert not requests
    assert not ledger.exists()


def test_recognition_context_is_current_page_only_without_local_paths(adapter):
    path, _, requests, _ = adapter
    MultimodalRecognizer().recognize(7, str(path))
    context = json.loads(requests[0]['messages'][1]['content'][0]['text'])
    assert context['page_no'] == 7
    assert context['scope'] == 'current_image_only'
    assert str(path) not in json.dumps(requests[0])


def test_grouping_context_preserves_source_semantics_and_does_not_reuse_another_request(adapter):
    _, _, requests, reply = adapter
    reply['body'] = completion('{"modules":[{"title":"知识框架","point_ids":["kp_1"]}]}')
    clusterer = ModelClusterer()
    first = point()
    result = clusterer.cluster([first], {1: '目录', 3: '代码', 99: 'other task title'})
    supplied = json.loads(requests[-1]['messages'][1]['content'])
    assert supplied['points'][0]['source_type'] == '课堂原话'
    assert supplied['points'][0]['source_pages'] == ['P1', 'P3']
    assert supplied['points'][0]['page_titles'] == ['目录', '代码']
    assert supplied['points'][0]['content'] == CODE
    assert 'other task title' not in json.dumps(supplied)
    assert result[0].knowledge_points[0] is first
    clusterer.cluster([point('另一门课', 'P2', 'AI推断')], {2: '新课'})
    next_context = json.loads(requests[-1]['messages'][1]['content'])
    assert CODE not in json.dumps(next_context, ensure_ascii=False)
    assert next_context['points'][0]['source_type'] == 'AI推断'


@pytest.mark.parametrize('stage', ['recognition', 'grouping'])
def test_oversized_context_is_rejected_without_truncation_or_spending(adapter, monkeypatch, stage):
    path, ledger, requests, reply = adapter
    monkeypatch.setitem(settings.__dict__, 'model_context_max_bytes', 1024)
    reply['body'] = completion('{"modules":[{"title":"too long","point_ids":["kp_1"]}]}') if stage == 'grouping' else reply['body']
    with pytest.raises(AppError) as error:
        if stage == 'recognition':
            MultimodalRecognizer().recognize(1, str(path))
        else:
            ModelClusterer().cluster([point('x' * 5000)], {1: '内容'})
    assert error.value.code == 'MODEL_CONTEXT_TOO_LARGE'
    assert error.value.retryable is False
    assert not requests
    assert not ledger.exists()


@pytest.mark.parametrize('stage', ['recognition', 'grouping'])
def test_single_json_fence_is_accepted_without_changing_code_or_sources(adapter, stage):
    path, _, _, reply = adapter
    body = json.dumps(page_result(), ensure_ascii=False) if stage == 'recognition' else '{"modules":[{"title":"代码","point_ids":["kp_1"]}]}'
    reply['body'] = completion('```json\n' + body + '\n```')
    if stage == 'recognition':
        assert MultimodalRecognizer().recognize(1, str(path)).structured['points'][0]['content'] == CODE
    else:
        result = ModelClusterer().cluster([point()], {})
        assert result[0].knowledge_points[0].content == CODE
        assert result[0].source_pages == 'P1,P3'


@pytest.mark.parametrize('stage', ['recognition', 'grouping'])
def test_duplicate_json_fields_are_rejected_instead_of_last_value_winning(adapter, stage):
    path, _, _, reply = adapter
    content = json.dumps(page_result(), ensure_ascii=False).replace('"title": "Vue"', '"title":"hidden","title":"Vue"') if stage == 'recognition' else '{"modules":[],"modules":[{"title":"代码","point_ids":["kp_1"]}]}'
    reply['body'] = completion(content)
    with pytest.raises(AppError) as error:
        MultimodalRecognizer().recognize(1, str(path)) if stage == 'recognition' else ModelClusterer().cluster([point()], {})
    assert error.value.code == ('RECOGNITION_FAILED' if stage == 'recognition' else 'CLUSTERING_FAILED')


def test_duplicate_envelope_fields_and_refusals_are_not_accepted_as_valid_results(adapter):
    path, _, _, reply = adapter
    valid = json.dumps(completion(json.dumps(page_result())))
    reply['raw'] = '{"choices":[],' + valid[1:]
    with pytest.raises(AppError):
        MultimodalRecognizer().recognize(1, str(path))
    reply.pop('raw')
    reply['body'] = completion(json.dumps(page_result()), refusal='cannot process')
    with pytest.raises(AppError):
        MultimodalRecognizer().recognize(1, str(path))


@pytest.mark.parametrize('finish', ['length', 'content_filter', 'tool_calls', None])
def test_grouping_only_accepts_a_completed_response(adapter, finish):
    _, _, _, reply = adapter
    reply['body'] = completion('{"modules":[{"title":"代码","point_ids":["kp_1"]}]}', finish)
    with pytest.raises(AppError) as error:
        ModelClusterer().cluster([point()], {})
    assert error.value.code == 'CLUSTERING_FAILED'


@pytest.mark.parametrize('changes', [{'title':'Vue\u2028另一个标题'}, {'uncertain_regions':['   ']}, {'uncertain_regions':['x'*1001]}])
def test_invalid_title_or_uncertainty_does_not_enter_saved_knowledge(adapter, changes):
    path, _, _, reply = adapter
    reply['body'] = completion(json.dumps(page_result(**changes), ensure_ascii=False))
    with pytest.raises(AppError) as error:
        MultimodalRecognizer().recognize(1, str(path))
    assert error.value.code == 'RECOGNITION_FAILED'


def test_overflowed_json_numbers_cannot_be_saved_in_response_metadata(adapter):
    path, _, _, reply = adapter
    raw = json.dumps(completion(json.dumps(page_result())))
    reply['raw'] = raw[:-1] + ',"usage":{"prompt_tokens":1e999}}'
    with pytest.raises(AppError) as error:
        MultimodalRecognizer().recognize(1, str(path))
    assert error.value.code == 'RECOGNITION_FAILED'


def test_invalid_unicode_in_legacy_context_stops_with_an_actionable_error(adapter):
    _, ledger, requests, _ = adapter
    with pytest.raises(AppError) as error:
        ModelClusterer().cluster([point('\ud800')], {})
    assert error.value.code == 'MODEL_CONTEXT_INVALID'
    assert not requests
    assert not ledger.exists()


def test_context_limit_counts_utf8_bytes_in_chinese_content(adapter, monkeypatch):
    _, ledger, requests, _ = adapter
    monkeypatch.setitem(settings.__dict__, 'model_context_max_bytes', 5000)
    with pytest.raises(AppError) as error:
        ModelClusterer().cluster([point('汉' * 2000)], {})
    assert error.value.code == 'MODEL_CONTEXT_TOO_LARGE'
    assert not requests
    assert not ledger.exists()


@pytest.mark.parametrize('bad_second_page', [False, True])
def test_real_adapters_integrate_with_saved_sources_partial_failure_and_word(client, adapter, monkeypatch, bad_second_page):
    from docx import Document
    from app.db import SessionLocal
    from app.models import Page
    from app.services.pipeline import pipeline
    path, ledger, requests, reply = adapter
    monkeypatch.setattr(pipeline, 'recognizer', MultimodalRecognizer())
    monkeypatch.setattr(settings, 'cluster_provider', 'model')
    reply['responder'] = lambda payload: completion(json.dumps(page_result(), ensure_ascii=False)) if isinstance(payload['messages'][1]['content'], list) else completion('{"modules":[{"title":"代码与公式","point_ids":["kp_1"]}]}')
    response = client.post('/api/tasks', files=[
        ('files', ('first.png', path.read_bytes(), 'image/png')),
        ('files', ('second.png', b'bad-png' if bad_second_page else path.read_bytes(), 'image/png')),
    ])
    task_id = response.json()['task_id']
    result = client.post(f'/api/tasks/{task_id}/pipeline')
    assert result.status_code == 200
    assert result.json()['status'] == ('partial' if bad_second_page else 'success')
    outline = client.get(f'/api/tasks/{task_id}/outline').json()
    assert outline['recognition_mode'] == 'deepseek'
    assert ('P1,P2' in outline['markdown']) is not bad_second_page
    export = client.get(f'/api/tasks/{task_id}/export')
    assert export.status_code == 200
    assert any(CODE in paragraph.text for paragraph in Document(io.BytesIO(export.content)).paragraphs)
    grouping = json.loads(requests[-1]['messages'][1]['content'])
    assert grouping['points'][0]['source_pages'] == (['P1'] if bad_second_page else ['P1', 'P2'])
    assert json.loads(ledger.read_text())['reserved_fen'] == (20 if bad_second_page else 30)
    with SessionLocal() as db:
        pages = db.query(Page).filter_by(task_id=task_id).order_by(Page.page_no).all()
        assert pages[0].recognized_structured['_meta']['input']['width'] == 32
        if bad_second_page:
            assert pages[1].failed_flag is True
            assert '图片' in pages[1].error_msg


def test_invalid_grouping_never_replaces_an_edited_saved_outline(client, adapter, monkeypatch):
    from app.services.pipeline import pipeline
    path, _, _, reply = adapter
    monkeypatch.setattr(pipeline, 'recognizer', MultimodalRecognizer())
    monkeypatch.setattr(settings, 'cluster_provider', 'model')
    reply['responder'] = lambda payload: completion(json.dumps(page_result())) if isinstance(payload['messages'][1]['content'], list) else completion('{"modules":[{"title":"原模块","point_ids":["kp_1"]}]}')
    task_id = client.post('/api/tasks', files=[('files', ('page.png', path.read_bytes(), 'image/png'))]).json()['task_id']
    assert client.post(f'/api/tasks/{task_id}/pipeline').status_code == 200
    original = client.get(f'/api/tasks/{task_id}/outline').json()
    edited = original['markdown'].replace('原模块', '已人工校对的模块')
    assert client.put(f'/api/tasks/{task_id}/outline', json={'markdown':edited}).status_code == 200
    saved = client.get(f'/api/tasks/{task_id}/outline').json()
    reply.pop('responder')
    reply['body'] = completion('{"modules":[],"modules":[{"title":"错误新模块","point_ids":["kp_1"]}]}')
    result = client.post(f'/api/tasks/{task_id}/regenerate')
    assert result.status_code == 502
    assert result.json()['code'] == 'CLUSTERING_FAILED'
    current = client.get(f'/api/tasks/{task_id}/outline').json()
    assert current['markdown'] == saved['markdown']
    assert current['version'] == saved['version']
