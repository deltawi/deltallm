import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.billing.accounting.accounting_protocol import ReserveDecision
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.spend.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.cache import CacheKeyBuilder, InMemoryBackend, NoopCacheMetrics, StreamingCacheHandler
from src.services.audit.content_buffer import AuditContentBuffer
from src.services.audit.payload_compaction import DEFAULT_AUDIT_PAYLOAD_MAX_BYTES
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


class _ContentStoredAuditService:
    """Content storage on for every organization; no flush."""

    ingestion_config = SimpleNamespace(payload_max_bytes=DEFAULT_AUDIT_PAYLOAD_MAX_BYTES)

    def peek_content_storage_policy(self, organization_id):  # noqa: ANN001, ANN201
        del organization_id
        return True


@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream-replay"])
async def test_cache_hit_hands_its_content_to_the_audit_buffer(
    client, test_app, monkeypatch, stream
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
    test_app.state.cache_key_builder = CacheKeyBuilder(custom_salt="accounting-cache-content")
    test_app.state.cache_metrics = NoopCacheMetrics()
    test_app.state.streaming_cache_handler = StreamingCacheHandler(backend)
    test_app.state.model_registry["gpt-4o-mini"][0]["model_info"] = {
        "mode": "chat",
        "input_cost_per_token": 1.0,
        "output_cost_per_token": 2.0,
        "input_cost_per_token_cache_hit": 0.25,
    }
    _refresh_runtime_registry(test_app)
    buffer = AuditContentBuffer(_ContentStoredAuditService())
    monkeypatch.setattr(test_app.state, "audit_content_buffer", buffer, raising=False)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "cached content"}],
        "stream": stream,
    }
    try:
        warm = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert warm.status_code == 200, warm.text
        hit = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert hit.status_code == 200, hit.text
        assert hit.headers["x-deltallm-cache-hit"] == "true"
        assert len(repository.finalizations) == 2

        # The miss and the hit each hand over their content.
        assert buffer.pending_items == 2
        item = buffer._items[1]
        assert item.target_event_id == repository.finalizations[1].audit_envelope["event_id"]
        assert item.event.metadata["cache_hit"] is True
        assert item.event.metadata["route"] == "/v1/chat/completions"
        sent = json.loads(item.request_json)
        assert sent["model"] == "gpt-4o-mini"
        assert sent["messages"] == body["messages"]
        cached = next(iter(backend._cache.values())).response
        responses = [
            payload.content_json for payload in item.payloads if payload.kind == "response"
        ]
        assert responses == [cached]
        assert responses[0]["object"] == "chat.completion"
        assert responses[0]["choices"]
    finally:
        await accounting.close()


async def test_http_provider_and_paid_cache_keep_the_complete_local_issue_proof(client, test_app):
    from tests.test_accounting_local_service import state

    funding, persistence, _, receipts, _, accounting = state(dwell_seconds=0)
    accounting.start()
    test_app.state.accounting_protocol_service = accounting
    test_app.state.accounting_protocol_enabled = True
    test_app.state.spend_tracking_service = SpendIngestionService(
        db_client=None, writer=MagicMock(), config=SpendIngestionConfig(), accounting=accounting
    )
    backend = InMemoryBackend(max_size=100)
    test_app.state.cache_backend = backend
    test_app.state.cache_key_builder = CacheKeyBuilder(custom_salt="local-accounting-cache")
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
        response = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert response.headers["x-deltallm-cache-hit"] == "true"
        assert test_app.state.http_client.post_calls == 1
        assert len(funding.calls) == len(persistence.calls) == 2
        terminal = persistence.calls[1][0]
        assert terminal.receipt.reservation.operation_id == terminal.finalization.operation_id
        assert terminal.finalization.exact_charge > 0
        assert terminal.finalization.spend_payload["cache_hit"] is True
        assert receipts.entries == receipts.retained_bytes == 0
        test_app.state.spend_tracking_service.writer.log_spend.assert_not_called()
    finally:
        await accounting.close()
