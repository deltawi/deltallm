import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from src.router.selection.request_state import SelectorState
from src.router.selection.service import SelectorService
from tests.router.selection.provider_fixtures import TrackingStream, bridge
from tests.router.selection.test_service import select, state


class CleanupBarrierStream(TrackingStream):
    def __init__(self, chunks, *, read_error=None):
        super().__init__(chunks, read_error=read_error)
        self.close_entered = asyncio.Event()
        self.close_finished = asyncio.Event()
        self.close_release = asyncio.Event()

    async def aclose(self) -> None:
        self.closed += 1
        self.close_entered.set()
        try:
            await self.close_release.wait()
        finally:
            self.close_finished.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks,headers,read_error",
    [
        pytest.param([b"x" * 65537], {}, None, id="observed-oversize"),
        pytest.param([], {"content-length": "65537"}, None, id="declared-oversize"),
        pytest.param([], {"content-encoding": "gzip"}, None, id="unexpected-encoding"),
        pytest.param([], {}, httpx.ReadError("private read failure"), id="transport-error"),
    ],
)
async def test_cancel_during_error_cleanup_aborts_owner_and_joiner_without_replay(
    selector_policy, policy_identity, chunks, headers, read_error, caplog
):
    stream = CleanupBarrierStream(chunks, read_error=read_error)
    request_entered, response_release, joiner_entered = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    requests = []

    async def handler(request):
        requests.append(request)
        request_entered.set()
        await response_release.wait()
        return httpx.Response(200, headers=headers, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service, operation = SelectorService(bridge(client)), state()

        async def join():
            joiner_entered.set()
            return await select(service, operation, selector_policy, policy_identity)

        async with asyncio.timeout(2), asyncio.TaskGroup() as group:
            owner = group.create_task(select(service, operation, selector_policy, policy_identity))
            await request_entered.wait()
            joiner = group.create_task(join())
            await joiner_entered.wait()
            assert operation._joiners == 1
            response_release.set()
            await stream.close_entered.wait()
            assert owner.cancel()
            for task in (owner, joiner):
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert task.cancelled()

        with pytest.raises(asyncio.CancelledError):
            await select(service, operation, selector_policy, policy_identity)
        assert operation.state is SelectorState.ABORTED and operation.usage.kind == "unknown"
        assert operation._decision is None and operation._joiners == 0
        assert operation._done.result() is None and operation._done.exception() is None
        assert stream.closed == 1 and stream.close_finished.is_set()
        assert len(requests) == 1 and not client.is_closed
    assert "private read failure" not in caplog.text


@pytest.mark.asyncio
async def test_original_cancellation_survives_cleanup_failure(
    selector_policy, policy_identity, caplog
):
    read_entered = asyncio.Event()

    class ReadingStream(TrackingStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            read_entered.set()
            async for chunk in super().__aiter__():
                yield chunk

    stream = ReadingStream(
        [], pause=asyncio.Event(), close_error=RuntimeError("private close failure")
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service, operation = SelectorService(bridge(client)), state()
        async with asyncio.timeout(2), asyncio.TaskGroup() as group:
            owner = group.create_task(select(service, operation, selector_policy, policy_identity))
            await read_entered.wait()
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
        assert owner.cancelled() and operation.state is SelectorState.ABORTED
        assert stream.closed == 1 and len(requests) == 1 and not client.is_closed
    assert "bounded_chat_response_cleanup_failed" in caplog.text
    assert "private close failure" not in caplog.text
