"""Shared request queues acknowledge durable acceptance before canonical work."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from tests.accounting_adapters.journal_terminal import JournalTerminalPersistence
from src.billing.accounting_local_cursors import LocalCursorStore
from src.billing.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting_local_leases import LocalAccountingHandle
from src.billing.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting_local_service import LocalAccountingService
from src.billing.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting_terminal_receipts import JournalReceipt
from src.db.accounting_journal import AccountingJournalRepository
from src.db.accounting_local_leases import AccountingLocalLeaseRepository
from src.db.accounting_protocol import AccountingProtocolRepository
from tests.test_accounting_journal_postgres import counts
from tests.test_accounting_journal_worker_postgres import worker
from tests.test_accounting_local_handles import values as handle_values
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _reservation,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)
from tests.test_preissued_permit_bank import fresh

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


@pytest.mark.parametrize("lost_phase", [None, "accept", "materialize"])
async def test_shared_local_journal_ack_and_canonical_commit_keep_one_outcome(
    accounting_db, lost_phase
):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window)
    funding = CountingClient(db)
    append = CountingClient(db, lose_ack=lost_phase == "accept")
    cursors = LocalCursorStore(
        generation=generation, max_entries=1024, max_retained_bytes=8 * 1024 * 1024
    )
    receipts = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    issuer = LocalPermitIssuer(
        AccountingLocalLeaseRepository(funding, owner_id="journal-queue-test"),
        cursors,
        receipts,
        target_operations=4,
    )
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
    )
    service.start()
    try:
        permits = await asyncio.gather(*(service.reserve(fresh(item)) for _ in range(4)))
        _, _, fields = handle_values()
        operations = [
            LocalAccountingHandle(
                reservation=permit.proof.reservation,
                proof=permit.proof,
                dispatch_token=permit.dispatch_token,
                accounting_partition=permit.accounting_partition,
                attempts=fields["attempts"],
            )
            for permit in permits
        ]
        records = [_finalization(operation.reservation) for operation in operations]
        accepted = await asyncio.gather(
            *(
                service.finalize_operation(operation, record)
                for operation, record in zip(operations, records, strict=True)
            )
        )
        assert funding.calls == 1 and append.calls == 1 + (lost_phase == "accept")
        assert all(
            type(ack) is JournalReceipt and "event_sequence" not in ack.model_dump()
            for ack in accepted
        )
        assert (
            receipts.entries == receipts.retained_bytes == service.finalizations.retained_bytes == 0
        )
        assert await counts(db, generation) == {
            "journal": 4,
            "payloads": 4,
            "operations": 0,
            "charged": 4,
        }
        assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
        assert await _settle_grants(db, generation) == 0
        processor = CountingClient(db)
        repo = worker(processor)
        claim = await repo.claim(generation=generation, worker_id="worker", expires_at=deadline())
        processor.lose_ack = lost_phase == "materialize"
        assert await repo.materialize(claim, expires_at=deadline()) == 4
        assert processor.calls == 2 + (lost_phase == "materialize")
        assert await _settle_grants(db, generation) == 1
        assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))
        replay = await asyncio.gather(
            *(
                service.finalize_operation(operation, record)
                for operation, record in zip(operations, records, strict=True)
            )
        )
        assert [ack.journal_sequence for ack in replay] == [
            ack.journal_sequence for ack in accepted
        ]
        assert all(ack.replayed for ack in replay)
        assert await counts(db, generation) == {
            "journal": 4,
            "payloads": 0,
            "operations": 4,
            "charged": 0,
        }
        assert funding.calls == 1 and receipts.entries == receipts.retained_bytes == 0
    finally:
        await service.close()


async def test_cancelled_acceptance_keeps_local_proofs_until_exact_retry(accounting_db):
    from tests.test_accounting_journal_postgres import at_ordinal
    from tests.test_accounting_local_leases_postgres import funded

    clients, generation = accounting_db
    db = clients[0]
    _, item, grant = await funded(db, generation)
    values = [at_ordinal(fresh(item), grant, index) for index in range(4)]
    receipts = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    for value in values:
        assert receipts.retain(value.receipt)

    class CancelAfterAppend:
        async def query_raw(self, query, *parameters):
            await db.query_raw(query, *parameters)
            raise asyncio.CancelledError()

    cancelled = LocalTerminalOwner(
        JournalTerminalPersistence(AccountingJournalRepository(CancelAfterAppend())),
        receipts,
        generation=generation,
        receipt_type=JournalReceipt,
    )
    charged = receipts.retained_bytes
    with pytest.raises(asyncio.CancelledError):
        await cancelled.finalize_batch(values, expires_at=deadline())
    assert receipts.entries == 4 and receipts.retained_bytes == charged
    assert (await counts(db, generation))["journal"] == 4
    retry = LocalTerminalOwner(
        JournalTerminalPersistence(AccountingJournalRepository(db)),
        receipts,
        generation=generation,
        receipt_type=JournalReceipt,
    )
    accepted = await retry.finalize_batch(values, expires_at=deadline())
    assert all(ack.replayed for ack in accepted)
    assert receipts.entries == receipts.retained_bytes == 0
    assert await counts(db, generation) == {
        "journal": 4,
        "payloads": 4,
        "operations": 0,
        "charged": 4,
    }
