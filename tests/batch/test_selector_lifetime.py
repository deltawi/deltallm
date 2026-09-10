import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
import json
from unittest.mock import AsyncMock

import pytest

from tests.batch import selector_fixtures
from tests.batch.selector_fixtures import answer_calls, selection_calls
from tests.test_routing_cache_identity import _publish

pytestmark = pytest.mark.app
selected_batch = selector_fixtures.selected_batch


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("execution_mode", ["single", "microbatch"])
async def test_partial_caller_admission_releases_lease_without_paid_work(
    selected_batch, monkeypatch, cancelled, execution_mode
):
    h = selected_batch
    prepared = await h.worker._prepare_item_for_execution(h.job, h.item())
    engine = h.worker._execution_engine
    acquire = engine._acquire_prepared_policy_lease
    release = AsyncMock(wraps=engine._release_prepared_policy_lease)

    async def partial_admission(**kwargs):
        await acquire(**kwargs)
        assert prepared.policy_lease is not None
        if cancelled:
            raise asyncio.CancelledError()
        raise TimeoutError("admission deadline")

    monkeypatch.setattr(engine, "_acquire_prepared_policy_lease", partial_admission)
    monkeypatch.setattr(engine, "_release_prepared_policy_lease", release)
    execute = (
        engine._execute_prepared_chat_item(h.job, prepared)
        if execution_mode == "single"
        else engine._execute_prepared_chat_microbatch_chunk(h.job, [prepared])
    )
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await execute
    else:
        await execute
    release.assert_awaited_once_with(prepared)
    assert prepared.policy_lease is None
    assert not h.calls and not h.checkpoints.writes


async def test_selector_timeout_saves_safe_default_and_releases_capacity(selected_batch):
    h = selected_batch
    h.policy["selector"]["timeout_ms"] = 100
    _publish(h.app, [h.policy])
    h.selector_gate = asyncio.Event()
    item = h.item()
    await asyncio.wait_for(h.worker._process_item(h.job, item), timeout=2)
    assert h.selector_closed.is_set() and h.active == 0
    assert len(selection_calls(h)) == 1
    assert answer_calls(h)[0]["model"] == "quality"
    assert item.selector_checkpoint["decision"]["lane"] == "quality"


@pytest.mark.parametrize("stage", ["selector", "answer"])
async def test_job_deadline_cancels_entire_item_lifetime(selected_batch, monkeypatch, stage):
    h = selected_batch
    wall_time = datetime.now(UTC)
    loop = asyncio.get_running_loop()
    clock = loop.time()

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return wall_time.astimezone(tz)

    monkeypatch.setattr("src.batch.selector_execution.datetime", FixedDatetime)
    h.job.expires_at = wall_time + timedelta(milliseconds=150)
    provider = h.provider
    answer_started, answer_closed = asyncio.Event(), asyncio.Event()
    if stage == "selector":
        h.selector_gate = asyncio.Event()
    else:

        async def blocked_answer(request):
            if json.loads(request.content).get("max_tokens") != 64:
                answer_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    answer_closed.set()
            return await provider(request)

        h.provider = blocked_answer
    # No wall-clock race between fixture/preflight work and either paid stage.
    # Advance the real event loop's timers only once the chosen provider is blocked.
    with monkeypatch.context() as timer_patch:
        timer_patch.setattr(loop, "time", lambda: clock)
        prepared = await h.worker._prepare_item_for_execution(h.job, h.item())
        assert prepared.selector.deadline.expires_at == pytest.approx(clock + 0.15, abs=1e-6, rel=0)
        task = asyncio.create_task(
            h.worker._execution_engine._execute_prepared_chat_item(h.job, prepared)
        )
        try:
            started = h.selector_started if stage == "selector" else answer_started
            await _drain_ready_until(lambda: started.is_set() or task.done())
            assert started.is_set()
            assert not task.done()
            assert prepared.policy_lease is not None
            clock = prepared.selector.deadline.expires_at + 0.001
            await _drain_ready_until(task.done)
            await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert prepared.policy_lease is None and not h.repository.completed_calls
    assert len(selection_calls(h)) == 1
    assert h.active == 0
    if stage == "selector":
        assert h.selector_closed.is_set() and not answer_calls(h)
    else:
        assert answer_started.is_set() and answer_closed.is_set()
        h.billing.accept_selector.assert_awaited_once()


async def _drain_ready_until(done: Callable[[], bool]) -> None:
    # All dependencies here are in-memory fakes. Bound scheduler turns so a
    # missing timeout/cancellation fails instead of hanging under the fixed clock.
    for _ in range(1000):
        if done():
            return
        await asyncio.sleep(0)
    pytest.fail("Batch execution did not reach the expected stage under the controlled clock")


async def test_answer_failure_does_not_drop_or_repeat_paid_selector_receipt(selected_batch):
    h = selected_batch
    h.answer_error = 400
    item = h.item()
    await h.worker._process_item(h.job, item)
    assert not h.repository.completed_calls and h.repository.failed_calls
    h.billing.accept_selector.assert_awaited_once()
    assert len(selection_calls(h)) == 1
    assert item.selector_checkpoint["decision"] is not None
