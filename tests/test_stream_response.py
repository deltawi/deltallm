from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.chat.executor import OpenedStream
from src.chat.stream_response import DeadlineStreamingResponse
from src.models.errors import TimeoutError
from src.router.execution import ManagedFailoverResult, RequestDeadline
from src.router.router import Deployment


def _deployment() -> Deployment:
    return Deployment(
        deployment_id="stream-deployment",
        model_name="stream-group",
        deltallm_params={"provider": "openai", "model": "openai/test"},
        model_info={"mode": "chat"},
    )


@pytest.mark.asyncio
async def test_response_deadline_covers_blocked_downstream_send_and_closes_resources() -> None:
    send_started = asyncio.Event()
    body_finalized = asyncio.Event()
    resources_closed = asyncio.Event()

    async def body():
        try:
            yield "data: chunk\n\n"
        finally:
            body_finalized.set()

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and message.get("more_body"):
            send_started.set()
            await asyncio.Event().wait()

    async def close(_exc: BaseException | None) -> None:
        resources_closed.set()

    response = DeadlineStreamingResponse(
        body(),
        deadline=RequestDeadline.after(0.05),
        close=close,
        media_type="text/event-stream",
    )

    with pytest.raises(TimeoutError, match="Request deadline exceeded"):
        await response.stream_response(send)

    assert send_started.is_set()
    assert body_finalized.is_set()
    assert resources_closed.is_set()


@pytest.mark.asyncio
async def test_response_cancellation_closes_resources_when_body_never_starts() -> None:
    response_started = asyncio.Event()
    resources_closed = asyncio.Event()
    body_started = False

    async def body():
        nonlocal body_started
        body_started = True
        yield "data: chunk\n\n"

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            response_started.set()
            await asyncio.Event().wait()

    async def close(_exc: BaseException | None) -> None:
        resources_closed.set()

    response = DeadlineStreamingResponse(
        body(),
        deadline=RequestDeadline.after(10),
        close=close,
        media_type="text/event-stream",
    )
    response_task = asyncio.create_task(response.stream_response(send))
    await response_started.wait()

    response_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await response_task

    assert body_started is False
    assert resources_closed.is_set()


@pytest.mark.asyncio
async def test_downstream_disconnect_closes_body_and_resources() -> None:
    body_finalized = asyncio.Event()
    resources_closed = asyncio.Event()

    async def body():
        try:
            yield "data: chunk\n\n"
        finally:
            body_finalized.set()

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and message.get("more_body"):
            raise OSError("client disconnected")

    async def close(_exc: BaseException | None) -> None:
        resources_closed.set()

    response = DeadlineStreamingResponse(
        body(),
        deadline=RequestDeadline.after(10),
        close=close,
        media_type="text/event-stream",
    )

    with pytest.raises(OSError, match="client disconnected"):
        await response.stream_response(send)

    assert body_finalized.is_set()
    assert resources_closed.is_set()


@pytest.mark.asyncio
async def test_managed_release_can_retry_after_interrupted_cleanup() -> None:
    release_started = asyncio.Event()
    release_calls = 0

    async def release() -> None:
        nonlocal release_calls
        release_calls += 1
        if release_calls == 1:
            release_started.set()
            await asyncio.Event().wait()

    managed = ManagedFailoverResult(
        value="value",
        deployment=_deployment(),
        deadline=RequestDeadline.after(10),
        _release=release,
    )
    first_release = asyncio.create_task(managed.release())
    await release_started.wait()

    first_release.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_release
    await managed.release()
    await managed.release()

    assert release_calls == 2


@pytest.mark.asyncio
async def test_opened_stream_close_can_retry_after_interrupted_cleanup() -> None:
    close_started = asyncio.Event()

    class ContextManager:
        def __init__(self) -> None:
            self.close_calls = 0

        async def __aexit__(self, *_args: Any) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                close_started.set()
                await asyncio.Event().wait()

    context_manager = ContextManager()
    opened = OpenedStream(
        context_manager=context_manager,
        response=None,
        translated_stream=None,
        first_line="data: first",
        adapter=None,  # type: ignore[arg-type]
        deployment=_deployment(),
        params={},
        api_base="https://example.test",
        client_stream_usage_requested=False,
        internal_stream_usage_requested=False,
        upstream_started=0,
    )
    first_close = asyncio.create_task(opened.close())
    await close_started.wait()

    first_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_close
    await opened.close()
    await opened.close()

    assert context_manager.close_calls == 2
