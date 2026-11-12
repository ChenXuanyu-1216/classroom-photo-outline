from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
import re
from urllib.parse import urlsplit
import httpx
import yaml
from app.config import settings
from app.errors import AppError, ModelTimeoutError, RecognitionFailedError, model_response_error
from app.prompts_loader import get_prompt_text, get_prompt_version
from app.services.model_budget import reserve_request, uses_deepseek
from app.services.image_preprocessor import prepare_image, diagram_detail_images
from app.services.model_context import recognition_context
from app.services.model_output import RecognizedPage
from app.services.model_response_parser import ModelResponseError, parse_completion
from app.services.model_network import report_transport_failure, wait_before_retry, retry_allowed


@dataclass
class RecognitionPoint:
    content: str
    kp_type: str = "concept"
    source_type: str = "课堂原话"


@dataclass
class RecognitionResult:
    text: str
    confidence: float = 0.95
    low_confidence: bool = False
    structured: dict = field(default_factory=dict)


def recognition_is_current(structured: dict | None, provider: str) -> bool:
    body = structured or {}
    meta = body.get('_meta') or {}
    if (meta.get('provider') != provider or meta.get('prompt_version') != get_prompt_version('recognize')
            or meta.get('output_schema_version') != '1.4.0'):
        return False
    if body.get('has_diagram') or body.get('relations') or body.get('flows') or body.get('unverified_relations'):
        return (meta.get('diagram_review', {}).get('performed') is True
                and meta.get('diagram_review_prompt_version') == get_prompt_version('review_diagram')
                and (provider != 'deepseek' or (meta.get('diagram_review_thinking') is True
                     and meta.get('diagram_review_reasoning_effort') == 'low'))
                and meta.get('diagram_review_max_tokens') == settings.diagram_review_max_tokens)
    return True


def _diagram_label_key(label):
    key = re.sub(r'\s+', '', label).casefold()
    # A Chinese label may carry an English gloss in one read only. Do not
    # merge alphanumeric labels such as L1/L2 or distinct Chinese slash nodes.
    alias = re.fullmatch(r'([\u4e00-\u9fff]{2,})[a-z]+', key)
    return alias[1] if alias else key


def _mark_parent_disagreements(initial: RecognizedPage, reviewed: RecognitionResult):
    """Keep the independent candidate but never assert a disputed edge as a quote.

    This is uncertainty reporting, not a vote on which read is correct. Both
    parent candidates remain visible; no class-specific answer is substituted.
    """
    if initial.flows or reviewed.structured.get('flows'):
        return 0
    previous = {}
    for relation in initial.relations:
        for child in relation.children:
            previous.setdefault(_diagram_label_key(child), {})[_diagram_label_key(relation.parent)] = relation.parent
    relations, warnings, count = [], [], 0
    for relation in reviewed.structured.get('relations', []):
        disputed = False
        for child in relation['children']:
            parents = previous.get(_diagram_label_key(child), {})
            if parents and _diagram_label_key(relation['parent']) not in parents:
                disputed = True
                count += 1
                warnings.append((f'「{child}」两次识别的父项不同：初读「{"、".join(parents.values())}」，'
                                 f'复核「{relation["parent"]}」；该组关联标为来源待核对。')[:1000])
        relations.append({**relation, 'source_type': '来源待核对'} if disputed else relation)
    if count:
        reviewed.structured['relations'] = relations
        regions = reviewed.structured['uncertain_regions']
        reviewed.structured['uncertain_regions'] = [*regions, *warnings][:30]
        reviewed.confidence = min(reviewed.confidence, 0.5)
        reviewed.structured['confidence'] = reviewed.confidence
        reviewed.low_confidence = True
        page = RecognizedPage.model_validate({k: v for k, v in reviewed.structured.items() if k != '_meta'})
        reviewed.text = page.title + '\n' + '\n'.join(f"- {point['content']}" for point in page.visible_points())
    return count


_MOCK_PAGES = {
    1: {
        "title": "数据库设计概述",
        "points": [
            RecognitionPoint("关系数据库基本概念", "concept", "课堂原话"),
            RecognitionPoint("数据模型的三要素：数据结构、数据操作、数据约束", "concept", "AI推断"),
            RecognitionPoint("ER图是概念模型设计的核心工具", "method", "课堂原话"),
            RecognitionPoint("数据库设计的目标是减少冗余、保持一致性", "conclusion", "AI推断"),
        ],
    },
    2: {
        "title": "范式与函数依赖",
        "points": [
            RecognitionPoint("第一范式要求属性不可再分", "concept", "课堂原话"),
            RecognitionPoint("第二范式消除部分函数依赖", "concept", "课堂原话"),
            RecognitionPoint("第三范式消除传递函数依赖", "concept", "AI推断"),
            RecognitionPoint("BCNF是第三范式的加强形式", "conclusion", "AI推断"),
        ],
    },
    3: {
        "title": "索引与查询优化",
        "points": [
            RecognitionPoint("B+树是数据库最常用的索引结构", "concept", "课堂原话"),
            RecognitionPoint("索引选择需要权衡查询性能与写入开销", "method", "AI推断"),
            RecognitionPoint("覆盖索引可以避免回表查询", "conclusion", "课堂原话"),
            RecognitionPoint("查询优化器基于代价选择执行计划", "concept", "AI推断"),
        ],
    },
}


