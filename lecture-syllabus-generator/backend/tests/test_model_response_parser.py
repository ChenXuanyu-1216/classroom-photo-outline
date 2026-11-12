"""Parser failures stay independent of HTTP transport, business errors and DBs."""
import json

import pytest

from app.services.model_output import RecognizedPage, ClusterAssignment, KnowledgeExpansion
from app.services.model_response_parser import MAX_RESPONSE_BYTES, ModelResponseError, parse_completion


SCHEMAS = [RecognizedPage, ClusterAssignment, KnowledgeExpansion]


def envelope(content, *, finish='stop', **message):
    return json.dumps({'choices': [{'finish_reason': finish, 'message': {'content': content, **message}}]}).encode()


@pytest.mark.parametrize('schema', SCHEMAS)
@pytest.mark.parametrize('body,kind', [
    (b'', 'empty_response'),
    (b' \r\n\t', 'empty_response'),
    (envelope(''), 'empty_content'),
    (envelope(' \r\n\t\u3000'), 'empty_content'),
    (b'{"choices":', 'invalid_format'),
    (b'\xff', 'invalid_format'),
    (b'{"choices":[]}', 'invalid_format'),
    (envelope('{'), 'invalid_format'),
    (envelope('{}'), 'invalid_format'),
    (envelope(None), 'invalid_format'),
    (envelope(42), 'invalid_format'),
    (envelope('', finish='length'), 'invalid_format'),
    (envelope('', refusal='declined'), 'invalid_format'),
    (b'{"choices":[{"finish_reason":"stop","message":{}}]}', 'invalid_format'),
])
def test_failure_kind_is_schema_independent(schema, body, kind):
    with pytest.raises(ModelResponseError) as error:
        parse_completion(body, schema)
    assert error.value.kind == kind


@pytest.mark.parametrize('schema,result', [
    (RecognizedPage, {'title': '课件', 'points': [], 'confidence': .9, 'readability': 'clear', 'uncertain_regions': []}),
    (ClusterAssignment, {'modules': [{'title': '主题', 'point_ids': ['kp_1']}]}),
    (KnowledgeExpansion, {'points': [{'content': '补充知识', 'kp_type': 'concept', 'based_on_ids': ['kp_1']}]}),
])
def test_valid_fenced_content_preserves_schema_and_envelope(schema, result):
    parsed, data = parse_completion(envelope('```json\n' + json.dumps(result, ensure_ascii=False) + '\n```'), schema)
    assert isinstance(parsed, schema)
    assert all(parsed.model_dump()[key] == value for key, value in result.items())
    assert len(data['choices']) == 1


@pytest.mark.parametrize('body', [
    b'{"secret-token":"private-provider-body"',
    envelope('{"secret-token":"private-provider-body", "modules":[]}'),
])
def test_parser_exception_does_not_expose_raw_content(body):
    with pytest.raises(ModelResponseError) as error:
        parse_completion(body, ClusterAssignment)
    assert 'secret-token' not in str(error.value)
    assert 'private-provider-body' not in str(error.value)


@pytest.mark.parametrize('body', [b' ' * (MAX_RESPONSE_BYTES + 1), ' ' * (MAX_RESPONSE_BYTES + 1)], ids=['bytes', 'text'])
def test_size_limit_precedes_empty_classification(body):
    with pytest.raises(ModelResponseError) as error:
        parse_completion(body, ClusterAssignment)
    assert error.value.kind == 'invalid_format'
