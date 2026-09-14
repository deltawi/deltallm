"""Admission invariants through the production allocated Prisma clients."""

import asyncio
from contextlib import asynccontextmanager
import os

from prisma.errors import RawQueryError
import pytest

from scripts.benchmarks.ingestion_database import (
    AUDIT,
    CAPACITY,
    ORGANIZATION,
    SPEND,
    ingestion_database,
)
from src.db.allocated_client import DatabaseUnavailableError
from src.db.audit_ingestion import AuditIngestionRepository, AuditOutboxEnvelope
from src.db.spend_ingestion import SpendIngestionRepository

pytestmark = pytest.mark.postgres


@pytest.fixture
async def queues():
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provision the migrated PostgreSQL test database")
        pytest.skip("DATABASE_URL is required")
    async with ingestion_database(url) as db:
        yield db


def envelope(event_id, organization_id=None, delivery="required"):
    return AuditOutboxEnvelope(
        event_id,
        "audit_event",
        organization_id,
        delivery,
        {"content": "private"},
        {"redacted": True},
        2,
    )


async def enqueue(db, queue, event_id, *, maximum=8, organization=None, delivery="required"):
    if queue == "audit":
        result = await AuditIngestionRepository(db).enqueue_bundle(
            envelopes=[envelope(event_id, organization, delivery)],
            max_pending_events=maximum,
            required_reserve=2,
        )
        return result.statuses[event_id]
    result = await SpendIngestionRepository(db).enqueue(
        event_id=event_id,
        event_type="spend",
        payload={"organization_id": organization},
        max_attempts=2,
        max_pending_events=maximum,
    )
    return result.status


async def capacity(db, queue):
    (row,) = await db.query_raw(
        f"SELECT pending_count, ctid::text AS tuple_id FROM {CAPACITY} WHERE queue_name=$1",
        queue,
    )
    return row


async def bounded_writers(db, queue, events, *, maximum=8, organizations=1):
    # Two independent process allocations, each with four active writers. There
    # is no hidden Prisma pool queue and no unbounded task fan-out in this test.
    gates = (asyncio.Semaphore(4), asyncio.Semaphore(4))
    clients = (db.acceptance, db.worker)

    # Establish every concurrent connection before testing the admission race.
    # connect() starts Prisma's engine but opens database connections lazily;
    # Separate cold connection setup from the bounded lock-contention race.
    # Startup/rejection behavior is measured separately by the arrival probe.
    ready = asyncio.Barrier(8)

    async def warm(client):
        async with client.tx() as tx:
            await tx.query_raw("SELECT 1")
            await ready.wait()

    async with asyncio.TaskGroup() as group:
        for client in clients:
            for _ in range(4):
                group.create_task(warm(client))

    async def submit(index, event_id):
        actor = index % 2
        async with gates[actor]:
            try:
                return await enqueue(
                    clients[actor],
                    queue,
                    event_id,
                    maximum=maximum,
                    organization=f"org-{index % organizations}",
                )
            except DatabaseUnavailableError as exc:
                # A bounded lock timeout is a valid fail-closed outcome on a
                # busy host. Only that confirmed rollback is recoverable here;
                # connection failures and ambiguous commits still fail the test.
                assert isinstance(exc.__cause__, RawQueryError)
                assert exc.__cause__.meta["code"] == "55P03"
                return "lock_timeout"

    # On an assertion/SQL failure, cancel and join siblings before fixture
    # teardown; gather's default exception path leaves submissions running.
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(submit(i, event)) for i, event in enumerate(events)]
    return [task.result() for task in tasks]


@pytest.mark.parametrize("queue", ["audit", "spend"])
@pytest.mark.parametrize("outcome", ["full", "duplicate"])
async def test_zero_delta_admission_ignores_locked_capacity_row(queues, queue, outcome):
    assert await enqueue(queues.acceptance, queue, "existing", maximum=1) == "accepted"
    before = await capacity(queues.observer, queue)
    async with queues.observer.tx() as holder:
        await holder.query_raw(f"SELECT * FROM {CAPACITY} WHERE queue_name=$1 FOR UPDATE", queue)
        assert (
            await enqueue(
                queues.acceptance,
                queue,
                "existing" if outcome == "duplicate" else "new",
                maximum=1,
            )
            == outcome
        )
    assert await capacity(queues.observer, queue) == before


