"""Real local-lease bulk bounds, exact recovery, and concurrent economic effects."""

import asyncio
import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from prisma.errors import RawQueryError

from src.billing.accounting_local_leases import (
    LocalPermitFinalization,
    LocalPermitReceipt,
    LocalPermitReturn,
)
from src.billing.accounting_protocol import PreissuedPermitAllocation
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_local_leases import AccountingLocalLeaseRepository
from src.db.accounting_local_lease_results import finalization_payload
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_local_lease_foundation_postgres import finalize as raw_finalize
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _outstanding,
    _reservation,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def owner(db, *, owner_id="local-bulk-test"):
    return AccountingLocalLeaseRepository(db, owner_id=owner_id, statement_budget_seconds=2)


def deadline():
    return asyncio.get_running_loop().time() + 6


def allocation(item):
    return PreissuedPermitAllocation(reservation=item, fence_token=uuid4(), target_operations=4)


def terminal(item, grant):
    return LocalPermitFinalization(
        receipt=LocalPermitReceipt(grant=grant, permit_ordinal=0, reservation=item),
        finalization=_finalization(item),
    )


async def funded(db, generation):
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    grant = (await owner(db).allocate_batch([allocation(item)], expires_at=deadline()))[0]
    return window_id, item, grant


async def test_eight_subjects_use_three_calls_and_reconcile_exactly(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    windows, items = [], []
    for _ in range(8):
        window = str(uuid4())
        await _create_window(db, generation, window)
        windows.append(window)
        items.append(_reservation(generation, window))
    counted = CountingClient(db)
    repository = owner(counted)
    grants = await repository.allocate_batch(
        [allocation(item) for item in items], expires_at=deadline()
    )
    assert counted.calls == 1
    receipts = [terminal(item, grant) for item, grant in zip(items, grants, strict=True)]
    finalized = await repository.finalize_batch(receipts, expires_at=deadline())
    assert counted.calls == 2
    assert [result.operation_id for result in finalized] == [item.operation_id for item in items]
    assert (
        await repository.return_batch(
            [LocalPermitReturn(grant=grant, first_unused_ordinal=1) for grant in grants],
            expires_at=deadline(),
        )
        == [3] * 8
    )
    assert counted.calls == 3
    assert await _settle_grants(db, generation) == 8
    assert await _outstanding(db, generation) == 0
    for window in windows:
        assert await _window(db, window) == (Decimal("0.6"), Decimal(0), Decimal(0))


@pytest.mark.parametrize("phase", ["allocate", "return", "finalize"])
async def test_lost_bulk_ack_recovers_exact_funding_return_or_terminal_once(accounting_db, phase):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window)
    counted = CountingClient(db, lose_ack=phase == "allocate")
    repository = owner(counted)
    grant = (await repository.allocate_batch([allocation(item)], expires_at=deadline()))[0]
    assert counted.calls == (2 if phase == "allocate" else 1)
    counted.lose_ack = phase == "finalize"
    receipt = terminal(item, grant)
    result = (await repository.finalize_batch([receipt], expires_at=deadline()))[0]
    assert result.replayed is (phase == "finalize")
    counted.lose_ack = phase == "return"
    suffix = LocalPermitReturn(grant=grant, first_unused_ordinal=1)
    assert await repository.return_batch([suffix], expires_at=deadline()) == [3]
    assert counted.calls == 4
    assert await _settle_grants(db, generation) == 1
    assert await repository.return_batch([suffix], expires_at=deadline()) == [3]
    replay = (await repository.finalize_batch([receipt], expires_at=deadline()))[0]
    assert replay.replayed and replay.event_sequence == result.event_sequence
    assert await _window(db, window) == (Decimal("0.6"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


async def test_two_replicas_share_partial_funding_without_overspend(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window, limit="6")
    items = [_reservation(generation, window) for _ in range(2)]
    repositories = [
        owner(client, owner_id=f"local-bulk-{index}") for index, client in enumerate(clients)
    ]
    batches = await asyncio.gather(
        *(
            repository.allocate_batch([allocation(item)], expires_at=deadline())
            for repository, item in zip(repositories, items, strict=True)
        )
    )
    grants = [batch[0] for batch in batches]
    assert sorted(grant.operation_limit for grant in grants) == [2, 4]
    assert await _window(db, window) == (Decimal(0), Decimal(6), Decimal(0))
    await asyncio.gather(
        *(
            repository.finalize_batch([terminal(item, grant)], expires_at=deadline())
            for repository, item, grant in zip(repositories, items, grants, strict=True)
        )
    )
    await asyncio.gather(
        *(
            repository.return_batch(
                [LocalPermitReturn(grant=grant, first_unused_ordinal=1)], expires_at=deadline()
            )
            for repository, grant in zip(repositories, grants, strict=True)
        )
    )
    assert await _settle_grants(db, generation) == 2
    assert await _window(db, window) == (Decimal("1.2"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


@pytest.mark.parametrize("wrong", ["owner", "fence"])
async def test_bad_second_return_rolls_back_the_entire_bulk_effect(accounting_db, wrong):
    clients, generation = accounting_db
    db = clients[0]
    _, _, first = await funded(db, generation)
    _, _, second = await funded(db, generation)
    repository = owner(db, owner_id="different" if wrong == "owner" else "local-bulk-test")
    if wrong == "fence":
        second = second.model_copy(update={"fence_token": uuid4()})
    with pytest.raises(AccountingProtocolUnavailable):
        await repository.return_batch(
            [
                LocalPermitReturn(grant=first, first_unused_ordinal=0),
                LocalPermitReturn(grant=second, first_unused_ordinal=0),
            ],
            expires_at=deadline(),
        )
    rows = await db.query_raw(
        "SELECT returned_operations FROM deltallm_accounting_grants WHERE generation=$1", generation
    )
    assert all(row["returned_operations"] == 0 for row in rows)


async def test_database_rejects_a_receipt_beyond_its_funded_recovery_deadline(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, item, grant = await funded(db, generation)
    payload = finalization_payload(terminal(item, grant))
    payload["reservation"]["expires_at"] = (grant.expires_at + timedelta(seconds=1)).isoformat()
    with pytest.raises(RawQueryError, match="accounting_local_finalization_grant"):
        await raw_finalize(db, generation, [payload])
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
    assert (
        await db.query_raw(
            "SELECT operation_id FROM deltallm_billing_operations WHERE accounting_generation=$1",
            generation,
        )
        == []
    )


async def test_concurrent_different_terminal_time_cannot_change_the_accepted_fact(accounting_db):
    clients, generation = accounting_db
    window, item, grant = await funded(clients[0], generation)
    first = terminal(item, grant)
    changed = first.model_copy(
        update={
            "finalization": first.finalization.model_copy(
                update={"occurred_at": first.finalization.occurred_at + timedelta(seconds=1)}
            )
        }
    )
    outcomes = await asyncio.gather(
        *(
            owner(client).finalize_batch([receipt], expires_at=deadline())
            for client, receipt in zip(clients, [first, changed], strict=True)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, list) for result in outcomes) == 1
    assert sum(isinstance(result, AccountingProtocolUnavailable) for result in outcomes) == 1
    await owner(clients[0]).return_batch(
        [LocalPermitReturn(grant=grant, first_unused_ordinal=1)], expires_at=deadline()
    )
    assert await _settle_grants(clients[0], generation) == 1
    assert await _window(clients[0], window) == (Decimal("0.6"), Decimal(0), Decimal(0))


@pytest.mark.parametrize(
    "phase,missing",
    [("single", "fence"), ("single", "generation"), ("single", "ordinal"), ("bulk", "generation")],
)
async def test_null_return_identity_cannot_mark_any_capacity_unused(accounting_db, phase, missing):
    clients, generation = accounting_db
    db = clients[0]
    window, _, grant = await funded(db, generation)
    rejected_generation = None if missing == "generation" else generation
    expected = (
        "accounting_local_permit_return_identity"
        if phase == "single"
        else "accounting_local_return_batch_shape"
    )
    with pytest.raises(RawQueryError, match=expected):
        if phase == "single":
            await db.query_raw(
                "SELECT * FROM deltallm_accounting_return_local_permits($1,$2,$3::uuid,$4::integer)",
                rejected_generation,
                grant.grant_id,
                None if missing == "fence" else str(grant.fence_token),
                None if missing == "ordinal" else 0,
            )
        else:
            await db.query_raw(
                "SELECT * FROM deltallm_accounting_return_local_permits_batch($1,$2,$3::jsonb)",
                rejected_generation,
                "local-bulk-test",
                json.dumps(
                    [
                        {
                            "grant_id": grant.grant_id,
                            "fence_token": str(grant.fence_token),
                            "first_unused_ordinal": 0,
                        }
                    ]
                ),
            )
    rows = await db.query_raw(
        "SELECT returned_operations,state FROM deltallm_accounting_grants WHERE grant_id=$1",
        grant.grant_id,
    )
    assert rows == [{"returned_operations": 0, "state": "active"}]
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
