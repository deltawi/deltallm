import asyncio
from dataclasses import replace
from threading import Event
from unittest.mock import AsyncMock

import pytest
import httpx

from src.callbacks import CustomLogger
from src.config import GeneralSettings, Settings
from src.guardrails.presidio import PresidioGuardrail
from src.ingress import initialize_ingress

pytestmark = [pytest.mark.app, pytest.mark.asyncio]


def configure(test_app, timeout=0.1):
    store = test_app.state.routing_runtime_generation_store
    generation = store.require_snapshot()
    config = replace(generation.failover_config, timeout=timeout)
    generation.failover_manager.config = config
    store.replace(replace(generation, failover_config=config))
    initialize_ingress(test_app, GeneralSettings(gateway_ingress_enabled=True), Settings())


async def request(client, test_app):
    return await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )


@pytest.mark.parametrize("phase", ["auth", "hook", "settlement"])
async def test_expiry_cancels_work_and_releases_ingress_without_provider_retry(
    test_app, client, monkeypatch, phase
):
    configure(test_app)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    if phase == "auth":
        monkeypatch.setattr(test_app.state.key_service, "validate_key", blocked)
    elif phase == "hook":

        class Hook(CustomLogger):
            async def async_pre_call_hook(self, *args):
                return await blocked()

        test_app.state.callback_manager.register_callback(Hook())
    else:
        monkeypatch.setattr(test_app.state.spend_tracking_service, "log_spend", blocked)
    async with asyncio.timeout(2):
        response = await request(client, test_app)
    assert response.status_code == 408, response.text
    assert response.json()["error"]["code"] == "request_deadline_exceeded"
    assert started.is_set() and cancelled.is_set()
    assert test_app.state.http_client.post_calls == (1 if phase == "settlement" else 0)
    rt = test_app.state.ingress_runtime
    assert rt.requests.active == rt.requests.waiters == rt.buffered_bytes == 0


async def test_guardrail_timeout_keeps_cpu_slot_and_denies_provider(test_app, client, monkeypatch):
    configure(test_app)
    executor = test_app.state.guardrail_registry.executor
    executor.max_pending = 1
    started, finish = Event(), Event()
    guardrail = PresidioGuardrail()

    def blocked(_data):
        started.set()
        finish.wait(3)

    monkeypatch.setattr(guardrail, "_pre_call", blocked)
    test_app.state.guardrail_registry.register(guardrail)
    provider = AsyncMock()
    monkeypatch.setattr(test_app.state.http_client, "post", provider)
    try:
        response = await request(client, test_app)
        assert response.status_code == 408
        assert started.is_set()
        assert executor.pending == 1
        configure(test_app, timeout=1)
        overloaded = await request(client, test_app)
        assert overloaded.status_code == 503, overloaded.text
        assert overloaded.json()["error"]["code"] == "gateway_work_unavailable"
        provider.assert_not_awaited()
        assert executor.pending == 1
    finally:
        finish.set()
        async with asyncio.timeout(2):
            while executor.pending:
                await asyncio.sleep(0)


@pytest.mark.parametrize(
    "endpoint", ["/v1/chat/completions", "/v1/responses", "/v1/completions", "/v1/messages"]
)
async def test_stream_deadline_keeps_lease_to_abort_and_closes_provider_once(test_app, endpoint):
    from tests.test_spend_operation_http import headers, install
    from tests.test_stream_accounting_commit import loopback_gateway, request_body

    configure(test_app, timeout=1)
    operations = install(test_app)
    closed = asyncio.Event()
    calls = 0

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"id":"deadline-test","model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    async def response(_):
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=SlowStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as provider:
        test_app.state.http_client.stream = provider.stream
        async with loopback_gateway(test_app) as base_url:
            async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
                lines = []
                with pytest.raises(httpx.RemoteProtocolError):
                    async with client.stream(
                        "POST", endpoint, headers=headers(test_app), json=request_body(endpoint)
                    ) as result:
                        assert result.status_code == 200
                        async for line in result.aiter_lines():
                            lines.append(line)
                            assert test_app.state.ingress_runtime.requests.active == 1
                assert any("partial" in line for line in lines)
                assert not any(line in ("data: [DONE]", "event: message_stop") for line in lines)
        assert calls == 1 and closed.is_set()
        assert test_app.state.ingress_runtime.requests.active == 0
        operations.begin.assert_awaited_once()
        operations.accept.assert_not_awaited()
