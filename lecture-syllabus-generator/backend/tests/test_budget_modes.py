import json

import pytest

from app.config import settings
from app.errors import AppError
from app.services import model_budget


def test_disabled_limit_keeps_prior_reservations_and_unblocks_metadata(client, tmp_path, monkeypatch):
    ledger = tmp_path / 'budget.json'
    ledger.write_text('{"reserved_fen":500,"cap_fen":500}', encoding='utf-8')
    monkeypatch.setattr(model_budget, 'BUDGET_PATH', ledger)
    monkeypatch.setattr(settings, 'model_budget_enforced', False)
    monkeypatch.setattr(settings, 'model_provider', 'deepseek')
    monkeypatch.setattr(settings, 'model_api_key', 'offline-test-key')
    model_budget.reserve_request({'messages': [{'content': 'test'}], 'max_tokens': 128})
    assert json.loads(ledger.read_text())['reserved_fen'] == 510
    budget = client.get('/api/meta').json()['model_budget']
    assert budget['enforced'] is False and budget['available'] is True
    assert budget['cap_cny'] is None and budget['remaining_cny'] is None
    assert budget['reserved_cny'] == 5.1


def test_opted_in_cap_still_stops_before_reservation(tmp_path, monkeypatch):
    ledger = tmp_path / 'budget.json'
    ledger.write_text('{"reserved_fen":500,"cap_fen":500}', encoding='utf-8')
    monkeypatch.setattr(settings, 'model_budget_enforced', True)
    with pytest.raises(AppError) as failure:
        model_budget.reserve_budget(ledger)
    assert failure.value.code == 'TEST_BUDGET_EXHAUSTED'
    assert json.loads(ledger.read_text())['reserved_fen'] == 500


def test_disabled_cap_does_not_silently_reset_corrupt_usage(tmp_path, monkeypatch):
    ledger = tmp_path / 'budget.json'
    ledger.write_text('corrupt history', encoding='utf-8')
    monkeypatch.setattr(settings, 'model_budget_enforced', False)
    with pytest.raises(AppError) as failure:
        model_budget.reserve_budget(ledger)
    assert failure.value.code == 'TEST_BUDGET_INVALID'
    assert ledger.read_text() == 'corrupt history'
