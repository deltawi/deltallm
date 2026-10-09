"""Real role graphs fund locally, accept journals, recover, and withdraw admission."""

import asyncio
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from src.accounting_settings import AccountingProtocolSettings
from src.api.internal_accounting import accounting_rpc_router
from src.billing.accounting.transport.accounting_http import AccountingHttpTransport
from src.bootstrap.accounting_local import build_api_accounting_runtime
from src.bootstrap.accounting_role_builders import (
    build_accounting_projection_runtime,
    build_accounting_request_runtime,
)
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.lifecycle_settings import LifecycleSettings
from src.outbound.network_policy import OutboundNetworkPolicy
from src.process_lifecycle import ProcessLifecycle
from src.telemetry.lifecycle import WorkerState
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_local_runtime_postgres import handle
from tests.test_accounting_permits_postgres import CountingClient
from tests.accounting_read_model_fixtures import reporting_finalization
from tests.accounting_read_model_fixtures import wait_for_native_projection
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _reservation,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


class ProcessingClient(CountingClient):
    def __init__(self, db):
        super().__init__(db)
        self.settled = asyncio.Event()
        self.progress = asyncio.Event()

    async def query_raw(self, query, *parameters):
        rows = await super().query_raw(query, *parameters)
        if "SELECT deltallm_accounting_reconcile_grants" in query and rows[0]["count"]:
            self.settled.set()
        if (
            "deltallm_accounting_reconcile_grants" in query
            or "deltallm_accounting_project_read_models" in query
        ):
            self.progress.set()
        return rows


class Wire(httpx.ASGITransport):
    def __init__(self, app):
        super().__init__(app=app)
        self.calls = []

    async def handle_async_request(self, request):
        self.calls.append(request.url.path)
        return await super().handle_async_request(request)


def lifecycle():
    return ProcessLifecycle(LifecycleSettings())


async def graph(clients, generation, *, native_lanes=False):
    config = AccountingProtocolSettings(
        accounting_protocol_enabled=True,
        accounting_protocol_generation=generation,
        accounting_grant_target_operations=4,
        accounting_microbatch_dwell_ms=0,
        accounting_execution_mode="local_journal" if native_lanes else "assigned",
        accounting_hot_path_db_pool_size=8 if native_lanes else 2,
        accounting_projection_batch_size=256 if native_lanes else 64,
    )
    projected = ProcessingClient(clients[0])
    projection = build_accounting_projection_runtime(
        projected, config, lifecycle(), owner_id="role-test"
    )
    request = build_accounting_request_runtime(clients[1], config, lifecycle())
    app = FastAPI()
    app.include_router(accounting_rpc_router(request.service, signing_secret="role-test-secret"))
    wire = Wire(app)

    async def resolve(host, port):
        return ("10.96.1.2",)

    transport = AccountingHttpTransport(
        service_url="http://accounting.test",
        signing_secret="role-test-secret",
        transport=wire,
        network_policy=OutboundNetworkPolicy(
            allow_http=True,
            allowed_ports=(80,),
            allowed_private_cidrs=("10.96.0.0/12",),
            resolver=resolve,
        ),
    )
    api = build_api_accounting_runtime(transport, config, lifecycle(), owner_id="api-role-test")
    return projection, request, api, transport, wire, projected


@pytest.mark.parametrize("lose_projection", [False, True])
@pytest.mark.parametrize("native_lanes", [False, True])
async def test_role_graph_has_real_startup_health_zero_call_warm_issue_and_exact_drain(
    accounting_db,
    lose_projection,
    native_lanes,
):
    clients, generation = accounting_db
    projection, request, api, transport, wire, projected = await graph(
        clients, generation, native_lanes=native_lanes
    )
    window = str(uuid4())
    await _create_window(clients[0], generation, window)
    try:
        await projection.start(expires_at=deadline())
        await request.start(expires_at=deadline())
        await api.start(expires_at=deadline())
        service = api.local.service
        assert all(
            owner.worker_health.state is WorkerState.READY for owner in (projection, request, api)
        )
        first = handle(await service.reserve(_reservation(generation, window)))
        funding = len(wire.calls)
        if lose_projection:
            await projection.presence.close(expires_at=deadline())
            assert not await request.monitor.refresh(expires_at=deadline())
            assert not await api.monitor.refresh(expires_at=deadline())
            with pytest.raises(AccountingProtocolUnavailable):
                await service.reserve(_reservation(generation, window))
            assert request.worker_health.state is WorkerState.DEGRADED
            operations = (first,)
        else:
            second = handle(await service.reserve(_reservation(generation, window)))
            assert len(wire.calls) == funding
            operations = (first, second)
        for operation in operations:
            receipt = await service.finalize_operation(operation, reporting_finalization(operation))
            assert receipt.operation_id == operation.reservation.operation_id
        assert service.issuer.receipt_store.entries == 0
        await api.close(expires_at=deadline())
        await wait_for_native_projection(clients[0], projected.progress, generation, timeout=2)
        assert await _window(clients[0], window) == (
            Decimal("0.6") * len(operations),
            Decimal(0),
            Decimal(0),
        )
        assert sum("/allocate/" in path for path in wire.calls) == 1
        assert sum("/return/" in path for path in wire.calls) == 1
        assert len(projection._lifecycle.producers) == (7 if native_lanes else 3)
        request._lifecycle.begin_drain()
        assert request.worker_health.state is WorkerState.STOPPING
        projection._lifecycle.begin_drain()
        assert projection.worker_health.state is not WorkerState.READY
    finally:
        try:
            await api.close(expires_at=deadline())
        finally:
            try:
                await request.close(expires_at=deadline())
            finally:
                try:
                    await projection.close(expires_at=deadline())
                finally:
                    await transport.close()


async def test_request_role_without_actual_projection_presence_cannot_start(accounting_db):
    clients, generation = accounting_db
    config = AccountingProtocolSettings(
        accounting_protocol_enabled=True, accounting_protocol_generation=generation
    )
    runtime = build_accounting_request_runtime(clients[0], config, lifecycle())
    try:
        with pytest.raises(RuntimeError, match="dependencies are not ready"):
            await runtime.start(expires_at=deadline())
        assert runtime.service.terminals.task is None
        assert runtime.monitor.worker_health.state is not WorkerState.READY
    finally:
        await runtime.close(expires_at=deadline())


async def test_native_projection_role_renews_recurring_budget_before_admission(accounting_db):
    clients, generation = accounting_db
    projection, request, api, transport, _, _ = await graph(clients, generation, native_lanes=True)
    window = str(uuid4())
    await _create_window(clients[0], generation, window)
    await clients[0].execute_raw(
        "UPDATE deltallm_accounting_budget_windows SET window_starts_at=NOW()-INTERVAL '2 hours',"
        "window_ends_at=NOW()-INTERVAL '1 hour',renewal_spec='1h' WHERE window_id=$1",
        window,
    )
    try:
        await projection.start(expires_at=deadline())
        await request.start(expires_at=deadline())
        await api.start(expires_at=deadline())
        operation = handle(
            await api.local.service.reserve(_reservation(generation, window, explicit_window=False))
        )
        await api.local.service.finalize_operation(operation, reporting_finalization(operation))
        await api.close(expires_at=deadline())
        assert api.local.worker_health.state is WorkerState.DISABLED
    finally:
        await api.close(expires_at=deadline())
        await request.close(expires_at=deadline())
        await projection.close(expires_at=deadline())
        await transport.close()
