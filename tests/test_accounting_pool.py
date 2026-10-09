from __future__ import annotations

import pytest

from src.config import DatabaseConnectionSettings
from src.db.accounting_pool import (
    AccountingPostgresClient,
    AccountingPostgresManager,
    _accounting_schema,
    accounting_postgres_dsn,
)
from src.db.telemetry_acceptance import (
    AcceptanceFailure,
    DatabasePoolAcquisitionTimeout,
    classify_acceptance_failure,
)


class FakeConnection:
    def __init__(self) -> None:
        self.calls = []

    async def fetch(self, query, *parameters, timeout):  # noqa: ANN001
        self.calls.append((query, parameters, timeout))
        return [{"operation_id": "operation-1"}]


class FakePool:
    def __init__(self, connection=None, *, acquire_error=None) -> None:  # noqa: ANN001
        self.connection = connection or FakeConnection()
        self.acquire_error = acquire_error
        self.released = []

    async def acquire(self, *, timeout):  # noqa: ANN001
        if self.acquire_error is not None:
            raise self.acquire_error
        return self.connection

    async def release(self, connection, *, timeout):  # noqa: ANN001
        self.released.append((connection, timeout))


def test_accounting_postgres_dsn_removes_prisma_pool_options_only():
    dsn = accounting_postgres_dsn(
        "postgresql://user:secret@db:5432/app?sslmode=require&schema=tenant"
        "&connection_limit=5&pool_timeout=2&socket_timeout=3&application_name=old"
    )

    assert dsn == "postgresql://user:secret@db:5432/app?sslmode=require"
    assert (
        _accounting_schema("postgresql://user:secret@db:5432/app?schema=tenant%22name")
        == '"tenant""name"'
    )


async def test_accounting_client_uses_bounded_pool_and_statement_deadlines():
    pool = FakePool()
    client = AccountingPostgresClient(
        pool,
        acquisition_seconds=0.2,
        statement_seconds=0.25,
    )

    rows = await client.query_raw("SELECT $1::text AS operation_id", "operation-1")

    assert rows == [{"operation_id": "operation-1"}]
    assert pool.connection.calls == [("SELECT $1::text AS operation_id", ("operation-1",), 0.25)]
    assert pool.released == [(pool.connection, 0.25)]


async def test_accounting_client_classifies_pool_acquisition_timeout():
    client = AccountingPostgresClient(
        FakePool(acquire_error=TimeoutError()),
        acquisition_seconds=0.2,
        statement_seconds=0.25,
    )

    with pytest.raises(DatabasePoolAcquisitionTimeout) as failure:
        await client.query_raw("SELECT 1")

    assert classify_acceptance_failure(failure.value) is AcceptanceFailure.POOL_TIMEOUT


async def test_accounting_pool_must_leave_a_telemetry_connection():
    manager = AccountingPostgresManager()
    settings = DatabaseConnectionSettings(
        url="postgresql://user:secret@db:5432/app",
        pool_size=2,
        pool_timeout=1,
    )

    with pytest.raises(RuntimeError, match="leave at least one telemetry connection"):
        await manager.connect(
            settings,
            pool_size=2,
            acquisition_seconds=0.2,
            statement_seconds=0.25,
            lock_seconds=0.2,
        )
