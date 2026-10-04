from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from prisma import Prisma
from prisma.errors import RawQueryError

from src.db.audit_ingestion import AuditIngestionRepository
from src.db.allocated_client import AllocatedPrisma, DatabaseOwner, DatabaseUnavailableError
from src.db.allocation_config import DatabasePolicy

pytestmark = pytest.mark.postgres

OUTBOX = "deltallm_audit_ingestion_outbox"
CAPACITY = "deltallm_telemetry_ingestion_capacity"


@pytest.fixture(params=["native", "allocated"])
async def isolated_audit_queue(request):
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provide DATABASE_URL for audit claim capacity tests")
        pytest.skip("DATABASE_URL is required")

    # Clone the migrated tables, including constraints/indexes, without touching
    # shared queue data. Identifiers below are generated here, never user input.
    schema = "audit_claim_" + uuid4().hex
    async with AsyncExitStack() as stack:
        owner = None
        if request.param == "allocated":
            policy = DatabasePolicy("telemetry_worker", 1, 0.2, 2, 0.3, 10)
            owner = DatabaseOwner(policy)
            db = AllocatedPrisma(datasource={"url": policy.connection_url(url)}, allocation=owner)
        else:
            db = Prisma(datasource={"url": url})
        lock_db = Prisma(datasource={"url": url})
        for client in (db, lock_db):
            stack.push_async_callback(client.disconnect)
            await client.connect()
        if owner is not None:
            stack.push_async_callback(owner.close)
        await db.execute_raw(f'CREATE SCHEMA "{schema}"')
        try:
            for table in (OUTBOX, CAPACITY):
                await db.execute_raw(
                    f'CREATE TABLE "{schema}".{table} (LIKE {table} INCLUDING ALL)'
                )
            await db.execute_raw(
                f"INSERT INTO \"{schema}\".{CAPACITY} (queue_name) VALUES ('audit')"
            )
            yield db, lock_db, schema
        finally:
            await db.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')


@asynccontextmanager
async def queue_transaction(db, schema):
    async with db.tx(timeout=timedelta(seconds=10)) as tx:
        await tx.execute_raw(f'SET LOCAL search_path TO "{schema}"')
        await tx.execute_raw("SET LOCAL lock_timeout = '300ms'")
        await tx.execute_raw("SET LOCAL statement_timeout = '2s'")
        yield tx


async def seed(tx, event_id, delivery_class, state):
    await tx.execute_raw(
        f"""
        INSERT INTO {OUTBOX} (
            event_id, record_type, delivery_class, payload_json,
            redacted_payload_json, policy_version, status, attempt_count,
            max_attempts, lease_expires_at, locked_by, claim_token, next_attempt_at
        ) VALUES (
            $1, 'audit_event', $2, '{{}}'::jsonb, '{{}}'::jsonb, 7,
            $3, $4, 1, NOW() - INTERVAL '1 minute', 'old-worker', 'old-claim', NOW() - INTERVAL '1 minute'
        )
        """,
        event_id,
        delivery_class,
        "queued" if state == "queued" else "processing",
        1 if state == "exhausted" else 0,
    )
    await tx.execute_raw(f"UPDATE {CAPACITY} SET pending_count = pending_count + 1")


async def capacity(tx):
    (row,) = await tx.query_raw(
        f"SELECT pending_count, ctid::text AS tuple_id FROM {CAPACITY} WHERE queue_name = 'audit'"
    )
    return row


async def claim(tx):
    return await AuditIngestionRepository(tx).claim_batch(
        limit=10, worker_id="new-worker", claim_token="new-claim", lease_seconds=30
    )


