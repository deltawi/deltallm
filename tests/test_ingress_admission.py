from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.types import Message, Scope

from src.ingress import IngressClass, IngressLimits, IngressRuntime, ingress_class
from src.middleware.ingress import IngressMiddleware
from src.middleware.request_timing import RequestTimingMiddleware
from src.metrics.request_phases import request_in_flight, request_route

pytestmark = [pytest.mark.hermetic, pytest.mark.asyncio]


def scope(runtime: IngressRuntime, path: str = "/v1/chat/completions", **kwargs) -> Scope:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(ingress_runtime=runtime)),
        **kwargs,
    }


def runtime(**kwargs) -> IngressRuntime:
    return IngressRuntime(replace(IngressLimits(enabled=True, max_active=1), **kwargs))


async def wait_until(predicate) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.parametrize(
    "path",
    [
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/completions",
        "/completions",
        "/v1/responses",
        "/responses",
        "/v1/messages",
        "/messages",
        "/v1/embeddings",
        "/embeddings",
        "/v1/audio/speech",
        "/v1/audio/transcriptions",
        "/v1/images/generations",
        "/v1/rerank",
        "/rerank",
        "/health/deployments",
        "/unknown",
        "/v1/chat/completions/",
    ],
)
async def test_full_gate_rejects_without_body_or_downstream_work(path: str) -> None:
    rt = runtime()
    gate = rt.gate(ingress_class(path, "POST"))
    for _ in range(rt.limits.control_max_active if gate is rt.control else 1):
        await gate.acquire(timeout_seconds=1)
    app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    try:
        await IngressMiddleware(app)(scope(rt, path), receive, send)
        app.assert_not_called()
        receive.assert_not_called()
        start, body = [call.args[0] for call in send.await_args_list]
        assert start["status"] == 503
        assert (b"retry-after", b"1") in start["headers"]
        payload = json.loads(body["body"])
        if path in {"/messages", "/v1/messages"}:
            assert payload["type"] == "error"
            assert payload["error"]["type"] == "overloaded_error"
        else:
            assert payload["error"]["code"] == "gateway_ingress_full"
        assert gate.active > 0
        assert rt.requests.waiters == rt.buffered_bytes == 0
    finally:
        while gate.active:
            await gate.release()


async def test_control_capacity_and_bytes_cannot_exhaust_inference() -> None:
    rt = runtime(control_max_active=1, control_max_buffered_bytes=4, max_buffered_bytes=4)
    await rt.control.acquire(timeout_seconds=1)
    rt.reserve_bytes(4, IngressClass.CONTROL)
    app = AsyncMock()
    try:
        await IngressMiddleware(app)(
            scope(rt),
            AsyncMock(return_value={"type": "http.request", "body": b"1234"}),
            AsyncMock(),
        )
        app.assert_awaited_once()
        assert rt.requests.active == rt.buffered_bytes == 0
        assert rt.control.active == 1
    finally:
        await rt.control.release()
        rt.release_bytes(4, IngressClass.CONTROL)


async def test_waiters_are_finite_cancelled_and_timeout_without_receiving_body() -> None:
    rt = runtime(max_waiters=2, queue_timeout_ms=50)
    await rt.requests.acquire(timeout_seconds=1)
    app, receive = AsyncMock(), AsyncMock()
    middleware = IngressMiddleware(app)
    sends = [AsyncMock(), AsyncMock()]
    waiters = [asyncio.create_task(middleware(scope(rt), receive, send)) for send in sends]
    try:
        await wait_until(lambda: rt.requests.waiters == 2)
        waiters[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiters[0]
        flood_sends = [AsyncMock() for _ in range(50)]
        await asyncio.gather(*(middleware(scope(rt), receive, send) for send in flood_sends))
        assert all(send.await_args_list[0].args[0]["status"] == 503 for send in flood_sends)
        await waiters[1]
        assert sends[1].await_args_list[0].args[0]["status"] == 503
        assert rt.requests.waiters == rt.buffered_bytes == 0
        receive.assert_not_called()
        app.assert_not_called()
    finally:
        for task in waiters:
            task.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        await rt.requests.release()


async def test_waiter_receives_capacity_after_owner_finishes() -> None:
    rt = runtime(max_waiters=1, queue_timeout_ms=1000)
    await rt.requests.acquire(timeout_seconds=1)
    app = AsyncMock()
    receive = AsyncMock(return_value={"type": "http.request", "body": b"body"})
    task = asyncio.create_task(IngressMiddleware(app)(scope(rt), receive, AsyncMock()))
    try:
        await wait_until(lambda: rt.requests.waiters == 1)
        receive.assert_not_called()
        await rt.requests.release()
        await task
        app.assert_awaited_once()
        assert rt.requests.active == rt.requests.waiters == rt.buffered_bytes == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "health", ["/health", "/health/liveliness", "/health/readiness", "/metrics"]
)
async def test_health_uses_its_own_finite_allocation(health: str) -> None:
    rt = runtime(health_max_active=1)
    await rt.requests.acquire(timeout_seconds=1)
    app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    middleware = IngressMiddleware(app)
    await middleware(scope(rt, health, method="GET"), receive, send)
    app.assert_awaited_once()
    await rt.health.acquire(timeout_seconds=1)
    try:
        app.reset_mock()
        await middleware(scope(rt, health, method="GET"), receive, send)
        app.assert_not_called()
        receive.assert_not_called()
        assert send.await_args_list[0].args[0]["status"] == 503
        assert rt.health.waiters == 0
    finally:
        await rt.requests.release()
        await rt.health.release()


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ([(b"content-length", b"5")], 413),
        ([(b"content-length", b"-1")], 400),
        ([(b"content-length", b"1"), (b"content-length", b"1")], 400),
        ([(b"content-length", b"1,1")], 400),
        ([(b"content-length", b"9" * 30)], 400),
    ],
)
async def test_invalid_or_oversized_declared_body_rejects_before_read(headers, status) -> None:
    rt = runtime(max_body_bytes=4)
    app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    await IngressMiddleware(app)(scope(rt, headers=headers), receive, send)
    assert send.await_args_list[0].args[0]["status"] == status
    app.assert_not_called()
    receive.assert_not_called()
    assert rt.requests.active == rt.buffered_bytes == 0


