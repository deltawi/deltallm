"""Byte rejection cannot create or lose durable economic capacity."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting_protocol import ReserveDecision
from src.billing.preissued_permits import PermitSubject, PreissuedPermitBank
from src.db.accounting_permits import AccountingPermitRepository
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
from tests.test_preissued_permit_bank_postgres import deadline

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def bank(client, maximum):
    return PreissuedPermitBank(
        AccountingPermitRepository(client, owner_id="native-byte-bank", statement_budget_seconds=2),
        target_operations=4,
        max_operations=256,
        max_subjects=100_000,
        max_retained_bytes=maximum,
    )


async def test_byte_rejection_has_no_database_or_budget_effect(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    counted = CountingClient(db)
    owner = bank(counted, PermitSubject.from_reservation(item).retained_bytes - 1)
    try:
        permits = await owner.reserve_batch([item], expires_at=deadline())
        assert permits[0].decision is ReserveDecision.CAPACITY_EXHAUSTED
        assert counted.calls == 0
        assert await _window(db, window_id) == (Decimal(0), Decimal(0), Decimal(0))
        assert await _outstanding(db, generation) == 0
        assert owner.retained_bytes == 0
    finally:
        await owner.close()


async def test_live_byte_capacity_keeps_escrow_and_warm_claim_call_bound(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    first = _reservation(generation, window_id)
    second = _reservation(generation, window_id)
    denied = _reservation(generation, window_id).model_copy(
        update={"attribution": first.attribution.model_copy(update={"model": "another-model"})}
    )
    maximum = PermitSubject.from_reservation(first).retained_bytes
    counted = CountingClient(db)
    owner = bank(counted, maximum)
    try:
        permits = await owner.reserve_batch([first], expires_at=deadline())
        assert permits[0].decision is ReserveDecision.DISPATCH
        assert counted.calls == 2
        assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))
        permits = await owner.reserve_batch([denied], expires_at=deadline())
        assert permits[0].decision is ReserveDecision.CAPACITY_EXHAUSTED
        assert counted.calls == 2
        assert await _outstanding(db, generation) == 4
        permits = await owner.reserve_batch([second], expires_at=deadline())
        assert permits[0].decision is ReserveDecision.DISPATCH
        assert counted.calls == 3
        assert owner.retained_bytes == maximum
        await _repository(db).finalize_batch(
            [_finalization(first), _finalization(second)], expires_at=deadline()
        )
        await owner.close()
        assert owner.retained_bytes == 0
        assert await _settle_grants(db, generation) == 0
        assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))
        await db.execute_raw(
            "UPDATE deltallm_accounting_grants SET expires_at=NOW()-INTERVAL '1 second' "
            "WHERE generation=$1",
            generation,
        )
        assert await _settle_grants(db, generation) == 1
        assert await _window(db, window_id) == (Decimal("1.2"), Decimal(0), Decimal(0))
        assert await _outstanding(db, generation) == 0
    finally:
        await owner.close()
