import asyncio
from unittest.mock import AsyncMock

import pytest

from src.ingress import IngressClass, IngressRuntime
from src.metrics.prometheus import get_prometheus_registry
from src.middleware.ingress import IngressMiddleware
from tests.test_ingress_admission import runtime, scope

pytestmark = [pytest.mark.hermetic, pytest.mark.asyncio]


def value(allocation):
    return get_prometheus_registry().get_sample_value(
        "deltallm_ingress_active", {"allocation": allocation}
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_hpa_observes_idle_and_stream_lifetime_until_cleanup(cancel):
    rt = runtime()
    for allocation in IngressClass:
        assert value(allocation.value) is not None
    before = value("inference")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        entered.set()
        await finish.wait()
        await send({"type": "http.response.body", "body": b"last", "more_body": False})

    task = asyncio.create_task(
        IngressMiddleware(app)(
            scope(rt),
            AsyncMock(return_value={"type": "http.request", "body": b"{}", "more_body": False}),
            AsyncMock(),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert value("inference") == before + 1
        IngressRuntime(rt.limits)  # Another embedded app must not reset live work.
        assert value("inference") == before + 1
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            finish.set()
            await task
        assert value("inference") == before
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
