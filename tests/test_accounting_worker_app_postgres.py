"""Minimal apps own native pools, signed routes, health, and clean shutdown."""

import os
from uuid import uuid4

import httpx
import pytest

from src.accounting_settings import AccountingProtocolSettings
from src.bootstrap.accounting_local import build_api_accounting_runtime
from src.bootstrap.accounting_worker_app import create_accounting_worker_app
from src.bootstrap.accounting_remote import RemoteAccountingOwner
from src.bootstrap.server_application import create_server_application
from src.billing.accounting_http import AccountingHttpTransport
from src.config import DatabaseConnectionSettings
from src.db.accounting_pool import AccountingPostgresClient, AccountingPostgresManager
from src.lifecycle_settings import LifecycleSettings
from src.outbound.network_policy import OutboundNetworkPolicy
from src.process_lifecycle import ProcessLifecycle
from src.telemetry.lifecycle import WorkerState
from src.shutdown import ShutdownOwner, shutdown_owner
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_local_runtime_postgres import handle
from tests.accounting_read_model_fixtures import reporting_finalization
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _reservation,
    accounting_db as _accounting_db,
)
from tests.test_native_server_application import startup
from tests.test_accounting_native_config import native

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def test_minimal_apps_use_native_pools_and_signed_local_journal_path(accounting_db):
    clients, generation = accounting_db
    config = AccountingProtocolSettings(
        accounting_protocol_enabled=True,
        accounting_protocol_generation=generation,
        accounting_microbatch_dwell_ms=0,
        accounting_grant_target_operations=4,
    )
    database = DatabaseConnectionSettings(
        url=os.environ["DATABASE_URL"], pool_size=2, pool_timeout=1
    )
    projection = create_accounting_worker_app(
        config=config,
        database=database,
        lifecycle=ProcessLifecycle(LifecycleSettings()),
        role="projection",
        owner_id="app-projection",
    )
    request = create_accounting_worker_app(
        config=config,
        database=database,
        lifecycle=ProcessLifecycle(LifecycleSettings()),
        role="request",
        owner_id="app-request",
        signing_secret="app-test-secret",
    )
    window = str(uuid4())
    await _create_window(clients[0], generation, window)

    async def resolve(host, port):
        return ("10.96.1.2",)

    transport = AccountingHttpTransport(
        service_url="http://accounting.test",
        signing_secret="app-test-secret",
        transport=httpx.ASGITransport(request),
        network_policy=OutboundNetworkPolicy(
            allow_http=True,
            allowed_ports=(80,),
            allowed_private_cidrs=("10.96.0.0/12",),
            resolver=resolve,
        ),
    )
    api = build_api_accounting_runtime(
        transport, config, ProcessLifecycle(LifecycleSettings()), owner_id="app-api"
    )
    try:
        async with projection.router.lifespan_context(projection):
            async with request.router.lifespan_context(request):
                runtime = request.state.accounting_role_state.runtime
                assert isinstance(runtime.service._client, AccountingPostgresClient)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(request), base_url="http://test"
                ) as client:
                    assert (await client.get("/health/readiness")).status_code == 200
                    assert (await client.get("/internal/accounting/v1/health")).status_code == 401
                    assert (
                        await client.post("/internal/accounting/v1/reserve/compact/batch")
                    ).status_code == 404
                    assert (await client.post("/v1/chat/completions", json={})).status_code == 404
                await api.start(expires_at=deadline())
                service = api.local.service
                operation = handle(await service.reserve(_reservation(generation, window)))
                receipt = await service.finalize_operation(
                    operation, reporting_finalization(operation)
                )
                assert receipt.operation_id == operation.reservation.operation_id
                await api.close(expires_at=deadline())
                assert runtime.worker_health.state is WorkerState.READY
    finally:
        await api.close(expires_at=deadline())
        await transport.close()
    assert not projection.state.shutdown_owner.failed and not request.state.shutdown_owner.failed
    assert request.state.accounting_role_state.runtime.monitor.task.done()
    assert projection.state.accounting_role_state.runtime.processing.task.done()
    assert projection.state.accounting_role_state.runtime.read_models.task.done()
    assert projection.state.accounting_role_state.runtime.recovery.task.done()


async def test_launcher_selected_roles_and_managed_api_use_the_migrated_native_path(
    accounting_db, monkeypatch
):
    clients, generation = accounting_db

    connected_pools = []
    connect = AccountingPostgresManager.connect

    async def capture_pool(manager, database, **limits):
        await connect(manager, database, **limits)
        connected_pools.append(manager._pool.get_max_size())

    monkeypatch.setattr(AccountingPostgresManager, "connect", capture_pool)

    def role_app(role):
        initial = startup(role, accounting_protocol_generation=generation)
        configured = type(initial)(
            settings=initial.settings.model_copy(
                update={
                    "database_url": os.environ["DATABASE_URL"],
                    "db_pool_size": initial.app_config.general_settings.accounting_hot_path_db_pool_size,
                }
            ),
            file_config=initial.file_config,
            app_config=initial.app_config,
            lifecycle=LifecycleSettings(migration_mode="external"),
        )
        return create_server_application(
            startup=configured, lifecycle=ProcessLifecycle(configured.lifecycle)
        )

    projection, request = role_app("accountingWorker"), role_app("accountingRequest")
    window = str(uuid4())
    await _create_window(clients[0], generation, window)

    async def resolve(host, port):
        return ("10.96.1.2",)

    def transport_factory(**kwargs):
        assert kwargs["max_connections"] == 16
        return AccountingHttpTransport(
            **{
                **kwargs,
                "transport": httpx.ASGITransport(request),
                "network_policy": OutboundNetworkPolicy(
                    allowed_private_cidrs=("10.96.0.0/12",), resolver=resolve
                ),
            }
        )

    monkeypatch.setattr(
        "src.bootstrap.accounting_remote.AccountingHttpTransport", transport_factory
    )
    api_lifecycle = ProcessLifecycle(LifecycleSettings())
    api = RemoteAccountingOwner(
        native(accounting_protocol_generation=generation),
        api_lifecycle,
        role="api",
        owner_id="launcher-api",
    )
    try:
        async with projection.router.lifespan_context(projection):
            async with request.router.lifespan_context(request):
                projected = projection.state.accounting_role_state.runtime
                assert connected_pools == [8, 2]
                assert len(projected._lifecycle.producers) == 7
                # Production roles are separate processes. In-process ASGI calls
                # must not inherit the RPC role's parent shutdown context.
                token = shutdown_owner.set(ShutdownOwner(api_lifecycle))
                try:
                    await api.start(expires_at=deadline())
                    operation = handle(await api.service.reserve(_reservation(generation, window)))
                    receipt = await api.service.finalize_operation(
                        operation, reporting_finalization(operation)
                    )
                    assert receipt.operation_id == operation.reservation.operation_id
                    assert api.worker_health.state is WorkerState.READY
                    await api.close()
                    assert api.runtime.monitor.task.done()
                finally:
                    await api.close()
                    shutdown_owner.reset(token)
                assert (
                    request.state.accounting_role_state.runtime.worker_health.state
                    is WorkerState.READY
                )
    finally:
        await api.close()
    assert not request.state.shutdown_owner.failed and not projection.state.shutdown_owner.failed
