"""Two request workers share durable fences, not local issued-proof memory."""

import asyncio
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from src.api.internal_accounting import accounting_rpc_router
from src.billing.accounting_health import AccountingBacklogPolicy, AccountingBacklogProbe
from src.billing.accounting_http import AccountingHttpTransport
from src.billing.accounting_journal_runtime import JournalProcessingWorker, JournalWorkerConfig
from src.billing.accounting_local_cursors import LocalCursorStore
from src.billing.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting_local_returns import LocalReturnWorker
from src.billing.accounting_local_runtime import LocalAccountingRuntime
from src.billing.accounting_local_service import LocalAccountingService
from src.billing.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting_remote_leases import RemoteLocalLeasePersistence
from src.billing.accounting_rpc_service import AccountingRpcService
from src.billing.accounting_terminal_receipts import JournalReceipt
from src.db.accounting_health import AccountingBacklogRepository
from src.db.accounting_journal_worker import AccountingJournalWorkerRepository
from src.outbound.network_policy import OutboundNetworkPolicy
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_local_runtime_postgres import handle
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_recovery_postgres import recovery
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _outstanding,
    _reservation,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


class MaterializationClient(CountingClient):
    def __init__(self, db):
        super().__init__(db)
        self.materialized = asyncio.Event()

    async def query_raw(self, query, *parameters):
        rows = await super().query_raw(query, *parameters)
        if "SELECT deltallm_accounting_materialize_terminal_journal" in query:
            self.materialized.set()
        return rows


class TwoWorkers(httpx.AsyncBaseTransport):
    def __init__(self, apps):
        self.apps = tuple(httpx.ASGITransport(app) for app in apps)
        self.paths = []

    async def handle_async_request(self, request):
        self.paths.append(request.url.path)
        index = 0 if "/allocate/" in request.url.path else 1
        return await self.apps[index].handle_async_request(request)


async def request_worker(db, generation, processing):
    probe = AccountingBacklogProbe(
        AccountingBacklogRepository(db), AccountingBacklogPolicy(generation=generation)
    )
    assert await probe.refresh(expires_at=deadline())
    rpc = AccountingRpcService(
        db, generation=generation, probe=probe, projection_health=processing, dwell_seconds=0
    )
    rpc.start()
    app = FastAPI()
    app.include_router(accounting_rpc_router(rpc, signing_secret="native-rpc-test"))
    return app, rpc


async def local_api(apps, generation):
    async def resolve(host, port):
        return ("10.96.1.2",)

    wire = TwoWorkers(apps)
    transport = AccountingHttpTransport(
        service_url="http://accounting.test",
        signing_secret="native-rpc-test",
        transport=wire,
        network_policy=OutboundNetworkPolicy(
            allow_http=True,
            allowed_ports=(80,),
            allowed_private_cidrs=("10.96.0.0/12",),
            resolver=resolve,
        ),
    )
    persistence = RemoteLocalLeasePersistence(
        transport, generation=generation, owner_id="native-api"
    )
    cursors = LocalCursorStore(
        generation=generation, max_entries=1024, max_retained_bytes=8 * 1024 * 1024
    )
    receipts = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    issuer = LocalPermitIssuer(persistence, cursors, receipts, target_operations=4)
    terminal = LocalTerminalOwner(
        persistence, receipts, generation=generation, receipt_type=JournalReceipt
    )
    service = LocalAccountingService(
        persistence, generation=generation, issuer=issuer, terminal=terminal, dwell_seconds=0
    )
    returns = LocalReturnWorker(persistence, cursors, issuer, poll_seconds=0.01)
    return LocalAccountingRuntime(service, returns), transport, wire, cursors, receipts, persistence


@pytest.mark.parametrize("replay", [False, True])
async def test_funding_and_terminal_on_different_workers_preserve_one_issue_and_exact_money(
    accounting_db, replay
):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    materialization = MaterializationClient(clients[1])
    processing = JournalProcessingWorker(
        AccountingJournalWorkerRepository(materialization),
        JournalWorkerConfig(generation=generation, worker_id="rpc-projection", poll_seconds=0.01),
    )
    await processing.start(expires_at=deadline())
    services = [await request_worker(client, generation, processing) for client in clients]
    local, transport, wire, cursors, receipts, persistence = await local_api(
        [app for app, _ in services], generation
    )
    try:
        await local.start(expires_at=deadline())
        operation = handle(await local.service.reserve(_reservation(generation, window)))
        assert receipts.entries == 1
        record = _finalization(operation.reservation)
        ack = await local.service.finalize_operation(operation, record)
        assert type(ack) is JournalReceipt and not ack.replayed
        assert receipts.entries == receipts.retained_bytes == 0
        if replay:
            from src.billing.accounting_local_leases import LocalPermitFinalization

            repeated = await persistence.finalize_batch(
                [LocalPermitFinalization(receipt=operation.proof, finalization=record)],
                expires_at=deadline(),
            )
            assert repeated[0].replayed and repeated[0].journal_sequence == ack.journal_sequence
        assert await local.close(expires_at=deadline())
        assert cursors.entries == cursors.retained_bytes == 0
        async with asyncio.timeout(1):
            await materialization.materialized.wait()
        worker, _ = recovery(db, generation)
        assert await worker.run_once(expires_at=deadline()) == 1
        assert await _window(db, window) == (Decimal("0.6"), Decimal(0), Decimal(0))
        assert await _outstanding(db, generation) == 0
        assert wire.paths.count("/internal/accounting/v1/allocate/local/batch") == 1
        assert wire.paths.count("/internal/accounting/v1/return/local/batch") == 1
        assert wire.paths.count("/internal/accounting/v1/finalize/local/batch") == 1 + replay
        rows = await db.query_raw(
            "SELECT count(*)::integer AS count FROM deltallm_accounting_terminal_journal WHERE generation=$1",
            generation,
        )
        assert rows == [{"count": 1}]
    finally:
        await local.close(expires_at=deadline())
        await transport.close()
        for _, service in services:
            await service.close(timeout_seconds=1)
        assert await processing.close(expires_at=deadline())
