from unittest.mock import MagicMock

import pytest

from src.billing.accounting_protocol import ReserveDecision
from src.billing.accounting_service import AccountingProtocolService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.cache import CacheKeyBuilder, InMemoryBackend, NoopCacheMetrics, StreamingCacheHandler
from tests.test_accounting_request_path import _AccountingRepository
from tests.test_cache import _refresh_runtime_registry


@pytest.mark.parametrize(
    "decision,status",
    [
        (ReserveDecision.DISPATCH, 200),
        (ReserveDecision.BUDGET_EXHAUSTED, 429),
        (ReserveDecision.CAPACITY_EXHAUSTED, 503),
        (None, 503),
    ],
)
async def test_cache_response_requires_authoritative_budget_and_terminal_ack(
    client, test_app, decision, status
):
    repository = _AccountingRepository()
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    test_app.state.accounting_protocol_service = accounting
    test_app.state.spend_tracking_service = SpendIngestionService(
        db_client=None, writer=MagicMock(), config=SpendIngestionConfig(), accounting=accounting
    )
    backend = InMemoryBackend(max_size=100)
    test_app.state.cache_backend = backend
    test_app.state.cache_key_builder = CacheKeyBuilder(custom_salt="accounting-cache")
    test_app.state.cache_metrics = NoopCacheMetrics()
    test_app.state.streaming_cache_handler = StreamingCacheHandler(backend)
    test_app.state.model_registry["gpt-4o-mini"][0]["model_info"] = {
        "mode": "chat",
        "input_cost_per_token": 1.0,
        "output_cost_per_token": 2.0,
        "input_cost_per_token_cache_hit": 0.25,
    }
    _refresh_runtime_registry(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "cached"}]}
    try:
        warm = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert warm.status_code == 200, warm.text
        if decision is None:
            test_app.state.accounting_protocol_enabled = True
            test_app.state.accounting_protocol_service = None
        else:
            repository.decision = decision
        response = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert response.status_code == status, response.text
        assert test_app.state.http_client.post_calls == 1
        assert len(repository.reservations) == (1 if decision is None else 2)
        if status == 200:
            assert response.headers["x-deltallm-cache-hit"] == "true"
            assert len(repository.finalizations) == 2
            assert repository.finalizations[1].exact_charge > 0
            assert repository.finalizations[1].spend_payload["cache_hit"] is True
        else:
            assert len(repository.finalizations) == 1
            assert "error" in response.json()
    finally:
        await accounting.close()
