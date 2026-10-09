"""Local runtime shutdown does not confuse journal acceptance with settlement."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting.journal.accounting_journal_runtime import (
    JournalProcessingWorker,
    JournalWorkerConfig,
)
from src.billing.accounting.journal.accounting_journal_terminal import JournalTerminalPersistence
from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.permits.accounting_local_returns import LocalReturnWorker
from src.billing.accounting.accounting_local_runtime import LocalAccountingRuntime
from src.billing.accounting.accounting_local_service import LocalAccountingService
from src.billing.accounting.journal.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting.journal.accounting_terminal_receipts import JournalReceipt
from src.db.accounting.journal.accounting_journal import AccountingJournalRepository
from src.db.accounting.journal.accounting_journal_worker import AccountingJournalWorkerRepository
from src.db.accounting.permits.accounting_local_leases import AccountingLocalLeaseRepository
from src.db.accounting.accounting_protocol import AccountingProtocolRepository
from tests.test_accounting_journal_postgres import counts
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_local_service import handle
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _outstanding,
    _reservation,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def runtime(db, generation, *, owner_id="local-runtime", funding=None, append=None):
    funding = funding or CountingClient(db)
    append = append or CountingClient(db)
    lease = AccountingLocalLeaseRepository(funding, owner_id=owner_id)
    cursors = LocalCursorStore(
        generation=generation, max_entries=1024, max_retained_bytes=8 * 1024 * 1024
    )
    receipts = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    issuer = LocalPermitIssuer(lease, cursors, receipts, target_operations=4)
    terminal = LocalTerminalOwner(
        JournalTerminalPersistence(AccountingJournalRepository(append)),
        receipts,
        generation=generation,
        receipt_type=JournalReceipt,
    )
    service = LocalAccountingService(
        AccountingProtocolRepository(db),
        generation=generation,
        issuer=issuer,
        terminal=terminal,
        dwell_seconds=0,
    )
    returns = LocalReturnWorker(lease, cursors, issuer, poll_seconds=0.01)
    return LocalAccountingRuntime(service, returns), funding, append, cursors, receipts, lease


def processor(db, generation):
    return JournalProcessingWorker(
        AccountingJournalWorkerRepository(db),
        JournalWorkerConfig(generation=generation, worker_id="runtime-processing"),
    )


@pytest.mark.parametrize("lost_phase", [None, "allocate", "accept", "return", "materialize"])
async def test_complete_local_drain_keeps_durable_journal_and_money_until_processing(
    accounting_db, lost_phase
):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    local, funding, append, cursors, receipts, _ = runtime(db, generation)
    funding.lose_ack = lost_phase == "allocate"
    append.lose_ack = lost_phase == "accept"
    await local.start(expires_at=deadline())
    operation = handle(await local.service.reserve(_reservation(generation, window)))
    ack = await local.service.finalize_operation(operation, _finalization(operation.reservation))
    assert type(ack) is JournalReceipt
    funding.lose_ack = lost_phase == "return"
    assert await local.close(expires_at=deadline())
    assert funding.calls == 2 + (lost_phase in {"allocate", "return"})
    assert append.calls == 1 + (lost_phase == "accept")
    assert (
        cursors.entries
        == cursors.retained_bytes
        == receipts.entries
        == receipts.retained_bytes
        == 0
    )
    assert await counts(db, generation) == {
        "journal": 1,
        "payloads": 1,
        "operations": 0,
        "charged": 1,
    }
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
    assert await _outstanding(db, generation) == 4
    assert await _settle_grants(db, generation) == 0

    class ProcessingClient(CountingClient):
        async def query_raw(self, query, *parameters):
            if (
                lost_phase == "materialize"
                and "SELECT deltallm_accounting_materialize_terminal_journal" in query
            ):
                self.lose_ack = True
            return await super().query_raw(query, *parameters)

    counted = ProcessingClient(clients[1])
    worker = processor(counted, generation)
    assert await worker.run_once(expires_at=deadline()) == 1
    assert counted.calls == 2 + (lost_phase == "materialize")
    assert await worker.close(expires_at=deadline())
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("0.6"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0
    assert await counts(db, generation) == {
        "journal": 1,
        "payloads": 0,
        "operations": 1,
        "charged": 0,
    }


async def test_two_local_runtimes_share_the_same_hard_budget_and_close_independently(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window, limit="6")
    locals_ = [
        runtime(client, generation, owner_id=f"local-runtime-{index}")[0]
        for index, client in enumerate(clients)
    ]
    try:
        await asyncio.gather(*(local.start(expires_at=deadline()) for local in locals_))
        permits = await asyncio.gather(
            *(local.service.reserve(_reservation(generation, window)) for local in locals_)
        )
        assert sorted(permit.proof.grant.operation_limit for permit in permits) == [2, 4]
        assert await _window(db, window) == (Decimal(0), Decimal(6), Decimal(0))
        operations = [handle(permit) for permit in permits]
        await asyncio.gather(
            *(
                local.service.finalize_operation(operation, _finalization(operation.reservation))
                for local, operation in zip(locals_, operations, strict=True)
            )
        )
        assert await asyncio.gather(*(local.close(expires_at=deadline()) for local in locals_)) == [
            True,
            True,
        ]
    finally:
        await asyncio.gather(*(local.close(expires_at=deadline()) for local in locals_))
    assert await _outstanding(db, generation) == 6
    worker = processor(db, generation)
    assert await worker.run_once(expires_at=deadline()) == 2
    assert await worker.close(expires_at=deadline())
    assert await _settle_grants(db, generation) == 2
    assert await _window(db, window) == (Decimal("1.2"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


@pytest.mark.parametrize("phase", ["funding", "terminal"])
async def test_cancelled_database_reply_keeps_durable_funding_or_acceptance_and_local_proofs(
    accounting_db, phase
):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    entered = asyncio.Event()

    class InterruptedClient(CountingClient):
        async def query_raw(self, query, *parameters):
            result = await super().query_raw(query, *parameters)
            entered.set()
            await asyncio.Event().wait()
            return result

    local, _, _, cursors, receipts, _ = runtime(
        db,
        generation,
        funding=InterruptedClient(db) if phase == "funding" else None,
        append=InterruptedClient(db) if phase == "terminal" else None,
    )
    await local.start(expires_at=deadline())
    service = local.service
    if phase == "funding":
        caller = asyncio.create_task(service.reserve(_reservation(generation, window)))
    else:
        operation = handle(await service.reserve(_reservation(generation, window)))
        caller = asyncio.create_task(
            service.finalize_operation(operation, _finalization(operation.reservation))
        )
    await entered.wait()
    cursor_charge, proof_charge = cursors.retained_bytes, receipts.retained_bytes
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not await local.close(expires_at=asyncio.get_running_loop().time() + 0.03)
    assert cursors.retained_bytes == cursor_charge and receipts.retained_bytes == proof_charge
    assert receipts.entries == (phase == "terminal")
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
    assert await _outstanding(db, generation) == 4
    assert await counts(db, generation) == {
        "journal": int(phase == "terminal"),
        "payloads": int(phase == "terminal"),
        "operations": 0,
        "charged": 1 if phase == "terminal" else None,
    }
