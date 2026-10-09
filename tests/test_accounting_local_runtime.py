"""One local lifecycle preserves proofs and shares startup and close deadlines."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.billing.accounting.permits.accounting_local_returns import LocalReturnWorker
from src.billing.accounting.accounting_local_runtime import LocalAccountingRuntime
from src.billing.accounting.durable_microbatch import DurableBatchClosed
from src.lifecycle_settings import LifecycleSettings
from src.process_lifecycle import ProcessLifecycle
from src.shutdown import ShutdownOwner, shutdown_owner
from src.telemetry.lifecycle import WorkerState
from tests.test_accounting_local_issue import deadline
from tests.test_accounting_local_issuer import items
from tests.test_accounting_local_returns import Returns
from tests.test_accounting_local_service import handle, state as service_state
from tests.test_accounting_protocol import finalization


def state():
    funding, terminal, cursors, receipts, issuer, service = service_state(dwell_seconds=0)
    service._generation_probe.protocol_ready = AsyncMock(return_value=True)
    returns = Returns()
    worker = LocalReturnWorker(returns, cursors, issuer, poll_seconds=0.01)
    runtime = LocalAccountingRuntime(service, worker)
    return funding, terminal, returns, cursors, receipts, issuer, service, worker, runtime


async def test_start_requires_one_actual_generation_probe_and_owned_return_worker():
    _, _, _, _, _, _, service, worker, runtime = state()
    assert not runtime.worker_health.ready
    assert not await runtime.readiness_probe(expires_at=deadline())
    with pytest.raises(RuntimeError, match="not active"):
        _ = runtime.service
    await runtime.start(expires_at=deadline())
    assert runtime.service is service and runtime.worker_health.ready
    assert worker.task is not None and not worker.task.done()
    service._generation_probe.protocol_ready.assert_awaited_once_with(7)
    await runtime.start(expires_at=deadline())
    assert service._generation_probe.protocol_ready.await_count == 1
    assert await runtime.close(expires_at=deadline())
    assert runtime.worker_health.state is WorkerState.DISABLED
    with pytest.raises(RuntimeError, match="restart"):
        await runtime.start(expires_at=deadline())


async def test_close_drains_terminal_queue_before_closing_the_return_owner():
    _, persistence, returns, cursors, receipts, issuer, service, worker, runtime = state()
    await runtime.start(expires_at=deadline())
    operation = handle(await service.reserve(items(1)[0]))
    persistence.entered = asyncio.Event()
    persistence.resume = asyncio.Event()
    original_terminal = persistence.finalize_batch

    async def blocked(values, *, expires_at):
        persistence.entered.set()
        await persistence.resume.wait()
        return await original_terminal(values, expires_at=expires_at)

    persistence.finalize_batch = blocked
    order = []
    closing_queues = asyncio.Event()
    original_queue_close, original_return_close = service.close, worker.close

    async def queue_close(*, timeout_seconds):
        order.append("queues")
        closing_queues.set()
        await original_queue_close(timeout_seconds=timeout_seconds)

    async def return_close(*, expires_at):
        order.append("returns")
        return await original_return_close(expires_at=expires_at)

    service.close, worker.close = queue_close, return_close
    terminal = asyncio.create_task(
        service.finalize_operation(operation, finalization(operation.reservation))
    )
    await persistence.entered.wait()
    closer = asyncio.create_task(runtime.close(expires_at=deadline()))
    try:
        async with asyncio.timeout(1):
            await closing_queues.wait()
        with pytest.raises(DurableBatchClosed):
            await issuer.reserve_batch(items(1), expires_at=deadline())
        assert order == ["queues"] and receipts.entries == 1
    finally:
        persistence.resume.set()
    assert await terminal
    assert await closer
    assert order == ["queues", "returns"]
    assert len(returns.calls) == 1
    assert (
        cursors.entries
        == cursors.retained_bytes
        == receipts.entries
        == receipts.retained_bytes
        == 0
    )


async def test_issued_but_unreported_operation_cannot_be_called_a_complete_drain():
    _, _, returns, cursors, receipts, _, service, _, runtime = state()
    await runtime.start(expires_at=deadline())
    await service.reserve(items(1)[0])
    charged = receipts.retained_bytes
    assert not await runtime.close(expires_at=deadline())
    assert len(returns.calls) == 1 and cursors.entries == 0
    assert receipts.entries == 1 and receipts.retained_bytes == charged
    assert runtime.worker_health.state is WorkerState.FAILED
    assert runtime.worker_health.detail == "drain_incomplete"
    assert not await runtime.close(expires_at=deadline())
    assert len(returns.calls) == 1


async def test_startup_failure_cleans_owners_and_never_selects_the_service():
    _, _, _, _, _, issuer, service, worker, runtime = state()
    service._generation_probe.protocol_ready.return_value = False
    with pytest.raises(RuntimeError, match="generation"):
        await runtime.start(expires_at=deadline())
    assert all(task.done() for task in runtime._queue_tasks)
    assert worker.task.done() and runtime.worker_health.state is WorkerState.FAILED
    assert runtime.worker_health.detail == "startup_failed"
    with pytest.raises(DurableBatchClosed):
        await issuer.reserve_batch(items(1), expires_at=deadline())
    with pytest.raises(RuntimeError, match="not active"):
        _ = runtime.service


async def test_close_before_start_stops_issue_without_starting_admission_queues():
    _, _, _, _, _, issuer, service, _, runtime = state()
    assert await runtime.close(expires_at=deadline())
    assert service.reservations.task is service.finalizations.task is None
    service._generation_probe.protocol_ready.assert_not_awaited()
    with pytest.raises(DurableBatchClosed):
        await issuer.reserve_batch(items(1), expires_at=deadline())


async def test_readiness_uses_the_actual_generation_and_fixed_health_details():
    _, _, _, _, _, _, service, worker, runtime = state()
    await runtime.start(expires_at=deadline())
    try:
        assert await runtime.readiness_probe(expires_at=deadline())
        service._generation_probe.protocol_ready.return_value = False
        assert not await runtime.readiness_probe(expires_at=deadline())
        worker.task.cancel()
        await asyncio.gather(worker.task, return_exceptions=True)
        assert not await runtime.readiness_probe(expires_at=deadline())
        assert runtime.worker_health.detail == "returns_unready"
        with pytest.raises(RuntimeError, match="not active"):
            _ = runtime.service
    finally:
        assert not await runtime.close(expires_at=deadline())


async def test_unexpected_queue_exit_is_not_hidden_by_another_healthy_owner():
    _, _, _, _, _, _, service, _, runtime = state()
    await runtime.start(expires_at=deadline())
    service.reservations.task.cancel()
    await asyncio.gather(service.reservations.task, return_exceptions=True)
    assert runtime.worker_health.state is WorkerState.FAILED
    assert runtime.worker_health.detail == "queues_failed"
    assert not await runtime.close(expires_at=deadline())


async def test_blocked_return_keeps_suffix_proof_within_one_close_budget():
    _, _, returns, cursors, receipts, _, service, _, runtime = state()
    await runtime.start(expires_at=deadline())
    operation = handle(await service.reserve(items(1)[0]))
    await service.finalize_operation(operation, finalization(operation.reservation))
    returns.resume = asyncio.Event()
    charged = cursors.retained_bytes
    started = asyncio.get_running_loop().time()
    assert not await runtime.close(expires_at=started + 0.03)
    assert asyncio.get_running_loop().time() - started < 0.2
    assert cursors.entries == 1 and cursors.retained_bytes == charged
    assert receipts.entries == 0 and runtime.worker_health.state is WorkerState.FAILED


async def test_cancelled_close_stops_all_owned_tasks_but_keeps_unused_funding():
    _, _, returns, cursors, receipts, _, service, worker, runtime = state()
    await runtime.start(expires_at=deadline())
    operation = handle(await service.reserve(items(1)[0]))
    await service.finalize_operation(operation, finalization(operation.reservation))
    returns.resume = asyncio.Event()
    charged = cursors.retained_bytes
    closer = asyncio.create_task(runtime.close(expires_at=deadline()))
    await returns.entered.wait()
    closer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closer
    assert all(task.done() for task in runtime._queue_tasks)
    assert worker.task.done() and runtime._close_task.done()
    assert cursors.entries == 1 and cursors.retained_bytes == charged and receipts.entries == 0
    assert not runtime.worker_health.ready


async def test_concurrent_close_has_one_cleanup_task_and_one_bulk_return():
    _, _, returns, _, _, _, service, worker, runtime = state()
    await runtime.start(expires_at=deadline())
    operation = handle(await service.reserve(items(1)[0]))
    await service.finalize_operation(operation, finalization(operation.reservation))
    queue_close, return_close = service.close, worker.close
    service.close, worker.close = AsyncMock(wraps=queue_close), AsyncMock(wraps=return_close)
    assert (
        await asyncio.gather(*(runtime.close(expires_at=deadline()) for _ in range(3)))
        == [True] * 3
    )
    assert service.close.await_count == worker.close.await_count == len(returns.calls) == 1


async def test_blocked_probe_retains_one_task_and_does_not_exceed_startup_deadline():
    _, _, _, _, _, _, service, _, runtime = state()
    entered, release = asyncio.Event(), asyncio.Event()

    async def ignores_cancellation():
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
        return True

    service.readiness_probe = ignores_cancellation
    started = asyncio.get_running_loop().time()
    try:
        with pytest.raises(RuntimeError, match="generation"):
            await runtime.start(expires_at=started + 0.03)
        assert entered.is_set() and asyncio.get_running_loop().time() - started < 0.2
        assert runtime._probe_task is not None and not runtime._probe_task.done()
        assert not runtime.worker_health.ready
    finally:
        release.set()
        await runtime._probe_task


async def test_close_during_startup_cannot_start_or_select_admission_after_drain():
    _, _, _, _, _, _, service, _, runtime = state()
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_probe():
        entered.set()
        await release.wait()
        return True

    service.readiness_probe = blocked_probe
    starter = asyncio.create_task(runtime.start(expires_at=deadline()))
    await entered.wait()
    assert await runtime.close(expires_at=deadline())
    with pytest.raises(RuntimeError):
        await starter
    assert all(task.done() for task in runtime._queue_tasks)
    assert not runtime.worker_health.ready
    with pytest.raises(RuntimeError, match="not active"):
        _ = runtime.service


async def test_cancelled_startup_cleans_all_tasks_without_selecting_admission():
    _, _, _, _, _, _, service, worker, runtime = state()
    entered = asyncio.Event()

    async def blocked_probe():
        entered.set()
        await asyncio.Event().wait()

    service.readiness_probe = blocked_probe
    starter = asyncio.create_task(runtime.start(expires_at=deadline()))
    await entered.wait()
    starter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starter
    assert all(task.done() for task in runtime._queue_tasks)
    assert runtime._probe_task.done() and worker.task.done()
    assert runtime.worker_health.detail == "startup_failed"


async def test_expired_startup_and_probe_do_not_create_tasks_or_database_calls():
    _, _, _, _, _, _, service, worker, runtime = state()
    expired = asyncio.get_running_loop().time() - 1
    with pytest.raises(TimeoutError):
        await runtime.start(expires_at=expired)
    assert not await runtime.readiness_probe(expires_at=expired)
    assert worker.task is None and runtime._probe_task is None
    assert service.reservations.task is service.finalizations.task is None
    service._generation_probe.protocol_ready.assert_not_awaited()


@pytest.mark.parametrize("failure", ["cancel", "close"])
async def test_return_task_failure_during_startup_cannot_select_the_service(failure):
    _, _, _, _, _, _, service, worker, runtime = state()
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_probe():
        entered.set()
        await release.wait()
        return True

    service.readiness_probe = blocked_probe
    starter = asyncio.create_task(runtime.start(expires_at=deadline()))
    await entered.wait()
    if failure == "cancel":
        worker.task.cancel()
        await asyncio.gather(worker.task, return_exceptions=True)
    else:
        assert await worker.close(expires_at=deadline())
    release.set()
    with pytest.raises(RuntimeError, match="workers are not ready"):
        await starter
    assert runtime.worker_health.detail == "startup_failed"
    with pytest.raises(RuntimeError, match="not active"):
        _ = runtime.service


async def test_overlapping_readiness_probes_do_not_create_another_task_or_call():
    _, _, _, _, _, _, service, _, runtime = state()
    await runtime.start(expires_at=deadline())
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_probe():
        entered.set()
        await release.wait()
        return True

    probe = AsyncMock(side_effect=blocked_probe)
    service.readiness_probe = probe
    first = asyncio.create_task(runtime.readiness_probe(expires_at=deadline()))
    try:
        await entered.wait()
        owned = runtime._probe_task
        assert not await runtime.readiness_probe(expires_at=deadline())
        assert runtime._probe_task is owned and probe.await_count == 1
    finally:
        release.set()
        assert await first
        assert await runtime.close(expires_at=deadline())


async def test_cancel_resistant_closer_stays_owned_under_the_process_deadline():
    _, _, _, _, _, _, service, _, runtime = state()
    await runtime.start(expires_at=deadline())
    release = asyncio.Event()

    async def resistant_close(*, timeout_seconds):
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    service.close = resistant_close
    lifecycle = ProcessLifecycle(
        LifecycleSettings(
            lifecycle_withdrawal_seconds=0,
            lifecycle_request_drain_seconds=0.01,
            lifecycle_cancellation_seconds=0.01,
            lifecycle_worker_drain_seconds=0.02,
            lifecycle_close_seconds=0.1,
            lifecycle_shutdown_seconds=0.2,
        )
    )
    lifecycle.mark_serving()
    lifecycle.begin_drain()
    owner = ShutdownOwner(lifecycle)
    token = shutdown_owner.set(owner)
    started = asyncio.get_running_loop().time()
    try:
        assert not await runtime.close(expires_at=deadline())
        assert asyncio.get_running_loop().time() - started < 0.2
        assert owner.failed and runtime._queue_close_task in owner.pending
        assert runtime.worker_health.state is WorkerState.FAILED
        retained_callbacks = len(runtime._queue_close_task._callbacks or ())
        for _ in range(5):
            assert not await runtime.close(expires_at=deadline())
        assert len(runtime._queue_close_task._callbacks or ()) == retained_callbacks
    finally:
        release.set()
        await asyncio.gather(*owner.pending, return_exceptions=True)
        shutdown_owner.reset(token)


@pytest.mark.parametrize("owner", ["issuer", "queues", "returns"])
async def test_construction_rejects_another_owner_or_running_tasks(owner):
    _, _, _, _, _, _, service, worker, runtime = state()
    try:
        if owner == "issuer":
            _, _, _, _, _, _, service, _, _ = state()
        elif owner == "queues":
            service.start()
        else:
            await worker.start(expires_at=deadline())
        with pytest.raises(ValueError, match="owner|running"):
            LocalAccountingRuntime(service, worker)
    finally:
        await runtime.close(expires_at=deadline())
        if owner == "queues":
            await service.close()


@pytest.mark.parametrize("value", [True, None, float("nan"), float("inf")])
async def test_invalid_lifecycle_deadlines_do_not_change_ownership(value):
    _, _, _, _, _, _, service, worker, runtime = state()
    for action in (runtime.start, runtime.close, runtime.readiness_probe):
        with pytest.raises(ValueError):
            await action(expires_at=value)
    assert runtime._close_task is None and worker.task is None
    assert service.reservations.task is service.finalizations.task is None
