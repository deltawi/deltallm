"""Ordinary HTTP admission/finalization ordering through real application routes."""

import gzip
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.billing.spend.spend import SpendTrackingService
from src.billing.spend.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.billing.spend.spend_operations import SpendPersistenceUnavailable
from src.telemetry.lifecycle import WorkerHealth, WorkerState


class ReadySpend(SpendIngestionService):
    @property
    def worker_health(self):
        return WorkerHealth(WorkerState.READY)


def install(test_app):
    operations = SimpleNamespace(begin=AsyncMock(), accept=AsyncMock(), unknown=AsyncMock())
    service = ReadySpend(
        db_client=None,
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True),
        operations=operations,
    )
    test_app.state.spend_tracking_service = service
    return operations


def headers(test_app):
    return {"Authorization": f"Bearer {test_app.state._test_key}", "Cache-Control": "no-store"}


@pytest.mark.parametrize(
    "path,body",
    [
        (
            "/v1/chat/completions",
            {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        ),
        ("/v1/embeddings", {"model": "text-embedding-3-small", "input": "hello"}),
    ],
)
async def test_intent_commits_before_provider_and_receipt_uses_same_identity(
    client, test_app, path, body
):
    operations = install(test_app)
    original = test_app.state.http_client.post

    async def post(*args, **kwargs):
        operations.begin.assert_awaited_once()
        operations.accept.assert_not_awaited()
        return await original(*args, **kwargs)

    test_app.state.http_client.post = post
    response = await client.post(path, headers=headers(test_app), json=body)
    assert response.status_code == 200, response.text
    operations.accept.assert_awaited_once()
    intent = operations.begin.call_args.args[0]
    receipt_handle, receipt = operations.accept.call_args.args
    assert intent == receipt_handle
    assert receipt["organization_id"] == intent.intent.principal.organization_id
    operations.unknown.assert_not_awaited()


async def test_full_persistence_rejects_before_external_call_with_local_503(client, test_app):
    operations = install(test_app)
    operations.begin.side_effect = SpendPersistenceUnavailable()
    test_app.state.spend_tracking_service.repository.enqueue = AsyncMock(
        side_effect=ConnectionError()
    )
    response = await client.post(
        "/v1/chat/completions",
        headers=headers(test_app),
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["type"] == "spend_persistence_unavailable"
    assert test_app.state.http_client.post_calls == 0
    operations.accept.assert_not_awaited()


async def test_receipt_outage_does_not_retry_provider(client, test_app):
    operations = install(test_app)
    operations.accept.side_effect = SpendPersistenceUnavailable()
    response = await client.post(
        "/v1/chat/completions",
        headers=headers(test_app),
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 503, response.text
    assert test_app.state.http_client.post_calls == 1
    operations.begin.assert_awaited_once()
    operations.accept.assert_awaited_once()


async def test_native_terminal_ack_failure_does_not_submit_a_second_outcome(client, test_app):
    from src.billing.accounting.accounting_protocol import AccountingOutcome
    from src.billing.accounting.accounting_service import AccountingProtocolService
    from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
    from src.db.runtime.telemetry_acceptance import AcceptanceFailure
    from tests.test_accounting_request_path import _AccountingRepository

    class LostCompletedAck(_AccountingRepository):
        async def finalize_batch(self, values, *, expires_at):
            self.finalizations.extend(values)
            raise AccountingProtocolUnavailable(AcceptanceFailure.CONNECTION)

    repository = LostCompletedAck()
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    test_app.state.accounting_protocol_enabled = True
    test_app.state.accounting_protocol_service = accounting
    test_app.state.spend_tracking_service = ReadySpend(
        db_client=None,
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True),
        accounting=accounting,
    )
    try:
        response = await client.post(
            "/v1/chat/completions",
            headers=headers(test_app),
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        )
    finally:
        await accounting.close()

    assert response.status_code == 503, response.text
    assert test_app.state.http_client.post_calls == 1
    (terminal,) = repository.finalizations
    assert terminal.outcome is AccountingOutcome.COMPLETED
    assert terminal.exact_charge > 0


async def test_stream_receipt_is_accepted_before_done(client, test_app):
    operations = install(test_app)
    response = await client.post(
        "/v1/chat/completions",
        headers=headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )
    assert response.status_code == 200, response.text
    assert "[DONE]" in response.text
    operations.begin.assert_awaited_once()
    operations.accept.assert_awaited_once()
    assert operations.accept.call_args.args[0] == operations.begin.call_args.args[0]


async def test_pricing_change_during_provider_io_does_not_reprice_receipt(client, test_app):
    operations = install(test_app)
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"input_cost_per_token": 1, "output_cost_per_token": 2}
    original = test_app.state.http_client.post

    async def post(*args, **kwargs):
        deployment.model_info = {"input_cost_per_token": 100, "output_cost_per_token": 200}
        return await original(*args, **kwargs)

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers=headers(test_app),
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200, response.text
    assert operations.accept.call_args.args[1]["cost_exact"] == "3.000000000000000000"


async def test_catalog_change_during_provider_io_does_not_reprice_receipt(
    client, test_app, monkeypatch
):
    from src.billing.pricing.cost import DEFAULT_MODEL_COST_MAP, ModelPricing

    operations = install(test_app)
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {}
    monkeypatch.setitem(DEFAULT_MODEL_COST_MAP, "gpt-4o-mini", ModelPricing(1, 2))
    original = test_app.state.http_client.post

    async def post(*args, **kwargs):
        monkeypatch.setitem(DEFAULT_MODEL_COST_MAP, "gpt-4o-mini", ModelPricing(100, 200))
        return await original(*args, **kwargs)

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers=headers(test_app),
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200, response.text
    assert operations.accept.call_args.args[1]["cost_exact"] == "3.000000000000000000"
    assert (
        operations.begin.call_args.args[0]
        .intent.attempts[0]
        .pricing["catalog.input_cost_per_token"]
        == "1"
    )


async def test_secondary_failure_inside_cache_middleware_returns_controlled_503(client, test_app):
    from src.cache import CacheKeyBuilder, InMemoryBackend
    from src.models.errors import BudgetExceededError

    operations = install(test_app)
    test_app.state.cache_backend = InMemoryBackend()
    test_app.state.cache_key_builder = CacheKeyBuilder(custom_salt="test")
    test_app.state.budget_service = SimpleNamespace(
        check_budgets=AsyncMock(side_effect=BudgetExceededError())
    )
    test_app.state.spend_tracking_service.repository.enqueue = AsyncMock(
        side_effect=ConnectionError("test outage")
    )
    response = await client.post(
        "/v1/chat/completions",
        headers=headers(test_app),
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["type"] == "spend_persistence_unavailable"
    operations.begin.assert_not_awaited()
    assert test_app.state.http_client.post_calls == 0


@pytest.mark.parametrize(
    "endpoint", ["/v1/chat/completions", "/v1/responses", "/v1/completions", "/v1/messages"]
)
async def test_real_client_close_at_terminal_keeps_one_reserved_receipt(test_app, endpoint):
    import httpx
    from tests.test_stream_accounting_commit import (
        TERMINAL_FRAMES,
        configured_stream,
        loopback_gateway,
        request_body,
    )

    async with configured_stream(test_app, hold_eof=True) as (_, upstream, calls, _):
        operations = install(test_app)
        async with loopback_gateway(test_app) as base_url:
            async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
                async with client.stream(
                    "POST", endpoint, headers=headers(test_app), json=request_body(endpoint)
                ) as response:
                    assert response.status_code == 200
                    async for line in response.aiter_lines():
                        if line in ("data: [DONE]", *TERMINAL_FRAMES.values()):
                            operations.accept.assert_awaited_once()
                            break
                    else:
                        pytest.fail("Terminal marker was not delivered")
        operations.begin.assert_awaited_once()
        assert operations.begin.call_args.args[0] == operations.accept.call_args.args[0]
        assert len(calls) == 1 and upstream.closed.is_set()


@pytest.mark.parametrize("native", [False, True])
async def test_disconnect_before_terminal_keeps_intent_and_closes_provider(test_app, native):
    import asyncio
    import httpx
    from src.billing.accounting.accounting_protocol import AccountingOutcome
    from src.billing.accounting.accounting_service import AccountingProtocolService
    from tests.test_accounting_request_path import _AccountingRepository
    from tests.test_stream_accounting_commit import loopback_gateway

    closed = asyncio.Event()

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"id":"interrupted","choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    stream = InterruptedStream()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    ) as provider:
        test_app.state.http_client.stream = provider.stream
        operations = install(test_app)
        accounting = None
        if native:
            repository = _AccountingRepository()
            accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
            accounting.start()
            test_app.state.accounting_protocol_enabled = True
            test_app.state.accounting_protocol_service = accounting
            test_app.state.spend_tracking_service = ReadySpend(
                db_client=None,
                writer=SpendTrackingService(None),
                config=SpendIngestionConfig(enabled=True),
                accounting=accounting,
            )
        try:
            async with loopback_gateway(test_app) as base_url:
                async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
                    async with client.stream(
                        "POST",
                        "/v1/chat/completions",
                        headers=headers(test_app),
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": "hello"}],
                            "stream": True,
                        },
                    ) as response:
                        async for line in response.aiter_lines():
                            if "partial" in line:
                                break
                        else:
                            pytest.fail("Missing partial stream frame")
                await asyncio.wait_for(closed.wait(), 5)
        finally:
            if accounting is not None:
                await accounting.close()
        if native:
            (reservation,) = repository.reservations
            (terminal,) = repository.finalizations
            assert terminal.operation_id == reservation.operation_id
            assert terminal.outcome is AccountingOutcome.UNCERTAIN
            assert terminal.uncertainty_reason == "stream_interrupted"
        else:
            operations.begin.assert_awaited_once()
            operations.accept.assert_not_awaited()
        deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
        assert (
            await test_app.state.router_state_backend.get_active_requests(deployment.deployment_id)
            == 0
        )


@pytest.mark.parametrize(
    "path,call_type,body",
    [
        ("/v1/images/generations", "image_generation", {"prompt": "cat"}),
        ("/v1/audio/speech", "audio_speech", {"input": "hello", "voice": "alloy"}),
        ("/v1/audio/transcriptions", "audio_transcription", {"response_format": "json"}),
        ("/v1/rerank", "rerank", {"query": "q", "documents": ["a", "b"]}),
    ],
)
async def test_media_modalities_reserve_before_provider_and_accept_same_slot(
    client, test_app, path, call_type, body
):
    import httpx

    operations = install(test_app)
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info.update(mode=call_type, cost_per_request=0.1)
    if call_type == "rerank":
        deployment.deltallm_params["provider"] = "vllm"

    async def post(url, **kwargs):
        operations.begin.assert_awaited_once()
        operations.accept.assert_not_awaited()
        request = httpx.Request("POST", url)
        if call_type == "audio_speech":
            return httpx.Response(200, content=b"audio-bytes", request=request)
        payload = {
            "image_generation": {"created": 1, "data": [{"url": "https://example.com/image.png"}]},
            "audio_transcription": {"text": "hello", "duration": 1.0},
            "rerank": {"results": [{"index": 0, "relevance_score": 0.9}]},
        }[call_type]
        return httpx.Response(200, json=payload, request=request)

    test_app.state.http_client.post = post
    payload = {"model": "gpt-4o-mini", **body}
    options = (
        {"data": payload, "files": {"file": ("sample.wav", b"RIFFDATA", "audio/wav")}}
        if call_type == "audio_transcription"
        else {"json": payload}
    )
    response = await client.post(path, headers=headers(test_app), **options)
    assert response.status_code == 200, response.text
    operations.accept.assert_awaited_once()
    handle, receipt = operations.accept.call_args.args
    assert handle == operations.begin.call_args.args[0]
    assert receipt["call_type"] == handle.intent.call_type == call_type
    assert receipt["organization_id"] == handle.intent.principal.organization_id
    operations.unknown.assert_not_awaited()


async def test_provider_pool_refusal_releases_native_allowance(client, test_app):
    from src.billing.accounting.accounting_protocol import AccountingOutcome
    from src.billing.accounting.accounting_service import AccountingProtocolService
    import httpx
    from tests.test_accounting_request_path import _AccountingRepository

    repository = _AccountingRepository()
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    test_app.state.accounting_protocol_enabled = True
    test_app.state.accounting_protocol_service = accounting
    test_app.state.spend_tracking_service = ReadySpend(
        db_client=None,
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True),
        accounting=accounting,
    )
    refusal = httpx.PoolTimeout("pool busy")
    setattr(refusal, "_provider_pool_refused_before_send", True)
    test_app.state.http_client.post = AsyncMock(side_effect=refusal)
    try:
        response = await client.post(
            "/v1/chat/completions",
            headers={**headers(test_app), "x-request-id": "test-correlation"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        )
    finally:
        await accounting.close()

    assert response.status_code == 503, response.text
    assert response.json()["error"]["type"] == "gateway_capacity_error"
    (terminal,) = repository.finalizations
    assert terminal.outcome is AccountingOutcome.NOT_DISPATCHED
    assert terminal.uncertainty_reason is None
    audit = terminal.audit_envelope["payload"]["event"]
    assert audit["error_type"] == "AccountingProviderNotDispatched"
    assert audit["request_id"] == str(terminal.operation_id)
    assert audit["correlation_id"] == response.headers["x-request-id"] == "test-correlation"


async def test_provider_failure_after_send_shows_its_reason_in_audit(client, test_app):
    from src.billing.accounting.accounting_protocol import AccountingOutcome
    from src.billing.accounting.accounting_service import AccountingProtocolService
    import httpx
    from tests.test_accounting_request_path import _AccountingRepository

    repository = _AccountingRepository()
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    test_app.state.accounting_protocol_enabled = True
    test_app.state.accounting_protocol_service = accounting
    test_app.state.spend_tracking_service = ReadySpend(
        db_client=None,
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True),
        accounting=accounting,
    )

    async def post(url, **kwargs):
        return httpx.Response(
            503,
            json={"error": {"message": "model server down"}},
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post
    try:
        response = await client.post(
            "/v1/chat/completions",
            headers=headers(test_app),
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        )
    finally:
        await accounting.close()

    assert response.status_code == 503, response.text
    (terminal,) = repository.finalizations
    assert terminal.outcome is AccountingOutcome.UNCERTAIN
    assert terminal.uncertainty_reason == "service_unavailable"
    audit = terminal.audit_envelope["payload"]["event"]
    assert audit["status"] == "error"
    assert audit["error_type"] == "service_unavailable"
    assert audit["metadata"]["uncertainty_reason"] == "service_unavailable"


async def _chat_with_provider_answer(client, test_app, status_code, body, response_headers=None):
    import json

    import httpx

    from src.billing.accounting.accounting_service import AccountingProtocolService
    from src.providers.error_body import bound_provider_error_response_body
    from tests.test_accounting_request_path import _AccountingRepository

    class ErrorBody(httpx.AsyncByteStream):
        def __init__(self, content):
            self._content = content

        async def __aiter__(self):
            yield self._content

        async def aclose(self):
            return None

    repository = _AccountingRepository()
    accounting = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    accounting.start()
    test_app.state.accounting_protocol_enabled = True
    test_app.state.accounting_protocol_service = accounting
    test_app.state.spend_tracking_service = ReadySpend(
        db_client=None,
        writer=SpendTrackingService(None),
        config=SpendIngestionConfig(enabled=True),
        accounting=accounting,
    )

    content = body if isinstance(body, bytes) else json.dumps(body).encode()

    async def post(url, **kwargs):
        # Run the shared upstream client's response hook, as a real call does.
        response = httpx.Response(
            status_code,
            headers=response_headers or {"content-type": "application/json"},
            stream=ErrorBody(content),
            request=httpx.Request("POST", url),
        )
        await bound_provider_error_response_body(response)
        await response.aread()
        return response

    test_app.state.http_client.post = post
    try:
        response = await client.post(
            "/v1/chat/completions",
            headers=headers(test_app),
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        )
    finally:
        await accounting.close()
    return response, repository.finalizations


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
async def test_provider_refusal_releases_native_allowance(client, test_app, status_code):
    from src.billing.accounting.accounting_protocol import AccountingOutcome

    response, finalizations = await _chat_with_provider_answer(
        client,
        test_app,
        status_code,
        {"error": {"message": "refused", "type": "invalid_request_error"}},
    )

    assert response.status_code >= 400, response.text
    (terminal,) = finalizations
    assert terminal.outcome is AccountingOutcome.NOT_DISPATCHED
    assert terminal.uncertainty_reason is None
    audit = terminal.audit_envelope["payload"]["event"]
    assert audit["error_type"] == "AccountingProviderRefused"
    assert audit["metadata"]["outcome_unknown"] is False
    assert audit["metadata"]["reason"] == "provider_refused"
    assert audit["metadata"]["refused_attempts"] == audit["metadata"]["attempt_count"]


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (400, {"error": {"message": "filtered", "code": "content_filter"}}),
        (408, {"error": {"message": "timed out"}}),
        (429, {"error": {"message": "slow down"}}),
        (500, {"error": {"message": "crashed"}}),
    ],
)
async def test_provider_error_that_may_be_billed_stays_provisional(
    client, test_app, status_code, body
):
    from src.billing.accounting.accounting_protocol import AccountingOutcome

    response, finalizations = await _chat_with_provider_answer(client, test_app, status_code, body)

    assert response.status_code >= 400, response.text
    (terminal,) = finalizations
    assert terminal.outcome is AccountingOutcome.UNCERTAIN
    assert terminal.audit_envelope["payload"]["event"]["error_type"] != "AccountingProviderRefused"


_CONTENT_FILTER_BODY = b'{"error":{"message":"filtered","code":"content_filter"}}'


@pytest.mark.parametrize(
    ("body", "response_headers"),
    [
        (
            gzip.compress(_CONTENT_FILTER_BODY),
            {"content-type": "application/json", "content-encoding": "gzip"},
        ),
        (_CONTENT_FILTER_BODY[:-1] + b" " * 70_000 + b"}", None),
        (b"Bad Request", {"content-type": "text/plain"}),
    ],
    ids=["compressed", "oversized", "not-json"],
)
async def test_provider_400_with_an_unreadable_body_stays_provisional(
    client, test_app, body, response_headers
):
    from src.billing.accounting.accounting_protocol import AccountingOutcome

    # The gateway cannot tell whether this 400 is a content-filter refusal, which
    # Azure OpenAI can bill, so it keeps the reservation.
    response, finalizations = await _chat_with_provider_answer(
        client, test_app, 400, body, response_headers
    )

    assert response.status_code >= 400, response.text
    (terminal,) = finalizations
    assert terminal.outcome is AccountingOutcome.UNCERTAIN
    assert terminal.audit_envelope["payload"]["event"]["error_type"] != "AccountingProviderRefused"
