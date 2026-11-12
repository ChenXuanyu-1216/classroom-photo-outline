"""Model assigns request-local IDs; original knowledge points stay authoritative."""

import httpx
import yaml

from app.config import settings
from app.errors import AppError, ClusteringFailedError, ModelTimeoutError, model_response_error
from app.prompts_loader import get_prompt_text
from app.services.clusterer import ClusterModule, numbered_title
from app.services.extractor import ExtractedKnowledgePoint
from app.services.model_budget import reserve_request, uses_deepseek
from app.services.model_context import grouping_context
from app.services.model_output import ClusterAssignment
from app.services.model_response_parser import ModelResponseError, parse_completion
from app.services.model_network import report_transport_failure, wait_before_retry, retry_allowed


class ModelClusterer:
    def cluster(self, points: list[ExtractedKnowledgePoint], page_titles: dict[int, str]) -> list[ClusterModule]:
        if not points:
            return []
        if not settings.model_api_key.strip():
            raise AppError(
                "CLUSTER_CONFIG_ERROR", "模型聚类需要配置 APP_MODEL_API_KEY，或将 APP_CLUSTER_PROVIDER 改为 rule。",
                retryable=False, status_code=503,
            )
        try:
            prompt = get_prompt_text("cluster")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("Prompt must be a nonempty string")
        except (OSError, UnicodeError, yaml.YAMLError, AttributeError, TypeError, ValueError):
            raise AppError(
                "CLUSTER_CONFIG_ERROR", "聚类提示词无效或无法读取，请检查 cluster.yaml。", status_code=503,
            ) from None

        point_map = {f"kp_{i}": point for i, point in enumerate(points, 1)}
        payload = {
            "model": settings.model_name,
            "messages": grouping_context(prompt, point_map, page_titles),
            "response_format": {"type": "json_object"},
            "max_tokens": 4000,
        }
        if uses_deepseek(settings.model_base_url, settings.model_provider):
            payload['thinking'] = {'type': 'disabled'}
        response = self._request(payload)
        try:
            assignment, _ = parse_completion(response.content, ClusterAssignment)
        except ModelResponseError as exc:
            raise model_response_error(exc.kind, 'grouping') from None

        assigned = [point_id for module in assignment.modules for point_id in module.point_ids]
        if len(assigned) != len(set(assigned)) or set(assigned) != set(point_map):
            raise ClusteringFailedError("模型聚类包含未知、重复或遗漏的知识点 ID，请重试。")

        modules = []
        for index, module in enumerate(assignment.modules, 1):
            original_points = [point_map[point_id] for point_id in module.point_ids]
            pages = {page for point in original_points for page in point.source_pages.split(",")}
            modules.append(ClusterModule(
                title=numbered_title(module.title, index),
                knowledge_points=original_points,
                source_pages=",".join(sorted(pages, key=lambda page: int(page[1:]))),
            ))
        return modules

    def _request(self, payload: dict) -> httpx.Response:
        # Retry transport failures and transient statuses only; never hide errors
        # behind rule-based output or expose provider response bodies to users.
        for attempt in range(max(0, settings.model_max_retry) + 1):
            if uses_deepseek(settings.model_base_url, settings.model_provider):
                if settings.model_base_url.strip().rstrip('/') not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'} or settings.model_name != 'deepseek-flash':
                    raise AppError('CLUSTER_CONFIG_ERROR', '请使用 DeepSeek 官方地址和 deepseek-flash 模型。', status_code=503)
            if uses_deepseek(settings.model_base_url, settings.model_provider) or settings.deployment_mode == 'cloud':
                reserve_request(payload)
            try:
                with httpx.Client(timeout=settings.model_timeout_seconds, trust_env=settings.model_trust_env) as client:
                    response = client.post(
                        f"{settings.model_base_url.strip().rstrip('/')}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {settings.model_api_key.strip()}"},
                    )
                    response.raise_for_status()
                    return response
            except httpx.TimeoutException as exc:
                report_transport_failure('grouping', exc)
                if retry_allowed(attempt, settings.model_max_retry) and wait_before_retry(attempt):
                    continue
                raise ModelTimeoutError() from None
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in {401, 402}:
                    raise AppError('MODEL_AUTH_ERROR' if status == 401 else 'MODEL_BALANCE_ERROR',
                                   '模型密钥认证失败，请检查配置。' if status == 401 else '模型账户余额不足，请检查官方账户余额。',
                                   status_code=503) from None
                retryable = status in {408, 429} or status >= 500
                if retryable and retry_allowed(attempt, settings.model_max_retry) and wait_before_retry(attempt, exc.response):
                    continue
                raise ClusteringFailedError(
                    f"模型聚类服务返回 HTTP {status}，请检查模型配置或稍后重试。", retryable=retryable,
                ) from None
            except httpx.TransportError as exc:
                report_transport_failure('grouping', exc)
                if retry_allowed(attempt, settings.model_max_retry) and wait_before_retry(attempt):
                    continue
                raise ClusteringFailedError("无法连接模型聚类服务，请检查连接后重试。") from None
            except httpx.InvalidURL:
                raise AppError("CLUSTER_CONFIG_ERROR", "模型服务地址无效，请检查 APP_MODEL_BASE_URL。", status_code=503) from None
        raise ClusteringFailedError()
