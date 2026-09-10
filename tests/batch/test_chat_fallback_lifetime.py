import asyncio
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.models.errors import ServiceUnavailableError
from tests.batch import selector_fixtures
from tests.test_routing_cache_identity import _publish

pytestmark = pytest.mark.app
selected_batch = selector_fixtures.selected_batch


@pytest.fixture
async def split_batch(selected_batch, monkeypatch):
    h = selected_batch
    h.policy.pop("selector")
    h.policy["members"] = [{"deployment_id": "quality"}]
    for member in h.app.state.model_registry["backing"]:
        member["deltallm_params"]["chat_batching"] = {
            "mode": "sync_microbatch",
            "max_in_flight": 1,
            "upstream_max_batch_size": 8,
        }
    _publish(h.app, [h.policy])
    for record in h.app.state._test_repo.records.values():
        record.max_parallel_requests = 10
        await h.app.state.key_service.invalidate_key_cache_by_hash(record.token)
    h.app.state.chat_microbatch_executor = SimpleNamespace(
        execute_chat_microbatch=AsyncMock(
            side_effect=ServiceUnavailableError(
                code="chat_microbatch_unsupported",
                affects_deployment_health=False,
            )
        )
    )
    prepared = [await h.worker._prepare_item_for_execution(h.job, h.item(i)) for i in (1, 2, 3)]
    engine = h.worker._execution_engine
    heartbeats = []
    start = engine._start_heartbeat_fn

    def track(**kwargs):
        task = start(**kwargs)
        heartbeats.append((kwargs["label"], task))
        return task

    monkeypatch.setattr(engine, "_start_heartbeat_fn", track)
    return h, prepared, heartbeats


async def test_split_cancellation_releases_unstarted_items_and_all_tasks(split_batch):
    h, prepared, heartbeats = split_batch
    started = asyncio.Event()

    async def blocked(request):
        started.set()
        await asyncio.Event().wait()

    h.provider = blocked
    task = asyncio.create_task(
        h.worker._execution_engine._execute_prepared_chat_microbatch_chunk(h.job, prepared)
    )
    await asyncio.wait_for(started.wait(), 1)
    refreshers = [item.policy_lease_refresher for item in prepared]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(item.policy_lease is None for item in prepared)
    assert all(item.policy_lease_refresher is None for item in prepared)
    assert all(refresher is None or refresher._task is None for refresher in refreshers)
    assert all(task.done() for _, task in heartbeats)
    assert not h.repository.completed_calls


