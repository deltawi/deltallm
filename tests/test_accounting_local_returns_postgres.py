"""Native return supervision keeps issued costs through suffix closure."""

from decimal import Decimal

import pytest

from src.billing.accounting_local_leases import LocalPermitFinalization, LocalPermitReceipt
from src.billing.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting_local_returns import LocalReturnWorker
from src.telemetry.lifecycle import WorkerState
from tests.test_accounting_local_issue_postgres import state
from tests.test_accounting_local_leases_postgres import deadline, funded, owner
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import (
    _finalization,
    _outstanding,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)
from src.billing.preissued_permits import PermitSubject

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


@pytest.mark.parametrize("lost_ack", [False, True])
async def test_supervised_close_returns_suffix_but_keeps_issued_terminal_owner(
    accounting_db, lost_ack
):
    clients, generation = accounting_db
    db = clients[0]
    window, item, grant = await funded(db, generation)
    counted = CountingClient(db, lose_ack=lost_ack)
    repository = owner(counted)
    cursors, retained, issue = state(generation)
    assert cursors.add(PermitSubject.from_reservation(item), grant)
    issued = issue.commit(
        [LocalPermitReceipt(grant=grant, permit_ordinal=0, reservation=item)],
        expires_at=deadline(),
    )
    issuer = LocalPermitIssuer(repository, cursors, retained)
    worker = LocalReturnWorker(repository, cursors, issuer, call_budget_seconds=5)
    assert await worker.close(expires_at=deadline())
    assert counted.calls == 1 + lost_ack
    assert worker.worker_health.state is WorkerState.DISABLED
    assert cursors.entries == cursors.retained_bytes == 0
    assert retained.entries == 1
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
    # Partition capacity stays charged until this entire grant is settled.
    assert await _outstanding(db, generation) == 4
    rows = await db.query_raw(
        "SELECT returned_operations,returned_exact::text AS returned_exact "
        "FROM deltallm_accounting_grants WHERE grant_id=$1",
        grant.grant_id,
    )
    assert rows[0]["returned_operations"] == 3
    assert Decimal(rows[0]["returned_exact"]) == Decimal(3)
    proof = issued.proofs[0].restore()
    terminal = LocalPermitFinalization(receipt=proof, finalization=_finalization(item))
    accepted = (await repository.finalize_batch([terminal], expires_at=deadline()))[0]
    assert retained.acknowledge(proof, accepted)
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("0.6"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == retained.entries == retained.retained_bytes == 0
    assert (await repository.finalize_batch([terminal], expires_at=deadline()))[0].replayed
    assert await _window(db, window) == (Decimal("0.6"), Decimal(0), Decimal(0))
