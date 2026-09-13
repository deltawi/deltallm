import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from prisma import Prisma
from pydantic import ValidationError

from src.config import GeneralSettings, Settings
from src.db.allocated_client import (
    AllocatedPrisma,
    AllocatedTransaction,
    DatabaseOwner,
    DatabaseUnavailableError,
)
from src.db.allocation_config import DatabasePolicy, resolve_allocation_settings
from src.database_settings import DatabaseAllocationSettings
from src.config_runtime.dynamic import DynamicConfigManager, DynamicConfigRestartRequiredError

pytestmark = pytest.mark.hermetic


def owner(allocation="foreground", size=1):
    return DatabaseOwner(DatabasePolicy(allocation, size, 0.01, 1, 0.05, 2))


async def test_cancelled_query_keeps_slot_until_native_operation_finishes():
    allocation = owner()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def native():
        entered.set()
        await finish.wait()
        return "accepted"

    caller = asyncio.create_task(allocation.query(native))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert allocation.gate.active == 1
    results = await asyncio.gather(
        *(allocation.query(native) for _ in range(100)), return_exceptions=True
    )
    assert all(isinstance(result, DatabaseUnavailableError) for result in results)
    assert len(allocation.tasks) == 1
    assert allocation.gate.waiters == 0
    finish.set()
    await asyncio.gather(*tuple(allocation.tasks))
    assert allocation.gate.active == 0
    assert await allocation.query(native) == "accepted"
    await allocation.close()


async def test_control_and_consumer_saturation_does_not_use_foreground_slots():
    control, consumer, auth, acceptance = (
        owner(name) for name in ("control", "telemetry_worker", "foreground", "telemetry")
    )
    entered = [asyncio.Event(), asyncio.Event()]
    finish = asyncio.Event()

    async def blocked(index):
        entered[index].set()
        await finish.wait()

    callers = [
        asyncio.create_task(pool.query(lambda i=i: blocked(i)))
        for i, pool in enumerate((control, consumer))
    ]
    try:
        await asyncio.gather(*(event.wait() for event in entered))
        for pool in (control, consumer):
            with pytest.raises(DatabaseUnavailableError):
                await pool.query(AsyncMock())
        assert await auth.query(AsyncMock(return_value="authorized")) == "authorized"
        assert await acceptance.query(AsyncMock(return_value="durable")) == "durable"
    finally:
        finish.set()
        await asyncio.gather(*callers)
        await asyncio.gather(*(pool.close() for pool in (control, consumer, auth, acceptance)))


async def test_prisma_raw_and_model_actions_share_owner_and_copies(monkeypatch):
    allocation = owner()
    client = AllocatedPrisma(allocation=allocation)
    native = AsyncMock(return_value={"data": {}})
    monkeypatch.setattr(Prisma, "_execute", native)
    await client._execute(method="query_raw", arguments={})
    await allocation.acquire()
    try:
        with pytest.raises(DatabaseUnavailableError):
            await client.deltallm_verificationtoken.find_many(take=1)
        assert native.await_count == 1
    finally:
        await allocation.release()
        await allocation.close()
    assert client._copy().allocation is allocation


class NativeTransaction:
    def __init__(self, allocation):
        self.client = AllocatedPrisma(allocation=allocation)
        self.start = AsyncMock(return_value=self.client)
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


async def test_transaction_owns_one_slot_through_commit_and_rejects_nested_transactions(
    monkeypatch,
):
    allocation = owner()
    native = NativeTransaction(allocation)
    native.client._tx_id = "test-transaction"
    monkeypatch.setattr(Prisma, "_execute", AsyncMock(return_value={}))
    async with AllocatedTransaction(allocation, native) as tx:
        assert allocation.gate.active == 1
        await tx._execute(method="query_raw", arguments={})
        assert allocation.gate.active == 1
        with pytest.raises(RuntimeError, match="Nested"):
            tx.tx()
    native.commit.assert_awaited_once()
    assert allocation.gate.active == 0
    await allocation.close()


