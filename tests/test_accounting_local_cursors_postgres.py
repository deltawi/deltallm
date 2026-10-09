"""Return-only cursor state follows exact native settlement acknowledgements."""

from decimal import Decimal, localcontext
from uuid import uuid4

import pytest

from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_leases import (
    LocalPermitFinalization,
    LocalPermitReceipt,
)
from src.billing.accounting.accounting_protocol import (
    AccountingOutcome,
    PreissuedPermitAllocation,
    PreissuedPermitClaim,
)
from src.db.accounting_permits import AccountingPermitRepository
from src.db.accounting_protocol import AccountingProtocolRepository
from src.billing.accounting.permits.preissued_permits import PermitSubject
from tests.test_accounting_local_leases_postgres import allocation, funded, owner, deadline
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

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def test_expired_cursor_keeps_escrow_until_exact_recovered_return_ack(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, item, grant = await funded(db, generation)
    subject = PermitSubject.from_reservation(item)
    state = LocalCursorStore(
        generation=generation, max_entries=1, max_retained_bytes=8 * 1024 * 1024
    )
    assert state.add(subject, grant)
    charge = state.retained_bytes
    state.prune(now=grant.dispatch_deadline + 1, minimum_validity_seconds=0.1)
    assert state.retiring_grants == state.entries == 1
    assert state.retained_bytes == charge
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
    suffix = state.return_candidates()[0]
    counted = CountingClient(db, lose_ack=True)
    returned = (await owner(counted).return_batch([suffix], expires_at=deadline()))[0]
    assert counted.calls == 2
    assert state.acknowledge_return(suffix, returned)
    assert state.entries == state.retained_bytes == 0
    assert not state.acknowledge_return(suffix, returned)
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


async def test_return_only_suffix_cannot_include_two_issued_ordinals(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, first, grant = await funded(db, generation)
    subject = PermitSubject.from_reservation(first)
    state = LocalCursorStore(
        generation=generation, max_entries=1, max_retained_bytes=8 * 1024 * 1024
    )
    assert state.add(subject, grant)
    receipts = []
    for item in (first, fresh(first)):
        ordinal = state.get(subject).next_ordinal
        receipts.append(
            LocalPermitFinalization(
                receipt=LocalPermitReceipt(grant=grant, permit_ordinal=ordinal, reservation=item),
                finalization=_finalization(item),
            )
        )
        state.advance(subject)
    state.retire_slice()
    suffix = state.return_candidates()[0]
    assert suffix.first_unused_ordinal == 2
    counted = CountingClient(db)
    repository = owner(counted)
    accepted = await repository.finalize_batch(receipts, expires_at=deadline())
    assert len(accepted) == 2
    returned = (await repository.return_batch([suffix], expires_at=deadline()))[0]
    assert counted.calls == 2
    with pytest.raises(ValueError, match="does not match"):
        state.acknowledge_return(suffix, returned + 1)
    assert state.entries == 1
    assert state.acknowledge_return(suffix, returned)
    assert state.entries == state.retained_bytes == 0
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("1.2"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


async def test_full_precision_return_recovery_matches_native_money_with_small_context(
    accounting_db,
):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window, limit="90000000000000000000")
    item = _reservation(generation, window, allowance="9999999999999999999.123456789123456789")
    counted = CountingClient(db)
    repository = owner(counted)
    grant = (await repository.allocate_batch([allocation(item)], expires_at=deadline()))[0]
    subject = PermitSubject.from_reservation(item)
    state = LocalCursorStore(
        generation=generation, max_entries=1, max_retained_bytes=8 * 1024 * 1024
    )
    assert state.add(subject, grant)
    state.advance(subject)
    receipt = LocalPermitFinalization(
        receipt=LocalPermitReceipt(grant=grant, permit_ordinal=0, reservation=item),
        finalization=_finalization(item, AccountingOutcome.NOT_DISPATCHED),
    )
    await repository.finalize_batch([receipt], expires_at=deadline())
    state.retire_slice()
    suffix = state.return_candidates()[0]
    counted.lose_ack = True
    with localcontext() as caller_context:
        caller_context.prec = 3
        returned = (await repository.return_batch([suffix], expires_at=deadline()))[0]
    assert counted.calls == 4
    assert state.acknowledge_return(suffix, returned)
    assert state.entries == state.retained_bytes == 0
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


async def test_preissued_funding_and_recovery_preserve_full_native_money(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window, limit="90000000000000000000")
    item = _reservation(generation, window, allowance="9999999999999999999.123456789123456789")
    counted = CountingClient(db, lose_ack=True)
    repository = AccountingPermitRepository(
        counted, owner_id="money-test", statement_budget_seconds=2
    )
    grant = (
        await repository.allocate_batch(
            [PreissuedPermitAllocation(reservation=item, fence_token=uuid4(), target_operations=1)],
            expires_at=deadline(),
        )
    )[0]
    assert counted.calls == 2
    assert grant.allowance == item.allowance
    claim = PreissuedPermitClaim(grant=grant, permit_ordinal=0, reservation=item)
    await repository.claim_batch([claim], expires_at=deadline())
    await AccountingProtocolRepository(counted, statement_budget_seconds=2).finalize_batch(
        [_finalization(item, AccountingOutcome.NOT_DISPATCHED)], expires_at=deadline()
    )
    assert counted.calls == 4
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0
