"""Reports must fingerprint the prompts loaded by the app, including overrides."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND.parent / 'scripts'

# Run the real report writers in fresh processes: the quality runner deliberately
# rejects a process that already imported the application's database. Empty case
# selections exercise report construction without requesting model completions.
CHILD = r'''
import importlib
import json
from pathlib import Path
import sys
import httpx

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
def reject_network(*args, **kwargs):
    raise AssertionError('Network is forbidden in prompt provenance tests')
httpx.HTTPTransport.handle_request = reject_network

from app.prompts_loader import load_prompt
loaded = load_prompt('expand')
runner = importlib.import_module(sys.argv[3])
manifest = {'dataset_id': 'fixture', 'annotation_status': 'draft_reference_not_human_gold',
            'pages': [{'sample_id': 'CP003'}, {'sample_id': 'CP008'}], 'task_groups': []}
output_root = Path(sys.argv[4])
if sys.argv[3] == 'run_quality_acceptance':
    report = runner.run_live(manifest, output_root)
    assert report['execution_status'] == 'completed', report
    output = Path(report['output_directory'])
else:
    output, report = runner.run_live(manifest, 1, output_root, case_ids=[])
    assert report['status'] == 'completed', report
assert report['request_count'] == 0
persisted = json.loads((output / 'report.json').read_text(encoding='utf-8'))
print(json.dumps({'loaded': loaded, 'prompt_hashes': persisted['prompt_hashes']}))
'''


@pytest.mark.parametrize('runner', ['run_quality_acceptance', 'run_content_regressions'])
@pytest.mark.parametrize('directory_mode', ['default', 'relative', 'absolute'])
def test_report_hashes_match_effective_loaded_prompts(tmp_path, prompt_workspace, runner, directory_mode):
    if directory_mode == 'default':
        prompts = BACKEND / 'prompts'
        configured = 'prompts'
    else:
        prompts = prompt_workspace / 'custom prompts'
        prompts.mkdir()
        (prompts / 'expand.yaml').write_bytes(b'prompt: fixture\nversion: test\n')
        (prompts / 'notes.txt').write_text('not a prompt', encoding='utf-8')
        configured = (os.path.relpath(prompts, BACKEND) if directory_mode == 'relative'
                      else str(prompts))
    expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in prompts.glob('*.yaml')}
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'deepseek-test-budget.json').write_text(
        '{"reserved_fen":0,"cap_fen":null,"enforced":false}', encoding='utf-8')
    environment = {**os.environ, 'APP_PROMPTS_DIR': configured,
                   'APP_DATA_DIR': str(data), 'APP_STORAGE_DIR': str(tmp_path / 'storage'),
                   'APP_DATABASE_URL': 'sqlite:///' + (data / 'unused.db').as_posix(),
                   'APP_MODEL_API_KEY': 'synthetic-unused-key',
                   'APP_MODEL_BASE_URL': 'https://api.deepseek.com',
                   'APP_MODEL_NAME': 'deepseek-flash', 'APP_MODEL_BUDGET_ENFORCED': 'false'}
    result = subprocess.run(
        [sys.executable, '-X', 'utf8', '-c', CHILD, str(BACKEND), str(SCRIPTS),
         runner, str(tmp_path / 'reports')], cwd=tmp_path, env=environment,
        capture_output=True, text=True, encoding='utf-8', timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout.strip().splitlines()[-1])
    if directory_mode != 'default':
        assert observed['loaded'] == {'prompt': 'fixture', 'version': 'test'}
    assert observed['prompt_hashes'] == expected


@pytest.fixture
def prompt_workspace():
    # Windows' system temp can be on another drive; relative APP_PROMPTS_DIR
    # needs a fixture on the backend's drive to exercise real path resolution.
    root = (BACKEND / 'data').resolve()
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='prompt-provenance-', dir=root) as temporary:
        path = Path(temporary).resolve()
        assert path.parent == root
        yield path
