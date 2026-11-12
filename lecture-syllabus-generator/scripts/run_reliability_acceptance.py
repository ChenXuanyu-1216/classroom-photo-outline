"""One opt-in probe or a read-only evidence summary; no scheduler or paid default.

Run without arguments (or ``probe``) for a dry response. Only ``probe --live``
launches the existing synthetic upload-to-Word check. ``summarize`` reads attempt
records from reliability and quality-pilot directories without calling a model.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import runpy
import subprocess
import sys
from uuid import uuid4


APP_ROOT = Path(__file__).resolve().parents[1]
REPO = APP_ROOT.parent
BACKEND = APP_ROOT / "backend"
PROBE_SCRIPT = APP_ROOT / "scripts/check_knowledge_expansion.py"
DEFAULT_PROBE_ROOT = BACKEND / "data/reliability"
DEFAULT_ROOTS = (DEFAULT_PROBE_ROOT, BACKEND / "data/acceptance")
PROBE_TIMEOUT_SECONDS = 240


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: dict):
    """Flush a complete JSON file before atomically replacing its prior version."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_configuration():
    # Import only configuration and the original ledger location, never app.db.
    # Keeping this lazy makes dry mode and summaries independent of .env or DBs.
    backend_path = str(BACKEND)
    added = backend_path not in sys.path
    if added:
        sys.path.insert(0, backend_path)
    try:
        from app.config import settings
        from app.services.model_budget import BUDGET_PATH
        return settings, BUDGET_PATH
    finally:
        if added:
            sys.path.remove(backend_path)


def _preflight(settings, ledger: Path):
    key = settings.model_api_key.strip()
    endpoint = settings.model_base_url.strip().rstrip("/")
    if not key or endpoint not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}:
        raise ValueError("official DeepSeek endpoint and a configured key are required")
    budget = json.loads(Path(ledger).read_text(encoding="utf-8"))
    if not isinstance(budget, dict) or type(budget.get('reserved_fen')) is not int or budget['reserved_fen'] < 0:
        raise ValueError("shared usage ledger must contain a nonnegative integer reservation")
    if not getattr(settings, 'model_budget_enforced', True):
        return {"enforced": False, "cap_cny": None, "reserved_cny": budget['reserved_fen'] / 100,
                "remaining_cny": None}, endpoint
    try:
        cap = Decimal(str(settings.model_budget_cny))
        if not cap.is_finite() or cap <= 0 or cap * 100 != (cap * 100).to_integral_value():
            raise ValueError("shared budget cap must be positive with at most two decimal places")
    except InvalidOperation:
        raise ValueError("invalid shared budget cap") from None
    cap_fen = int(cap * 100)
    # Missing ledgers must not silently become a fresh allowance. This is a read,
    # not a reservation; the child still reserves each request on this ledger.
    if not isinstance(budget, dict):
        raise ValueError("shared budget ledger must be an object")
    reserved = budget.get("reserved_fen")
    recorded_cap = budget.get("cap_fen", cap_fen)
    if recorded_cap is None and budget.get('enforced') is False:
        recorded_cap = cap_fen
    if (type(reserved) is not int or reserved < 0 or type(recorded_cap) is not int
            or recorded_cap <= 0 or cap_fen > recorded_cap):
        raise ValueError("invalid shared budget ledger or an increased cap")
    remaining = cap_fen - reserved
    if remaining < 30:
        raise ValueError("at least 0.30 CNY of the original shared allowance is required")
    return {"cap_cny": float(cap), "reserved_cny": reserved / 100,
            "remaining_cny": remaining / 100}, endpoint