@pytest.mark.parametrize(
    ("chunks", "headers", "status"),
    [
        ([b"12", b"345"], [], 413),
        ([b"12"], [(b"content-length", b"1")], 400),
        ([b"12"], [(b"content-length", b"3")], 400),
    ],
)
async def test_actual_body_is_bounded_even_without_honest_content_length(
    chunks, headers, status
) -> None:
    rt = runtime(max_body_bytes=4)
    receive = AsyncMock(
        side_effect=[
            {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
            for i, chunk in enumerate(chunks)
        ]
    )
    app, send = AsyncMock(), AsyncMock()
    await IngressMiddleware(app)(scope(rt, headers=headers), receive, send)
    assert send.await_args_list[0].args[0]["status"] == status
    app.assert_not_called()
    assert rt.requests.active == rt.buffered_bytes == 0


async def test_global_bytes_and_permit_stay_owned_through_stream_and_cleanup() -> None:
    rt = runtime(max_active=2, max_buffered_bytes=4)
    streaming, finish_stream, cleanup, finish_cleanup = [asyncio.Event() for _ in range(4)]

    async def app(scope, receive, send) -> None:
        assert (await receive())["body"] == b"1234"
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        streaming.set()
        await finish_stream.wait()
        await send({"type": "http.response.body", "body": b"b", "more_body": False})
        cleanup.set()
        await finish_cleanup.wait()

    middleware = IngressMiddleware(app)
    task = asyncio.create_task(
        middleware(
            scope(rt),
            AsyncMock(return_value={"type": "http.request", "body": b"1234"}),
            AsyncMock(),
        )
    )
    try:
        await asyncio.wait_for(streaming.wait(), 1)
        assert rt.requests.active == 1
        assert rt.buffered_bytes == 4
        send = AsyncMock()
        await middleware(
            scope(rt), AsyncMock(return_value={"type": "http.request", "body": b"x"}), send
        )
        assert (
            json.loads(send.await_args_list[1].args[0]["body"])["error"]["code"]
            == "gateway_ingress_buffer_full"
        )
        assert rt.requests.active == 1
        finish_stream.set()
        await asyncio.wait_for(cleanup.wait(), 1)
        assert rt.requests.active == 1
        assert rt.buffered_bytes == 4
        finish_cleanup.set()
        await task
        assert rt.requests.active == rt.buffered_bytes == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "termination", ["disconnect", "cancel", "timeout", "send_failure", "app_failure"]
)
async def test_termination_releases_every_owned_resource(termination: str) -> None:
    rt = runtime(body_timeout_seconds=0.02)
    blocked = asyncio.Event()
    calls = 0

    async def receive() -> Message:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b"123", "more_body": True}
        if termination == "disconnect":
            return {"type": "http.disconnect"}
        if termination in {"cancel", "timeout"}:
            blocked.set()
            await asyncio.Event().wait()
        return {"type": "http.request", "body": b""}

    async def app(scope, receive, send) -> None:
        if termination == "app_failure":
            raise RuntimeError("application failed")
        await send({"type": "http.response.start", "status": 200})

    send = AsyncMock(side_effect=OSError("closed") if termination == "send_failure" else None)
    task = asyncio.create_task(IngressMiddleware(app)(scope(rt), receive, send))
    try:
        if termination == "cancel":
            await asyncio.wait_for(blocked.wait(), 1)
            task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)
        if termination == "timeout":
            assert send.await_args_list[0].args[0]["status"] == 408
        elif termination == "disconnect":
            send.assert_not_called()
        else:
            expected = {
                "cancel": asyncio.CancelledError,
                "send_failure": OSError,
                "app_failure": RuntimeError,
            }[termination]
            assert isinstance(results[0], expected)
        assert rt.requests.active == rt.requests.waiters == rt.buffered_bytes == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_repeated_cancellation_during_cleanup_does_not_leak_permit() -> None:
    rt = runtime()
    started, cleanup = asyncio.Event(), asyncio.Event()

    async def app(scope, receive, send) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        IngressMiddleware(app)(
            scope(rt), AsyncMock(return_value={"type": "http.request", "body": b"abc"}), AsyncMock()
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        await asyncio.wait_for(cleanup.wait(), 1)
        assert rt.requests.active == 1
        assert rt.buffered_bytes == 3
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rt.requests.active == rt.buffered_bytes == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("completion", ["final_body", "disconnect", "cancel"])
async def test_request_timing_completion_preserves_ingress_cleanup_ownership(completion):
    rt = runtime(max_buffered_bytes=4)
    started, cleanup, release = [asyncio.Event() for _ in range(3)]
    gauge = request_in_flight.labels(request_route("/v1/chat/completions"))
    before = gauge._value.get()

    async def app(scope, receive, send):
        assert (await receive())["body"] == b"1234"
        started.set()
        try:
            await send({"type": "http.response.start", "status": 200})
            if completion == "final_body":
                await send({"type": "http.response.body", "body": b"done"})
            elif completion == "disconnect":
                assert (await receive())["type"] == "http.disconnect"
            else:
                await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()

    receive = AsyncMock(
        side_effect=[{"type": "http.request", "body": b"1234"}, {"type": "http.disconnect"}]
    )
    task = asyncio.create_task(
        RequestTimingMiddleware(IngressMiddleware(app))(scope(rt), receive, AsyncMock())
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        if completion == "cancel":
            task.cancel()
        await asyncio.wait_for(cleanup.wait(), 1)
        assert rt.requests.active == 1
        assert rt.buffered_bytes == 4
        assert gauge._value.get() == before + (completion == "cancel")
        rejected_receive, rejected_send = AsyncMock(), AsyncMock()
        await RequestTimingMiddleware(IngressMiddleware(app))(
            scope(rt), rejected_receive, rejected_send
        )
        rejected_receive.assert_not_called()
        assert rejected_send.await_args_list[0].args[0]["status"] == 503
        release.set()
        results = await asyncio.gather(task, return_exceptions=True)
        if completion == "cancel":
            assert isinstance(results[0], asyncio.CancelledError)
        assert rt.requests.active == rt.requests.waiters == rt.buffered_bytes == 0
        assert gauge._value.get() == before
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("bypass", ["disabled", "uninitialized", "websocket"])
async def test_disabled_or_non_http_traffic_preserves_original_call(bypass: str) -> None:
    rt = runtime(enabled=bypass != "disabled")
    request_scope = scope(rt)
    if bypass == "uninitialized":
        del request_scope["app"].state.ingress_runtime
    elif bypass == "websocket":
        request_scope["type"] = "websocket"
    app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    await IngressMiddleware(app)(request_scope, receive, send)
    app.assert_awaited_once_with(request_scope, receive, send)
    receive.assert_not_called()
    assert rt.requests.active == rt.buffered_bytes == 0


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/messages", "/ui/api/config"])
async def test_drain_rejects_before_body_and_dependencies_even_without_ingress_limits(
    enabled, path
):
    from src.lifecycle_settings import LifecycleSettings
    from src.process_lifecycle import ProcessLifecycle

    rt = runtime(enabled=enabled)
    request = scope(rt, path)
    lifecycle = ProcessLifecycle(LifecycleSettings())
    request["app"].state.process_lifecycle = lifecycle
    lifecycle.begin_drain()
    app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    await IngressMiddleware(app)(request, receive, send)
    app.assert_not_called()
    receive.assert_not_called()
    assert send.await_args_list[0].args[0]["status"] == 503
    assert rt.requests.active == rt.control.active == rt.buffered_bytes == 0


async def test_queued_request_rechecks_drain_and_releases_only_its_permit():
    from src.lifecycle_settings import LifecycleSettings
    from src.process_lifecycle import ProcessLifecycle

    rt = runtime(max_waiters=1, queue_timeout_ms=1000)
    request = scope(rt)
    lifecycle = ProcessLifecycle(LifecycleSettings())
    lifecycle.mark_serving()
    request["app"].state.process_lifecycle = lifecycle
    await rt.requests.acquire(timeout_seconds=1)
    app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    task = asyncio.create_task(IngressMiddleware(app)(request, receive, send))
    await wait_until(lambda: rt.requests.waiters == 1)
    lifecycle.begin_drain()
    await rt.requests.release()
    await task
    app.assert_not_called()
    receive.assert_not_called()
    assert send.await_args_list[0].args[0]["status"] == 503
    assert rt.requests.active == rt.requests.waiters == 0
