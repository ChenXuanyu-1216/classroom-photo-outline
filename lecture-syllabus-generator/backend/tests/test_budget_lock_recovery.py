"""Real child processes exercise locks using only pytest-owned ledgers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.errors import AppError
from app.services import model_budget


BACKEND = Path(__file__).resolve().parents[1]


def _child(source, ledger):
    # conftest pins all app paths and disables live credentials before collection.
    env = os.environ.copy()
    env.update({"APP_MODEL_PROVIDER": "mock", "APP_MODEL_API_KEY": "",
                "APP_MODEL_BUDGET_ENFORCED": "true", "APP_MODEL_BUDGET_CNY": "5",
                "PYTHONIOENCODING": "utf-8"})
    return subprocess.Popen([sys.executable, "-c", source, str(ledger)],
                            cwd=BACKEND, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, encoding="utf-8")


def _finish_child(child):
    try:
        return child.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()
        stdout, stderr = child.communicate(timeout=5)
        pytest.fail("Budget test child did not exit: " + stdout + stderr)


def test_live_process_keeps_its_budget_lock_until_reservation_finishes(tmp_path):
    """Catch releasing/unlinking a live owner's lock or checking cap unlocked."""
    ledger = tmp_path / "live-budget.json"
    ledger.write_text('{"reserved_fen":490}', encoding="utf-8")
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    child = _child('''
import json, sys, time
from pathlib import Path
from app.services.model_budget import reserve_budget
ledger = Path(sys.argv[1])
read_text = Path.read_text
def hold_read(path, *args, **kwargs):
    if path == ledger:
        (ledger.parent / "ready").write_text("locked")
        deadline = time.monotonic() + 15
        while not (ledger.parent / "release").exists():
            if time.monotonic() > deadline:
                raise AssertionError("Parent did not release live holder")
            time.sleep(0.01)
    return read_text(path, *args, **kwargs)
Path.read_text = hold_read
print(json.dumps({"reserved_fen": reserve_budget(ledger)}), flush=True)
''', ledger)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Child never acquired its budget lock"
        for _ in range(2):
            with pytest.raises(AppError) as busy:
                model_budget.reserve_budget(ledger)
            assert busy.value.code == "TEST_BUDGET_BUSY"
            assert json.loads(ledger.read_text())["reserved_fen"] == 490
    finally:
        release.write_text("continue")
        stdout, stderr = _finish_child(child)
    assert child.returncode == 0, stdout + stderr
    assert json.loads(stdout) == {"reserved_fen": 500}
    with pytest.raises(AppError) as exhausted:
        model_budget.reserve_budget(ledger)
    assert exhausted.value.code == "TEST_BUDGET_EXHAUSTED"
    assert json.loads(ledger.read_text())["reserved_fen"] == 500


def test_crash_after_ledger_replace_keeps_reservation_charged(tmp_path):
    """Catch a crash leaving permanent contention or refunding a saved charge."""
    ledger = tmp_path / "charged-budget.json"
    ledger.write_text('{"reserved_fen":490}', encoding="utf-8")
    child = _child('''
import os, sys
from pathlib import Path
from app.services.model_budget import reserve_budget
ledger = Path(sys.argv[1])
replace = Path.replace
def exit_after_replace(path, target):
    result = replace(path, target)
    if target == ledger:
        os._exit(75)
    return result
Path.replace = exit_after_replace
reserve_budget(ledger)
''', ledger)
    stdout, stderr = _finish_child(child)
    assert child.returncode == 75, stdout + stderr
    assert json.loads(ledger.read_text())["reserved_fen"] == 500
    with pytest.raises(AppError) as exhausted:
        model_budget.reserve_budget(ledger)
    assert exhausted.value.code == "TEST_BUDGET_EXHAUSTED"
    assert json.loads(ledger.read_text())["reserved_fen"] == 500


def test_unknown_legacy_lock_is_preserved_until_coordinated_migration(tmp_path):
    """Catch treating an unmarked old lock as proof that its writer has died."""
    ledger = tmp_path / "legacy-budget.json"
    ledger.write_text('{"reserved_fen":490}', encoding="utf-8")
    legacy = ledger.with_suffix(".lock")
    legacy.write_bytes(b"")
    with pytest.raises(AppError) as busy:
        model_budget.reserve_budget(ledger)
    assert busy.value.code == "TEST_BUDGET_BUSY"
    assert legacy.read_bytes() == b""
    assert json.loads(ledger.read_text())["reserved_fen"] == 490
    # The fixture is test-owned; this models removal only after old workers stop.
    legacy.unlink()
    assert model_budget.reserve_budget(ledger) == 500
