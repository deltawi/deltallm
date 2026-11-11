"""Pod pools work through a real PgBouncer in transaction mode.

CI starts PgBouncer with the aliases from ``python -m src.db.runtime.pooler_config``
and sets ``DELTALLM_TEST_PGBOUNCER_URL`` to the pooler URL template. It also
declares one alias without ``connect_query`` (``deltallm_test_raw``) to prove
that startup refuses a pooler that does not apply the deadlines.
"""

import asyncio
import os
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from src.config import DatabaseConnectionSettings
from src.database_pooler import validate_pooler_url
from src.db.runtime.accounting_pool import AccountingPostgresManager
from src.db.runtime.allocated_client import DatabaseUnavailableError
from src.db.runtime.allocation_config import DatabasePolicy
from src.db.runtime.client import PrismaClientManager

pytestmark = pytest.mark.postgres


@pytest.fixture
def pooler_url():
    url = os.getenv("DELTALLM_TEST_PGBOUNCER_URL")
    if not url:
        if os.getenv("DELTALLM_TEST_PGBOUNCER_REQUIRED"):
            pytest.fail("This CI job must provide DELTALLM_TEST_PGBOUNCER_URL")
        pytest.skip("DELTALLM_TEST_PGBOUNCER_URL is required")
    return validate_pooler_url(url)


def _settings(pooler_url, pool_size=2):
    return DatabaseConnectionSettings(
        url=os.environ["DATABASE_URL"], pool_size=pool_size, pool_timeout=1, pooler_url=pooler_url
    )


async def _connect(pooler_url, allocation, *, connections=1, background=False, queue=0.0):
    manager = PrismaClientManager()
    policy = DatabasePolicy(
        allocation,
        connections,
        0.5,
        5.0 if background else 1.0,
        1.0 if background else 0.2,
        10.0 if background else 2.0,
        queue,
    )
    await manager.connect(_settings(pooler_url), policy=policy)
    return manager


async def test_each_allocation_gets_its_alias_deadlines_through_the_pooler(pooler_url):
    managers = []
    try:
        for allocation in ("control", "telemetry_worker"):
            managers.append(await _connect(pooler_url, allocation, background=True))
        for allocation in ("foreground", "telemetry", "telemetry_settlement"):
            managers.append(await _connect(pooler_url, allocation))
        for manager in managers:
            rows = await manager.client.query_raw(
                "SELECT current_setting('statement_timeout') AS statement"
            )
            background = manager.allocation.policy.allocation in {"control", "telemetry_worker"}
            assert rows[0]["statement"] == ("5s" if background else "1s")
    finally:
        for manager in managers:
            await manager.disconnect()


async def test_every_server_connection_of_an_alias_uses_its_search_path(pooler_url):
    # Two overlapping transactions hold both server connections of the alias
    # (default_pool_size = 2 in CI). Each must use the alias search_path, not
    # the PostgreSQL default "$user", public.
    manager = await _connect(pooler_url, "control", connections=2, background=True, queue=5.0)
    both = asyncio.Barrier(2)

    async def read():
        async with manager.client.tx() as tx:
            await tx.query_raw("SELECT 1 AS started")
            await asyncio.wait_for(both.wait(), 5)
            rows = await tx.query_raw(
                "SELECT current_setting('search_path') AS path, pg_backend_pid() AS pid"
            )
            return rows[0]

    try:
        first, second = await asyncio.gather(read(), read())
        assert first["pid"] != second["pid"]
        assert first["path"] == second["path"] == "public"
    finally:
        await manager.disconnect()


async def test_statement_deadline_applies_through_the_pooler(pooler_url):
    manager = await _connect(pooler_url, "foreground")
    try:
        started = time.monotonic()
        with pytest.raises(DatabaseUnavailableError) as error:
            await manager.client.query_raw("SELECT pg_sleep(1.4)")
        # PostgreSQL cancelled it at the alias's 1 s statement_timeout (57014),
        # before the caller's own deadline (acquisition + statement = 1.5 s).
        assert error.value.__cause__ is not None
        assert "57014" in repr(error.value.__cause__) or "statement timeout" in str(
            error.value.__cause__
        )
        assert time.monotonic() - started < 1.4
        assert await manager.client.query_raw("SELECT 1 AS one") == [{"one": 1}]
    finally:
        await manager.disconnect()


async def test_transactions_and_parameters_work_in_transaction_pooling(pooler_url):
    manager = await _connect(pooler_url, "control", connections=4, background=True, queue=10.0)
    table = '"pooler_' + uuid4().hex + '"'
    client = manager.client
    try:
        await client.execute_raw(f"CREATE TABLE {table} (id text PRIMARY KEY, n int NOT NULL)")

        async def write(index):
            async with client.tx() as tx:
                await tx.execute_raw(f"INSERT INTO {table} (id, n) VALUES ($1, $2)", str(index), 1)
                await tx.execute_raw(f"UPDATE {table} SET n = n + $1 WHERE id = $2", 1, str(index))

        # Four client connections share the alias's two server connections
        # (default_pool_size = 2 in CI), and four more transactions wait for
        # a pool slot. Each transaction keeps one server connection to COMMIT.
        await asyncio.gather(*(write(index) for index in range(8)))
        with pytest.raises(RuntimeError):
            async with client.tx() as tx:
                await tx.execute_raw(f"INSERT INTO {table} (id, n) VALUES ($1, $2)", "x", 1)
                raise RuntimeError("roll back")
        # The same parameterized statement many times: no named prepared
        # statement may leak between server connections.
        for _ in range(30):
            rows = await client.query_raw(
                f"SELECT count(*)::int AS c, sum(n)::int AS s FROM {table}"
            )
            assert rows == [{"c": 8, "s": 16}]
    finally:
        await client.execute_raw(f"DROP TABLE IF EXISTS {table}")
        await manager.disconnect()


async def test_accounting_pool_uses_its_alias_without_named_statements(pooler_url):
    manager = AccountingPostgresManager()
    await manager.connect(
        _settings(pooler_url),
        pool_size=1,
        acquisition_seconds=0.5,
        statement_seconds=0.25,
        lock_seconds=0.2,
    )
    try:
        assert manager.client is not None
        rows = await manager.client.query_raw(
            "SELECT current_setting('jit') AS jit, current_setting('statement_timeout') AS t"
        )
        assert rows == [{"jit": "off", "t": "250ms"}]
        for value in range(30):
            assert await manager.client.query_raw("SELECT $1::int AS v", value) == [{"v": value}]
        assert await manager.client.query_raw("SELECT $1::jsonb AS payload", {"a": 1}) == [
            {"payload": {"a": 1}}
        ]
        # Returning a connection to the pool must not RESET ALL the shared
        # server session: the alias deadlines stay after many releases.
        for _ in range(5):
            rows = await manager.client.query_raw(
                "SELECT current_setting('jit') AS jit, current_setting('statement_timeout') AS t"
            )
            assert rows == [{"jit": "off", "t": "250ms"}]
    finally:
        await manager.disconnect()


async def test_startup_refuses_an_alias_that_does_not_set_the_deadlines(pooler_url):
    # An alias without {allocation} and without connect_query.
    template = urlunsplit(urlsplit(pooler_url)._replace(path="/deltallm_test_raw"))
    manager = PrismaClientManager()
    policy = DatabasePolicy("foreground", 1, 0.5, 1.0, 0.2, 2.0)
    with pytest.raises(RuntimeError, match="native deadlines"):
        await manager.connect(_settings(template), policy=policy)
    await manager.disconnect()
