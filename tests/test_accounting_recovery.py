"""Recovery has one owner, fixed work, and no invented healthy zero."""

import asyncio

import pytest

from src.billing.accounting_health import (
    AccountingBacklogPolicy,
    AccountingBacklogProbe,
    AccountingBacklogSnapshot,
)
from src.billing.accounting_recovery import AccountingRecoveryWorker, RecoveryAction, RecoveryConfig
from src.concurrency import CapacityGateFull
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_permit_results import invalid_result
from src.db.accounting_recovery import AccountingRecoveryRepository
from src.telemetry.lifecycle import WorkerState


def deadline(seconds=1):
    return asyncio.get_running_loop().time() + seconds


class Persistence:
    def __init__(self):
        self.calls = []
        self.snapshots = []
        self.entered = asyncio.Event()
        self.resume = None
        self.error = None
        self.count = 1
        self.failed = 0

    async def recover(self, action, **kwargs):
        self.calls.append((action, kwargs))
        self.entered.set()
        if self.resume is not None:
            await self.resume.wait()
        if self.error is not None:
            raise self.error
        return self.count

    async def snapshot(self, **kwargs):
        self.snapshots.append(kwargs)
        return AccountingBacklogSnapshot(
            generation=7,
            protocol_state="active",
            partition_count=4,
            outstanding_operations=self.failed,
            pending_entries=self.failed,
            pending_bytes=4 * self.failed,
            failed_entries=self.failed,
            oldest_age_seconds=0.0 if self.failed else None,
            capacity_saturated=False,
        )


def worker(persistence, **kwargs):
    return AccountingRecoveryWorker(
        persistence,
        AccountingBacklogProbe(persistence, AccountingBacklogPolicy(generation=7)),
        RecoveryConfig(generation=7, **kwargs),
    )


async def test_tick_has_four_separate_actions_and_one_probe_with_one_deadline():
    persistence = Persistence()
    runtime = worker(persistence)
    end = deadline()
    assert await runtime.run_once(expires_at=end) == 4
    assert [action for action, _ in persistence.calls] == list(RecoveryAction)
    assert all(
        values == {"generation": 7, "limit": 128, "expires_at": end}
        for _, values in persistence.calls
    )
    assert persistence.snapshots == [{"generation": 7, "expires_at": end}]
    assert runtime.probe.worker_health.ready


async def test_process_withdrawal_stops_cycles_without_inventing_a_failed_or_ready_worker():
    persistence = Persistence()
    runtime = worker(persistence)
    await runtime.start(expires_at=deadline())
    calls = len(persistence.calls)
    runtime.stop_claims()
    await runtime.task
    assert runtime.worker_health.state is WorkerState.STOPPING
    assert len(persistence.calls) == calls
    assert await runtime.close(expires_at=deadline())
    assert runtime.worker_health.state is WorkerState.DISABLED


@pytest.mark.parametrize("value", [True, -1, 129, "1", None])
async def test_bad_count_cannot_continue_or_sample_health(value):
    persistence = Persistence()
    persistence.count = value
    runtime = worker(persistence)
    with pytest.raises(AccountingProtocolUnavailable):
        await runtime.run_once(expires_at=deadline())
    assert len(persistence.calls) == 1 and not persistence.snapshots


async def test_rollover_failure_cannot_publish_a_healthy_backlog():
    class FailedRollover(Persistence):
        async def recover(self, action, **kwargs):
            count = await super().recover(action, **kwargs)
            if action is RecoveryAction.ROLL_WINDOWS:
                raise invalid_result()
            return count

    persistence = FailedRollover()
    runtime = worker(persistence)
    with pytest.raises(AccountingProtocolUnavailable):
        await runtime.run_once(expires_at=deadline())
    assert len(persistence.calls) == 4 and not persistence.snapshots
    assert not runtime.dependencies_checked and not runtime.worker_health.ready


async def test_overlap_rejects_without_a_waiter_or_extra_query():
    persistence = Persistence()
    persistence.resume = asyncio.Event()
    runtime = worker(persistence)
    task = asyncio.create_task(runtime.run_once(expires_at=deadline()))
    await persistence.entered.wait()
    with pytest.raises(CapacityGateFull):
        await runtime.run_once(expires_at=deadline())
    assert len(persistence.calls) == 1
    persistence.resume.set()
    assert await task == 4


@pytest.mark.parametrize("phase", ["cancel", "timeout"])
async def test_interruption_stops_the_cycle_and_releases_its_gate(phase):
    persistence = Persistence()
    persistence.resume = asyncio.Event()
    runtime = worker(persistence)
    task = asyncio.create_task(runtime.run_once(expires_at=deadline(0.03)))
    await persistence.entered.wait()
    if phase == "cancel":
        task.cancel()
    with pytest.raises(asyncio.CancelledError if phase == "cancel" else TimeoutError):
        await task
    assert len(persistence.calls) == 1 and not persistence.snapshots
    persistence.resume = None
    assert await runtime.run_once(expires_at=deadline()) == 4


