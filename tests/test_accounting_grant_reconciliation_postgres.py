"""Grant-only reconciliation preserves known per-operation debits."""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from prisma.errors import RawQueryError

from src.billing.accounting.accounting_protocol import (
    AccountingOutcome,
    AccountingScope,
    BudgetWindowRef,
)
from src.db.accounting.journal.accounting_journal import AccountingJournalRepository
from tests.test_accounting_journal_postgres import at_ordinal
from tests.test_accounting_journal_worker_postgres import worker
from tests.test_accounting_local_leases_postgres import allocation, deadline, owner
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _reservation,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def release(db, generation, grant_id, amount="3", evidence="Reviewed provider statement"):
    (result,) = await db.query_raw(
        "SELECT deltallm_accounting_release_grant_hold($1,$2,$3::numeric,$4)::text AS amount",
        generation,
        grant_id,
        amount,
        evidence,
    )
    return Decimal(result["amount"])


async def test_reviewed_grant_hold_releases_every_scope_once(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    organization_window, team_window = str(uuid4()), str(uuid4())
    item = _reservation(generation, organization_window)
    await _create_window(db, generation, organization_window)
    await _create_window(db, generation, team_window)
    await db.execute_raw(
        "UPDATE deltallm_accounting_budget_windows SET scope_type='team',scope_id=$2 WHERE window_id=$1",
        team_window,
        item.attribution.team_id,
    )
    item = item.model_copy(
        update={
            "windows": (
                *item.windows,
                BudgetWindowRef(
                    window_id=UUID(team_window),
                    scope_type=AccountingScope.TEAM,
                    scope_id=item.attribution.team_id,
                    policy_generation=1,
                ),
            )
        }
    )
    grant = (await owner(db).allocate_batch([allocation(item)], expires_at=deadline()))[0]
    try:
        with pytest.raises(RawQueryError, match="accounting_grant_reconciliation_state"):
            await release(db, generation, grant.grant_id)
        value = at_ordinal(item, grant, 0).model_copy(
            update={"finalization": _finalization(item, AccountingOutcome.UNCERTAIN)}
        )
        await AccountingJournalRepository(db).append_batch([value], expires_at=deadline())
        repository = worker(db)
        claim = await repository.claim(
            generation=generation, worker_id="reconcile", expires_at=deadline()
        )
        assert await repository.materialize(claim, expires_at=deadline()) == 1
        await db.execute_raw(
            "UPDATE deltallm_accounting_grants SET dispatch_expires_at=NOW()-INTERVAL '2 seconds',"
            "expires_at=NOW()-INTERVAL '1 second' WHERE grant_id=$1",
            grant.grant_id,
        )
        assert await _settle_grants(db, generation) == 1
        for window in (organization_window, team_window):
            assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(4))
        with pytest.raises(RawQueryError, match="accounting_grant_reconciliation_amount"):
            await release(db, generation, grant.grant_id, amount="4")
        await db.execute_raw(
            "UPDATE deltallm_accounting_budget_windows SET provisional_exact=0 WHERE window_id=$1",
            team_window,
        )
        with pytest.raises(RawQueryError, match="accounting_grant_reconciliation_window_drift"):
            await release(db, generation, grant.grant_id)
        assert await _window(db, organization_window) == (Decimal(0), Decimal(0), Decimal(4))
        await db.execute_raw(
            "UPDATE deltallm_accounting_budget_windows SET provisional_exact=4 WHERE window_id=$1",
            team_window,
        )

        assert await asyncio.gather(
            *(release(client, generation, grant.grant_id) for client in clients)
        ) == [Decimal(3), Decimal(3)]
        with pytest.raises(RawQueryError, match="accounting_grant_reconciliation_conflict"):
            await release(db, generation, grant.grant_id, evidence="Different evidence")
        for window in (organization_window, team_window):
            assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(1))
        (audit,) = await db.query_raw(
            "SELECT released_exact::text AS amount,evidence_reason,reconciled_by "
            "FROM deltallm_accounting_grant_reconciliations WHERE grant_id=$1",
            grant.grant_id,
        )
        assert Decimal(audit["amount"]) == Decimal(3)
        assert audit["evidence_reason"] == "Reviewed provider statement" and audit["reconciled_by"]
    finally:
        # Only this disposable fixture removes its audit entry to tear down the grant.
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_grant_reconciliations WHERE grant_id=$1",
            grant.grant_id,
        )
