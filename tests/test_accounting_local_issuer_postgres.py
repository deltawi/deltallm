"""Native bulk funding and local issue retain exact money through closure."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting.permits.accounting_local_leases import (
    LocalAccountingHandle,
    LocalDispatchPermit,
    LocalPermitFinalization,
)
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.journal.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting.accounting_local_service import LocalAccountingService
from src.billing.accounting.transport.accounting_local_wire import (
    wire_local_terminals,
    restore_wire_terminals,
)
from src.billing.accounting.accounting_protocol import ReserveDecision
from src.db.accounting.accounting_protocol import AccountingProtocolRepository
from src.db.accounting.permits.accounting_local_leases import AccountingLocalLeaseRepository
from tests.test_accounting_local_leases_postgres import deadline, owner
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
from tests.test_preissued_permit_bank import fresh
from tests.test_accounting_local_handles import values as handle_values

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


@pytest.mark.parametrize("lost_phase", [None, "fund", "terminal"])
async def test_native_warm_zero_calls_two_round_partial_and_replay_after_closure(
    accounting_db, lost_phase
):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window)
    counted = CountingClient(db, lose_ack=lost_phase == "fund")
    repository = owner(counted)
    cursors = LocalCursorStore(
        generation=generation, max_entries=1024, max_retained_bytes=8 * 1024 * 1024
    )
    retained = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    issuer = LocalPermitIssuer(repository, cursors, retained, target_operations=4)
    initial = await issuer.reserve_batch([item], expires_at=deadline())
    assert counted.calls == 1 + (lost_phase == "fund")
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
    warm = await issuer.reserve_batch([fresh(item) for _ in range(3)], expires_at=deadline())
    assert counted.calls == 1 + (lost_phase == "fund")
    assert cursors.entries == cursors.available_permits == 0
    partial = await issuer.reserve_batch([fresh(item) for _ in range(7)], expires_at=deadline())
    assert counted.calls == 3 + (lost_phase == "fund")
    assert [permit.decision for permit in partial.permits] == [ReserveDecision.DISPATCH] * 6 + [
        ReserveDecision.BUDGET_EXHAUSTED
    ]
    assert await _window(db, window) == (Decimal(0), Decimal(10), Decimal(0))
    assert cursors.entries == cursors.staged_grants == cursors.available_permits == 0
    proofs = initial.proofs + warm.proofs + partial.proofs
    assert retained.entries == len(proofs) == 10
    terminals = [
        LocalPermitFinalization(
            receipt=proof.restore(), finalization=_finalization(proof.restore().reservation)
        )
        for proof in proofs
    ]
    counted.lose_ack = lost_phase == "terminal"
    terminal_owner = LocalTerminalOwner(repository, retained, generation=generation)
    accepted = await terminal_owner.finalize_batch(terminals, expires_at=deadline())
    assert counted.calls == 4 + (lost_phase is not None)
    assert [value.replayed for value in accepted] == [lost_phase == "terminal"] * 10
    assert retained.entries == retained.retained_bytes == 0
    assert await _settle_grants(db, generation) == 2
    assert await _window(db, window) == (Decimal("6.0"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == retained.entries == retained.retained_bytes == 0
    replay = await terminal_owner.finalize_batch(terminals, expires_at=deadline())
    assert [value.replayed for value in replay] == [True] * 10
    assert await _window(db, window) == (Decimal("6.0"), Decimal(0), Decimal(0))


@pytest.mark.parametrize("lost_phase", [None, "fund", "terminal"])
async def test_native_shared_queues_reconcile_and_replay_after_proof_removal(
    accounting_db, lost_phase
):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window)
    counted = CountingClient(db, lose_ack=lost_phase == "fund")
    repository = AccountingLocalLeaseRepository(counted, owner_id="shared-queue-test")
    cursors = LocalCursorStore(
        generation=generation, max_entries=1024, max_retained_bytes=8 * 1024 * 1024
    )
    receipts = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    issuer = LocalPermitIssuer(repository, cursors, receipts, target_operations=4)
    terminal_owner = LocalTerminalOwner(repository, receipts, generation=generation)
    service = LocalAccountingService(
        AccountingProtocolRepository(db),
        generation=generation,
        issuer=issuer,
        terminal=terminal_owner,
    )
    service.start()
    try:
        permits = await asyncio.gather(*(service.reserve(fresh(item)) for _ in range(4)))
        assert counted.calls == 1 + (lost_phase == "fund")
        assert all(isinstance(permit, LocalDispatchPermit) for permit in permits)
        assert receipts.entries == 4 and await _outstanding(db, generation) == 4
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
        remote = restore_wire_terminals(
            wire_local_terminals(
                [
                    LocalPermitFinalization(receipt=operation.proof, finalization=record)
                    for operation, record in zip(operations, records, strict=True)
                ],
                generation=generation,
            ),
            generation=generation,
            observed_monotonic=100000000,
        )
        operations = [
            operation.model_copy(update={"proof": value.receipt})
            for operation, value in zip(operations, remote, strict=True)
        ]
        counted.lose_ack = lost_phase == "terminal"
        accepted = await asyncio.gather(
            *(
                service.finalize_operation(operation, record)
                for operation, record in zip(operations, records, strict=True)
            )
        )
        assert counted.calls == 2 + (lost_phase is not None)
        assert [result.replayed for result in accepted] == [lost_phase == "terminal"] * 4
        assert (
            receipts.entries == receipts.retained_bytes == service.finalizations.retained_bytes == 0
        )
        assert await _settle_grants(db, generation) == 1
        assert await _outstanding(db, generation) == 0
        assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))
        replay = await asyncio.gather(
            *(
                service.finalize_operation(operation, record)
                for operation, record in zip(operations, records, strict=True)
            )
        )
        assert all(result.replayed for result in replay)
        assert [result.event_sequence for result in replay] == [
            result.event_sequence for result in accepted
        ]
        assert receipts.entries == receipts.retained_bytes == 0
        assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))
    finally:
        await service.close()
