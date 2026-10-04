import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from prisma.errors import RawQueryError

from src.db.allocated_client import AllocatedPrisma, DatabaseOwner, DatabaseUnavailableError
from src.db.allocation_config import DatabasePolicy
from src.db.repositories import KeyRepository
from src.models.errors import AuthenticationError, AuthenticationUnavailableError
from src.services.key_service import KeyService

pytestmark = pytest.mark.postgres


@pytest.fixture
async def allocated_databases():
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provide DATABASE_URL for allocation tests")
        pytest.skip("DATABASE_URL is required")
    async with AsyncExitStack() as stack:
        clients = {}
        for name in ("control", "foreground", "telemetry", "telemetry_worker"):
            policy = DatabasePolicy(name, 1, 0.2, 0.8, 0.1, 2)
            allocation = DatabaseOwner(policy)
            client = AllocatedPrisma(
                datasource={"url": policy.connection_url(url)}, allocation=allocation
            )
            stack.push_async_callback(client.disconnect)
            stack.push_async_callback(allocation.close)
            await client.connect()
            clients[name] = client
        yield clients


async def test_real_reporting_and_consumer_pressure_preserves_auth_and_durable_write(
    allocated_databases,
):
    clients = allocated_databases
    control, worker, auth, acceptance = (
        clients[name] for name in ("control", "telemetry_worker", "foreground", "telemetry")
    )
    table = '"allocation_' + uuid4().hex + '"'
    await control.execute_raw(f"CREATE TABLE {table} (event_id text PRIMARY KEY)")
    try:
        async with control.tx() as reporting, worker.tx() as consumer:
            await reporting.query_raw("SELECT 1")
            await consumer.query_raw("SELECT 1")
            for blocked in (control, worker):
                results = await asyncio.gather(
                    *(blocked.query_raw("SELECT 1") for _ in range(50)), return_exceptions=True
                )
                assert all(isinstance(result, DatabaseUnavailableError) for result in results)
                assert blocked.allocation.gate.waiters == 0
            # Exercise the actual auth repository while background pools are full.
            with pytest.raises(AuthenticationError):
                await KeyService(KeyRepository(auth), salt="allocation-test").validate_key(
                    uuid4().hex
                )
            await acceptance.execute_raw(f"INSERT INTO {table} (event_id) VALUES ($1)", "accepted")
            assert await auth.query_raw(f"SELECT event_id FROM {table}") == [
                {"event_id": "accepted"}
            ]
        assert await control.query_raw(f"SELECT COUNT(*)::int AS count FROM {table}") == [
            {"count": 1}
        ]
    finally:
        await control.execute_raw(f"DROP TABLE {table}")


async def test_real_native_statement_deadline_and_connection_recovery(allocated_databases):
    client = allocated_databases["foreground"]
    assert (await client.query_raw("SHOW statement_timeout"))[0]["statement_timeout"] == "800ms"
    assert (await client.query_raw("SHOW lock_timeout"))[0]["lock_timeout"] == "100ms"
    with pytest.raises(DatabaseUnavailableError) as failure:
        await client.query_raw("SELECT 1 FROM pg_sleep(5)")
    assert isinstance(failure.value.__cause__, RawQueryError)
    assert "statement timeout" in str(failure.value.__cause__)
    assert await client.query_raw("SELECT 1 AS alive") == [{"alive": 1}]
    assert client.allocation.gate.active == 0


async def test_real_native_lock_deadline_rolls_back_and_does_not_authorize_on_exhaustion(
    allocated_databases,
):
    holder, auth = allocated_databases["control"], allocated_databases["foreground"]
    key = uuid4().int % (2**63 - 1)
    async with holder.tx() as holding:
        await holding.query_raw("SELECT 1 FROM pg_advisory_xact_lock($1)", key)
        with pytest.raises(DatabaseUnavailableError) as failure:
            async with auth.tx() as transaction:
                await transaction.query_raw("SELECT 1 FROM pg_advisory_xact_lock($1)", key)
        assert isinstance(failure.value.__cause__, RawQueryError)
        assert "lock timeout" in str(failure.value.__cause__)
        assert auth.allocation.gate.active == 0
    async with auth.tx() as transaction:
        await transaction.query_raw("SELECT 1 FROM pg_advisory_xact_lock($1)", key)
        with pytest.raises(AuthenticationUnavailableError):
            await KeyService(KeyRepository(auth)).validate_key(uuid4().hex)
    assert await auth.query_raw("SELECT 1 AS alive") == [{"alive": 1}]


async def test_real_native_transaction_expiry_releases_connection(allocated_databases):
    client = allocated_databases["telemetry"]
    with pytest.raises(Exception) as failure:
        async with client.tx(timeout=timedelta(milliseconds=80)) as transaction:
            # Explicitly cross the shorter native transaction budget. No row or
            # connection may remain held after the context handles that failure.
            await transaction.query_raw("SELECT 1 FROM pg_sleep(0.15)")
            await transaction.query_raw("SELECT 1")
    assert isinstance(failure.value, (DatabaseUnavailableError, TimeoutError))
    await client.allocation.close()
    assert client.allocation.gate.active == 0


async def test_real_cancelled_query_keeps_capacity_until_native_statement_finishes(
    allocated_databases,
):
    client = allocated_databases["telemetry"]
    observer = allocated_databases["control"]
    caller = asyncio.create_task(
        client.query_raw("SELECT 1 FROM pg_sleep(0.5) /* allocation cancellation */")
    )
    try:
        async with asyncio.timeout(1):
            while True:
                rows = await observer.query_raw(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name = 'deltallm_telemetry' AND wait_event = 'PgSleep') AS waiting"
                )
                if rows[0]["waiting"]:
                    break
                await asyncio.sleep(0.005)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert client.allocation.gate.active == 1
        with pytest.raises(DatabaseUnavailableError):
            await client.query_raw("SELECT 1")
        await asyncio.gather(*tuple(client.allocation.tasks), return_exceptions=True)
        assert client.allocation.gate.active == 0
        assert await client.query_raw("SELECT 1 AS alive") == [{"alive": 1}]
    finally:
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)


async def test_bootstrap_manager_verifies_native_deadlines_on_live_server(allocated_databases):
    from src.config import DatabaseConnectionSettings
    from src.db.client import PrismaClientManager

    manager = PrismaClientManager()
    policy = DatabasePolicy("control", 1, 0.2, 1.3, 0.15, 2.5)
    try:
        await manager.connect(
            DatabaseConnectionSettings(url=os.environ["DATABASE_URL"], pool_size=1, pool_timeout=1),
            policy=policy,
        )
        assert await manager.client.query_raw("SELECT 1 AS alive") == [{"alive": 1}]
    finally:
        await manager.disconnect()
    assert manager.allocation.gate.active == 0
