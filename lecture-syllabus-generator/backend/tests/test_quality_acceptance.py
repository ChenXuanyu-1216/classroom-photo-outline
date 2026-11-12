import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'run_quality_acceptance.py'


def load_runner():
    spec = importlib.util.spec_from_file_location('quality_acceptance_runner', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dataset(tmp_path):
    photo = tmp_path / 'page.png'
    photo.write_bytes(b'fixed fixture bytes')
    draft = tmp_path / 'draft.json'
    draft.write_text(json.dumps({'expected_points': []}), encoding='utf-8')
    data = {'dataset_id': 'fixture', 'version': '1', 'pages': [{
        'sample_id': 'P01', 'course_id': 'C01', 'file_path': 'page.png',
        'sha256': hashlib.sha256(photo.read_bytes()).hexdigest(),
        'expectation_path': 'draft.json', 'annotation_status': 'draft_ai_assisted_pending_human_review',
    }], 'task_groups': [{'task_group_id': 'G1', 'ordered_sample_ids': ['P01']}],
        'authorizations': [{'external_model_upload': 'declined'}]}
    path = tmp_path / 'dataset.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


def test_live_dataset_requires_separate_explicit_cloud_authorization(dataset):
    path, _ = dataset
    with pytest.raises(ValueError, match='授权'):
        load_runner().prepare_manifest(path, live=True)


def test_authorization_is_limited_to_provider_dataset_and_exact_photos(dataset, tmp_path):
    path, data = dataset
    authorization = {'provider': 'deepseek', 'dataset_id': 'fixture',
                     'cloud_upload_allowed': True, 'sample_sha256': {'P01': data['pages'][0]['sha256']}}
    permission = tmp_path / 'permission.json'
    permission.write_text(json.dumps(authorization), encoding='utf-8')
    frozen = load_runner().prepare_manifest(path, permission, live=True)
    assert frozen['pages'][0]['sample_id'] == 'P01'
    assert frozen['annotation_status'] == 'draft_reference_not_human_gold'
    authorization['provider'] = 'other-provider'
    permission.write_text(json.dumps(authorization), encoding='utf-8')
    with pytest.raises(ValueError, match='授权'):
        load_runner().prepare_manifest(path, permission, live=True)


@pytest.mark.parametrize('change', ['changed_bytes', 'path_escape', 'missing_photo_authorization'])
def test_changed_or_out_of_scope_input_stops_before_paid_calls(dataset, tmp_path, change):
    path, data = dataset
    authorization = {'provider': 'deepseek', 'dataset_id': 'fixture', 'cloud_upload_allowed': True,
                     'sample_sha256': {'P01': data['pages'][0]['sha256']}}
    if change == 'changed_bytes':
        (tmp_path / 'page.png').write_bytes(b'changed')
    elif change == 'path_escape':
        data['pages'][0]['file_path'] = '../outside.png'
        path.write_text(json.dumps(data), encoding='utf-8')
    else:
        authorization['sample_sha256'] = {}
    permission = tmp_path / 'permission.json'
    permission.write_text(json.dumps(authorization), encoding='utf-8')
    with pytest.raises(ValueError):
        load_runner().prepare_manifest(path, permission, live=True)


def test_generated_content_cannot_fill_missing_original_coverage():
    expected = [{'point_id': 'k1', 'critical_tokens': ['v-model', 'name']}]
    observed = [{'content': '<input v-model="name">', 'source_type': 'AI生成'}]
    result = load_runner().token_coverage(expected, observed)
    assert result['expected_point_count'] == 1
    assert result['all_critical_tokens_present_count'] == 0
    assert result['coverage'] == 0
    assert result['points'][0]['missing_tokens'] == ['v-model', 'name']


def test_coverage_retains_case_and_code_symbols_and_exposes_missing_tokens():
    expected = [{'point_id': 'k1', 'critical_tokens': ['Value', '!=', 'None']},
                {'point_id': 'k2', 'critical_tokens': ['P(y|x)']}]
    observed = [{'content': 'value == None; P(y|x)', 'source_type': '课堂原话'}]
    result = load_runner().token_coverage(expected, observed)
    assert result['all_critical_tokens_present_count'] == 1
    assert result['coverage'] == 0.5
    assert result['points'][0]['missing_tokens'] == ['Value', '!=']


def test_empty_draft_and_empty_token_list_are_not_perfect_quality_scores():
    runner = load_runner()
    assert runner.token_coverage([], [])['coverage'] is None
    result = runner.token_coverage([{'point_id': 'k1', 'critical_tokens': []}], [])
    assert result['coverage'] is None
    assert result['points'][0]['status'] == 'unscored_no_tokens'


def test_started_attempt_survives_without_terminal_record(tmp_path):
    runner = load_runner()
    path = tmp_path / 'attempt.json'
    runner.write_json(path, {'run_id': 'one', 'mode': 'live', 'purpose': 'quality_pilot', 'status': 'started'})
    persisted = json.loads(path.read_text(encoding='utf-8'))
    assert persisted['status'] == 'started'
    assert persisted['run_id'] == 'one'


def cached_pilot(tmp_path, manifest):
    runner = load_runner()
    previous = tmp_path / 'previous'
    runner.write_json(previous / 'frozen-inputs.json', manifest)
    runner.write_json(previous / 'G1/recognition.json', {'pages': [{
        'sample_id': 'P01', 'page_no': 1, 'recognized_text': '原始内容',
        'structured': {'title': '原页', 'points': [{'content': '原始内容'}],
                       '_meta': {'provider': 'deepseek', 'prompt_version': '1.3.0',
                                 'request_id': 'real-cached-request',
                                 'input': {'sha256': manifest['pages'][0]['sha256']}}},
        'confidence': 0.9, 'low_confidence': False, 'failed': False, 'error_msg': None,
    }]})
    return previous


def test_recheck_reuses_only_exact_image_hashes_and_keeps_provider_metadata(dataset, tmp_path):
    runner = load_runner()
    manifest = runner.prepare_manifest(dataset[0])
    previous = cached_pilot(tmp_path, manifest)
    cached = runner.load_cached_recognition(manifest, previous, refresh_samples=[])
    assert cached['P01']['structured']['_meta']['prompt_version'] == '1.3.0'
    assert runner.load_cached_recognition(manifest, previous, refresh_samples=['P01']) == {}


def test_valid_diagram_only_cache_is_reused_without_losing_structure(dataset, tmp_path):
    runner = load_runner()
    manifest = runner.prepare_manifest(dataset[0])
    previous = cached_pilot(tmp_path, manifest)
    path = previous / 'G1/recognition.json'
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    snapshot['pages'][0]['structured'].update(points=[], flows=[{'name': '流程', 'steps': ['A', 'B']}])
    runner.write_json(path, snapshot)
    assert runner.load_cached_recognition(manifest, previous, [])['P01']['structured']['flows'][0]['steps'] == ['A', 'B']


def test_request_plan_includes_possible_detail_review_before_paid_calls():
    runner = load_runner()
    manifest = {'pages': list(range(8)), 'task_groups': ['C1', 'C2']}
    assert runner.planned_requests(manifest, {}) == (12, 20)
    assert runner.planned_requests(manifest, {i: {} for i in range(6)}) == (6, 8)
    assert runner.planned_requests(manifest, {i: {} for i in range(8)}) == (4, 4)
    assert runner.planned_requests(manifest, {}, model_retries=1) == (12, 40)


@pytest.mark.parametrize('change', ['hash', 'provider', 'failed', 'unknown_refresh',
                                   'metadata_hash', 'page_no', 'request_id'])
def test_recheck_rejects_changed_input_or_unverified_cache(dataset, tmp_path, change):
    runner = load_runner()
    manifest = runner.prepare_manifest(dataset[0])
    previous = cached_pilot(tmp_path, manifest)
    if change == 'hash':
        frozen = json.loads((previous / 'frozen-inputs.json').read_text(encoding='utf-8'))
        frozen['pages'][0]['sha256'] = 'different'
        runner.write_json(previous / 'frozen-inputs.json', frozen)
    elif change in {'provider', 'failed', 'metadata_hash', 'page_no', 'request_id'}:
        path = previous / 'G1/recognition.json'
        snapshot = json.loads(path.read_text(encoding='utf-8'))
        if change == 'provider':
            snapshot['pages'][0]['structured']['_meta']['provider'] = 'mock'
        elif change == 'failed':
            snapshot['pages'][0]['failed'] = True
        elif change == 'metadata_hash':
            snapshot['pages'][0]['structured']['_meta']['input']['sha256'] = 'other-image'
        elif change == 'page_no':
            snapshot['pages'][0]['page_no'] = 9
        else:
            snapshot['pages'][0]['structured']['_meta']['request_id'] = ''
        runner.write_json(path, snapshot)
    with pytest.raises(ValueError):
        runner.load_cached_recognition(manifest, previous,
                                       refresh_samples=['unknown'] if change == 'unknown_refresh' else [])


@pytest.mark.parametrize('ledger_data', [None, {}, {'reserved_fen': True, 'cap_fen': 500},
                                       {'reserved_fen': 440, 'cap_fen': 400}])
def test_quality_preflight_never_recreates_missing_or_invalid_shared_allowance(tmp_path, ledger_data):
    path = tmp_path / 'budget.json'
    if ledger_data is not None:
        path.write_text(json.dumps(ledger_data), encoding='utf-8')
    before = path.read_bytes() if path.exists() else None
    with pytest.raises((ValueError, OSError)):
        load_runner().shared_budget_preflight(SimpleNamespace(model_budget_cny=5), path, 6, 6)
    assert (path.read_bytes() if path.exists() else None) == before


def test_quality_preflight_counts_only_needed_requests_and_respects_cap(tmp_path):
    path = tmp_path / 'budget.json'
    path.write_text('{"reserved_fen":440,"cap_fen":500}', encoding='utf-8')
    runner = load_runner()
    settings = SimpleNamespace(model_budget_cny=5)
    assert runner.shared_budget_preflight(settings, path, 6, 6)['remaining_cny'] == 0.6
    with pytest.raises(ValueError):
        runner.shared_budget_preflight(settings, path, 7, 12)
    with pytest.raises(ValueError):
        runner.shared_budget_preflight(SimpleNamespace(model_budget_cny=6), path, 6, 6)


def test_quality_preflight_accepts_explicitly_disabled_local_cap(tmp_path):
    path = tmp_path / 'budget.json'
    path.write_text('{"reserved_fen":500,"cap_fen":500}', encoding='utf-8')
    settings = SimpleNamespace(model_budget_cny=5, model_budget_enforced=False)
    budget = load_runner().shared_budget_preflight(settings, path, 12, 12)
    assert budget['enforced'] is False and budget['cap_cny'] is None
    assert budget['reserved_cny'] == 5 and budget['remaining_cny'] is None


def test_optional_cap_can_be_reenabled_without_discarding_uncapped_history(tmp_path):
    path = tmp_path / 'budget.json'
    path.write_text('{"reserved_fen":320,"cap_fen":null,"enforced":false}', encoding='utf-8')
    settings = SimpleNamespace(model_budget_cny=5, model_budget_enforced=True)
    assert load_runner().shared_budget_preflight(settings, path, 3, 3)['remaining_cny'] == 1.8
    assert json.loads(path.read_text())['reserved_fen'] == 320


@pytest.mark.parametrize('change', ['rejected', 'missing', 'swapped', 'changed_bytes'])
def test_partial_or_mismapped_upload_never_receives_cached_transcription(tmp_path, change):
    runner = load_runner()
    paths = [tmp_path / 'one.jpg', tmp_path / 'two.jpg']
    for index, path in enumerate(paths):
        path.write_bytes(str(index).encode())
    inputs = [{'absolute_image_path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
              for path in paths]
    pages = [SimpleNamespace(page_no=i + 1, file_path=str(path), filename=path.name)
             for i, path in enumerate(paths)]
    upload = {'page_count': 2, 'rejected': []}
    runner.validate_uploaded_inputs(upload, pages, inputs)
    if change == 'rejected':
        upload['rejected'] = [{'filename': 'one.jpg'}]
    elif change == 'missing':
        pages.pop()
    elif change == 'swapped':
        pages[0].file_path, pages[1].file_path = pages[1].file_path, pages[0].file_path
    else:
        paths[0].write_bytes(b'changed after validation')
    with pytest.raises(ValueError):
        runner.validate_uploaded_inputs(upload, pages, inputs)


def test_selected_recognition_uses_query_page_number_and_stops_on_business_failure():
    runner = load_runner()
    calls = []

    class Client:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(is_success=True, json=lambda: {'status': 'partial'})

    with pytest.raises(ValueError):
        runner.recognize_selected(Client(), 42, [2, 5])
    assert calls == [('/api/tasks/42/recognize', {'params': {'page_no': 2}})]


def test_interrupted_pilot_remains_valid_incomplete_evidence(tmp_path):
    runner = load_runner()
    record = {'run_id': 'interrupted', 'mode': 'live', 'purpose': 'quality_pilot',
              'status': 'started', 'started_at': '2026-10-21T00:00:00+00:00', 'finished_at': None}
    path = tmp_path / 'attempt.json'
    with pytest.raises(KeyboardInterrupt):
        try:
            raise KeyboardInterrupt()
        finally:
            runner.persist_attempt_finish(path, record)
    persisted = json.loads(path.read_text(encoding='utf-8'))
    assert persisted['status'] == 'started' and persisted['finished_at'] is None
    spec = importlib.util.spec_from_file_location('acceptance_metrics', SCRIPT.with_name('acceptance_metrics.py'))
    metrics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metrics)
    assert metrics.reliability_summary([persisted])['quality_pilot']['live']['incomplete'] == 1