async def test_split_waiting_items_keep_their_original_heartbeats(split_batch, monkeypatch):
    h, prepared, heartbeats = split_batch
    started, renewed, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    renewals = Counter()
    provider = h.provider

    async def renew(**kwargs):
        if started.is_set():
            renewals[kwargs["item_id"]] += 1
            if all(renewals[item.item.item_id] for item in prepared):
                renewed.set()
        return True

    async def blocked(request):
        started.set()
        await release.wait()
        return await provider(request)

    monkeypatch.setattr(h.repository, "renew_item_lease", renew)
    h.provider = blocked
    task = asyncio.create_task(
        h.worker._execution_engine._execute_prepared_chat_microbatch_chunk(h.job, prepared)
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(renewed.wait(), 0.5)
        assert len(heartbeats) == len(prepared)
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert len(h.repository.completed_calls) == len(prepared)
    assert all(task.done() for _, task in heartbeats)


async def test_split_escaping_exception_cleans_up_entire_chunk(split_batch, monkeypatch):
    h, prepared, heartbeats = split_batch
    monkeypatch.setattr(
        h.worker._execution_engine,
        "_execute_prepared_chat_item",
        AsyncMock(side_effect=RuntimeError("escaped")),
    )
    with pytest.raises(RuntimeError, match="escaped"):
        await h.worker._execution_engine._execute_prepared_chat_microbatch_chunk(h.job, prepared)
    assert all(item.policy_lease is None for item in prepared)
    assert all(item.policy_lease_refresher is None for item in prepared)
    assert all(task.done() for _, task in heartbeats)


async def test_split_cancellation_between_items_releases_remaining_leases(split_batch, monkeypatch):
    h, prepared, heartbeats = split_batch
    engine = h.worker._execution_engine
    execute = engine._execute_prepared_chat_item
    between = asyncio.Event()

    async def first_only(*args, **kwargs):
        await execute(*args, **kwargs)
        between.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, "_execute_prepared_chat_item", first_only)
    task = asyncio.create_task(engine._execute_prepared_chat_microbatch_chunk(h.job, prepared))
    await asyncio.wait_for(between.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(h.repository.completed_calls) == 1
    assert all(item.policy_lease is None for item in prepared)
    assert all(task.done() for _, task in heartbeats)


async def test_split_failed_dispatch_fence_never_calls_provider_or_marks_failure(
    split_batch, monkeypatch
):
    h, prepared, heartbeats = split_batch
    renew = h.repository.renew_item_lease

    async def reclaimed(**kwargs):
        if kwargs.get("expires_at") is not None:
            return False
        return await renew(**kwargs)

    monkeypatch.setattr(h.repository, "renew_item_lease", reclaimed)
    await h.worker._execution_engine._execute_prepared_chat_microbatch_chunk(h.job, prepared)
    assert not h.calls and not h.repository.completed_calls and not h.repository.failed_calls
    assert all(item.policy_lease is None for item in prepared)
    assert all(task.done() for _, task in heartbeats)


@pytest.mark.parametrize("failure", ["heartbeat", "persistence"])
async def test_chunk_setup_and_result_failures_cleanup_all_resources(
    split_batch, monkeypatch, failure
):
    h, prepared, heartbeats = split_batch
    engine = h.worker._execution_engine
    if failure == "heartbeat":
        start = engine._start_heartbeat_fn

        def fail_second(**kwargs):
            if heartbeats:
                raise RuntimeError("heartbeat setup failed")
            return start(**kwargs)

        monkeypatch.setattr(engine, "_start_heartbeat_fn", fail_second)
    else:
        monkeypatch.setattr(
            engine,
            "_persist_completion_rows_with_outbox",
            AsyncMock(side_effect=RuntimeError("persistence failed")),
        )
    await engine._execute_prepared_chat_microbatch_chunk(h.job, prepared)
    assert all(item.policy_lease is None for item in prepared)
    assert all(task.done() for _, task in heartbeats)


async def test_split_reuses_admission_and_stops_watch_before_terminal_state(
    split_batch, monkeypatch
):
    h, prepared, heartbeats = split_batch
    engine = h.worker._execution_engine
    original = engine._persist_completion_rows_with_outbox

    async def persist(**kwargs):
        completed_id = kwargs["item_ids"][0]
        assert all(task.done() for label, task in heartbeats if label == f"item:{completed_id}")
        return await original(**kwargs)

    monkeypatch.setattr(engine, "_persist_completion_rows_with_outbox", persist)
    from src.batch import policy

    acquire = AsyncMock(wraps=policy.acquire_rate_limit_controls)
    monkeypatch.setattr(policy, "acquire_rate_limit_controls", acquire)
    await engine._execute_prepared_chat_microbatch_chunk(h.job, prepared)
    assert acquire.await_count == len(prepared)
    assert len(h.repository.completed_calls) == len(prepared)
    assert len(heartbeats) == len(prepared)


async def test_waiting_item_lease_loss_cancels_current_answer_and_releases_chunk(
    split_batch, monkeypatch
):
    h, prepared, heartbeats = split_batch
    entered, cancelled = asyncio.Event(), asyncio.Event()
    renew = h.repository.renew_item_lease

    async def reclaimed(**kwargs):
        if entered.is_set() and kwargs["item_id"] == prepared[1].item.item_id:
            return False
        return await renew(**kwargs)

    async def blocked(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(h.repository, "renew_item_lease", reclaimed)
    h.provider = blocked
    await asyncio.wait_for(
        h.worker._execution_engine._execute_prepared_chat_microbatch_chunk(h.job, prepared), 2
    )
    assert cancelled.is_set()
    assert not h.repository.completed_calls and not h.repository.failed_calls
    assert all(item.policy_lease is None for item in prepared)
    assert all(task.done() for _, task in heartbeats)


async def test_split_failed_item_stops_heartbeat_before_failure_and_continues(
    split_batch, monkeypatch
):
    h, prepared, heartbeats = split_batch
    engine, provider = h.worker._execution_engine, h.provider
    mark_failed = engine._mark_item_failed
    calls = 0

    async def fail_first(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            from src.models.errors import InvalidRequestError

            raise InvalidRequestError(message="invalid item", affects_deployment_health=False)
        return await provider(request)

    async def persist_failure(**kwargs):
        failed_id = kwargs["item"].item_id
        assert all(task.done() for label, task in heartbeats if label == f"item:{failed_id}")
        return await mark_failed(**kwargs)

    monkeypatch.setattr(engine, "_mark_item_failed", persist_failure)
    h.provider = fail_first
    await engine._execute_prepared_chat_microbatch_chunk(h.job, prepared)
    assert len(h.repository.failed_calls) == 1
    assert len(h.repository.completed_calls) == 2
    assert calls == 3
    assert all(item.policy_lease is None for item in prepared)
    assert all(task.done() for _, task in heartbeats)