@pytest.mark.parametrize("queue", ["audit", "spend"])
@pytest.mark.parametrize("organizations", [1, 13], ids=["hot", "mixed"])
async def test_concurrent_near_full_admission_never_exceeds_global_bound(
    queues, queue, organizations
):
    for i in range(5):
        assert await enqueue(queues.acceptance, queue, f"seed-{i}") == "accepted"
    events = [f"new-{i}" for i in range(32)]
    outcomes = await bounded_writers(
        queues,
        queue,
        events,
        organizations=organizations,
    )
    # Check the concurrent wave before recovery, so a later retry cannot hide
    # over-admission or a leaked debit from a rolled-back lock waiter.
    assert set(outcomes) <= {"accepted", "full", "lock_timeout"}
    assert outcomes.count("accepted") <= 3
    assert int((await capacity(queues.observer, queue))["pending_count"]) == (
        5 + outcomes.count("accepted")
    )
    for index, outcome in enumerate(outcomes):
        if outcome == "lock_timeout":
            outcomes[index] = await enqueue(
                queues.acceptance,
                queue,
                events[index],
                organization=f"org-{index % organizations}",
            )
    assert outcomes.count("accepted") == 3
    assert outcomes.count("full") == 29
    assert int((await capacity(queues.observer, queue))["pending_count"]) == 8


@pytest.mark.parametrize("queue", ["audit", "spend"])
async def test_concurrent_duplicate_retries_have_one_durable_effect(queues, queue):
    outcomes = await bounded_writers(queues, queue, ["same-event"] * 32)
    assert set(outcomes) <= {"accepted", "duplicate", "lock_timeout"}
    assert int((await capacity(queues.observer, queue))["pending_count"]) == (
        outcomes.count("accepted")
    )
    for index, outcome in enumerate(outcomes):
        if outcome == "lock_timeout":
            outcomes[index] = await enqueue(queues.acceptance, queue, "same-event")
    assert outcomes.count("accepted") == 1
    assert outcomes.count("duplicate") == 31
    assert int((await capacity(queues.observer, queue))["pending_count"]) == 1


async def test_best_effort_cannot_consume_required_reserve(queues):
    repo = AuditIngestionRepository(queues.acceptance)
    result = await repo.enqueue_bundle(
        envelopes=[envelope(f"bulk-{i}", delivery="best_effort") for i in range(8)],
        max_pending_events=8,
        required_reserve=2,
    )
    assert list(result.statuses.values()).count("accepted") == 6
    result = await repo.enqueue_bundle(
        envelopes=[envelope(f"required-{i}") for i in range(4)],
        max_pending_events=8,
        required_reserve=2,
    )
    assert list(result.statuses.values()).count("accepted") == 2
    assert result.pending_count == 8


