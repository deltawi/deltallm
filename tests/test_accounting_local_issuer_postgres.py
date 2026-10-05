"""Native bulk funding and local issue retain exact money through closure."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting_local_cursors import LocalCursorStore
from src.billing.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting_local_leases import LocalPermitFinalization
from src.billing.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting_protocol import ReserveDecision
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
    accepted = await repository.finalize_batch(terminals, expires_at=deadline())
    assert counted.calls == 4 + (lost_phase is not None)
    assert [value.replayed for value in accepted] == [lost_phase == "terminal"] * 10
    for proof, value in zip(proofs, accepted, strict=True):
        assert retained.acknowledge(proof.restore(), value)
    assert await _settle_grants(db, generation) == 2
    assert await _window(db, window) == (Decimal("6.0"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == retained.entries == retained.retained_bytes == 0
    replay = await repository.finalize_batch(terminals, expires_at=deadline())
    assert [value.replayed for value in replay] == [True] * 10
    assert await _window(db, window) == (Decimal("6.0"), Decimal(0), Decimal(0))
