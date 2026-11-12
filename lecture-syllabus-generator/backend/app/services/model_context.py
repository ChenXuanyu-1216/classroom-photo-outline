"""Request-local contexts, explicit source semantics and fail-closed size limits."""
import json
import re

from app.config import settings
from app.errors import AppError


def _bounded(messages: list[dict]) -> list[dict]:
    size = 0
    for message in messages:
        content = message['content']
        texts = [content] if isinstance(content, str) else [block['text'] for block in content if block['type'] == 'text']
        try:
            size += sum(len(text.encode('utf-8')) for text in texts)
        except UnicodeError:
            raise AppError('MODEL_CONTEXT_INVALID', '上下文含无效字符，请核对或重新识别相关页面。') from None
    if size > settings.model_context_max_bytes:
        raise AppError('MODEL_CONTEXT_TOO_LARGE', '本次 AI 上下文过长，请减少照片数量或拆成多个任务；内容未被截断。', status_code=413)
    return messages


def recognition_context(prompt: str, page_no: int, image_url: str, *, detail_images=()) -> list[dict]:
    if type(page_no) is not int or page_no < 1:
        raise AppError('MODEL_CONTEXT_INVALID', '识别页码无效，请检查任务。')
    context = {'version': '1.0.0', 'page_no': page_no, 'scope': 'current_image_only', 'output_format': 'JSON'}
    details = []
    for label, url in detail_images:
        details.extend([{'type': 'text', 'text': f'同一张图的{label}重叠细节，必须结合第一张全图追踪连线。'},
                        {'type': 'image_url', 'image_url': {'url': url}}])
    return _bounded([
        {'role': 'system', 'content': prompt},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': json.dumps(context, ensure_ascii=False)},
            {'type': 'image_url', 'image_url': {'url': image_url}},
            *details,
        ]},
    ])


def grouping_context(prompt: str, point_map: dict, page_titles: dict[int, str]) -> list[dict]:
    supplied = []
    for point_id, point in point_map.items():
        pages = point.source_pages.split(',')
        if not pages or any(not re.fullmatch(r'P[1-9][0-9]*', page) for page in pages):
            raise AppError('MODEL_CONTEXT_INVALID', '知识点来源页码无效，请重新识别相关页面。')
        pages = sorted(set(pages), key=lambda page: int(page[1:]))
        supplied.append({
            'id': point_id, 'content': point.content, 'kp_type': point.kp_type,
            'source_type': point.source_type, 'source_pages': pages,
            'page_titles': [page_titles.get(int(page[1:]), page) for page in pages],
        })
    return _bounded([
        {'role': 'system', 'content': prompt},
        {'role': 'user', 'content': json.dumps({'version': '1.0.0', 'scope': 'current_task_only', 'points': supplied}, ensure_ascii=False, allow_nan=False)},
    ])
