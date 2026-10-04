"""Native reserved-slot, receipt fencing and interruption recovery contracts."""

import asyncio
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import pytest
from prisma import Prisma
from prisma.errors import RawQueryError

from scripts.benchmarks.ingestion_database import ingestion_database
from src.billing.operation_reservation import BillingOperationUnavailable
from src.billing.spend_operations import (
    OperationAttempt,
    OperationHandle,
    OperationPrincipal,
    SpendOperationIntent,
)
from src.db.spend_ingestion import SpendIngestionRepository
from src.db.spend_operations import SpendOperationRepository

pytestmark = pytest.mark.postgres


@pytest.fixture
async def dbs():
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provision PostgreSQL")
        pytest.skip("DATABASE_URL required")
    async with ingestion_database(url) as db:
        await db.observer.execute_raw(
            "CREATE TABLE deltallm_spendlog_events (LIKE public.deltallm_spendlog_events INCLUDING ALL)"
        )
        # Warm native connections outside the operation deadline so this fixture
        # tests cancellation/locking rather than cold transport establishment.
        await db.acceptance.query_raw("SELECT 1")
        await db.worker.query_raw("SELECT 1")
        yield db


def handle():
    return OperationHandle(
        event_id=uuid4(),
        owner_token=uuid4(),
        intent=SpendOperationIntent(
            principal=OperationPrincipal(api_key="hashed-test-key", organization_id="tenant-one"),
            model="model-one",
            call_type="completion",
            started_at=datetime.now(UTC),
            attempts=(
                OperationAttempt(
                    deployment_id="dep-one",
                    provider="openai",
                    model="model-one",
                    pricing={"currency": "USD"},
                ),
            ),
        ),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )


def deadline():
    return asyncio.get_running_loop().time() + 0.25


async def begin(repo, operation, capacity=1):
    await repo.begin(operation, capacity=capacity, max_attempts=2, expires_at=deadline())


def payload(operation):
    return {
        **operation.intent.principal.model_dump(),
        "model": operation.intent.model,
        "call_type": operation.intent.call_type,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "cost_exact": "0.100000000000000000",
        "spend_event_version": 2,
        "start_time": operation.intent.started_at.isoformat(),
        "end_time": operation.intent.started_at.isoformat(),
    }


async def accept(repo, operation, data=None):
    await repo.accept(
        event_id=str(operation.event_id),
        owner_token=str(operation.owner_token),
        payload=payload(operation) if data is None else data,
        expires_at=deadline(),
    )


async def row(db, operation):
    return (
        await db.query_raw(
            "SELECT * FROM deltallm_spend_ingestion_outbox WHERE event_id=$1",
            str(operation.event_id),
        )
    )[0]


async def test_reserved_slot_settles_at_full_capacity_without_admission_lock(dbs):
    admission = SpendOperationRepository(dbs.acceptance)
    settlement = SpendOperationRepository(dbs.worker)
    operation = handle()
    await begin(admission, operation)
    with pytest.raises(BillingOperationUnavailable):
        await begin(admission, handle())
    async with dbs.observer.tx() as lock:
        await SpendIngestionRepository(lock)._lock_enqueue_admission()
        await lock.query_raw(
            "SELECT 1 FROM deltallm_telemetry_ingestion_capacity WHERE queue_name='spend' FOR UPDATE"
        )
        await accept(settlement, operation)
    state = await row(dbs.observer, operation)
    assert state["operation_state"] == "accepted" and state["status"] == "queued"
    assert await SpendIngestionRepository(dbs.acceptance).reconcile_capacity() == 1