async def test_start_waits_for_a_real_cycle_and_close_does_not_prove_global_drain():
    persistence = Persistence()
    persistence.resume = asyncio.Event()
    runtime = worker(persistence)
    started = asyncio.create_task(runtime.start(expires_at=deadline()))
    await persistence.entered.wait()
    assert not started.done() and not runtime.worker_health.ready
    persistence.resume.set()
    await started
    assert runtime.worker_health.ready
    assert await runtime.close(expires_at=deadline())
    assert runtime.task.done() and runtime.worker_health.state is WorkerState.DISABLED
    with pytest.raises(RuntimeError, match="cannot restart"):
        await runtime.start(expires_at=deadline())
    with pytest.raises(RuntimeError, match="closed"):
        await runtime.run_once(expires_at=deadline())


@pytest.mark.parametrize("failure", ["database", "dead_letter", "unexpected"])
async def test_failed_start_is_not_ready_and_does_not_expose_financial_text(failure):
    persistence = Persistence()
    if failure == "dead_letter":
        persistence.failed = 1
    else:
        persistence.error = invalid_result() if failure == "database" else RuntimeError("private")
    runtime = worker(persistence)
    with pytest.raises(RuntimeError):
        await runtime.start(expires_at=deadline())
    assert runtime.task.done() and not runtime.worker_health.ready
    assert "private" not in (runtime.worker_health.detail or "")


async def test_closed_probe_cannot_become_healthy_after_a_late_reply():
    persistence = Persistence()
    runtime = worker(persistence)
    await runtime.start(expires_at=deadline())
    persistence.entered.clear()
    persistence.resume = asyncio.Event()
    tick = asyncio.create_task(runtime.run_once(expires_at=deadline()))
    await persistence.entered.wait()
    assert not await runtime.close(expires_at=deadline())
    assert not runtime.worker_health.ready
    persistence.resume.set()
    with pytest.raises(AccountingProtocolUnavailable):
        await tick
    assert not runtime.probe.worker_health.ready


@pytest.mark.parametrize("end", [float("nan"), float("inf"), True])
async def test_invalid_deadline_cannot_start_or_query(end):
    persistence = Persistence()
    runtime = worker(persistence)
    with pytest.raises(ValueError):
        await runtime.start(expires_at=end)
    with pytest.raises(ValueError):
        await runtime.run_once(expires_at=end)
    assert runtime.task is None and not persistence.calls


async def test_expired_start_does_not_create_a_task():
    runtime = worker(Persistence())
    with pytest.raises(TimeoutError):
        await runtime.start(expires_at=deadline(-1))
    assert runtime.task is None


async def test_old_valid_backlog_keeps_repair_live_but_not_ready():
    class OldBacklog(Persistence):
        async def snapshot(self, **kwargs):
            value = await super().snapshot(**kwargs)
            return value.model_copy(
                update={
                    "outstanding_operations": 1,
                    "pending_entries": 1,
                    "pending_bytes": 4,
                    "oldest_age_seconds": 120.0,
                }
            )

    runtime = worker(OldBacklog())
    await runtime.start(expires_at=deadline())
    assert runtime.dependencies_checked and not runtime.task.done()
    assert runtime.worker_health.state is WorkerState.DEGRADED
    assert runtime.worker_health.detail == "terminal_age_limit"
    assert await runtime.close(expires_at=deadline())


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": True},
        {"batch_size": True},
        {"batch_size": 257},
        {"poll_seconds": 0.09},
        {"call_budget_seconds": float("inf")},
    ],
)
def test_copied_config_is_strict(changes):
    config = RecoveryConfig(generation=7).model_copy(update=changes)
    with pytest.raises(ValueError):
        AccountingRecoveryWorker(Persistence(), worker(Persistence()).probe, config)


class QueryClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def query_raw(self, query, *parameters):
        self.calls.append((query, parameters))
        return self.rows


@pytest.mark.parametrize("action", list(RecoveryAction))
async def test_repository_uses_one_allowlisted_function_and_preserves_scalar_count(action):
    client = QueryClient([{"count": 4}])
    assert (
        await AccountingRecoveryRepository(client).recover(
            action, generation=7, limit=4, expires_at=deadline()
        )
        == 4
    )
    assert len(client.calls) == 1 and client.calls[0][1] == (7, 4)
    assert "$1,$2::integer" in client.calls[0][0]


@pytest.mark.parametrize(
    "rows",
    [[], [{"count": 0}, {"count": 0}], [{}], [{"count": True}], [{"count": -1}], [{"count": 5}]],
)
async def test_repository_never_turns_bad_result_into_empty_success(rows):
    with pytest.raises(AccountingProtocolUnavailable):
        await AccountingRecoveryRepository(QueryClient(rows)).recover(
            RecoveryAction.SETTLE_GRANTS, generation=7, limit=4, expires_at=deadline()
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"action": "settle_grants"},
        {"generation": True},
        {"generation": 0},
        {"limit": True},
        {"limit": 257},
    ],
)
async def test_bad_repository_input_never_queries(changes):
    client = QueryClient([{"count": 0}])
    values = {
        "action": RecoveryAction.SETTLE_GRANTS,
        "generation": 7,
        "limit": 4,
        "expires_at": deadline(),
        **changes,
    }
    with pytest.raises(ValueError):
        await AccountingRecoveryRepository(client).recover(**values)
    assert not client.calls
