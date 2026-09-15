"""Ordinary HTTP admission/finalization ordering through real application routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.billing.spend_operations import SpendPersistenceUnavailable
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
    from src.billing.cost import DEFAULT_MODEL_COST_MAP, ModelPricing

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
                        if line in ("data: [DONE]", "event: message_stop"):
                            operations.accept.assert_awaited_once()
                            break
                    else:
                        pytest.fail("Terminal marker was not delivered")
        operations.begin.assert_awaited_once()
        assert operations.begin.call_args.args[0] == operations.accept.call_args.args[0]
        assert len(calls) == 1 and upstream.closed.is_set()


async def test_disconnect_before_terminal_keeps_intent_and_closes_provider(test_app):
    import asyncio
    import httpx
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
