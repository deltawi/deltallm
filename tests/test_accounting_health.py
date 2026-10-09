"""Durable health keeps unknowns distinct from a verified empty snapshot."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.billing.accounting.health.accounting_health import (
    MAX_RETAINED_BACKLOG_BYTES,
    AccountingBacklogPolicy,
    AccountingBacklogProbe,
    AccountingBacklogSnapshot,
)
from src.db.accounting.health.accounting_health import AccountingBacklogRepository
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.runtime.telemetry_acceptance import AcceptanceFailure
from src.telemetry.lifecycle import WorkerState
from tests.test_preissued_permit_bytes import retained_object_bytes


def snapshot(**changes):
    fields = dict(
        generation=7,
        protocol_state="active",
        partition_count=4,
        outstanding_operations=0,
        pending_entries=0,
        pending_bytes=0,
        failed_entries=0,
        oldest_age_seconds=None,
        capacity_saturated=False,
    )
    return AccountingBacklogSnapshot(**{**fields, **changes})


def deadline(seconds=1):
    return asyncio.get_running_loop().time() + seconds


@dataclass
class Clock:
    value: float = 1.0

    def __call__(self):
        return self.value


class Persistence:
    def __init__(self, value=None):
        self.value = value or snapshot()
        self.calls = []
        self.error = None
        self.entered = asyncio.Event()
        self.resume = None

    async def snapshot(self, **arguments):
        self.calls.append(arguments)
        self.entered.set()
        if self.resume is not None:
            await self.resume.wait()
        if self.error is not None:
            raise self.error
        return self.value


def probe(persistence, clock, **policy):
    return AccountingBacklogProbe(
        persistence, AccountingBacklogPolicy(generation=7, **policy), clock=clock
    )


@pytest.mark.parametrize("state", ["prepared", "active", "draining", "fenced"])
async def test_one_snapshot_has_a_fixed_retained_byte_charge_at_all_scalar_limits(state):
    persistence = Persistence(
        snapshot(
            generation=2**63 - 1,
            protocol_state=state,
            partition_count=64,
            outstanding_operations=64_000_000,
            pending_entries=64_000_000,
            pending_bytes=64 * 67_108_864,
            failed_entries=64_000_000,
            oldest_age_seconds=1.7976931348623157e308,
            capacity_saturated=True,
        )
    )
    runtime = AccountingBacklogProbe(persistence, AccountingBacklogPolicy(generation=2**63 - 1))
    assert runtime.retained_snapshot_bytes == 0
    assert not await runtime.refresh(expires_at=deadline())
    assert runtime.retained_snapshot_bytes == MAX_RETAINED_BACKLOG_BYTES
    assert retained_object_bytes(runtime.snapshot) <= MAX_RETAINED_BACKLOG_BYTES
    runtime.close()
    assert runtime.retained_snapshot_bytes == MAX_RETAINED_BACKLOG_BYTES


async def test_unknown_empty_and_funded_unsettled_are_distinct_states():
    persistence, clock = Persistence(), Clock()
    runtime = probe(persistence, clock)
    assert runtime.snapshot is None and not runtime.worker_health.ready
    assert runtime.worker_health.detail == "backlog_unknown"
    assert await runtime.refresh(expires_at=deadline())
    assert runtime.snapshot.sampled_drained
    persistence.value = snapshot(outstanding_operations=4)
    assert await runtime.refresh(expires_at=deadline())
    assert runtime.worker_health.ready and not runtime.snapshot.sampled_drained
    assert len(persistence.calls) == 2


@pytest.mark.parametrize(
    "changes,detail",
    [
        ({"failed_entries": 1}, "terminal_failed"),
        ({"capacity_saturated": True}, "terminal_capacity"),
        ({"pending_entries": 3, "pending_bytes": 12}, "terminal_backlog_limit"),
        ({"oldest_age_seconds": 61.0}, "terminal_age_limit"),
        ({"protocol_state": "fenced"}, "generation_inactive"),
        ({"protocol_state": "prepared"}, "generation_inactive"),
    ],
)
async def test_durable_failure_age_capacity_and_generation_are_visible(changes, detail):
    fields = snapshot(
        outstanding_operations=4,
        pending_entries=1,
        pending_bytes=4,
        oldest_age_seconds=1.0,
    ).model_dump()
    persistence = Persistence(snapshot(**{**fields, **changes}))
    runtime = probe(persistence, Clock(), max_pending_entries=2)
    assert not await runtime.refresh(expires_at=deadline())
    assert runtime.worker_health.detail == detail


async def test_caching_does_not_reset_age_and_stale_snapshots_are_unready():
    clock = Clock()
    persistence = Persistence(
        snapshot(
            outstanding_operations=4, pending_entries=1, pending_bytes=4, oldest_age_seconds=59.0
        )
    )
    runtime = probe(persistence, clock)
    assert await runtime.refresh(expires_at=deadline())
    clock.value += 2
    assert runtime.worker_health.detail == "terminal_age_limit"
    clock.value += 3
    assert runtime.worker_health.detail == "backlog_stale"
    assert len(persistence.calls) == 1


async def test_overlapping_refresh_is_rejected_without_tasks_waiters_or_another_query():
    persistence, clock = Persistence(), Clock()
    runtime = probe(persistence, clock)
    assert await runtime.refresh(expires_at=deadline())
    persistence.entered.clear()
    persistence.resume = asyncio.Event()
    task = asyncio.create_task(runtime.refresh(expires_at=deadline()))
    await persistence.entered.wait()
    assert runtime.worker_health.ready
    assert not await runtime.refresh(expires_at=deadline())
    assert len(persistence.calls) == 2
    persistence.resume.set()
    assert await task


@pytest.mark.parametrize("failure", ["error", "cancel", "timeout"])
async def test_failure_keeps_prior_snapshot_but_never_reports_it_as_a_fresh_zero(failure):
    clock, persistence = Clock(), Persistence(snapshot(outstanding_operations=4))
    runtime = probe(persistence, clock)
    assert await runtime.refresh(expires_at=deadline())
    before = runtime.snapshot
    if failure == "error":
        persistence.error = AccountingProtocolUnavailable(AcceptanceFailure.CONNECTION)
        assert not await runtime.refresh(expires_at=deadline())
    else:
        persistence.entered.clear()
        persistence.resume = asyncio.Event()
        task = asyncio.create_task(runtime.refresh(expires_at=deadline(0.02)))
        await persistence.entered.wait()
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert not await task
        persistence.resume.set()
        persistence.resume = None
    assert runtime.snapshot is before and not runtime.snapshot.sampled_drained
    assert runtime.worker_health.detail == "backlog_unavailable"
    persistence.error = None
    assert await runtime.refresh(expires_at=deadline())


async def test_unexpected_failure_is_visible_to_the_owner_without_unsafe_health_details():
    persistence, clock = Persistence(), Clock()
    runtime = probe(persistence, clock)
    persistence.error = RuntimeError("secret detail")
    with pytest.raises(RuntimeError, match="secret detail"):
        await runtime.refresh(expires_at=deadline())
    assert runtime.worker_health.detail == "backlog_unavailable"
    assert runtime.snapshot is None


async def test_close_during_refresh_cannot_restore_readiness():
    persistence, clock = Persistence(), Clock()
    persistence.resume = asyncio.Event()
    runtime = probe(persistence, clock)
    task = asyncio.create_task(runtime.refresh(expires_at=deadline()))
    await persistence.entered.wait()
    runtime.close()
    persistence.resume.set()
    assert not await task and runtime.snapshot is None
    assert runtime.worker_health.state is WorkerState.STOPPING
    assert not await runtime.refresh(expires_at=deadline())
    assert len(persistence.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": 8},
        {"pending_entries": True},
        {"oldest_age_seconds": float("nan")},
        {"pending_entries": 1},
        {"failed_entries": 1},
        {"capacity_saturated": 1},
    ],
)
async def test_copied_invalid_observations_cannot_replace_a_valid_snapshot(changes):
    persistence, clock = Persistence(), Clock()
    runtime = probe(persistence, clock)
    assert await runtime.refresh(expires_at=deadline())
    before = runtime.snapshot
    persistence.value = before.model_copy(update=changes)
    assert not await runtime.refresh(expires_at=deadline())
    assert runtime.snapshot is before and not runtime.worker_health.ready


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "1"])
async def test_bad_deadline_makes_no_query(bad):
    persistence = Persistence()
    with pytest.raises(ValueError):
        await probe(persistence, Clock()).refresh(expires_at=bad)
    assert not persistence.calls


async def test_expired_refresh_makes_no_query_and_clock_regression_is_unready():
    persistence, clock = Persistence(), Clock()
    runtime = probe(persistence, clock)
    assert not await runtime.refresh(expires_at=deadline(-1))
    assert not persistence.calls
    assert await runtime.refresh(expires_at=deadline())
    clock.value -= 1
    assert runtime.worker_health.detail == "backlog_stale"


async def test_slow_query_does_not_reset_snapshot_age_on_reply():
    clock, persistence = Clock(), Persistence()
    persistence.resume = asyncio.Event()
    runtime = probe(persistence, clock)
    task = asyncio.create_task(runtime.refresh(expires_at=deadline()))
    await persistence.entered.wait()
    clock.value += 6
    persistence.resume.set()
    assert not await task
    assert runtime.snapshot is not None and runtime.worker_health.detail == "backlog_stale"


class Database:
    def __init__(self, rows=None):
        self.rows = (
            rows
            if rows is not None
            else [
                {
                    **snapshot().model_dump(),
                    "covered_partitions": 4,
                    "expected_covered_partitions": 4,
                }
            ]
        )
        self.calls = []

    async def query_raw(self, query, *parameters):
        self.calls.append((query, parameters))
        return self.rows


async def test_snapshot_repository_has_one_bounded_read_and_no_health_write():
    db = Database()
    value = await AccountingBacklogRepository(db).snapshot(generation=7, expires_at=deadline())
    assert value == snapshot() and len(db.calls) == 1
    query, arguments = db.calls[0]
    assert arguments == (7,)
    assert query.strip() == "SELECT * FROM deltallm_accounting_backlog_snapshot($1)"
    assert not any(word in query.upper() for word in ("UPDATE ", "INSERT ", "DELETE "))


def test_snapshot_sql_function_keeps_fixed_cells_and_ordered_queue_head():
    migration = (
        Path(__file__).resolve().parents[1]
        / "prisma/migrations/20261006050000_accounting_bounded_health_plan/migration.sql"
    ).read_text()
    assert "generate_series(0,63)" in migration
    assert "ORDER BY j.accepted_at,j.sequence LIMIT 1" in migration
    assert "OFFSET 0" in migration and "sum(capacity.pending_entries)" in migration
    assert "SET enable_seqscan=off SET enable_bitmapscan=off" in migration
    assert "indisvalid AND i.indisready" in migration


@pytest.mark.parametrize(
    "change",
    [
        {"generation": 8},
        {"pending_entries": True},
        {"covered_partitions": 3},
        {"covered_partitions": True},
        {"expected_covered_partitions": 3},
        {"expected_covered_partitions": True},
        {"pending_bytes": 4},
        {"oldest_age_seconds": 0.0},
    ],
)
async def test_repository_invalid_missing_or_wrong_generation_is_not_zero(change):
    row = {
        **snapshot().model_dump(),
        "covered_partitions": 4,
        "expected_covered_partitions": 4,
        **change,
    }
    with pytest.raises(AccountingProtocolUnavailable):
        await AccountingBacklogRepository(Database([row])).snapshot(
            generation=7, expires_at=deadline()
        )


@pytest.mark.parametrize("rows", [[], [{}, {}]])
async def test_missing_or_duplicate_protocol_is_unavailable(rows):
    with pytest.raises(AccountingProtocolUnavailable):
        await AccountingBacklogRepository(Database(rows)).snapshot(
            generation=7, expires_at=deadline()
        )


@pytest.mark.parametrize("generation", [True, 0, -1, 2**63, 1.0])
async def test_invalid_generation_cannot_start_a_database_read(generation):
    db = Database()
    with pytest.raises(ValueError):
        await AccountingBacklogRepository(db).snapshot(generation=generation, expires_at=deadline())
    assert db.calls == []