async def test_cancelled_transaction_start_rolls_back_before_releasing_capacity():
    allocation = owner()
    native = NativeTransaction(allocation)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def start():
        entered.set()
        await finish.wait()
        return native.client

    native.start.side_effect = start
    transaction = AllocatedTransaction(allocation, native)
    caller = asyncio.create_task(transaction.start())
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert allocation.gate.active == 1
    finish.set()
    await allocation.close()
    native.rollback.assert_awaited_once()
    native.commit.assert_not_awaited()
    assert allocation.gate.active == 0


async def test_swallowed_native_query_failure_cannot_report_successful_commit(monkeypatch):
    allocation = owner()
    native = NativeTransaction(allocation)
    monkeypatch.setattr(Prisma, "_execute", AsyncMock(side_effect=RuntimeError("statement failed")))
    with pytest.raises(DatabaseUnavailableError):
        async with AllocatedTransaction(allocation, native) as tx:
            with pytest.raises(RuntimeError, match="statement failed"):
                await tx._execute(method="query_raw", arguments={})
    native.rollback.assert_awaited_once()
    native.commit.assert_not_awaited()
    assert allocation.gate.active == 0
    await allocation.close()


async def test_native_transaction_deadlines_cannot_be_expanded_by_repository_options():
    allocation = owner()
    client = AllocatedPrisma(allocation=allocation)
    manager = client.tx(max_wait=timedelta(seconds=60), timeout=60000)
    assert manager.native._max_wait == timedelta(seconds=0.01)
    assert manager.native._timeout == timedelta(seconds=2)
    await allocation.close()


def test_native_url_keeps_credentials_tls_and_schema_but_overrides_unbounded_settings():
    policy = DatabasePolicy("telemetry", 3, 0.2, 1, 0.1, 2)
    url = policy.connection_url(
        "postgresql://user:password@db/test?schema=tenant&sslmode=require&connection_limit=999&pool_timeout=0&options=-c%20statement_timeout%3D0"
    )
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "user:password@db"
    assert query["schema"] == ["tenant"]
    assert query["sslmode"] == ["require"]
    assert query["connection_limit"] == ["3"]
    assert query["pool_timeout"] == query["connect_timeout"] == ["1"]
    assert query["options"][0].endswith(
        "-c statement_timeout=1000 -c lock_timeout=100 -c idle_in_transaction_session_timeout=2000"
    )


def test_database_allocation_precedence_and_deadline_validation():
    environment = Settings(db_foreground_pool_size=9)
    assert resolve_allocation_settings(GeneralSettings(), environment).db_foreground_pool_size == 9
    assert (
        resolve_allocation_settings(
            GeneralSettings(db_foreground_pool_size=3), environment
        ).db_foreground_pool_size
        == 3
    )
    for settings_type in (GeneralSettings, Settings):
        with pytest.raises(ValidationError, match="lock <= statement"):
            settings_type(db_lock_timeout_seconds=3)
        with pytest.raises(ValidationError):
            settings_type(db_foreground_pool_size=0)


@pytest.mark.parametrize("field", DatabaseAllocationSettings.model_fields)
async def test_database_allocation_updates_require_restart_even_when_adding_default(field):
    manager = DynamicConfigManager(None, None, {})
    await manager.initialize()
    try:
        with pytest.raises(DynamicConfigRestartRequiredError, match=field):
            await manager.update_config(
                {"general_settings": {field: getattr(DatabaseAllocationSettings(), field)}},
                updated_by="allocation-test",
            )
    finally:
        await manager.close()


@pytest.mark.parametrize(
    "code,sqlstate", [("P2024", None), ("P1001", None), ("P2010", "57014"), ("P2010", "55P03")]
)
async def test_native_availability_failures_are_sanitized_without_provider_retry(code, sqlstate):
    from prisma.errors import DataError
    from src.models.errors import RoutingFailureAction
    from src.router.health_policy import affects_deployment_health

    allocation = owner()
    native_error = DataError(
        {
            "user_facing_error": {
                "error_code": code,
                "message": "private database endpoint",
                "meta": {"code": sqlstate},
            }
        }
    )
    with pytest.raises(DatabaseUnavailableError) as failure:
        await allocation.query(AsyncMock(side_effect=native_error))
    assert failure.value.status_code == 503
    assert "private" not in str(failure.value)
    assert failure.value.__cause__ is native_error
    assert not affects_deployment_health(failure.value)
    assert failure.value.routing_failure_action == RoutingFailureAction.FAIL_FAST
    assert allocation.gate.active == 0
    await allocation.close()