async def wait_for_lock(observer):
    async with asyncio.timeout(2):
        while True:
            (row,) = await observer.query_raw("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_locks l JOIN pg_stat_activity a USING (pid)
                    WHERE l.locktype='advisory' AND NOT l.granted
                      AND a.application_name='deltallm_telemetry'
                ) AS waiting
            """)
            if row["waiting"]:
                return
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("queue", ["audit", "spend"])
async def test_cancelled_lock_wait_rolls_back_and_releases_allocation(queues, queue):
    task = None
    try:
        async with queues.observer.tx() as holder:
            await holder.query_raw(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))::text",
                f"deltallm:{queue}-ingestion-capacity",
            )
            task = asyncio.create_task(enqueue(queues.acceptance, queue, "cancelled"))
            await wait_for_lock(holder)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await asyncio.gather(*tuple(queues.acceptance.allocation.tasks), return_exceptions=True)
        assert queues.acceptance.allocation.gate.active == 0
        assert int((await capacity(queues.observer, queue))["pending_count"]) == 0
        assert await enqueue(queues.acceptance, queue, "cancelled") == "accepted"
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class LostCommitAcknowledgement:
    """Fault at the transport boundary after PostgreSQL actually commits."""

    def __init__(self, client):
        self.client = client

    def is_transaction(self):
        return False

    @asynccontextmanager
    async def tx(self):
        async with self.client.tx() as tx:
            yield tx
        raise DatabaseUnavailableError()


@pytest.mark.parametrize("queue", ["audit", "spend"])
async def test_lost_commit_acknowledgement_retry_preserves_event_identity(queues, queue):
    with pytest.raises(DatabaseUnavailableError):
        await enqueue(LostCommitAcknowledgement(queues.acceptance), queue, "ambiguous")
    assert await enqueue(queues.acceptance, queue, "ambiguous") == "duplicate"
    assert int((await capacity(queues.observer, queue))["pending_count"]) == 1


async def test_policy_change_is_read_after_lock_and_before_persistence(queues):
    await queues.observer.execute_raw(
        f"INSERT INTO {ORGANIZATION} (organization_id, audit_content_storage_enabled) VALUES ('org', TRUE)"
    )
    task = None
    try:
        async with queues.observer.tx() as holder:
            await AuditIngestionRepository(holder).lock_content_policy("org")
            await holder.execute_raw(
                f"UPDATE {ORGANIZATION} SET audit_content_storage_enabled=FALSE, "
                "audit_content_policy_version=9 WHERE organization_id='org'"
            )
            task = asyncio.create_task(
                enqueue(queues.acceptance, "audit", "policy", organization="org")
            )
            await wait_for_lock(holder)
        assert await task == "accepted"
        (row,) = await queues.observer.query_raw(
            f"SELECT payload_json, redacted_payload_json, policy_version FROM {AUDIT} WHERE event_id='policy'"
        )
        assert row["payload_json"] == row["redacted_payload_json"] == {"redacted": True}
        assert int(row["policy_version"]) == 9
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("queue,table", [("audit", AUDIT), ("spend", SPEND)])
async def test_stale_worker_block_and_replay_preserve_capacity_and_payload(queues, queue, table):
    await enqueue(queues.acceptance, queue, "recoverable")
    repository = (
        AuditIngestionRepository(queues.worker)
        if queue == "audit"
        else SpendIngestionRepository(queues.worker)
    )
    first = await repository.claim_batch(
        limit=1, worker_id="old", claim_token="old", lease_seconds=30
    )
    assert [row.event_id for row in first] == ["recoverable"]
    await queues.observer.execute_raw(
        f"UPDATE {table} SET lease_expires_at=NOW()-INTERVAL '1 minute' WHERE event_id='recoverable'"
    )
    second = await repository.claim_batch(
        limit=1, worker_id="new", claim_token="new", lease_seconds=30
    )
    assert [row.event_id for row in second] == ["recoverable"]
    assert (
        await repository.mark_completed(
            event_ids=["recoverable"], worker_id="old", claim_token="old"
        )
        == 0
    )
    await queues.observer.execute_raw(
        f"UPDATE {table} SET lease_expires_at=NOW()-INTERVAL '1 minute' WHERE event_id='recoverable'"
    )
    assert (
        await repository.claim_batch(
            limit=1, worker_id="last", claim_token="last", lease_seconds=30
        )
        == []
    )
    (before,) = await queues.observer.query_raw(
        f"SELECT * FROM {table} WHERE event_id='recoverable'"
    )
    assert before["status"] == "blocked"
    assert int((await capacity(queues.observer, queue))["pending_count"]) == 1
    assert await repository.replay_blocked(event_id="recoverable", replayed_by="operator")
    (after,) = await queues.observer.query_raw(
        f"SELECT * FROM {table} WHERE event_id='recoverable'"
    )
    assert after["payload_json"] == before["payload_json"]
    assert after["event_id"] == before["event_id"]
    assert after["status"] == "retry"
    assert int(after["attempt_count"]) == 0
    assert int(after["replay_count"]) == 1
    assert int((await capacity(queues.observer, queue))["pending_count"]) == 1


async def test_slow_policy_fails_closed_with_bounded_pool_and_recovers(queues):
    async with queues.observer.tx() as holder:
        await AuditIngestionRepository(holder).lock_content_policy("slow-org")
        with pytest.raises(DatabaseUnavailableError) as failure:
            await enqueue(queues.acceptance, "audit", "timed-out", organization="slow-org")
        assert "lock timeout" in str(failure.value.__cause__)
        assert queues.acceptance.allocation.gate.active == 0
        assert queues.acceptance.allocation.gate.waiters == 0
        assert int((await capacity(holder, "audit"))["pending_count"]) == 0
    assert (
        await enqueue(queues.acceptance, "audit", "timed-out", organization="slow-org")
        == "accepted"
    )


async def test_measurement_probe_preserves_policy_lock_and_atomic_acceptance(queues):
    from scripts.benchmarks.measure_admission import ProbeClient, Sample

    sample = Sample(0, 0, 0)
    async with queues.observer.tx() as holder:
        await AuditIngestionRepository(holder).lock_content_policy("measured")
        with pytest.raises(DatabaseUnavailableError) as failure:
            await enqueue(
                ProbeClient(queues.acceptance, sample), "audit", "probe", organization="measured"
            )
        assert "lock timeout" in str(failure.value.__cause__)
        assert int((await capacity(holder, "audit"))["pending_count"]) == 0
    assert (
        await enqueue(
            ProbeClient(queues.acceptance, Sample(1, 0, 0)),
            "audit",
            "probe",
            organization="measured",
        )
        == "accepted"
    )
