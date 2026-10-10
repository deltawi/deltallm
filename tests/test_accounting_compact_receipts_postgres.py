"""Compact settled proofs and compatibility holds close the same exact grant."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.billing.accounting.journal.accounting_recovery import RecoveryAction
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting.journal.accounting_journal import AccountingJournalRepository
from src.db.accounting.accounting_recovery import AccountingRecoveryRepository
from tests.test_accounting_journal_postgres import at_ordinal
from tests.test_accounting_journal_worker_postgres import worker
from tests.test_accounting_local_leases_postgres import deadline, funded
from tests.test_accounting_local_leases_postgres import allocation, owner, terminal
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


async def mixed_receipts(db, generation):
    window, item, grant = await funded(db, generation)
    outcomes = (
        AccountingOutcome.COMPLETED,
        AccountingOutcome.COMPLETED,
        AccountingOutcome.UNCERTAIN,
        AccountingOutcome.NOT_DISPATCHED,
    )
    values = []
    for ordinal, outcome in enumerate(outcomes):
        value = at_ordinal(fresh(item), grant, ordinal)
        finalization = _finalization(value.receipt.reservation, outcome)
        if ordinal == 1:
            finalization = finalization.model_copy(update={"unresolved_attempts": 1})
        values.append(value.model_copy(update={"finalization": finalization}))
    acceptance = AccountingJournalRepository(db, statement_budget_seconds=2)
    await acceptance.append_batch(values, expires_at=deadline())
    processing = worker(db)
    claim = await processing.claim(generation=generation, worker_id="mixed", expires_at=deadline())
    assert await processing.materialize(claim, expires_at=deadline()) == 4
    return window, grant, values, claim


async def test_mixed_outcomes_skip_only_complete_proven_reservations_and_replay(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, grant, values, claim = await mixed_receipts(db, generation)
    rows = await db.query_raw(
        "SELECT j.permit_ordinal,j.receipt_only,j.committed_exact::text AS committed,"
        "j.provisional_exact::text AS provisional,j.released_exact::text AS released,"
        "(SELECT count(*)::integer FROM deltallm_accounting_reservations r "
        "WHERE r.operation_id=j.operation_id) AS reservations "
        "FROM deltallm_accounting_terminal_journal j WHERE grant_id=$1 ORDER BY permit_ordinal",
        grant.grant_id,
    )
    assert [row["receipt_only"] for row in rows] == [True, False, False, False]
    assert [row["reservations"] for row in rows] == [0, 1, 1, 1]
    assert [Decimal(row["committed"]) for row in rows] == [
        Decimal(".6"),
        Decimal(".6"),
        Decimal(0),
        Decimal(0),
    ]
    assert [Decimal(row["provisional"]) for row in rows] == [
        Decimal(0),
        Decimal(".4"),
        Decimal(1),
        Decimal(0),
    ]
    assert [Decimal(row["released"]) for row in rows] == [
        Decimal(".4"),
        Decimal(0),
        Decimal(0),
        Decimal(1),
    ]
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("1.2"), Decimal(0), Decimal("1.4"))
    assert await worker(db).materialize(claim, expires_at=deadline()) == 4
    assert all(
        receipt.replayed
        for receipt in await AccountingJournalRepository(db).append_batch(
            values, expires_at=deadline()
        )
    )
    assert await _settle_grants(db, generation) == 0
    assert await _window(db, window) == (Decimal("1.2"), Decimal(0), Decimal("1.4"))


@pytest.mark.parametrize("corruption", ["compact", "reservation"])
async def test_missing_economic_basis_cannot_close_or_release_the_grant(accounting_db, corruption):
    clients, generation = accounting_db
    db = clients[0]
    window, _, values, _ = await mixed_receipts(db, generation)
    before = await _window(db, window)
    with pytest.raises(AccountingProtocolUnavailable):
        async with db.tx() as transaction:
            if corruption == "compact":
                await transaction.execute_raw(
                    "UPDATE deltallm_accounting_terminal_journal SET receipt_only=FALSE "
                    "WHERE operation_id=$1",
                    str(values[0].receipt.reservation.operation_id),
                )
            else:
                await transaction.execute_raw(
                    "DELETE FROM deltallm_accounting_reservations WHERE operation_id=$1",
                    str(values[1].receipt.reservation.operation_id),
                )
            await AccountingRecoveryRepository(transaction, statement_budget_seconds=2).recover(
                RecoveryAction.SETTLE_GRANTS,
                generation=generation,
                limit=256,
                expires_at=deadline(),
            )
    assert await _window(db, window) == before
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("1.2"), Decimal(0), Decimal("1.4"))


async def test_compact_and_direct_receipts_charge_every_budget_scope_once(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    organization_window = str(uuid4())
    await _create_window(db, generation, organization_window)
    item = _reservation(generation, organization_window, explicit_window=False)
    windows = [organization_window]
    attribution = item.attribution
    for scope, identifier in (
        ("api_key", attribution.api_key),
        ("user", attribution.user_id),
        ("team", attribution.team_id),
        ("team_model", f"{attribution.team_id}:{attribution.model}"),
    ):
        window_id = str(uuid4())
        await db.execute_raw(
            "INSERT INTO deltallm_accounting_budget_windows "
            "(window_id,protocol_name,generation,scope_type,scope_id,period_key,"
            "policy_generation,limit_exact,window_starts_at,window_ends_at) "
            "VALUES ($1,'primary',$2,$3,$4,$1,1,10,NOW()-INTERVAL '1 minute',"
            "NOW()+INTERVAL '1 hour')",
            window_id,
            generation,
            scope,
            identifier,
        )
        windows.append(window_id)
    repository = owner(db)
    grant = (await repository.allocate_batch([allocation(item)], expires_at=deadline()))[0]
    # A direct compatibility receipt and a compact journal receipt share one grant.
    direct = terminal(item, grant)
    await repository.finalize_batch([direct], expires_at=deadline())
    compact = at_ordinal(fresh(item), grant, 1)
    await AccountingJournalRepository(db).append_batch([compact], expires_at=deadline())
    processing = worker(db)
    claim = await processing.claim(
        generation=generation, worker_id="five-scopes", expires_at=deadline()
    )
    assert await processing.materialize(claim, expires_at=deadline()) == 1
    from src.billing.accounting.permits.accounting_local_leases import LocalPermitReturn

    assert await repository.return_batch(
        [LocalPermitReturn(grant=grant, first_unused_ordinal=2)], expires_at=deadline()
    ) == [2]
    assert await _settle_grants(db, generation) == 1
    for window in windows:
        assert await _window(db, window) == (Decimal("1.2"), Decimal(0), Decimal(0))
    assert (await repository.finalize_batch([direct], expires_at=deadline()))[0].replayed
    assert (await AccountingJournalRepository(db).append_batch([compact], expires_at=deadline()))[
        0
    ].replayed
    assert await _settle_grants(db, generation) == 0
    for window in windows:
        assert await _window(db, window) == (Decimal("1.2"), Decimal(0), Decimal(0))


async def test_zero_cost_compact_receipt_retains_capacity_and_closes_exactly(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window, allowance="0")
    grant = (await owner(db).allocate_batch([allocation(item)], expires_at=deadline()))[0]
    value = at_ordinal(item, grant, 0)
    value = value.model_copy(
        update={"finalization": value.finalization.model_copy(update={"exact_charge": Decimal(0)})}
    )
    await AccountingJournalRepository(db).append_batch([value], expires_at=deadline())
    processing = worker(db)
    claim = await processing.claim(
        generation=generation, worker_id="zero-compact", expires_at=deadline()
    )
    assert await processing.materialize(claim, expires_at=deadline()) == 1
    from src.billing.accounting.permits.accounting_local_leases import LocalPermitReturn

    await owner(db).return_batch(
        [LocalPermitReturn(grant=grant, first_unused_ordinal=1)], expires_at=deadline()
    )
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(0))
    rows = await db.query_raw(
        "SELECT receipt_only, committed_exact::text AS committed "
        "FROM deltallm_accounting_terminal_journal WHERE grant_id=$1",
        grant.grant_id,
    )
    assert rows[0]["receipt_only"] is True and Decimal(rows[0]["committed"]) == 0
