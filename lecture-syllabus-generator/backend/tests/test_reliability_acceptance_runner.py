"""The CLI must preserve evidence without implicitly starting a paid campaign."""

from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_reliability_acceptance.py"
SECRET = "sk-test-secret-never-persist"


def load_runner():
    spec = importlib.util.spec_from_file_location("reliability_acceptance_runner", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def passed_report():
    return {"status": "passed", "original_count": 2, "ai_generated_count": 3,
            "branches_present": {"supervised": True, "unsupervised": True, "reinforcement": True},
            "edit_save_export": "passed", "recognition_request_id": "request-123",
            "message": SECRET, "private_raw_content": SECRET}


def attempt(run_id, status="success", purpose="availability_probe", mode="live"):
    return {"run_id": run_id, "mode": mode, "purpose": purpose, "status": status,
            "started_at": "2026-10-01T00:00:00+00:00",
            "finished_at": None if status == "started" else "2026-10-01T00:01:00+00:00"}


@pytest.fixture
def configured(tmp_path, monkeypatch):
    runner = load_runner()
    ledger = tmp_path / "shared-data" / "deepseek-test-budget.json"
    ledger.parent.mkdir()
    ledger.write_text('{"reserved_fen":320,"cap_fen":500}', encoding="utf-8")
    settings = SimpleNamespace(
        model_api_key=SECRET, model_base_url="https://api.deepseek.com/v1", model_name="deepseek-flash",
        model_budget_cny=Decimal("5.00"), baseline_version="fixture-v1",
        data_dir=str(ledger.parent), storage_dir=str(tmp_path / "shared-storage"),
    )
    monkeypatch.setattr(runner, "_load_configuration", lambda: (settings, ledger))
    context = {"code_commit": "a" * 40, "prompt_hashes": {"expand.yaml": "f" * 64},
               "code_file_hashes": {"app/core.py": "e" * 64}, "python_version": "3.10-test",
               "baseline_version": "fixture-v1", "model_name": "deepseek-flash"}
    monkeypatch.setattr(runner, "_version_context", lambda settings: context)
    return runner, settings, ledger, tmp_path / "probes"


def test_default_probe_never_reads_live_configuration_or_starts_child(tmp_path, monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner, "_load_configuration", lambda: pytest.fail("dry mode read live config"))
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("dry mode started child"))
    output = tmp_path / "must-not-exist"
    result = runner.run_probe(output_root=output)
    assert result["status"] == "not_called"
    assert result["paid_request_started"] is False
    assert not output.exists()


def test_live_probe_persists_started_before_one_bounded_child_and_retains_only_safe_fields(configured, monkeypatch):
    runner, settings, ledger, output = configured
    before = ledger.read_bytes()
    calls = []
    times = iter(["2026-10-21T01:00:00+00:00", "2026-10-21T01:00:10+00:00"])
    monkeypatch.setattr(runner, "utc_now", lambda: next(times))

    def child(command, **kwargs):
        paths = list(output.rglob("attempt.json"))
        assert len(paths) == 1
        saved = json.loads(paths[0].read_text(encoding="utf-8"))
        assert saved["status"] == "started"
        assert saved["finished_at"] is None
        assert saved["purpose"] == "availability_probe"
        assert saved["context"]["code_commit"] == "a" * 40
        assert command == [runner.sys.executable, str(runner.PROBE_SCRIPT), "--live"]
        assert kwargs["timeout"] == 240
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        environment = kwargs["env"]
        assert environment["APP_MODEL_MAX_RETRY"] == "0"
        assert environment["APP_MODEL_PROVIDER"] == "deepseek"
        assert environment["APP_CLUSTER_PROVIDER"] == "model"
        assert environment["APP_KNOWLEDGE_EXPANSION_ENABLED"] == "true"
        assert environment["APP_DATA_DIR"] == settings.data_dir
        assert environment["APP_MODEL_BUDGET_CNY"] == "5.00"
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(passed_report()), SECRET)

    monkeypatch.setattr(runner.subprocess, "run", child)
    result = runner.run_probe(live=True, output_root=output)
    assert len(calls) == 1
    assert result["status"] == "success"
    assert result["started_at"] == "2026-10-21T01:00:00+00:00"
    assert result["finished_at"] == "2026-10-21T01:00:10+00:00"
    persisted = Path(result["attempt_path"]).read_text(encoding="utf-8")
    assert json.loads(persisted)["probe_checks"]["recognition_request_id"] == "request-123"
    assert SECRET not in persisted
    assert SECRET not in json.dumps(result)
    assert ledger.read_bytes() == before
    summary = runner.summarize([output])
    assert summary["sample_size"] == summary["successes"] == 1
    assert summary["long_term_status"] == "insufficient_evidence"


