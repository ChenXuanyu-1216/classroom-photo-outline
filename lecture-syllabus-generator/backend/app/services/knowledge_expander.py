"""Generate related knowledge; labels and page associations belong to the server."""
from urllib.parse import urlsplit

import httpx
import yaml

from app.config import settings
from app.errors import AppError, ModelTimeoutError, model_response_error
from app.prompts_loader import get_prompt_text
from app.services.extractor import ExtractedKnowledgePoint
from app.services.model_budget import reserve_request, uses_deepseek
from app.services.model_context import grouping_context
from app.services.model_output import KnowledgeExpansion
from app.services.model_response_parser import ModelResponseError, parse_completion
from app.services.model_network import report_transport_failure, wait_before_retry, retry_allowed
from app.services.vue_form_grounding import ground_native_radio_points, ground_component_model_points, ground_native_form_edge_cases
from app.services.evaluation_grounding import ground_evaluation_points


def _failed(message='AI 知识拓展结果无效或不完整，请重试；已有大纲已保留。', retryable=True):
    return AppError('KNOWLEDGE_EXPANSION_FAILED', message, retryable=retryable, status_code=502)


class KnowledgeExpander:
    def expand(self, points: list[ExtractedKnowledgePoint], page_titles: dict[int, str]) -> list[ExtractedKnowledgePoint]:
        if not settings.knowledge_expansion_enabled or settings.model_provider not in {'deepseek', 'multimodal'} or not points:
            return []
        endpoint = settings.model_base_url.strip().rstrip('/')
        try:
            parsed = urlsplit(endpoint)
            if (not settings.model_api_key.strip() or parsed.scheme not in {'https', 'http'}
                    or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError('Invalid model configuration')
            is_deepseek = uses_deepseek(endpoint, settings.model_provider)
            if is_deepseek and (endpoint not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}
                                or settings.model_name != 'deepseek-flash'):
                raise ValueError('Invalid DeepSeek configuration')
            prompt = get_prompt_text('expand')
            if not isinstance(prompt, str) or not prompt.strip() or 'json' not in prompt.lower():
                raise ValueError('Invalid expansion prompt')
        except (OSError, UnicodeError, yaml.YAMLError, AttributeError, TypeError, ValueError):
            raise AppError('EXPANSION_CONFIG_ERROR', '知识拓展配置无效，请检查模型连接、密钥及 expand.yaml。', status_code=503) from None

        point_map = {f'kp_{i}': point for i, point in enumerate(points, 1)}
        payload = {
            'model': settings.model_name,
            'messages': grouping_context(prompt, point_map, page_titles),
            'response_format': {'type': 'json_object'},
            'max_tokens': settings.model_max_tokens,
        }
        if is_deepseek:
            payload['thinking'] = {'type': 'disabled'}
        response = self._request(endpoint, payload, is_deepseek)
        try:
            expansion, _ = parse_completion(response.content, KnowledgeExpansion)
        except ModelResponseError as exc:
            raise model_response_error(exc.kind, 'expansion') from None
        try:
            # Reject the entire response before reference-backed consolidation;
            # an invalid later item must never be hidden by de-duplication.
            for point in expansion.points:
                ids = point.based_on_ids
                if len(ids) != len(set(ids)) or any(point_id not in point_map for point_id in ids):
                    raise ValueError('Invalid knowledge references')
            seen = {point.content.strip() for point in points}
            result = []
            additions = ground_native_radio_points(expansion.points, point_map, page_titles)
            additions = ground_component_model_points(additions, point_map)
            additions = ground_native_form_edge_cases(additions, point_map, page_titles)
            additions = ground_evaluation_points(additions, point_map)
            for point in additions:
                ids = point.based_on_ids
                if point.content.strip() in seen:
                    continue
                seen.add(point.content.strip())
                anchors = [point_map[point_id] for point_id in ids]
                pages = {page for anchor in anchors for page in anchor.source_pages.split(',')}
                result.append(ExtractedKnowledgePoint(
                    content=point.content, kp_type=point.kp_type, source_type='AI生成',
                    source_pages=','.join(sorted(pages, key=lambda page: int(page[1:]))),
                    page_id=anchors[0].page_id, dedup_key=point.content,
                ))
            if not result:
                raise ValueError('No new knowledge')
            return result
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise _failed() from None

    def _request(self, endpoint: str, payload: dict, is_deepseek: bool) -> httpx.Response:
        for attempt in range(max(0, settings.model_max_retry) + 1):
            if is_deepseek or settings.deployment_mode == 'cloud':
                reserve_request(payload)
            try:
                with httpx.Client(timeout=settings.model_timeout_seconds, trust_env=settings.model_trust_env) as client:
                    response = client.post(f'{endpoint}/chat/completions', json=payload,
                                           headers={'Authorization': f'Bearer {settings.model_api_key.strip()}'})
                    response.raise_for_status()
                    return response
            except httpx.TimeoutException as exc:
                report_transport_failure('expansion', exc)
                if retry_allowed(attempt, settings.model_max_retry) and wait_before_retry(attempt):
                    continue
                raise ModelTimeoutError() from None
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 401:
                    raise AppError('MODEL_AUTH_ERROR', '模型密钥认证失败，请检查配置。', status_code=503) from None
                if status == 402:
                    raise AppError('MODEL_BALANCE_ERROR', '模型账户余额不足，请检查官方账户余额。', status_code=503) from None
                retryable = status in {408, 429} or status >= 500
                if retryable and retry_allowed(attempt, settings.model_max_retry) and wait_before_retry(attempt, exc.response):
                    continue
                raise _failed(f'知识拓展服务返回 HTTP {status}，请检查配置或稍后重试。', retryable) from None
            except httpx.TransportError as exc:
                report_transport_failure('expansion', exc)
                if retry_allowed(attempt, settings.model_max_retry) and wait_before_retry(attempt):
                    continue
                raise _failed('无法连接知识拓展服务，请检查网络后重试。') from None
            except httpx.InvalidURL:
                raise AppError('EXPANSION_CONFIG_ERROR', '模型服务地址无效，请检查 APP_MODEL_BASE_URL。', status_code=503) from None
        raise _failed()