async def test_duplicate_dispatch_never_reexecutes_and_receipt_replay_is_immutable(dbs):
    repo = SpendOperationRepository(dbs.acceptance)
    operation = handle()
    await begin(repo, operation)
    with pytest.raises(BillingOperationUnavailable):
        await begin(repo, operation)
    await accept(repo, operation)
    await dbs.observer.execute_raw(
        "UPDATE deltallm_spend_ingestion_outbox SET status='blocked',blocked_at=NOW(),"
        "last_error='worker_validation_failed' WHERE event_id=$1",
        str(operation.event_id),
    )
    blocked = await row(dbs.observer, operation)
    await accept(repo, operation)
    replayed = await row(dbs.observer, operation)
    assert replayed["status"] == "blocked"
    assert replayed["blocked_at"] == blocked["blocked_at"]
    assert replayed["last_error"] == "worker_validation_failed"
    for invalid in (
        {**payload(operation), "cost_exact": "2"},
        {**payload(operation), "organization_id": "other"},
    ):
        with pytest.raises(BillingOperationUnavailable):
            await accept(repo, operation, invalid)
    with pytest.raises(BillingOperationUnavailable):
        await accept(repo, operation.model_copy(update={"owner_token": uuid4()}))
    assert await SpendIngestionRepository(dbs.acceptance).reconcile_capacity() == 1


async def test_expired_intent_remains_counted_unknown_and_accepts_late_owner_receipt(dbs):
    repo = SpendOperationRepository(dbs.acceptance)
    operation = handle()
    await begin(repo, operation)
    await dbs.observer.execute_raw(
        "UPDATE deltallm_spend_ingestion_outbox SET operation_expires_at=NOW()-interval '1 second' WHERE event_id=$1",
        str(operation.event_id),
    )
    assert await repo.recover_expired() == 1
    assert await repo.recover_expired() == 0
    assert (await row(dbs.observer, operation))["operation_state"] == "unknown"
    queue = SpendIngestionRepository(dbs.worker)
    assert (
        await queue.claim_batch(limit=10, worker_id="test", claim_token="test", lease_seconds=1)
        == []
    )
    assert await queue.cleanup_terminal(completed_retention_hours=0, limit=100) == 0
    assert await queue.reconcile_capacity() == 1
    assert (
        await queue.replay_blocked(event_id=str(operation.event_id), replayed_by="operator")
        is False
    )
    with pytest.raises(RawQueryError):
        await dbs.observer.execute_raw(
            "UPDATE deltallm_spend_ingestion_outbox SET status='retry' WHERE event_id=$1",
            str(operation.event_id),
        )
    await accept(repo, operation)
    assert (await row(dbs.observer, operation))["status"] == "queued"


async def test_next_attempt_appends_frozen_snapshot_and_cannot_rewrite_attribution(dbs):
    repo = SpendOperationRepository(dbs.acceptance)
    operation = handle()
    await begin(repo, operation)
    attempt = operation.intent.attempts[0].model_copy(update={"deployment_id": "dep-two"})
    next_handle = operation.model_copy(
        update={
            "intent": operation.intent.model_copy(
                update={"attempts": (*operation.intent.attempts, attempt)}
            )
        }
    )
    await begin(repo, next_handle)
    assert len((await row(dbs.observer, operation))["operation_intent"]["attempts"]) == 2
    bad = next_handle.model_copy(
        update={
            "intent": next_handle.intent.model_copy(
                update={"model": "other-model", "attempts": (*next_handle.intent.attempts, attempt)}
            )
        }
    )
    with pytest.raises(BillingOperationUnavailable):
        await begin(repo, bad)


async def test_cancelled_admission_rolls_back_and_keeps_capacity(dbs):
    repo = SpendOperationRepository(dbs.acceptance)
    operation = handle()
    entered = asyncio.Event()
    original = repo.transactions._transaction
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def held(expires_at):
        async with original(expires_at) as tx:
            yield tx
            entered.set()
            await asyncio.Event().wait()

    repo.transactions._transaction = held
    task = asyncio.create_task(begin(repo, operation))
    try:
        ready = asyncio.create_task(entered.wait())
        try:
            done, _ = await asyncio.wait(
                (task, ready), timeout=1, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                await task
            assert ready in done
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert await SpendIngestionRepository(dbs.worker).reconcile_capacity() == 0


async def test_independent_replicas_cannot_over_admit_shared_capacity(dbs):
    owners = [SpendOperationRepository(dbs.acceptance), SpendOperationRepository(dbs.worker)]
    limit = asyncio.Semaphore(4)
    operations = [handle() for _ in range(12)]

    async def submit(index, operation):
        async with limit:
            try:
                await begin(owners[index % 2], operation, capacity=3)
                return True
            except BillingOperationUnavailable:
                return False

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(submit(i, operation)) for i, operation in enumerate(operations)]
    accepted = sum(task.result() for task in tasks)
    assert accepted == 3
    assert await SpendIngestionRepository(dbs.observer).reconcile_capacity() == 3


