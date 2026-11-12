"""Narrow regression checks, not an automatic semantic accuracy certificate."""
import argparse
import hashlib
import json
from pathlib import Path
import re


CASES = Path(__file__).resolve().parents[2] / 'evaluation/classroom-photos/content-regressions.json'


def _label(value):
    return re.sub(r'\s+', '', value).lower().replace('分类与回归树', '分类和回归树')


def _same(actual, expected):
    # Allow the visible English gloss following an otherwise exact Chinese
    # label. This is a lexical convenience, not a test of that gloss's quality.
    a, e = _label(actual), _label(expected)
    return a == e or (a.startswith(e) and re.fullmatch(r'[a-z()/（）、,.-]+', a[len(e):]) is not None)


def diagram_checks(structured, case):
    edges = [(relation['parent'], child) for relation in structured.get('relations', [])
             for child in relation['children']]
    def contains(edge):
        return any(_same(parent, edge[0]) and _same(child, edge[1]) for parent, child in edges)
    missing = [edge for edge in case.get('required_edges', []) if not contains(edge)]
    forbidden = [edge for edge in case.get('forbidden_edges', []) if contains(edge)]
    expected_flow = case.get('required_flow')
    primary_flows = []
    for flow in structured.get('flows', []):
        allowed_feedback = re.search(r'反馈|回退', flow.get('name', '')) and any(
            len(flow['steps']) == len(allowed) and all(_same(a, e) for a, e in zip(flow['steps'], allowed))
            for allowed in case.get('allowed_feedback_flows', []))
        if not allowed_feedback:
            primary_flows.append(flow)
    flow_matches = None if expected_flow is None else bool(primary_flows) and all(
        len(flow['steps']) == len(expected_flow) and all(_same(a, e) for a, e in zip(flow['steps'], expected_flow))
        for flow in primary_flows)
    applicable = bool(case.get('required_edges') or case.get('forbidden_edges') or expected_flow)
    contradictory_points = []
    for point in structured.get('points', []):
        content = _label(point.get('content', ''))
        if any(_label(parent) in content and _label(child) in content and
               re.search(r'包含|下属|子节点|下方|：|:', content) for parent, child in case.get('forbidden_edges', [])):
            contradictory_points.append(point['content'])
        raw = point.get('content', '')
        if expected_flow and '→' in raw and not re.search(r'反馈|回退', raw):
            if sum(_label(step) in content for step in expected_flow) >= 2:
                chain = re.split(r'[：:]', raw)[-1].strip(' 。.')
                steps = [step.strip() for step in chain.split('→')]
                if len(steps) != len(expected_flow) or not all(_same(a, e) for a, e in zip(steps, expected_flow)):
                    contradictory_points.append(raw)
    hierarchy_flow_conflicts = [] if not expected_flow else [
        [parent, child] for parent, child in edges
        if any(_same(parent, stage) for stage in expected_flow)
        and any(_same(child, stage) for stage in expected_flow)]
    # These photo-specific notes have readable text but no unambiguous owner.
    # Keep the text; do not accept an invented stage assignment as transcription.
    notes = case.get('required_standalone_notes', [])
    points = structured.get('points', [])
    missing_notes = [note for note in notes
                     if not any(_label(note) in _label(point.get('content', '')) for point in points)]
    unsupported_note_attribution = [
        [parent, child] for parent, child in edges
        if any(_same(parent, stage) for stage in expected_flow or [])
        and any(_label(note) in _label(child) for note in notes)]
    for point in points:
        content = _label(point.get('content', ''))
        if point.get('source_type') == '来源待核对':
            continue
        if (any(_label(note) in content for note in notes)
                and any(_label(stage) in content for stage in expected_flow or [])):
            unsupported_note_attribution.append(point['content'])
    return {'passed': applicable and not missing and not forbidden and not contradictory_points
                      and not hierarchy_flow_conflicts and not missing_notes
                      and not unsupported_note_attribution and flow_matches is not False,
            'missing_edges': missing, 'forbidden_edges_found': forbidden, 'flow_matches': flow_matches,
            'contradictory_points': contradictory_points,
            'hierarchy_flow_conflicts': hierarchy_flow_conflicts,
            'missing_notes': missing_notes, 'unsupported_note_attribution': unsupported_note_attribution,
            'scope': 'frozen_known_diagram_regression_only'}


