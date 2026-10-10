"""Wait for the full generation, not the first grant notification."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.accounting_read_model_fixtures import wait_for_native_projection


async def test_first_grant_notification_does_not_complete_generation_wait():
    progress, observed = asyncio.Event(), asyncio.Event()
    drained = False

    async def query(*parameters):
        observed.set()
        return [{"drained": drained}]

    db = AsyncMock()
    db.query_raw.side_effect = query
    progress.set()
    task = asyncio.create_task(wait_for_native_projection(db, progress, 1, timeout=1))
    try:
        await observed.wait()
        assert not task.done()
        drained = True
        progress.set()
        await task
        assert db.query_raw.await_count == 2
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_generation_wait_keeps_its_declared_deadline():
    db = AsyncMock()
    db.query_raw.return_value = [{"drained": False}]
    with pytest.raises(TimeoutError):
        await wait_for_native_projection(db, asyncio.Event(), 1, timeout=0.01)
    assert db.query_raw.await_count == 1
