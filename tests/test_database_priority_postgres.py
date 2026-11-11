import asyncio
from contextlib import AsyncExitStack
import os
from uuid import uuid4

import pytest

from src.database_caller import database_caller
from src.db.runtime.allocated_client import AllocatedPrisma, DatabaseOwner, DatabaseUnavailableError
from src.db.runtime.allocation_config import DatabasePolicy

pytestmark = pytest.mark.postgres


@pytest.fixture
async def batch_pool():
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provide DATABASE_URL for allocation tests")
        pytest.skip("DATABASE_URL is required")
    # A deliberately small batch worker pool: 4 connections, 8 waiting
    # positions, and the chart's 2 s capacity wait.
    policy = DatabasePolicy("control", 4, 0.2, 1.0, 0.5, 2, 2.0)
    allocation = DatabaseOwner(policy, prioritized=True)
    async with AsyncExitStack() as stack:
        client = AllocatedPrisma(
            datasource={"url": policy.connection_url(url)}, allocation=allocation
        )
        stack.push_async_callback(client.disconnect)
        stack.push_async_callback(allocation.close)
        await client.connect()
        yield client, allocation


async def test_real_burst_of_claims_and_background_work_never_refuses_paid_writes(batch_pool):
    client, allocation = batch_pool
    table = '"priority_' + uuid4().hex + '"'
    await client.execute_raw(f"CREATE TABLE {table} (id text PRIMARY KEY, kind text NOT NULL)")
    most_background = 0
    sampling = True

    async def sample() -> None:
        nonlocal most_background
        while sampling:
            most_background = max(most_background, allocation.gate.capped_active)
            await asyncio.sleep(0.005)

    async def slow(caller) -> None:
        with database_caller(caller):
            await client.query_raw("SELECT 1 FROM pg_sleep(0.05)")

    async def paid_write(caller, row_id: str) -> None:
        with database_caller(caller):
            async with client.tx() as transaction:
                await transaction.execute_raw(
                    f"INSERT INTO {table} (id, kind) VALUES ($1, $2)", row_id, caller
                )

    try:
        sampler = asyncio.create_task(sample())
        burst = [asyncio.create_task(slow("batch_claim")) for _ in range(40)]
        burst += [asyncio.create_task(slow("batch_maintenance")) for _ in range(10)]
        # The burst holds every connection and every waiting position. Then
        # as many paid writes arrive as there are waiting positions.
        await asyncio.sleep(0.02)
        paid = [
            asyncio.create_task(paid_write(caller, f"{caller}-{index}"))
            for index in range(4)
            for caller in ("batch_result_save", "batch_checkpoint")
        ]
        paid_results = await asyncio.gather(*paid, return_exceptions=True)
        burst_results = await asyncio.gather(*burst, return_exceptions=True)
        sampling = False
        await sampler

        assert paid_results == [None] * 8
        rows = await client.query_raw(f"SELECT kind, count(*)::int AS n FROM {table} GROUP BY kind")
        assert {row["kind"]: row["n"] for row in rows} == {
            "batch_result_save": 4,
            "batch_checkpoint": 4,
        }
        refused = [result for result in burst_results if result is not None]
        assert refused and all(isinstance(result, DatabaseUnavailableError) for result in refused)
        # Background work never held more than its capped share of the pool.
        assert most_background <= 1
        assert allocation.gate.active == 0 and allocation.gate.waiters == 0
    finally:
        await client.execute_raw(f"DROP TABLE IF EXISTS {table}")