@pytest.mark.parametrize(("change", "value"), [
    ("model_api_key", ""), ("model_base_url", "https://example.test/v1"),
    ("model_base_url", "https://api.deepseek.com.evil.test"),
    ("model_budget_cny", Decimal("5.01")),
])
def test_invalid_live_configuration_stops_before_attempt_or_child(configured, monkeypatch, change, value):
    runner, settings, _, output = configured
    setattr(settings, change, value)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("invalid config started child"))
    with pytest.raises(ValueError):
        runner.run_probe(live=True, output_root=output)
    assert not output.exists()


@pytest.mark.parametrize("ledger_content", [None, "broken", '{"reserved_fen":true}',
    '{"reserved_fen":-1}', '{"reserved_fen":471,"cap_fen":500}',
    '{"reserved_fen":320,"cap_fen":true}', '{"reserved_fen":320,"cap_fen":400}'])
def test_missing_corrupt_or_insufficient_shared_budget_cannot_create_a_fresh_allowance(configured, monkeypatch, ledger_content):
    runner, _, ledger, output = configured
    if ledger_content is None:
        ledger.unlink()
    else:
        ledger.write_text(ledger_content, encoding="utf-8")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("bad ledger started child"))
    with pytest.raises((OSError, ValueError)):
        runner.run_probe(live=True, output_root=output)
    assert not output.exists()


def test_exact_thirty_fen_remaining_is_allowed_without_reserving_a_new_budget(configured, monkeypatch):
    runner, _, ledger, output = configured
    ledger.write_text('{"reserved_fen":470,"cap_fen":500}', encoding="utf-8")
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, json.dumps(passed_report()), ""))
    assert runner.run_probe(live=True, output_root=output)["status"] == "success"
    assert json.loads(ledger.read_text(encoding="utf-8"))["reserved_fen"] == 470


def test_probe_honors_disabled_cap_and_passes_it_to_child(configured, monkeypatch):
    runner, settings, ledger, output = configured
    settings.model_budget_enforced = False
    ledger.write_text('{"reserved_fen":500,"cap_fen":500}', encoding='utf-8')

    def child(command, **kwargs):
        assert kwargs['env']['APP_MODEL_BUDGET_ENFORCED'] == 'false'
        return subprocess.CompletedProcess(command, 0, json.dumps(passed_report()), '')

    monkeypatch.setattr(runner.subprocess, 'run', child)
    result = runner.run_probe(live=True, output_root=output)
    assert result['status'] == 'success'
    assert result['budget_before']['enforced'] is False
    assert ledger.read_text() == '{"reserved_fen":500,"cap_fen":500}'


def test_optional_cap_can_be_reenabled_with_uncapped_usage_history(configured):
    runner, settings, ledger, _ = configured
    settings.model_budget_enforced = True
    ledger.write_text('{"reserved_fen":320,"cap_fen":null,"enforced":false}', encoding='utf-8')
    budget, _ = runner._preflight(settings, ledger)
    assert budget['remaining_cny'] == 1.8
    assert json.loads(ledger.read_text())['reserved_fen'] == 320


@pytest.mark.parametrize(("returncode", "stdout", "error_type"), [
    (1, json.dumps(passed_report()), "SubprocessNonzeroExit"),
    (0, SECRET + " not JSON", "InvalidProbeJSON"),
    (0, "[]", "InvalidProbeJSON"),
    (0, json.dumps({"status": "passed"}), "ProbeChecksFailed"),
])
def test_child_failure_is_persisted_and_not_leaked(configured, monkeypatch, returncode, stdout, error_type):
    runner, _, _, output = configured
    calls = []
    def child(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, SECRET)
    monkeypatch.setattr(runner.subprocess, "run", child)
    result = runner.run_probe(live=True, output_root=output)
    assert len(calls) == 1
    assert result["status"] == "failed"
    assert result["error_type"] == error_type
    assert result["finished_at"] is not None
    assert SECRET not in Path(result["attempt_path"]).read_text(encoding="utf-8")
    summary = runner.summarize([output])
    assert summary["sample_size"] == summary["failures"] == 1