class Recognizer(ABC):
    @abstractmethod
    def recognize(self, page_no: int, file_path: str) -> RecognitionResult:
        pass


class MockRecognizer(Recognizer):
    def recognize(self, page_no: int, file_path: str) -> RecognitionResult:
        page_data = _MOCK_PAGES.get(page_no)
        if page_data is None:
            generic = _MOCK_PAGES[((page_no - 1) % 3) + 1]
            title = f"模块{page_no}"
            points = [
                RecognitionPoint(
                    f"知识点{page_no}-{i+1}",
                    p.kp_type,
                    p.source_type,
                )
                for i, p in enumerate(generic["points"])
            ]
            page_data = {"title": title, "points": points}

        text_lines = [page_data["title"]]
        for p in page_data["points"]:
            text_lines.append(f"  - {p.content}")
        text = "\n".join(text_lines)

        return RecognitionResult(
            text=text,
            confidence=0.95,
            low_confidence=False,
            structured={
                "title": page_data["title"],
                "points": [
                    {"content": p.content, "kp_type": p.kp_type, "source_type": p.source_type}
                    for p in page_data["points"]
                ],
            },
        )


class MultimodalRecognizer(Recognizer):
    def __init__(self):
        self.provider = settings.model_provider.lower()
        self.api_key = settings.model_api_key
        self.base_url = settings.model_base_url
        self.model_name = settings.model_name
        self.timeout = settings.model_timeout_seconds
        self.max_retry = settings.model_max_retry
        self.budget_path = None

    def recognize(self, page_no: int, file_path: str) -> RecognitionResult:
        return self._recognize(page_no, file_path, diagram_review=False)

    def _recognize(self, page_no: int, file_path: str, *, diagram_review: bool) -> RecognitionResult:
        if not self.api_key.strip():
            raise AppError("MODEL_CONFIG_ERROR", "请在本地 .env 配置 APP_MODEL_API_KEY。", status_code=503)
        endpoint = self.base_url.strip().rstrip('/')
        parsed = urlsplit(endpoint)
        if not parsed.hostname or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise AppError("MODEL_CONFIG_ERROR", "模型服务地址无效，请检查 APP_MODEL_BASE_URL。", status_code=503)
        is_deepseek = uses_deepseek(endpoint, self.provider)
        if is_deepseek and (endpoint not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}
                            or self.model_name != "deepseek-flash"):
            raise AppError("MODEL_CONFIG_ERROR", "DeepSeek 测试请使用官方接口地址和 deepseek-flash 模型。", status_code=503)
        try:
            prompt = get_prompt_text("recognize")
            prompt_version = get_prompt_version("recognize")
            if diagram_review:
                review_prompt = get_prompt_text('review_diagram')
                if not isinstance(review_prompt, str) or not review_prompt.strip():
                    raise ValueError('Invalid diagram review prompt')
                prompt = review_prompt
            if not isinstance(prompt, str) or not prompt.strip() or "json" not in prompt.lower():
                raise ValueError("Invalid recognition prompt")
        except (OSError, UnicodeError, yaml.YAMLError, AttributeError, TypeError, ValueError):
            raise AppError("MODEL_CONFIG_ERROR", "识别提示词无效，请检查 recognize.yaml。", status_code=503) from None

        image = prepare_image(file_path)
        payload = {
            "model": self.model_name,
            "messages": recognition_context(prompt, page_no, image.data_url,
                detail_images=diagram_detail_images(image) if diagram_review else ()),
            "response_format": {"type": "json_object"},
            "max_tokens": settings.diagram_review_max_tokens if diagram_review else settings.model_max_tokens,
        }
        if is_deepseek:
            payload["thinking"] = {"type": "enabled" if diagram_review else "disabled"}
            if diagram_review:
                payload['reasoning_effort'] = 'low'

        started = time.monotonic()
        for attempt in range(max(0, self.max_retry) + 1):
            if is_deepseek or settings.deployment_mode == 'cloud':
                reserve_request(payload, self.budget_path)
            try:
                with httpx.Client(timeout=self.timeout, trust_env=settings.model_trust_env) as client:
                    resp = client.post(
                        f"{endpoint}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {self.api_key.strip()}"},
                    )
                    resp.raise_for_status()
            except httpx.TimeoutException as exc:
                report_transport_failure('recognition', exc)
                if retry_allowed(attempt, self.max_retry) and wait_before_retry(attempt):
                    continue
                raise ModelTimeoutError() from None
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 401:
                    raise AppError("MODEL_AUTH_ERROR", "API-Key 认证失败，请检查本地密钥。", status_code=502) from None
                if status == 402:
                    raise AppError("MODEL_BALANCE_ERROR", "模型账户余额不足，请检查官方账户余额。", status_code=503) from None
                retryable = status in {408, 429} or status >= 500
                if retryable and retry_allowed(attempt, self.max_retry) and wait_before_retry(attempt, exc.response):
                    continue
                raise RecognitionFailedError(
                    f"模型服务返回 HTTP {status}，请检查配置或稍后重试。", retryable=retryable,
                ) from None
            except httpx.TransportError as exc:
                report_transport_failure('recognition', exc)
                if retry_allowed(attempt, self.max_retry) and wait_before_retry(attempt):
                    continue
                raise RecognitionFailedError("无法连接模型服务，请检查网络后重试。", reason_code='MODEL_CONNECTION_FAILED') from None
            except httpx.InvalidURL:
                raise AppError("MODEL_CONFIG_ERROR", "模型服务地址无效，请检查 APP_MODEL_BASE_URL。", status_code=503) from None

            try:
                page, data = parse_completion(resp.content, RecognizedPage)
            except ModelResponseError as exc:
                raise model_response_error(exc.kind, 'recognition') from None

            if diagram_review and not (page.relations or page.flows or page.unverified_relations):
                raise RecognitionFailedError('图示复核未保留图形结构，请重新识别。', reason_code='MODEL_OUTPUT_INVALID')

            # A detected diagram may need detail images before any connector
            # or text can be recovered. Do not reject that initial observation
            # before the independent review has had a chance to read it.
            needs_detail_read = page.has_diagram and not diagram_review
            if page.readability == 'unreadable' or (not page.visible_points() and not needs_detail_read):
                raise RecognitionFailedError('图片内容不可辨认，请更换清晰图片后重新上传。', retryable=False,
                                             reason_code='IMAGE_UNREADABLE')

            pending_edges = any(edge.source_type == '来源待核对' for flow in page.flows for edge in flow.edges)
            confidence = min(page.confidence, 0.5) if (page.readability == "partial" or page.uncertain_regions
                                                       or page.unverified_relations or pending_edges) else page.confidence
            structured = page.model_dump()
            structured["_meta"] = {
                "provider": 'deepseek' if is_deepseek else self.provider,
                "model": data.get("model", self.model_name),
                "request_id": data.get("id"),
                "usage": data.get("usage", {}),
                "prompt_version": prompt_version,
                "input": image.metadata,
                "context_version": "1.0.0",
                "output_schema_version": "1.4.0",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "attempts": attempt + 1,
                "confidence_basis": "model_self_report_not_calibrated_accuracy",
            }
            if diagram_review:
                structured['_meta']['diagram_review_prompt_version'] = get_prompt_version('review_diagram')
                structured['_meta']['diagram_review_thinking'] = is_deepseek
                structured['_meta']['diagram_review_reasoning_effort'] = 'low' if is_deepseek else None
                structured['_meta']['diagram_review_max_tokens'] = settings.diagram_review_max_tokens
            elif page.has_diagram or page.relations or page.flows or page.unverified_relations:
                # Re-read independently from global+detail images. Do not feed
                # the first candidate to the reviewer and anchor its mistakes.
                reviewed = self._recognize(page_no, file_path, diagram_review=True)
                disputed_count = _mark_parent_disagreements(page, reviewed)
                review_meta = reviewed.structured['_meta']
                review_meta['diagram_review'] = {
                    'performed': True, 'initial_request_id': data.get('id'),
                    'initial_usage': data.get('usage', {}), 'detail_image_count': 5,
                    'basis': 'second_model_visual_read_not_independent_human_validation',
                    'disputed_edge_count': disputed_count,
                    'conflict_policy': 'preserve_candidate_mark_unverified_not_choose_a_winner',
                }
                review_meta['attempts'] += attempt + 1
                review_meta['elapsed_seconds'] = round(time.monotonic() - started, 3)
                return reviewed
            text = page.title + "\n" + "\n".join(f"- {point['content']}" for point in page.visible_points())
            return RecognitionResult(text, confidence, confidence < settings.low_confidence_threshold, structured)

        raise RecognitionFailedError("重试次数耗尽")


class FallbackRecognizer(Recognizer):
    def recognize(self, page_no: int, file_path: str) -> RecognitionResult:
        return RecognitionResult(
            text="[识别服务不可用，使用兜底结果]",
            confidence=0.0,
            low_confidence=True,
            structured={"title": f"页面{page_no}", "points": []},
        )


def get_recognizer() -> Recognizer:
    provider = settings.model_provider.lower()
    if provider in {"multimodal", "deepseek"}:
        return MultimodalRecognizer()
    elif provider == "fallback":
        return FallbackRecognizer()
    elif provider == "mock":
        return MockRecognizer()
    raise AppError("MODEL_CONFIG_ERROR", "APP_MODEL_PROVIDER 无效，请检查模型配置。", status_code=503)