def _file_hash(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version_context(settings):
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True,
        stderr=subprocess.DEVNULL, timeout=10,
    ).strip()
    versions = {}
    for name in ("fastapi", "httpx", "SQLAlchemy", "pydantic", "Pillow", "python-docx"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    code_hashes = {path.relative_to(BACKEND).as_posix(): _file_hash(path)
                   for path in sorted((BACKEND / "app").rglob("*.py"))}
    for path in (Path(__file__), PROBE_SCRIPT):
        code_hashes["scripts/" + path.name] = _file_hash(path)
    prompts_dir = BACKEND / getattr(settings, "prompts_dir", "prompts")
    return {
        "code_commit": commit,
        "code_file_hashes": code_hashes,
        "prompt_hashes": {path.name: _file_hash(path)
                          for path in sorted(prompts_dir.glob("*.yaml"))},
        "python_version": platform.python_version(),
        "package_versions": versions,
        "baseline_version": settings.baseline_version,
        "model_name": settings.model_name,
        "model_base_url": settings.model_base_url.strip().rstrip("/"),
        "local_budget_enforced": getattr(settings, "model_budget_enforced", True),
        "input": "synthetic_definition_only",
    }


def _verified_checks(report, key):
    if not isinstance(report, dict):
        return None
    branches = report.get("branches_present")
    request_id = report.get("recognition_request_id")
    if (report.get("status") != "passed"
            or any(type(report.get(name)) is not int or report[name] <= 0
                   for name in ("original_count", "ai_generated_count"))
            or not isinstance(branches, dict)
            or any(branches.get(name) is not True
                   for name in ("supervised", "unsupervised", "reinforcement"))
            or report.get("edit_save_export") != "passed"
            or not isinstance(request_id, str) or not request_id.strip()
            or key in request_id):
        return None
    # Never persist the full child stdout, stderr, messages, or raw model output.
    return {
        "status": "passed", "original_count": report["original_count"],
        "ai_generated_count": report["ai_generated_count"],
        "branches_present": {name: True for name in ("supervised", "unsupervised", "reinforcement")},
        "edit_save_export": "passed", "recognition_request_id": request_id,
    }


def run_probe(live=False, output_root=DEFAULT_PROBE_ROOT):
    """Perform at most one bounded child invocation, with durable attempt evidence.

    Preflight failures make no attempt record because no probe was launched.
    Interruption or parent-process death after the initial write leaves a started
    record; summaries retain that record as an incomplete, unsuccessful attempt.
    """
    if not live:
        return {"status": "not_called", "purpose": "availability_probe",
                "paid_request_started": False, "scheduler_started": False,
                "note": "默认仅演练；长期付费验证尚未由此命令启动。"}

    settings, ledger = _load_configuration()
    budget_before, endpoint = _preflight(settings, ledger)
    context = _version_context(settings)
    key = settings.model_api_key.strip()
    if key in json.dumps(context, ensure_ascii=False):
        raise ValueError("sensitive configuration cannot be persisted in version context")
    environment = os.environ.copy()
    environment.update({
        "APP_MODEL_PROVIDER": "deepseek", "APP_CLUSTER_PROVIDER": "model",
        "APP_KNOWLEDGE_EXPANSION_ENABLED": "true", "APP_MODEL_MAX_RETRY": "0",
        "APP_MODEL_API_KEY": key, "APP_MODEL_BASE_URL": endpoint,
        "APP_MODEL_NAME": settings.model_name,
        "APP_MODEL_BUDGET_CNY": str(settings.model_budget_cny),
        "APP_MODEL_BUDGET_ENFORCED": str(getattr(settings, 'model_budget_enforced', True)).lower(),
        "APP_DATA_DIR": str(settings.data_dir), "APP_STORAGE_DIR": str(settings.storage_dir),
        "PYTHONIOENCODING": "utf-8",
    })
    run_id = uuid4().hex
    directory = Path(output_root).resolve() / run_id
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "attempt.json"
    record = {
        "schema_version": 1, "run_id": run_id, "mode": "live", "purpose": "availability_probe",
        "status": "started", "started_at": utc_now(), "finished_at": None,
        "context": context, "budget_before": budget_before,
        "budget_ledger": str(Path(ledger).resolve()),
        "timeout_seconds": PROBE_TIMEOUT_SECONDS, "max_retry": 0,
    }
    write_json(path, record)
    error_type = None
    try:
        completed = subprocess.run(
            [sys.executable, str(PROBE_SCRIPT), "--live"], cwd=BACKEND, env=environment,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS, check=False,
        )
        if completed.returncode != 0:
            error_type = "SubprocessNonzeroExit"
        else:
            try:
                report = json.loads(completed.stdout)
            except (ValueError, TypeError):
                report = None
            if not isinstance(report, dict):
                error_type = "InvalidProbeJSON"
            else:
                checks = _verified_checks(report, key)
                if checks is None:
                    error_type = "ProbeChecksFailed"
                else:
                    record["probe_checks"] = checks
    except subprocess.TimeoutExpired:
        error_type = "TimeoutExpired"
    except OSError:
        error_type = "OSError"
    record.update({"status": "failed" if error_type else "success", "finished_at": utc_now()})
    if error_type:
        record["error_type"] = error_type
    write_json(path, record)
    return {**record, "attempt_path": str(path)}


def summarize(roots=None):
    """Read all attempt.json files; malformed or conflicting evidence is an error."""
    explicit_roots = roots is not None
    selected = list(DEFAULT_ROOTS if roots is None else roots)
    directories = []
    for root in selected:
        root = Path(root).resolve()
        if not root.exists():
            if explicit_roots:
                raise FileNotFoundError("explicit evidence root does not exist")
            continue
        if not root.is_dir():
            raise ValueError("evidence roots must be directories")
        directories.append(root)
    files = sorted({path.resolve() for root in directories for path in root.rglob("attempt.json")})
    records = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    helper = runpy.run_path(str(Path(__file__).with_name("acceptance_metrics.py")))
    summary = helper["reliability_summary"](records)
    explanations = {
        "not_started": "长期验证尚未开始；没有真实 availability_probe 记录。",
        "insufficient_evidence": "长期可靠性证据不足；单次成功或短周期试跑不能证明长期成功率。",
        "pass": "统计门槛通过；仍须人工确认采样分布及版本可比性后解释结果。",
        "fail": "统计验收门槛未通过；这不等于已证明真实成功率低于目标。",
    }
    return {
        **summary, "record_files": len(files), "source_roots": sorted({str(path) for path in directories}),
        "long_term_explanation": explanations[summary["long_term_status"]],
        "manual_review_required": True,
        "sampling_distribution_review": "not_confirmed",
        "version_comparability_review": "not_confirmed",
        "review_note": "需人工确认采样分布及代码、提示词、模型版本可比性；间隔和唯一编号不能证明独立性。",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    probe = commands.add_parser("probe", help="默认不调用；仅 --live 明确执行一次真实探测")
    probe.add_argument("--live", action="store_true")
    probe.add_argument("--output-root", type=Path, default=DEFAULT_PROBE_ROOT)
    summary = commands.add_parser("summarize", help="只读汇总现有探测与质量试跑记录")
    summary.add_argument("--root", type=Path, action="append", help="可重复指定，替代默认两类目录")
    summary.add_argument("--output", "--json-output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "summarize":
            if args.output is not None and args.output.name.casefold() == "attempt.json":
                raise ValueError("summary output must not replace an attempt record")
            result = summarize(args.root)
            if args.output is not None:
                write_json(args.output, result)
        else:
            result = run_probe(live=getattr(args, "live", False),
                               output_root=getattr(args, "output_root", DEFAULT_PROBE_ROOT))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if result.get("status") == "failed" else 0
    except (ValueError, TypeError, OSError, subprocess.SubprocessError) as error:
        # Exception messages can contain raw child data or configuration secrets.
        print(json.dumps({"status": "error", "error_type": type(error).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