@pytest.mark.parametrize(("field", "value"), [
    ("status", "failed"), ("original_count", 0), ("original_count", True),
    ("ai_generated_count", 0), ("ai_generated_count", "3"),
    ("branches_present", {"supervised": True, "unsupervised": True}),
    ("branches_present", {"supervised": True, "unsupervised": True, "reinforcement": 1}),
    ("edit_save_export", "failed"), ("recognition_request_id", ""),
    ("recognition_request_id", SECRET),
])
def test_zero_exit_does_not_replace_all_semantic_probe_checks(configured, monkeypatch, field, value):
    runner, _, _, output = configured
    report = passed_report()
    report[field] = value
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, json.dumps(report), SECRET))
    result = runner.run_probe(live=True, output_root=output)
    assert result["status"] == "failed"
    assert result["error_type"] == "ProbeChecksFailed"
    assert SECRET not in Path(result["attempt_path"]).read_text(encoding="utf-8")


@pytest.mark.parametrize(("exception", "expected"), [
    (subprocess.TimeoutExpired("fake", 240, output=SECRET, stderr=SECRET), "TimeoutExpired"),
    (OSError(SECRET), "OSError"),
])
def test_timeout_and_launch_errors_persist_failed_without_retry(configured, monkeypatch, exception, expected):
    runner, _, _, output = configured
    calls = []
    def child(*args, **kwargs):
        calls.append(args)
        raise exception
    monkeypatch.setattr(runner.subprocess, "run", child)
    result = runner.run_probe(live=True, output_root=output)
    assert len(calls) == 1
    assert result["status"] == "failed"
    assert result["error_type"] == expected
    assert SECRET not in json.dumps(result)


def test_process_death_leaves_a_started_record_in_the_summary_denominator(configured, monkeypatch):
    runner, _, _, output = configured
    def die(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(runner.subprocess, "run", die)
    with pytest.raises(KeyboardInterrupt):
        runner.run_probe(live=True, output_root=output)
    summary = runner.summarize([output])
    assert summary["sample_size"] == summary["incomplete"] == 1
    assert summary["success_rate"] == 0
    assert summary["long_term_status"] == "insufficient_evidence"


def test_summary_reads_both_roots_without_mutation_and_deduplicates_overlapping_paths(tmp_path):
    runner = load_runner()
    reliability, quality = tmp_path / "reliability", tmp_path / "acceptance"
    records = [(reliability / "ok/attempt.json", attempt("ok")),
               (reliability / "interrupted/attempt.json", attempt("unfinished", "started")),
               (quality / "pilot/course/attempt.json", attempt("pilot", purpose="quality_pilot")),
               (quality / "offline/attempt.json", attempt("offline", mode="offline"))]
    for path, value in records:
        runner.write_json(path, value)
    snapshots = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path, _ in records}
    result = runner.summarize([reliability, quality, reliability / "ok"])
    assert result["sample_size"] == 2
    assert result["successes"] == result["incomplete"] == 1
    assert result["quality_pilot"]["live"]["successes"] == 1
    assert result["offline_availability_probe"]["successes"] == 1
    assert result["record_files"] == 4
    assert result["manual_review_required"] is True
    assert result["sampling_distribution_review"] == "not_confirmed"
    assert result["version_comparability_review"] == "not_confirmed"
    assert "证据不足" in result["long_term_explanation"]
    for path, before in snapshots.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_no_default_record_directories_means_not_started_not_an_error(tmp_path, monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner, "DEFAULT_ROOTS", (tmp_path / "a", tmp_path / "b"))
    result = runner.summarize()
    assert result["long_term_status"] == "not_started"
    assert "尚未开始" in result["long_term_explanation"]
    assert result["sample_size"] == result["record_files"] == 0


@pytest.mark.parametrize("content", ["not-json", "[]", '{"status":"failed"}'])
def test_bad_record_is_an_error_and_never_silently_dropped(tmp_path, content):
    runner = load_runner()
    root = tmp_path / "runs"
    root.mkdir()
    (root / "attempt.json").write_text(content, encoding="utf-8")
    with pytest.raises((TypeError, ValueError)):
        runner.summarize([root])


