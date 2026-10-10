"""Staged abort and atomic local issue preserve native escrow and exact settlement."""

from decimal import Decimal

import pytest

from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_issue import LocalIssueCommit
from src.billing.accounting.permits.accounting_local_leases import (
    LocalPermitFinalization,
    LocalPermitReceipt,
)
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.permits.preissued_permits import PermitSubject
from tests.test_accounting_local_leases_postgres import allocation, funded, owner, deadline
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import (
    _finalization,
    _outstanding,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)
from tests.test_preissued_permit_bank import fresh

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def state(generation):
    cursors = LocalCursorStore(
        generation=generation, max_entries=1024, max_retained_bytes=8 * 1024 * 1024
    )
    retained = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    return cursors, retained, LocalIssueCommit(cursors, retained)


def receipt(item, grant, ordinal):
    return LocalPermitReceipt(grant=grant, permit_ordinal=ordinal, reservation=item)


@pytest.mark.parametrize("lost_ack", [False, True])
async def test_aborted_cold_funding_returns_no_part_of_the_warm_prefix(accounting_db, lost_ack):
    clients, generation = accounting_db
    db = clients[0]
    window, item, warm = await funded(db, generation)
    subject = PermitSubject.from_reservation(item)
    cursors, retained, issue = state(generation)
    assert cursors.add(subject, warm)
    issued = issue.commit([receipt(item, warm, 0)], expires_at=deadline())
    counted = CountingClient(db)
    repository = owner(counted)
    cold = (await repository.allocate_batch([allocation(fresh(item))], expires_at=deadline()))[0]
    assert cursors.stage(subject, cold)
    charge = cursors.retained_bytes
    assert await _window(db, window) == (Decimal(0), Decimal(8), Decimal(0))
    cursors.abort_staging()
    assert cursors.retained_bytes == charge
    assert cursors.get(subject).next_ordinal == 1 and cursors.available_permits == 3
    returned = cursors.return_candidates()
    assert len(returned) == 1 and returned[0].grant == cold
    assert returned[0].first_unused_ordinal == 0
    counted.lose_ack = lost_ack
    counts = await repository.return_batch(returned, expires_at=deadline())
    assert counted.calls == 2 + lost_ack
    assert cursors.acknowledge_return(returned[0], counts[0])
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
    assert await _outstanding(db, generation) == 4
    proof = issued.proofs[0].restore()
    terminal = LocalPermitFinalization(receipt=proof, finalization=_finalization(item))
    accepted = (await repository.finalize_batch([terminal], expires_at=deadline()))[0]
    assert retained.acknowledge(proof, accepted)
    cursors.retire_slice()
    suffix = cursors.return_candidates()[0]
    assert suffix.grant == warm and suffix.first_unused_ordinal == 1
    count = (await repository.return_batch([suffix], expires_at=deadline()))[0]
    assert cursors.acknowledge_return(suffix, count)
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("0.6"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0
    assert (
        cursors.entries
        == cursors.retained_bytes
        == retained.entries
        == retained.retained_bytes
        == 0
    )


@pytest.mark.parametrize("lost_ack", [False, True])
async def test_three_grant_issue_has_one_bulk_terminal_and_exact_recovered_proofs(
    accounting_db, lost_ack
):
    clients, generation = accounting_db
    db = clients[0]
    window, item, warm = await funded(db, generation)
    subject = PermitSubject.from_reservation(item)
    cursors, retained, issue = state(generation)
    assert cursors.add(subject, warm)
    initial = issue.commit([receipt(item, warm, 0)], expires_at=deadline())
    counted = CountingClient(db)
    repository = owner(counted)
    grants = await repository.allocate_batch(
        [allocation(fresh(item)), allocation(fresh(item))], expires_at=deadline()
    )
    # Funding follows ordered fences, not caller order. Use the larger grant first.
    second, third = sorted(grants, key=lambda grant: grant.operation_limit, reverse=True)
    assert warm.operation_limit == second.operation_limit == 4
    assert third.operation_limit == 2
    assert await _window(db, window) == (Decimal(0), Decimal(10), Decimal(0))
    assert cursors.stage(subject, second) and cursors.stage(subject, third)
    proposed = (
        [receipt(fresh(item), warm, ordinal) for ordinal in range(1, 4)]
        + [receipt(fresh(item), second, ordinal) for ordinal in range(4)]
        + [receipt(fresh(item), third, 0)]
    )
    result = issue.commit(proposed, expires_at=deadline())
    assert cursors.available_permits == 1 and cursors.get(subject).grant == third
    proofs = initial.proofs + result.proofs
    terminals = [
        LocalPermitFinalization(
            receipt=proof.restore(), finalization=_finalization(proof.restore().reservation)
        )
        for proof in proofs
    ]
    counted.lose_ack = lost_ack
    accepted = await repository.finalize_batch(terminals, expires_at=deadline())
    assert counted.calls == 2 + lost_ack
    assert [value.replayed for value in accepted] == [lost_ack] * 9
    for proof, value in zip(proofs, accepted, strict=True):
        assert retained.acknowledge(proof.restore(), value)
    cursors.retire_slice()
    suffix = cursors.return_candidates()[0]
    assert suffix.grant == third and suffix.first_unused_ordinal == 1
    count = (await repository.return_batch([suffix], expires_at=deadline()))[0]
    assert cursors.acknowledge_return(suffix, count)
    assert await _settle_grants(db, generation) == 3
    assert await _window(db, window) == (Decimal("5.4"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0
    assert (
        cursors.entries
        == cursors.retained_bytes
        == retained.entries
        == retained.retained_bytes
        == 0
    )
