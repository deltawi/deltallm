"""Native reporting owns fixed keys, rejects partial replies, and retains retries."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.billing.accounting.reporting.accounting_read_model_claims import (
    ReadModelClaim,
    ReadModelWorkerConfig,
)
from src.billing.accounting.health.accounting_read_model_health import (
    ReadModelHealth,
    ReadModelProgress,
)
from src.billing.accounting.reporting.accounting_read_model_runtime import ReadModelProcessingWorker
from src.concurrency import CapacityGateFull
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting.permits.accounting_permit_results import invalid_result
from src.db.accounting.reporting.accounting_read_model import AccountingReadModelRepository
from src.telemetry.lifecycle import WorkerState


def deadline(seconds=1):
    return asyncio.get_running_loop().time() + seconds


def claim(**changes):
    return ReadModelClaim(
        **{
            "generation": 7,
            "worker_id": "report-test",
            "lease_token": uuid4(),
            "accounting_partition": 0,
            "after_sequence": 10,
            "sequences": (11, 12),
            "source_bytes": 1024,
            **changes,
        }
    )


class Database:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.error = None

    async def query_raw(self, query, *parameters):
        self.calls.append((query, parameters))
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        return self.rows


def claim_row(**changes):
    return {
        "generation": 7,
        "accounting_partition": 0,
        "last_sequence": 10,
        "sequences": [11, 12],
        "source_bytes": 1024,
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": True},
        {"accounting_partition": True},
        {"after_sequence": -1},
        {"sequences": (12, 11)},
        {"sequences": (11, 11)},
        {"sequences": (10,)},
        {"sequences": (True,)},
        {"sequences": tuple(range(11, 268))},
        {"source_bytes": 1_048_577},
        {"source_bytes": True},
    ],
)
def test_claim_rejects_invalid_fences_keys_and_byte_charges(changes):
    with pytest.raises(ValueError):
        claim(**changes)


async def test_initialization_and_empty_page_require_actual_generation_and_complete_cells():
    db = Database([{"generation": 7, "partition_count": 4, "slots": 4}])
    repo = AccountingReadModelRepository(db)
    await repo.initialize(generation=7, expires_at=deadline())
    assert len(db.calls) == 1
    db.rows = [
        claim_row(accounting_partition=None, last_sequence=None, sequences=None, source_bytes=None)
    ]
    assert (
        await repo.claim(
            generation=7, worker_id="report-test", limit=64, lease_seconds=30, expires_at=deadline()
        )
        is None
    )
    assert len(db.calls) == 2
    db.rows = []
    with pytest.raises(AccountingProtocolUnavailable):
        await repo.initialize(generation=7, expires_at=deadline())


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": 8},
        {"generation": True},
        {"partition_count": True},
        {"slots": 0},
        {"slots": None},
        {"slots": 3},
        {"slots": 65},
    ],
)
async def test_initialization_cannot_invent_ready_cells(changes):
    db = Database([{"generation": 7, "partition_count": 4, "slots": 4, **changes}])
    with pytest.raises(AccountingProtocolUnavailable):
        await AccountingReadModelRepository(db).initialize(generation=7, expires_at=deadline())


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": 8},
        {"generation": True},
        {"accounting_partition": True},
        {"last_sequence": True},
        {"sequences": [12, 11]},
        {"sequences": [11, 11]},
        {"sequences": [True]},
        {"sequences": [11, 12, 13]},
        {"source_bytes": 1_048_577},
        {"accounting_partition": None},
        {"sequences": []},
    ],
)
async def test_claim_result_is_strict_and_cannot_extend_the_entry_or_byte_budget(changes):
    db = Database([claim_row(**changes)])
    with pytest.raises(AccountingProtocolUnavailable):
        await AccountingReadModelRepository(db).claim(
            generation=7, worker_id="report-test", limit=2, lease_seconds=30, expires_at=deadline()
        )


@pytest.mark.parametrize("count", [None, True, -1, 1, 3])
async def test_commit_rejects_missing_partial_or_fabricated_counts(count):
    db = Database([{"count": count}])
    with pytest.raises(AccountingProtocolUnavailable):
        await AccountingReadModelRepository(db).materialize(claim(), expires_at=deadline())
    assert len(db.calls) == 1


async def test_copied_invalid_handle_fails_before_any_statement():
    db = Database([{"count": 2}])
    copied = claim().model_copy(update={"generation": True})
    with pytest.raises(ValueError):
        await AccountingReadModelRepository(db).materialize(copied, expires_at=deadline())
    assert not db.calls


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": True},
        {"limit": True},
        {"limit": 0},
        {"limit": 257},
        {"lease_seconds": True},
        {"lease_seconds": 4},
        {"worker_id": ""},
    ],
)
async def test_invalid_claim_input_fails_before_any_statement(changes):
    db = Database([claim_row()])
    with pytest.raises(ValueError):
        await AccountingReadModelRepository(db).claim(
            **{
                "generation": 7,
                "worker_id": "report-test",
                "limit": 64,
                "lease_seconds": 30,
                "expires_at": deadline(),
                **changes,
            }
        )
    assert not db.calls


class Persistence:
    def __init__(self):
        self.calls = []
        self.value = None
        self.error = None
        self.entered = asyncio.Event()
        self.resume = None
        self.count = 2

    async def initialize(self, **kwargs):
        self.calls.append(("initialize", kwargs))

    async def claim(self, **kwargs):
        self.calls.append(("claim", kwargs))
        return self.value

    async def materialize(self, value, **kwargs):
        self.calls.append(("materialize", value, kwargs))
        self.entered.set()
        if self.resume is not None:
            await self.resume.wait()
        if self.error is not None:
            raise self.error
        return self.count

    async def progress(self, **kwargs):
        self.calls.append(("progress", kwargs))
        return ReadModelProgress(generation=7, partition_count=4, slots=4, pending_partitions=0)


def worker(persistence):
    return ReadModelProcessingWorker(
        persistence, ReadModelWorkerConfig(generation=7, worker_id="report-test")
    )


async def test_start_waits_for_real_initialization_and_one_claim_then_stops_owned_task():
    db = Persistence()
    owner = worker(db)
    await owner.start(expires_at=deadline())
    assert owner.worker_health.state is WorkerState.READY
    assert [entry[0] for entry in db.calls] == ["initialize", "claim", "progress"]
    owner.stop_claims()
    assert owner.worker_health.state is WorkerState.STOPPING
    assert await owner.close(expires_at=deadline())
    assert owner.task.done() and owner.worker_health.state is WorkerState.DISABLED
    with pytest.raises(RuntimeError):
        await owner.start(expires_at=deadline())


@pytest.mark.parametrize("operation", ["claim", "progress"])
async def test_start_uses_existing_recovery_loop_within_its_original_deadline(operation):
    db = Persistence()
    value = None if operation == "claim" else await db.progress()
    transient = AsyncMock(side_effect=[TimeoutError(), value])
    if operation == "claim":
        db.claim = transient
    else:
        db.progress = transient
    owner = worker(db)
    expires = deadline()
    try:
        await owner.start(expires_at=expires)
        assert transient.await_count == 2
        assert owner.worker_health.state is WorkerState.READY
        assert all(call.kwargs["expires_at"] <= expires for call in transient.await_args_list)
    finally:
        assert await owner.close(expires_at=deadline())


async def test_persistent_startup_failure_cannot_report_ready_or_extend_the_deadline():
    db = Persistence()
    db.claim = AsyncMock(side_effect=TimeoutError())
    owner = worker(db)
    with pytest.raises(TimeoutError, match="startup deadline"):
        await owner.start(expires_at=deadline(0.1))
    assert not owner._started.is_set()
    assert owner.task.done()
    assert owner.worker_health.state is WorkerState.FAILED
    assert not await owner.close(expires_at=deadline())


async def test_secondary_failure_does_not_replace_the_progress_owners_observation():
    db = Persistence()
    health = ReadModelHealth(7)
    health.observe(await db.progress(), observed_at=health._clock())
    entered = asyncio.Event()

    async def claim_once(**kwargs):
        if not entered.is_set():
            entered.set()
            raise TimeoutError()
        return None

    db.claim = claim_once
    owner = ReadModelProcessingWorker(
        db,
        ReadModelWorkerConfig(generation=7, worker_id="secondary"),
        progress_health=health,
        observe_progress=False,
    )
    starting = asyncio.create_task(owner.start(expires_at=deadline()))
    try:
        await entered.wait()
        assert not owner._started.is_set()
        assert owner.worker_health.state is WorkerState.DEGRADED
        assert health.worker_health.state is WorkerState.READY
        owner._wake.set()
        await starting
        assert owner.worker_health.state is WorkerState.READY
        assert not any(call[0] == "progress" for call in db.calls[1:])
    finally:
        assert await owner.close(expires_at=deadline())
        if not starting.done():
            starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)


async def test_transient_commit_error_retains_the_same_small_handle_for_idempotent_retry():
    db = Persistence()
    db.value = claim()
    db.error = invalid_result()
    owner = worker(db)
    end = deadline()
    with pytest.raises(AccountingProtocolUnavailable):
        await owner.run_once(expires_at=end)
    assert owner.retained_claim == db.value and owner.retained_claim_bytes == 16_384
    db.error = None
    assert await owner.run_once(expires_at=deadline()) == 2
    assert [entry[0] for entry in db.calls] == ["claim", "materialize", "materialize"]
    assert owner.retained_claim is None and owner.retained_claim_bytes == 0
    assert db.calls[1][2]["expires_at"] == end
    assert await owner.close(expires_at=deadline())


async def test_overlap_has_no_waiter_and_cancel_does_not_evict_the_owned_page():
    db = Persistence()
    db.value = claim()
    db.resume = asyncio.Event()
    owner = worker(db)
    task = asyncio.create_task(owner.run_once(expires_at=deadline()))
    await db.entered.wait()
    with pytest.raises(CapacityGateFull):
        await owner.run_once(expires_at=deadline())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.retained_claim == db.value and owner.retained_claim_bytes == 16_384
    db.resume.set()
    assert await owner.run_once(expires_at=deadline()) == 2
    assert await owner.close(expires_at=deadline())


async def test_task_failure_stays_failed_after_close_and_never_reports_a_drained_backlog():
    db = Persistence()
    owner = worker(db)
    await owner.start(expires_at=deadline())
    owner.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner.task
    assert owner.worker_health.state is WorkerState.FAILED
    assert not await owner.close(expires_at=deadline())
    assert owner.worker_health.state is WorkerState.FAILED


async def test_expired_shutdown_deadline_still_stops_admission_and_cancels_owned_task():
    db = Persistence()
    owner = worker(db)
    await owner.start(expires_at=deadline())
    await owner.close(expires_at=asyncio.get_running_loop().time() - 1)
    assert owner._closing
    await asyncio.sleep(0)
    assert owner.task.done()
    with pytest.raises(RuntimeError):
        await owner.run_once(expires_at=deadline())


async def test_initialize_checks_concurrent_cells_once_with_a_new_snapshot():
    class ConcurrentCells(Database):
        async def query_raw(self, query, *parameters):
            self.rows = [
                {"generation": 7, "partition_count": 4, "slots": 0 if not self.calls else 4}
            ]
            return await super().query_raw(query, *parameters)

    db = ConcurrentCells([])
    end = deadline()
    await AccountingReadModelRepository(db).initialize(generation=7, expires_at=end)
    assert len(db.calls) == 2
    assert db.calls[0][1] == db.calls[1][1]
    assert "INSERT" in db.calls[0][0] and "INSERT" not in db.calls[1][0]


@pytest.mark.parametrize("recovered", [False, True])
async def test_lost_claim_reply_recovers_only_the_original_generation_worker_and_fence(recovered):
    db = Database(
        [claim_row()]
        if recovered
        else [
            claim_row(
                accounting_partition=None,
                last_sequence=None,
                sequences=None,
                source_bytes=None,
            )
        ]
    )
    db.error = TimeoutError()
    repo = AccountingReadModelRepository(db)
    if recovered:
        value = await repo.claim(
            generation=7,
            worker_id="report-test",
            limit=2,
            lease_seconds=30,
            expires_at=deadline(),
        )
        assert str(value.lease_token) == db.calls[0][1][3]
    else:
        with pytest.raises(AccountingProtocolUnavailable):
            await repo.claim(
                generation=7,
                worker_id="report-test",
                limit=2,
                lease_seconds=30,
                expires_at=deadline(),
            )
    assert len(db.calls) == 2
    assert db.calls[0][1][:4] == db.calls[1][1][:4]


async def test_expired_claim_deadline_does_not_start_or_recover_a_statement():
    db = Database([claim_row()])
    with pytest.raises(AccountingProtocolUnavailable):
        await AccountingReadModelRepository(db).claim(
            generation=7,
            worker_id="report-test",
            limit=2,
            lease_seconds=30,
            expires_at=asyncio.get_running_loop().time() - 1,
        )
    assert not db.calls