def test_explicit_missing_root_is_an_error_even_if_another_root_exists(tmp_path):
    runner = load_runner()
    existing = tmp_path / "exists"
    existing.mkdir()
    with pytest.raises(FileNotFoundError):
        runner.summarize([existing, tmp_path / "missing"])


def test_conflicting_records_across_roots_are_rejected(tmp_path):
    runner = load_runner()
    runner.write_json(tmp_path / "one/attempt.json", attempt("same"))
    runner.write_json(tmp_path / "two/attempt.json", attempt("same", "failed"))
    with pytest.raises(ValueError, match="conflicting"):
        runner.summarize([tmp_path])


def test_cli_defaults_to_dry_and_summary_can_atomically_write_json(tmp_path, monkeypatch, capsys):
    runner = load_runner()
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("offline CLI started child"))
    assert runner.main([]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "not_called"
    root = tmp_path / "runs"
    root.mkdir()
    destination = tmp_path / "reports/summary.json"
    assert runner.main(["summarize", "--root", str(root), "--root", str(root),
                        "--output", str(destination)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["long_term_status"] == "not_started"
    assert json.loads(destination.read_text(encoding="utf-8")) == printed


@pytest.mark.parametrize("output_name", ["attempt.json", "ATTEMPT.JSON"])
def test_cli_rejects_overwriting_attempt_records_with_a_summary(tmp_path, capsys, output_name):
    runner = load_runner()
    path = tmp_path / "attempt.json"
    runner.write_json(path, attempt("one"))
    before = path.read_bytes()
    destination = tmp_path / output_name
    assert runner.main(["summarize", "--root", str(tmp_path), "--output", str(destination)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    assert path.read_bytes() == before


def test_cli_summary_error_never_prints_untrusted_record_content(tmp_path, capsys):
    runner = load_runner()
    (tmp_path / "attempt.json").write_text(SECRET, encoding="utf-8")
    assert runner.main(["summarize", "--root", str(tmp_path)]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "error"
    assert SECRET not in output


def test_atomic_json_failure_preserves_the_previous_complete_record(tmp_path, monkeypatch):
    runner = load_runner()
    path = tmp_path / "attempt.json"
    runner.write_json(path, {"status": "started"})
    before = path.read_bytes()
    def refuse_replace(*args, **kwargs):
        raise OSError("simulated replacement failure")
    monkeypatch.setattr(runner.os, "replace", refuse_replace)
    with pytest.raises(OSError):
        runner.write_json(path, {"status": "success"})
    assert path.read_bytes() == before


def test_version_context_records_frozen_prompt_and_code_hashes(tmp_path, monkeypatch):
    runner = load_runner()
    backend = tmp_path / "backend"
    (backend / "prompts").mkdir(parents=True)
    (backend / "app").mkdir()
    (backend / "prompts/expand.yaml").write_bytes(b"abc")
    (backend / "app/core.py").write_bytes(b"abc")
    monkeypatch.setattr(runner, "BACKEND", backend)
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *a, **k: "a" * 40 + "\n")
    settings = SimpleNamespace(baseline_version="fixture-v1", model_name="deepseek-flash",
                               model_base_url="https://api.deepseek.com")
    context = runner._version_context(settings)
    known_sha256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert context["code_commit"] == "a" * 40
    assert context["prompt_hashes"]["expand.yaml"] == known_sha256
    assert context["code_file_hashes"]["app/core.py"] == known_sha256
    assert context["baseline_version"] == "fixture-v1"
    assert context["python_version"]


def test_version_context_hashes_the_effective_configured_prompt_directory(tmp_path, monkeypatch):
    runner = load_runner()
    backend = tmp_path / "backend"
    (backend / "prompts").mkdir(parents=True)
    (backend / "prompts/expand.yaml").write_bytes(b"unused default prompt")
    (backend / "custom-prompts").mkdir()
    (backend / "custom-prompts/expand.yaml").write_bytes(b"abc")
    monkeypatch.setattr(runner, "BACKEND", backend)
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *a, **k: "a" * 40 + "\n")
    settings = SimpleNamespace(baseline_version="fixture-v1", model_name="deepseek-flash",
                               model_base_url="https://api.deepseek.com", prompts_dir="custom-prompts")
    context = runner._version_context(settings)
    assert context["prompt_hashes"]["expand.yaml"] == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