@pytest.mark.parametrize(
    ("delivery_class", "state", "expected_status"),
    [
        (None, None, None),
        ("best_effort", "queued", "processing"),
        ("required", "exhausted", "blocked"),
        ("required", "reclaim", "processing"),
        ("best_effort", "reclaim", "processing"),
    ],
    ids=["empty", "ordinary-claim", "required-exhausted", "required-reclaim", "bulk-reclaim"],
)
async def test_zero_delta_claim_does_not_wait_for_or_rewrite_capacity(
    isolated_audit_queue, delivery_class, state, expected_status
):
    db, lock_db, schema = isolated_audit_queue
    async with queue_transaction(db, schema) as tx:
        if delivery_class is not None:
            await seed(tx, "event", delivery_class, state)
        before = await capacity(tx)

    async with queue_transaction(lock_db, schema) as holder:
        await holder.query_raw(f"SELECT * FROM {CAPACITY} WHERE queue_name = 'audit' FOR UPDATE")
        # The native row lock remains held until claim commits. The original
        # unconditional UPDATE fails the native lock deadline in every case.
        async with queue_transaction(db, schema) as tx:
            records = await claim(tx)
            assert [record.event_id for record in records] == (
                ["event"] if expected_status == "processing" else []
            )
            assert await capacity(tx) == before
            if expected_status is not None:
                (row,) = await tx.query_raw(
                    f"SELECT status, locked_by, claim_token, blocked_at FROM {OUTBOX}"
                )
                assert row["status"] == expected_status
                if expected_status == "blocked":
                    assert row["blocked_at"] is not None
                    assert row["locked_by"] is None
                    assert row["claim_token"] is None
                else:
                    assert row["locked_by"] == "new-worker"
                    assert row["claim_token"] == "new-claim"


async def test_mixed_exhaustion_releases_only_best_effort_capacity_once(isolated_audit_queue):
    db, _, schema = isolated_audit_queue
    async with queue_transaction(db, schema) as tx:
        await seed(tx, "bulk-1", "best_effort", "exhausted")
        await seed(tx, "bulk-2", "best_effort", "exhausted")
        await seed(tx, "required", "required", "exhausted")
        await seed(tx, "queued", "required", "queued")
        before = await capacity(tx)

        assert [record.event_id for record in await claim(tx)] == ["queued"]
        after = await capacity(tx)
        assert int(before["pending_count"]) == 4
        assert int(after["pending_count"]) == 2
        assert after["tuple_id"] != before["tuple_id"]
        rows = await tx.query_raw(
            f"SELECT event_id, status, policy_version, payload_json, redacted_payload_json FROM {OUTBOX}"
        )
        assert {row["event_id"]: row["status"] for row in rows} == {
            "bulk-1": "failed",
            "bulk-2": "failed",
            "required": "blocked",
            "queued": "processing",
        }
        assert all(int(row["policy_version"]) == 7 for row in rows)
        assert all(row["payload_json"] == row["redacted_payload_json"] == {} for row in rows)

        # Polling again neither releases blocked/terminal records again nor
        # writes the singleton row with an unchanged count.
        assert await claim(tx) == []
        assert await capacity(tx) == after


async def test_actual_capacity_release_rolls_back_exhaustion_on_lock_timeout(isolated_audit_queue):
    db, lock_db, schema = isolated_audit_queue
    async with queue_transaction(db, schema) as tx:
        await seed(tx, "expired", "best_effort", "exhausted")
        before = await capacity(tx)

    async with queue_transaction(lock_db, schema) as holder:
        await holder.query_raw(f"SELECT * FROM {CAPACITY} WHERE queue_name = 'audit' FOR UPDATE")
        error_type = DatabaseUnavailableError if isinstance(db, AllocatedPrisma) else RawQueryError
        with pytest.raises(error_type) as failure:
            async with queue_transaction(db, schema) as tx:
                await claim(tx)
        native_error = failure.value.__cause__ if isinstance(db, AllocatedPrisma) else failure.value
        assert isinstance(native_error, RawQueryError)
        assert "lock timeout" in str(native_error)

    async with queue_transaction(db, schema) as tx:
        assert await capacity(tx) == before
        (row,) = await tx.query_raw(f"SELECT status, claim_token FROM {OUTBOX}")
        assert row == {"status": "processing", "claim_token": "old-claim"}
        assert await claim(tx) == []
        assert int((await capacity(tx))["pending_count"]) == 0
        (row,) = await tx.query_raw(f"SELECT status, claim_token FROM {OUTBOX}")
        assert row == {"status": "failed", "claim_token": None}
