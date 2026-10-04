"""Prove dormant permit schema safety before the runtime can select it."""

import asyncio
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from prisma.errors import RawQueryError

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


async def allocate(db, reservation, *, owner, fence, target=4):
    rows = await db.query_raw(
        "SELECT * FROM deltallm_accounting_allocate_permit_grant("
        "$1,$2,$3::uuid,$4::integer,30,$5::jsonb)",
        reservation.protocol_generation,
        owner,
        str(fence),
        target,
        reservation.model_dump_json(),
    )
    assert len(rows) == 1
    return rows[0]


async def claim(db, generation, grant, reservations, *, ordinals=None, owner=None, fence=None):
    values = []
    for ordinal, reservation in zip(
        ordinals if ordinals is not None else range(len(reservations)), reservations, strict=True
    ):
        value = reservation.model_dump(mode="json")
        value["permit_ordinal"] = ordinal
        values.append(value)
    return await db.query_raw(
        "SELECT * FROM deltallm_accounting_claim_permit_batch($1,$2,$3,$4::uuid,$5::jsonb)",
        generation,
        owner or grant["grantee_id"],
        grant["grant_id"],
        fence or str(grant["fence_token"]),
        json.dumps(values),
    )


async def test_existing_runtime_keeps_assigned_grants_without_permit_identity(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    await _repository(db, target_operations=4).reserve_batch(
        [item], expires_at=asyncio.get_running_loop().time() + 2
    )
    assert await db.query_raw(
        "SELECT dispatch_mode,fence_token,unit_allowance_exact "
        "FROM deltallm_accounting_grants WHERE generation=$1",
        generation,
    ) == [{"dispatch_mode": "assigned", "fence_token": None, "unit_allowance_exact": None}]
    assert await db.query_raw(
        "SELECT accounting_permit_ordinal,accounting_grant_fence_token "
        "FROM deltallm_billing_operations WHERE operation_id=$1",
        str(item.operation_id),
    ) == [{"accounting_permit_ordinal": None, "accounting_grant_fence_token": None}]


async def test_fenced_permit_replay_and_settlement_have_one_economic_effect(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id, limit="5")
    items = [_reservation(generation, window_id) for _ in range(2)]
    grant = await allocate(db, items[0], owner="permit-test-owner", fence=uuid4())
    assert grant["decision"] == "dispatch"
    assert grant["operation_limit"] == 4
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))
    first = await claim(db, generation, grant, items)
    assert {row["operation_id"] for row in first} == {str(item.operation_id) for item in items}
    assert all(row["decision"] == "dispatch" and row["dispatch_token"] for row in first)
    replay = await claim(db, generation, grant, items)
    assert all(row["decision"] == "replay" and row["dispatch_token"] is None for row in replay)
    repository = _repository(db)
    terminals = [_finalization(item) for item in items]
    await repository.finalize_batch(terminals, expires_at=asyncio.get_running_loop().time() + 2)
    repeated = await repository.finalize_batch(
        terminals, expires_at=asyncio.get_running_loop().time() + 2
    )
    assert all(receipt.replayed for receipt in repeated)
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET expires_at=NOW()-INTERVAL '1 second' "
        "WHERE grant_id=$1",
        grant["grant_id"],
    )
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal("1.2"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


@pytest.mark.parametrize("mismatch", ["owner", "fence", "ordinal"])
async def test_permit_owner_fence_and_ordinal_cannot_be_reused(accounting_db, mismatch):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    grant = await allocate(db, item, owner="permit-test-owner", fence=uuid4())
    await claim(db, generation, grant, [item])
    kwargs = {}
    expected = "accounting_permit_grant_unavailable"
    if mismatch == "owner":
        kwargs["owner"] = "another-owner"
    elif mismatch == "fence":
        kwargs["fence"] = str(uuid4())
    else:
        expected = "accounting_permit_ordinal_conflict"
    with pytest.raises(RawQueryError, match=expected):
        await claim(db, generation, grant, [_reservation(generation, window_id)], **kwargs)
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))


async def test_two_replicas_cannot_allocate_past_one_hard_budget(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id, limit="5")
    item = _reservation(generation, window_id)
    grants = await asyncio.gather(
        *(
            allocate(client, item, owner=f"permit-owner-{index}", fence=uuid4())
            for index, client in enumerate(clients)
        )
    )
    assert all(grant["decision"] == "dispatch" for grant in grants)
    assert sum(grant["operation_limit"] for grant in grants) == 5
    assert await _window(db, window_id) == (Decimal(0), Decimal(5), Decimal(0))


async def test_expiry_releases_a_grant_that_never_dispatched_provider_work(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    grant = await allocate(
        db, _reservation(generation, window_id), owner="permit-test-owner", fence=uuid4()
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET expires_at=NOW()-INTERVAL '1 second' "
        "WHERE grant_id=$1",
        grant["grant_id"],
    )
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal(0), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0
