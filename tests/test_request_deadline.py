from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ingress import INFERENCE_PATHS
from src.middleware.request_deadline import RequestDeadlineMiddleware
from src.models.errors import TimeoutError as ProxyTimeoutError
from src.request_deadline import (
    RequestDeadline,
    bind_request_deadline,
    current_request_deadline,
    inherited_request_deadline,
)

pytestmark = [pytest.mark.hermetic, pytest.mark.asyncio]


def scope(path="/v1/chat/completions", method="POST"):
    return {
        "type": "http",
        "path": path,
        "method": method,
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace()),
    }


@pytest.fixture
def deadline_runtime(monkeypatch):
    runtime = SimpleNamespace(failover_config=SimpleNamespace(timeout=0.02))
    monkeypatch.setattr(
        "src.middleware.request_deadline.pin_routing_runtime_generation", lambda *_: runtime
    )
    return runtime


@pytest.mark.parametrize("path", sorted(INFERENCE_PATHS))
async def test_inference_deadline_covers_work_before_routing(path, deadline_runtime):
    async def app(_scope, _receive, _send):
        assert current_request_deadline() is not None
        await asyncio.Event().wait()

    send = AsyncMock()
    await RequestDeadlineMiddleware(app)(scope(path), AsyncMock(), send)
    assert send.await_args_list[0].args[0]["status"] == 408
    assert current_request_deadline() is None


async def test_health_and_control_do_not_inherit_inference_timeout():
    app = AsyncMock()
    await RequestDeadlineMiddleware(app)(scope("/health", "GET"), AsyncMock(), AsyncMock())
    app.assert_awaited_once()


async def test_started_stream_is_closed_without_replacement_response(deadline_runtime):
    cleaned = False

    async def app(_scope, _receive, send):
        nonlocal cleaned
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {"type": "http.response.body", "body": b"data: first\n\n", "more_body": True}
            )
            await asyncio.Event().wait()
        finally:
            cleaned = True

    send = AsyncMock()
    with pytest.raises(ProxyTimeoutError):
        await RequestDeadlineMiddleware(app)(scope(), AsyncMock(), send)
    assert cleaned
    assert len(send.await_args_list) == 2


async def test_route_budget_uses_ingress_origin_and_cannot_extend_it():
    now = asyncio.get_running_loop().time()
    parent = RequestDeadline(now + 10, started_at=now - 20)
    with bind_request_deadline(parent):
        assert inherited_request_deadline(100).expires_at == parent.expires_at
        assert inherited_request_deadline(25).expires_at == now + 5
        with pytest.raises(ProxyTimeoutError) as exc:
            inherited_request_deadline(10).require_remaining()
        assert exc.value.affects_deployment_health is False
    assert current_request_deadline() is None


async def test_route_shortening_reschedules_outer_deadline_for_finalization(deadline_runtime):
    deadline_runtime.failover_config.timeout = 10
    reached_finalization = False

    async def app(_scope, _receive, _send):
        nonlocal reached_finalization
        inherited_request_deadline(0.02)
        reached_finalization = True
        await asyncio.Event().wait()

    send = AsyncMock()
    async with asyncio.timeout(0.5):
        await RequestDeadlineMiddleware(app)(scope(), AsyncMock(), send)
    assert reached_finalization
    assert send.await_args_list[0].args[0]["status"] == 408
