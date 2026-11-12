from decimal import Decimal

import pytest

from app.config import settings
from app.db import SessionLocal, engine
from app.errors import AppError


def test_database_budget_is_atomic_and_request_idempotent(monkeypatch):
    from app.services.database_budget import initialize_budget, reserve_database_budget
    from app.models import ModelBudget, ModelReservation
    monkeypatch.setattr(settings, 'model_budget_enforced', True)
    monkeypatch.setattr(settings, 'model_budget_cny', Decimal('0.20'))
    initialize_budget(0)
    assert reserve_database_budget(10, 'first-attempt') == 10
    assert reserve_database_budget(10, 'first-attempt') == 10
    with pytest.raises(AppError) as conflict:
        reserve_database_budget(20, 'first-attempt')
    assert conflict.value.code == 'TEST_BUDGET_CONFLICT'
    assert reserve_database_budget(10, 'second-attempt') == 20
    with pytest.raises(AppError) as exhausted:
        reserve_database_budget(1, 'third-attempt')
    assert exhausted.value.code == 'TEST_BUDGET_EXHAUSTED'
    with SessionLocal() as db:
        assert db.get(ModelBudget, 'deepseek').reserved_fen == 20
        assert db.query(ModelReservation).count() == 2


def test_missing_shared_ledger_never_means_zero_spending():
    from app.services.database_budget import reserve_database_budget, database_budget_status
    with pytest.raises(AppError) as missing:
        reserve_database_budget(10, 'no-ledger')
    assert missing.value.code == 'TEST_BUDGET_UNINITIALIZED'
    assert database_budget_status()['available'] is False


def test_initialization_cannot_reset_existing_spending():
    from app.services.database_budget import initialize_budget
    from app.models import ModelBudget
    initialize_budget(100)
    initialize_budget(100)
    with pytest.raises(AppError):
        initialize_budget(0)
    with SessionLocal() as db:
        assert db.get(ModelBudget, 'deepseek').reserved_fen == 100


def test_cloud_calls_and_metadata_use_shared_ledger_even_with_a_local_path(monkeypatch, tmp_path):
    from app.services.database_budget import initialize_budget
    from app.services.model_budget import reserve_request, budget_status
    initialize_budget(0)
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    local = tmp_path / 'must-not-be-created.json'
    reserved = reserve_request({'messages': [], 'max_tokens': 100}, local)
    assert reserved >= 10
    assert not local.exists()
    assert budget_status()['reserved_cny'] == reserved / 100


def test_database_failure_blocks_budget_instead_of_falling_back_to_files():
    from app.models import ModelBudget
    from app.services.database_budget import reserve_database_budget, database_budget_status
    ModelBudget.__table__.drop(engine)
    with pytest.raises(AppError) as unavailable:
        reserve_database_budget(10, 'unavailable')
    assert unavailable.value.code == 'TEST_BUDGET_INVALID'
    assert database_budget_status()['available'] is False


@pytest.mark.parametrize('stage', ['recognition', 'grouping', 'expansion'])
@pytest.mark.parametrize('funded', [True, False])
def test_uncertain_cloud_model_timeout_is_not_replayed(monkeypatch, tmp_path, valid_image_bytes, stage, funded):
    import httpx
    from app.services.extractor import ExtractedKnowledgePoint
    from app.services.recognizer import MultimodalRecognizer
    from app.services.model_clusterer import ModelClusterer
    from app.services.knowledge_expander import KnowledgeExpander
    if funded:
        from app.services.database_budget import initialize_budget
        initialize_budget(0)
    monkeypatch.setattr(settings, 'deployment_mode', 'cloud')
    monkeypatch.setattr(settings, 'model_provider', 'multimodal')
    monkeypatch.setattr(settings, 'model_base_url', 'https://model.invalid/v1')
    monkeypatch.setattr(settings, 'model_api_key', 'synthetic-only')
    monkeypatch.setattr(settings, 'model_max_retry', 2)
    monkeypatch.setattr(settings, 'knowledge_expansion_enabled', True)
    for module in ['recognizer', 'model_clusterer', 'knowledge_expander']:
        monkeypatch.setattr('app.services.' + module + '.wait_before_retry', lambda *args: True)
    requests = []
    original = httpx.Client
    def timeout(request):
        requests.append(request.url.path)
        raise httpx.ReadTimeout('Synthetic unknown outcome', request=request)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(timeout), **kwargs))
    point = ExtractedKnowledgePoint('课堂概念', 'concept', '课堂原话', 'P1', 1, '课堂概念')
    with pytest.raises(AppError) as failure:
        if stage == 'recognition':
            path = tmp_path / 'photo.png'
            path.write_bytes(valid_image_bytes)
            MultimodalRecognizer().recognize(1, str(path))
        elif stage == 'grouping':
            ModelClusterer().cluster([point], {1: '课件'})
        else:
            KnowledgeExpander().expand([point], {1: '课件'})
    assert len(requests) == (1 if funded else 0)
    if not funded:
        assert failure.value.code == 'TEST_BUDGET_UNINITIALIZED'
