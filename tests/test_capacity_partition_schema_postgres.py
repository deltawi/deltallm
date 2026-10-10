"""Database-enforced bounds for the inactive partition preparation migration."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from prisma import Prisma
from prisma.errors import RawQueryError, UniqueViolationError
import pytest

from scripts.benchmarks.ingestion_database import schema_url

pytestmark = pytest.mark.postgres
MIGRATION = Path(__file__).resolve().parents[1] / (
    "prisma/migrations/20260914100000_telemetry_capacity_partition_preparation/migration.sql"
)


@pytest.fixture
async def partition_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provision PostgreSQL")
        pytest.skip("DATABASE_URL is required")
    schema = "partition_" + uuid4().hex
    admin = Prisma(datasource={"url": schema_url(url, "public")})
    clients = [Prisma(datasource={"url": schema_url(url, schema)}) for _ in range(2)]
    await admin.connect()
    try:
        await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
        try:
            for client in clients:
                await client.connect()
            # This migration contains only two plain DDL statements. Execute
            # the real migration in an isolated namespace, including its FKs.
            for statement in MIGRATION.read_text().split(";"):
                if statement.strip():
                    await clients[0].execute_raw(statement)
            yield clients
        finally:
            for client in clients:
                if client.is_connected():
                    await client.disconnect()
            await admin.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        await admin.disconnect()


async def layout(db, capacity=17, reserve=4, count=3, *, queue="audit"):
    await db.execute_raw(
        """INSERT INTO deltallm_telemetry_capacity_layout
           (queue_name, epoch, capacity, required_reserve, partition_count)
           VALUES ($1, 1, $2, $3, $4)""",
        queue,
        capacity,
        reserve,
        count,
    )


async def partition(
    db, index, capacity=17, reserve=4, count=3, *, epoch=1, quota=None, ceiling=None, queue="audit"
):
    await db.execute_raw(
        """INSERT INTO deltallm_telemetry_capacity_partition
           (queue_name, epoch, partition_id, capacity, required_reserve,
            partition_count, quota, best_effort_ceiling)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        queue,
        epoch,
        index,
        capacity,
        reserve,
        count,
        capacity // count + (index < capacity % count) if quota is None else quota,
        (capacity - reserve) // count + (index < (capacity - reserve) % count)
        if ceiling is None
        else ceiling,
    )


@pytest.mark.parametrize(
    "capacity,reserve,count",
    [
        (1, 0, 1),
        (1, 1, 1),
        (17, 4, 3),
        (17, 17, 3),
        (10000, 9999, 64),
        (9223372036854775807, 1, 64),
    ],
)
async def test_complete_layout_exactly_partitions_capacity_and_reserve(
    partition_db, capacity, reserve, count
):
    db = partition_db[0]
    await layout(db, capacity, reserve, count)
    for index in range(count):
        await partition(db, index, capacity, reserve, count)
    (row,) = await db.query_raw("""
        SELECT SUM(quota)::text AS total, SUM(best_effort_ceiling)::text AS optional,
               SUM(pending_count)::text AS pending
        FROM deltallm_telemetry_capacity_partition
    """)
    assert (int(row["total"]), int(row["optional"]), int(row["pending"])) == (
        capacity,
        capacity - reserve,
        0,
    )


@pytest.mark.parametrize(
    "capacity,reserve,count,queue",
    [
        (0, 0, 1, "audit"),
        (8, -1, 2, "audit"),
        (8, 9, 2, "audit"),
        (8, 0, 0, "audit"),
        (100, 0, 65, "audit"),
        (8, 0, 9, "audit"),
        (8, 1, 2, "spend"),
        (8, 0, 2, "other"),
    ],
)
async def test_invalid_layout_is_rejected(partition_db, capacity, reserve, count, queue):
    with pytest.raises(RawQueryError):
        await layout(partition_db[0], capacity, reserve, count, queue=queue)


@pytest.mark.parametrize(
    "overrides",
    [
        {"epoch": 2},
        {"capacity": 18},
        {"reserve": 3},
        {"count": 4},
        {"quota": 7},
        {"ceiling": 6},
        {"index": -1},
        {"index": 3},
    ],
)
async def test_partition_cannot_forge_quota_or_layout(partition_db, overrides):
    await layout(partition_db[0])
    with pytest.raises(RawQueryError):
        await partition(partition_db[0], **{"index": 0, **overrides})


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE deltallm_telemetry_capacity_layout SET state='active'",
        "UPDATE deltallm_telemetry_capacity_layout SET protocol_version=1",
        "UPDATE deltallm_telemetry_capacity_layout SET epoch=0",
        "UPDATE deltallm_telemetry_capacity_layout SET epoch=2",
        "UPDATE deltallm_telemetry_capacity_layout SET capacity=18",
        "UPDATE deltallm_telemetry_capacity_partition SET pending_count=1",
        "UPDATE deltallm_telemetry_capacity_partition SET pending_count=-1",
        "DELETE FROM deltallm_telemetry_capacity_layout",
    ],
)
async def test_preparation_cannot_activate_or_reassign_existing_partitions(partition_db, sql):
    await layout(partition_db[0])
    await partition(partition_db[0], 0)
    with pytest.raises(RawQueryError):
        await partition_db[0].execute_raw(sql)


async def test_concurrent_duplicate_grants_cannot_exceed_global_bound(partition_db):
    await layout(partition_db[0])
    gate = asyncio.Semaphore(4)

    async def grant(index):
        async with gate:
            try:
                await partition(partition_db[index % 2], index % 3)
                return True
            except (RawQueryError, UniqueViolationError) as exc:
                # Only a uniqueness conflict is expected; do not hide connection
                # failures, deadlocks or a broken quota formula as deduplication.
                assert isinstance(exc, RawQueryError) and exc.meta["code"] == "23505"
                return False

    outcomes = await asyncio.gather(*(grant(index) for index in range(32)))
    assert sum(outcomes) == 3
    (row,) = await partition_db[0].query_raw("""
        SELECT SUM(quota)::int AS total, SUM(best_effort_ceiling)::int AS optional
        FROM deltallm_telemetry_capacity_partition
    """)
    assert row == {"total": 17, "optional": 13}
    with pytest.raises((RawQueryError, UniqueViolationError)):
        await layout(partition_db[1])


async def test_spend_and_audit_layouts_are_independent_and_recreation_is_atomic(partition_db):
    db = partition_db[0]
    await layout(db)
    await layout(db, reserve=0, queue="spend")
    await partition(db, 0)
    await partition(db, 0, reserve=0, queue="spend")
    with pytest.raises(RuntimeError, match="interrupt"):
        async with db.tx() as tx:
            await tx.execute_raw(
                "DELETE FROM deltallm_telemetry_capacity_partition WHERE queue_name='audit'"
            )
            await tx.execute_raw(
                "DELETE FROM deltallm_telemetry_capacity_layout WHERE queue_name='audit'"
            )
            raise RuntimeError("interrupt")
    rows = await db.query_raw(
        "SELECT queue_name FROM deltallm_telemetry_capacity_partition ORDER BY queue_name"
    )
    assert rows == [{"queue_name": "audit"}, {"queue_name": "spend"}]
