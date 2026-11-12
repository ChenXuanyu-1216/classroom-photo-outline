"""Pure completion decoding and schema validation; no transport or persistence."""
from enum import Enum
import json
import math
import re

from pydantic import BaseModel


MAX_RESPONSE_BYTES = 1_000_000


class ModelResponseFailure(str, Enum):
    EMPTY_RESPONSE = 'empty_response'
    EMPTY_CONTENT = 'empty_content'
    INVALID_FORMAT = 'invalid_format'


class ModelResponseError(ValueError):
    """Neutral parse failure. Never retain provider text in the exception."""
    def __init__(self, kind: ModelResponseFailure):
        self.kind = kind.value
        super().__init__(f'Invalid model response ({self.kind})')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('Non-finite JSON number')


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('JSON number overflow')
    return result


def _json_object(text: str, *, allow_fence: bool = False) -> dict:
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_RESPONSE_BYTES:
        raise ValueError('Invalid response text')
    text = text.strip()
    if allow_fence and text.startswith('```'):
        wrapped = re.fullmatch(r'```(?:json)?[ \t]*\r?\n([\s\S]*)\r?\n```', text, re.IGNORECASE)
        if not wrapped:
            raise ValueError('Invalid JSON fence')
        text = wrapped.group(1)
    result = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant, parse_float=_finite_float)
    if not isinstance(result, dict):
        raise ValueError('Expected a JSON object')
    return result


def parse_completion(body: bytes | str, schema: type[BaseModel]) -> tuple[BaseModel, dict]:
    try:
        if isinstance(body, bytes):
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError('Response too large')
            body = body.decode('utf-8')
        if not isinstance(body, str):
            raise ValueError('Invalid response type')
        if len(body.encode('utf-8')) > MAX_RESPONSE_BYTES:
            raise ValueError('Response too large')
        if not body.strip():
            raise ModelResponseError(ModelResponseFailure.EMPTY_RESPONSE)
        data = _json_object(body)
        choices = data.get('choices')
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError('Expected one completion')
        choice = choices[0]
        message = choice.get('message')
        # A refusal or truncation remains a protocol failure even with no text.
        if choice.get('finish_reason') != 'stop' or not isinstance(message, dict) or message.get('refusal'):
            raise ValueError('Incomplete or refused completion')
        content = message.get('content')
        if not isinstance(content, str):
            raise ValueError('Missing completion text')
        if not content.strip():
            raise ModelResponseError(ModelResponseFailure.EMPTY_CONTENT)
        return schema.model_validate(_json_object(content, allow_fence=True)), data
    except ModelResponseError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise ModelResponseError(ModelResponseFailure.INVALID_FORMAT) from None