def expansion_checks(case_id, points):
    text = '\n'.join(p['content'] for p in points if p.get('source_type') == 'AI生成')
    lower = text.lower()
    clauses = re.split(r'[。；;\n]', lower)
    def binding(subject, prop, event):
        return any(all(token in clause for token in (subject, prop, event)) for clause in clauses)
    if case_id == 'vue_controls':
        # Require explicit coverage so a topic disappearing is never a pass.
        required = ['v-model', 'textarea', 'value', 'input', 'checkbox', 'radio', 'checked', 'change', 'select']
        forbidden = [r'(?:所有|全部|都|统一).{0,15}value.{0,15}input',
                     r'表单控件上.{0,10}等价于.{0,20}value.{0,20}input',
                     r'(?:(?<![a-z0-9_-])radio(?![a-z0-9_-])|单选按钮)[^。；;\n]{0,18}(?:绑定|使用|采用)\s*`?value`?\s*(?:属性)?\s*(?:/|与|和|及|并监听)\s*`?change`?',
                     r'v-model(?:(?!dom).){0,45}(?:不是同步立即生效|(?:模型|数据)(?:赋值|更新)是异步)']
        scoped = {'text_binding': binding('textarea', 'value', 'input'),
                  'checkbox_binding': binding('checkbox', 'checked', 'change'),
                  'radio_binding': binding('radio', 'checked', 'change'),
                  'select_binding': binding('select', 'value', 'change')}
    elif case_id == 'label_metrics':
        required = ['类别', '准确率', '精确率', '召回率', 'f1', '概率', '分数', '对数损失']
        forbidden = [r'非概率模型.{0,15}(?:只能|仅能|无法计算准确率|不能计算准确率)',
                     r'(?:准确率|精确率|召回率).{0,8}(?:必须有|必须输出)概率',
                     r'非概率(?:判别)?模型如逻辑回归', r'逻辑回归(?:属于|是)非概率',
                     r'后者只依赖决策边界附近的样本',
                     r'对数损失[^。；;\n，,]{0,20}(?<!不)(?:要求|需要|必须使用)(?:输出|使用)?(?:经过|已|良好)校准的?概率',
                     r'(?:对数损失(?:和|及|与)概率校准|概率校准(?:和|及|与)对数损失)(?:则|才)?(?:要求|需要|必须使用)(?:输出|输入)?(?:经过校准的)?概率',
                     r'概率校准(?:则|才)?(?:要求|需要|必须使用)输入(?:经过校准的)?概率']
        scoped = {'labels_without_probability': re.search(
            r'(?:不需要|不要求|无需).{0,10}概率|(?:只需(?:要)?|仅需(?:要)?).{0,12}(?:真实|预测)(?:类别|标签)'
            r'|真实(?:类别|标签)(?:和|与|加|及)预测(?:类别|标签)(?:即可计算|可(?:以)?(?:直接)?(?:计算|构成)).{0,12}(?:准确率|混淆矩阵)',
            lower) is not None}
    else:
        raise ValueError('Unknown frozen expansion case')
    missing = [token for token in required if token not in lower]
    found = []
    for pattern in forbidden:
        for match in re.finditer(pattern, lower):
            # A named misconception can be longer than 16 characters. Its
            # label applies within this clause, never across a sentence break.
            clause_start = max(lower.rfind(mark, 0, match.start()) for mark in ['。', '；', ';', '\n']) + 1
            prefix = lower[clause_start:match.start()]
            suffix = lower[match.end():match.end()+16]
            denied_after = re.search(r'出错|是错误|是误区|不准确|不正确|不适用|不成立', suffix)
            if not denied_after and not re.search(r'不能认为|不能把|误以为|误区|或认为|并非|不等于|不意味着|(?:不能|不可|不要|不应|而非)$|(?:不要|不应|不能)(?:笼统地)?(?:说|认为)\s*$|不能笼统概括为\s*$', prefix):
                found.append(pattern)
                break
    return {'passed': not missing and not found and all(scoped.values()), 'missing_coverage_tokens': missing,
            'scoped_checks': scoped,
            'forbidden_claim_patterns': found, 'manual_semantic_review_required': True}


def check_run(root):
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    observed = {}
    for path in root.glob('*/recognition.json'):
        for page in json.loads(path.read_text(encoding='utf-8'))['pages']:
            observed[page['sample_id']] = page['structured'] or {}
    results = {sample: diagram_checks(observed.get(sample, {}), case)
               for sample, case in cases['diagram_cases'].items()}
    return {'cases_version': cases['version'], 'diagram_cases': results,
            'diagram_regressions_passed': all(item['passed'] for item in results.values()),
            'formal_content_acceptance': 'pending_independent_human_review'}


def check_targeted_run(root):
    """Rescore immutable observations separately from their original report."""
    report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    if report.get('status') != 'completed':
        raise ValueError('运行未结束，不能认定重复回归通过。')
    cases_hash = hashlib.sha256(CASES.read_bytes()).hexdigest()
    if report.get('cases_sha256') != cases_hash:
        raise ValueError('冻结反例已变更，不能沿用原运行的验收条件。')
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    entries = []
    for original in report['entries']:
        case_id, repeat = original['case_id'], original['repeat']
        path = root / f'{repeat}-{case_id}.json'
        checks = {'passed': False}
        if path.exists():
            observed = json.loads(path.read_text(encoding='utf-8'))['observed']
            checks = (diagram_checks(observed, cases['diagram_cases'][case_id])
                      if case_id in cases['diagram_cases'] else expansion_checks(case_id, observed))
        entries.append({'case_id': case_id, 'repeat': repeat, 'checks': checks,
                        'original_status': original['status']})
    expected = {(case_id, repeat) for case_id in [*cases['diagram_cases'], *cases['expansion_cases']]
                for repeat in range(1, report['repeat_count']+1)}
    observed_keys = {(entry['case_id'], entry['repeat']) for entry in entries}
    passed = sum(entry['checks']['passed'] for entry in entries)
    return {'scope': 'retrospective_checker_repair_known_regressions_not_human_gold',
            'cases_sha256': cases_hash,
            'source_evaluator_sha256': report.get('evaluator_sha256'),
            'evaluator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'original_passed_count': report['passed_count'], 'original_gate_passed': report['development_gate_passed'],
            'passed_count': passed, 'attempt_count': len(entries), 'request_count': report['request_count'],
            'development_gate_passed': (report['repeat_count'] >= 3 and observed_keys == expected
                                        and len(entries) == len(expected) and passed == len(expected)),
            'entries': entries, 'formal_content_acceptance': 'pending_independent_human_review'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--targeted', action='store_true', help='只读复核定向重复运行，不调用模型')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = check_targeted_run(args.run) if args.targeted else check_run(args.run)
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('development_gate_passed', result.get('diagram_regressions_passed')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
