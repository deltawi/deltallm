"""Prove the inactive bank uses only funded, durable database claims."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting.accounting_protocol import ReserveDecision
from src.billing.accounting.permits.preissued_permits import PreissuedPermitBank
from tests.accounting_adapters.permit_repository import AccountingPermitRepository
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _outstanding,
    _repository,
    _reservation,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def bank(client, *, owner_id="native-permit-bank", lane=0):
    return PreissuedPermitBank(
        AccountingPermitRepository(client, owner_id=owner_id, statement_budget_seconds=2),
        target_operations=4,
        max_operations=256,
        max_subjects=16,
        lane=lane,
    )


def deadline():
    return asyncio.get_running_loop().time() + 6


async def test_hot_subject_warm_requests_keep_one_claim_call(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id, limit="100")
    counted = CountingClient(db)
    owner = bank(counted)
    items = [_reservation(generation, window_id) for _ in range(32)]
    try:
        first = await owner.reserve_batch(items[:8], expires_at=deadline())
        assert all(permit.decision is ReserveDecision.DISPATCH for permit in first)
        assert counted.calls == 2
        for item in items[8:]:
            permits = await owner.reserve_batch([item], expires_at=deadline())
            assert permits[0].decision is ReserveDecision.DISPATCH
        assert counted.calls == 26
        rows = await db.query_raw(
            "SELECT accounting_grant_id,accounting_permit_ordinal FROM deltallm_billing_operations "
            "WHERE accounting_generation=$1 ORDER BY accounting_permit_ordinal",
            generation,
        )
        assert len({row["accounting_grant_id"] for row in rows}) == 1
        assert [row["accounting_permit_ordinal"] for row in rows] == list(range(32))
        assert owner.active_subjects == owner.available_permits == 0
        await _repository(db).finalize_batch(
            [_finalization(item) for item in items], expires_at=deadline()
        )
        assert await _settle_grants(db, generation) == 1
        assert await _window(db, window_id) == (Decimal("19.2"), Decimal(0), Decimal(0))
        assert await _outstanding(db, generation) == 0
    finally:
        await owner.close()


async def test_two_banks_share_one_hard_budget_without_borrowing_ordinals(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id, limit="10")
    counted = [CountingClient(client) for client in clients]
    owners = [
        bank(client, owner_id=f"replica-{index}", lane=index)
        for index, client in enumerate(counted)
    ]
    items = [[_reservation(generation, window_id) for _ in range(4)] for _ in owners]
    try:
        first = await asyncio.gather(
            *(
                owner.reserve_batch([group[0]], expires_at=deadline())
                for owner, group in zip(owners, items, strict=True)
            )
        )
        assert all(permits[0].decision is ReserveDecision.DISPATCH for permits in first)
        assert await _window(db, window_id) == (Decimal(0), Decimal(8), Decimal(0))
        remaining = await asyncio.gather(
            *(
                owner.reserve_batch(group[1:], expires_at=deadline())
                for owner, group in zip(owners, items, strict=True)
            )
        )
        assert all(
            permit.decision is ReserveDecision.DISPATCH
            for permits in remaining
            for permit in permits
        )
        assert [client.calls for client in counted] == [3, 3]
        await _repository(db).finalize_batch(
            [_finalization(item) for group in items for item in group], expires_at=deadline()
        )
        assert await _settle_grants(db, generation) == 2
        assert await _window(db, window_id) == (Decimal("4.8"), Decimal(0), Decimal(0))
        assert await _outstanding(db, generation) == 0
    finally:
        for owner in owners:
            await owner.close()


async def test_partial_hard_budget_grant_does_not_dispatch_unfunded_work(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id, limit="2")
    counted = CountingClient(db)
    owner = bank(counted)
    items = [_reservation(generation, window_id) for _ in range(5)]
    try:
        permits = await owner.reserve_batch(items, expires_at=deadline())
        assert [permit.decision for permit in permits] == [ReserveDecision.DISPATCH] * 2 + [
            ReserveDecision.BUDGET_EXHAUSTED
        ] * 3
        assert counted.calls == 3
        assert await _window(db, window_id) == (Decimal(0), Decimal(2), Decimal(0))
        assert (
            len(
                await db.query_raw(
                    "SELECT operation_id FROM deltallm_billing_operations WHERE accounting_generation=$1",
                    generation,
                )
            )
            == 2
        )
        await _repository(db).finalize_batch(
            [_finalization(item) for item in items[:2]], expires_at=deadline()
        )
        assert await _settle_grants(db, generation) == 1
        assert await _window(db, window_id) == (Decimal("1.2"), Decimal(0), Decimal(0))
    finally:
        await owner.close()


async def test_closed_bank_leaves_claimed_work_for_conservative_crash_recovery(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    owner = bank(db)
    item = _reservation(generation, window_id)
    permits = await owner.reserve_batch([item], expires_at=deadline())
    assert permits[0].decision is ReserveDecision.DISPATCH
    await owner.close()
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET created_at=NOW()-INTERVAL '10 minutes',"
        "expires_at=NOW()-INTERVAL '1 second' "
        "WHERE operation_id=$1",
        str(item.operation_id),
    )
    rows = await db.query_raw(
        "SELECT deltallm_accounting_reconcile_expired_grants($1,16) AS recovered", generation
    )
    assert rows == [{"recovered": 1}]
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal(0), Decimal(0), Decimal(1))
    assert await _outstanding(db, generation) == 0


async def test_allowance_text_identity_matches_the_database_grant_subject(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    owner = bank(db)
    try:
        first = _reservation(generation, window_id, allowance="1")
        second = _reservation(generation, window_id, allowance="1.0")
        for item in (first, second):
            permits = await owner.reserve_batch([item], expires_at=deadline())
            assert permits[0].decision is ReserveDecision.DISPATCH
        assert owner.active_subjects == 2
        assert await _window(db, window_id) == (Decimal(0), Decimal(8), Decimal(0))
    finally:
        await owner.close()
