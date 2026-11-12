"""Business result schemas and invariants, independent of response decoding."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _valid_text(value: str, *, single_line: bool = False) -> str:
    if not value.strip():
        raise ValueError('Empty text')
    value.encode('utf-8')
    if any((ord(char) < 32 and char not in '\t\r\n') or ord(char) == 127 for char in value):
        raise ValueError('Invalid control character')
    if single_line and any(ord(char) < 32 or char in '\x85\u2028\u2029' for char in value):
        raise ValueError('Expected a single line')
    return value


class RecognizedPoint(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    content: str = Field(min_length=1, max_length=20000)
    kp_type: Literal['concept', 'conclusion', 'method', 'example']
    source_type: Literal['课堂原话', 'AI推断', '来源待核对']

    @field_validator('content')
    @classmethod
    def content_is_visible(cls, value):
        return _valid_text(value)


class DiagramRelation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    parent: str = Field(min_length=1, max_length=300)
    children: list[str] = Field(min_length=1, max_length=60)
    source_type: Literal['课堂原话', '来源待核对'] = '课堂原话'

    @field_validator('parent')
    @classmethod
    def visible_parent(cls, value):
        return _valid_text(value).strip()

    @field_validator('children')
    @classmethod
    def visible_children(cls, values):
        result = [_valid_text(value).strip() for value in values]
        if any(len(value) > 300 for value in result) or len({''.join(v.split()) for v in result}) != len(result):
            raise ValueError('Invalid or duplicate children')
        return result


class DiagramEdge(BaseModel):
    """A visible directed connector; unlike tree containment it may form a cycle."""
    model_config = ConfigDict(extra='forbid', strict=True)
    source: str = Field(min_length=1, max_length=300)
    target: str = Field(min_length=1, max_length=300)
    label: str = Field(default='', max_length=300)
    source_type: Literal['课堂原话', '来源待核对'] = '课堂原话'

    @field_validator('source', 'target')
    @classmethod
    def visible_endpoint(cls, value):
        return _valid_text(value).strip()

    @field_validator('label')
    @classmethod
    def visible_label(cls, value):
        return _valid_text(value).strip() if value else value


class DiagramFlow(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=200)
    steps: list[str] = Field(min_length=2, max_length=40)
    edges: list[DiagramEdge] = Field(default_factory=list, max_length=120)

    @field_validator('name')
    @classmethod
    def visible_name(cls, value):
        return _valid_text(value, single_line=True).strip()

    @field_validator('steps')
    @classmethod
    def visible_steps(cls, values):
        result = [_valid_text(value).strip() for value in values]
        if any(len(value) > 300 for value in result) or len({''.join(v.split()) for v in result}) != len(result):
            raise ValueError('Invalid or repeated main flow steps')
        return result


class RecognizedPage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=200)
    points: list[RecognizedPoint] = Field(max_length=100)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    readability: Literal['clear', 'partial', 'unreadable']
    uncertain_regions: list[str] = Field(max_length=30)
    relations: list[DiagramRelation] = Field(default_factory=list, max_length=60)
    unverified_relations: list[DiagramRelation] = Field(default_factory=list, max_length=60)
    flows: list[DiagramFlow] = Field(default_factory=list, max_length=10)
    has_diagram: bool = False

    @model_validator(mode='before')
    @classmethod
    def flow_connections_are_not_confirmed_containment(cls, data):
        # A flow arrow does not establish containment. The legacy relation
        # shape carries no connector type, so keep it pending on flow pages.
        # This also handles mixed diagrams conservatively, without losing the
        # candidate labels or passing a directed feedback loop off as a tree.
        if (isinstance(data, dict) and isinstance(data.get('flows'), list) and data['flows']
                and isinstance(data.get('relations'), list) and data['relations']
                and isinstance(data.get('unverified_relations', []), list)):
            data = dict(data)
            data['unverified_relations'] = [*data.get('unverified_relations', []), *data['relations']]
            data['relations'] = []
            regions = data.get('uncertain_regions')
            if isinstance(regions, list) and len(regions) < 30:
                data['uncertain_regions'] = [*regions, '流程图的连线类型与方向尚未确认，相关标注已保留为来源待核对。']
        return data

    @model_validator(mode='after')
    def hierarchy_has_no_cycles(self):
        graph = {}
        for relation in self.relations:
            graph.setdefault(''.join(relation.parent.split()), set()).update(''.join(child.split()) for child in relation.children)
        complete, visiting = set(), set()
        def visit(node):
            if node in visiting:
                raise ValueError('Cyclic hierarchy')
            if node in complete:
                return
            visiting.add(node)
            for child in graph.get(node, ()):
                visit(child)
            visiting.remove(node)
            complete.add(node)
        for node in graph:
            visit(node)
        return self

    def visible_points(self):
        """Render relations once, without asking grouping to rewrite their order."""
        result = [point.model_dump() for point in self.points]
        result.extend({'content': (f'{relation.parent} 包含：' if relation.source_type == '课堂原话'
                                   else f'图示归属待核对：{relation.parent} → ') + '、'.join(relation.children),
                       'kp_type': 'concept', 'source_type': relation.source_type} for relation in self.relations)
        result.extend({'content': f'图示关联待核对：{relation.parent}；相关标注：' + '、'.join(relation.children)
                                  + '（关系类型与方向未确认）',
                       'kp_type': 'concept', 'source_type': '来源待核对'} for relation in self.unverified_relations)
        result.extend({'content': (f'{flow.name}（节点）：' + '、'.join(flow.steps) if flow.edges
                                  else f'{flow.name}：' + ' → '.join(flow.steps)),
                       'kp_type': 'method', 'source_type': '课堂原话'} for flow in self.flows)
        result.extend({'content': f'{flow.name}（连线）：{edge.source} '
                                  + (f'—[{edge.label}]→ ' if edge.label else '→ ') + edge.target,
                       'kp_type': 'method', 'source_type': edge.source_type}
                      for flow in self.flows for edge in flow.edges)
        return result

    @field_validator('title')
    @classmethod
    def title_is_single_line(cls, value):
        return _valid_text(value, single_line=True).strip()

    @field_validator('uncertain_regions')
    @classmethod
    def uncertainties_are_bounded(cls, values):
        for value in values:
            _valid_text(value)
            if len(value) > 1000:
                raise ValueError('Uncertainty description too long')
        return values


class ModuleAssignment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=120)
    point_ids: list[str] = Field(min_length=1, max_length=2000)

    @field_validator('title')
    @classmethod
    def title_is_single_line(cls, value):
        return _valid_text(value, single_line=True).strip()


class ClusterAssignment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    modules: list[ModuleAssignment] = Field(min_length=1, max_length=2000)


class ExpandedPoint(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    content: str = Field(min_length=1, max_length=1500)
    kp_type: Literal['concept', 'conclusion', 'method', 'example']
    based_on_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator('content')
    @classmethod
    def content_is_visible(cls, value):
        return _valid_text(value)


class KnowledgeExpansion(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    points: list[ExpandedPoint] = Field(min_length=1, max_length=8)