async def test_dependency_budget_and_indexed_plans_at_representative_cardinality(tmp_path):
    from argparse import Namespace
    from tests.performance.measure_spend_operation_sql import measure

    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provision PostgreSQL")
        pytest.skip("DATABASE_URL required")
    await measure(Namespace(output=tmp_path / "plans.json"), database_url=url)


@pytest.mark.parametrize("cancel", [False, True])
async def test_committed_receipt_survives_lost_ack_and_retains_its_native_slot(
    dbs, monkeypatch, cancel
):
    operation = handle()
    await begin(SpendOperationRepository(dbs.acceptance), operation)
    repository = SpendOperationRepository(dbs.worker)
    entered, release = asyncio.Event(), asyncio.Event()
    execute = Prisma._execute
    intercepted = False

    async def hold_ack(self, **kwargs):
        nonlocal intercepted
        result = await execute(self, **kwargs)
        query = kwargs.get("arguments", {}).get("query", "")
        if (
            self is dbs.worker
            and query.startswith("UPDATE deltallm_spend_ingestion_outbox")
            and not intercepted
        ):
            intercepted = True
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(Prisma, "_execute", hold_ack)
    caller = asyncio.create_task(accept(repository, operation))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        # The returned SQL result already represents an atomic commit, even
        # though its acknowledgement to the request is delayed or lost.
        assert (await row(dbs.observer, operation))["operation_state"] == "accepted"
        if cancel:
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
        else:
            with pytest.raises(BillingOperationUnavailable):
                await caller
        assert dbs.worker.allocation.gate.active == 1
        assert any(not task.done() for task in dbs.worker.allocation.tasks)
    finally:
        release.set()
        await asyncio.gather(caller, *tuple(dbs.worker.allocation.tasks), return_exceptions=True)
    assert dbs.worker.allocation.gate.active == 0
    await accept(repository, operation)
    assert await SpendIngestionRepository(dbs.observer).reconcile_capacity() == 1
    state = await row(dbs.observer, operation)
    assert state["operation_state"] == "accepted" and state["status"] == "queued"


async def test_concurrent_different_receipts_cannot_rewrite_the_committed_value(dbs):
    operation = handle()
    await begin(SpendOperationRepository(dbs.acceptance), operation)
    values = [payload(operation), {**payload(operation), "cost_exact": "0.200000000000000000"}]
    repositories = [SpendOperationRepository(dbs.acceptance), SpendOperationRepository(dbs.worker)]
    results = await asyncio.gather(
        *(accept(repo, operation, data) for repo, data in zip(repositories, values)),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, BillingOperationUnavailable) for result in results) == 1
    committed = (await row(dbs.observer, operation))["payload_json"]
    assert committed == values[results.index(None)]
    assert await SpendIngestionRepository(dbs.observer).reconcile_capacity() == 1


@pytest.mark.parametrize("expires_at", [0.0, float("nan"), float("inf")])
async def test_invalid_receipt_deadline_cannot_change_the_reserved_intent(dbs, expires_at):
    operation = handle()
    repository = SpendOperationRepository(dbs.acceptance)
    await begin(repository, operation)
    with pytest.raises(BillingOperationUnavailable):
        await repository.accept(
            event_id=str(operation.event_id),
            owner_token=str(operation.owner_token),
            payload=payload(operation),
            expires_at=expires_at,
        )
    assert (await row(dbs.observer, operation))["operation_state"] == "dispatched"