@pytest.mark.parametrize(
    "code,sqlstate", [("P2002", None), ("P2025", None), ("P2010", "PBR01"), ("P2010", "23505")]
)
async def test_record_errors_preserve_repository_conflict_and_isolation_semantics(code, sqlstate):
    from prisma.errors import DataError

    allocation = owner()
    native_error = DataError(
        {"user_facing_error": {"error_code": code, "meta": {"code": sqlstate}}}
    )
    with pytest.raises(DataError) as failure:
        await allocation.query(AsyncMock(side_effect=native_error))
    assert failure.value is native_error
    assert allocation.gate.active == 0
    await allocation.close()


async def test_transaction_cannot_spawn_duplicate_or_unstarted_finish_tasks():
    allocation = owner()
    native = NativeTransaction(allocation)
    transaction = AllocatedTransaction(allocation, native)
    with pytest.raises(RuntimeError, match="not open"):
        await transaction.commit()
    assert not allocation.tasks
    await transaction.start()
    await transaction.commit()
    with pytest.raises(RuntimeError, match="not open"):
        await transaction.rollback()
    assert allocation.gate.active == 0
    await allocation.close()


async def test_deadline_during_commit_keeps_slot_until_acknowledgement():
    allocation = owner()
    native = NativeTransaction(allocation)
    commit_entered, finish = asyncio.Event(), asyncio.Event()

    async def commit():
        commit_entered.set()
        await finish.wait()

    native.commit.side_effect = commit
    with pytest.raises(DatabaseUnavailableError):
        async with AllocatedTransaction(allocation, native, transaction_seconds=0.02):
            pass
    assert commit_entered.is_set()
    assert allocation.gate.active == 1
    with pytest.raises(DatabaseUnavailableError):
        await allocation.query(AsyncMock())
    finish.set()
    await allocation.close()
    assert allocation.gate.active == 0
    native.commit.assert_awaited_once()
    native.rollback.assert_not_awaited()


async def test_swallowed_body_deadline_cannot_commit():
    allocation = owner()
    native = NativeTransaction(allocation)
    with pytest.raises(DatabaseUnavailableError):
        async with AllocatedTransaction(allocation, native, transaction_seconds=0.02):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
    native.commit.assert_not_awaited()
    native.rollback.assert_awaited_once()
    await allocation.close()
    assert allocation.gate.active == 0


async def test_manual_transaction_expiry_releases_slot_and_disables_old_client(monkeypatch):
    monkeypatch.setattr(DatabasePolicy, "native_query_seconds", property(lambda self: 0.03))
    allocation = owner()
    native = NativeTransaction(allocation)
    transaction = AllocatedTransaction(allocation, native, transaction_seconds=0.01)
    client = await transaction.start()
    await asyncio.gather(*tuple(allocation.tasks))
    assert allocation.gate.active == 0
    with pytest.raises(DatabaseUnavailableError):
        await client._execute(method="query_raw", arguments={})
    with pytest.raises(RuntimeError, match="not open"):
        await transaction.commit()
    assert await allocation.query(AsyncMock(return_value="recovered")) == "recovered"
    await allocation.close()


@pytest.mark.parametrize("settings_type", [GeneralSettings, Settings])
def test_deadline_validation_does_not_expose_unrelated_credentials(settings_type):
    with pytest.raises(ValidationError) as failure:
        settings_type(
            db_lock_timeout_seconds=3,
            redis_bulk_url="redis://user:PR3CANARY@x",
        )
    message = str(failure.value)
    assert "PR3CANARY" not in message
    assert "redis://" not in message
    assert "lock <= statement <= transaction" in message
