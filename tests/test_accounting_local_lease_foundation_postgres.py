"""Inactive local funding preserves short dispatch and conservative recovery."""

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting_protocol import AccountingOutcome
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


async def allocate(db, item, *, target=4, ttl=30, fence=None):
    fence = fence or uuid4()
    rows = await db.query_raw(
        "SELECT * FROM deltallm_accounting_allocate_local_permit_grant("
        "$1,$2,$3::uuid,$4::integer,$5::integer,$6::jsonb)",
        item.protocol_generation,
        "local-foundation:" + str(fence),
        str(fence),
        target,
        ttl,
        json.dumps(item.model_dump(mode="json")),
    )
    assert len(rows) == 1
    return rows[0]


def entry(item, grant, *, ordinal=0, outcome=AccountingOutcome.COMPLETED):
    return {
        "grant_id": grant["grant_id"],
        "grantee_id": grant["grantee_id"],
        "fence_token": grant["fence_token"],
        "permit_ordinal": ordinal,
        "reservation": item.model_dump(mode="json"),
        "finalization": _finalization(item, outcome).model_dump(mode="json"),
    }


async def finalize(db, generation, items):
    return await db.query_raw(
        "SELECT * FROM deltallm_accounting_finalize_local_permit_batch($1,$2::jsonb)",
        generation,
        json.dumps(items),
    )


async def return_suffix(db, generation, grant, ordinal):
    return await db.query_raw(
        "SELECT * FROM deltallm_accounting_return_local_permits($1,$2,$3::uuid,$4::integer)",
        generation,
        grant["grant_id"],
        str(grant["fence_token"]),
        ordinal,
    )


async def expire(db, grant):
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET created_at=NOW()-INTERVAL '10 minutes',"
        "dispatch_expires_at=NOW()-INTERVAL '2 seconds',expires_at=NOW()-INTERVAL '1 second' "
        "WHERE grant_id=$1",
        grant["grant_id"],
    )


@pytest.mark.parametrize("boundary", ["ttl", "window"])
async def test_dispatch_deadline_stays_short_while_receipt_funding_is_retained(
    accounting_db, boundary
):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    if boundary == "window":
        await db.execute_raw(
            "UPDATE deltallm_accounting_budget_windows SET window_ends_at=NOW()+INTERVAL '5 seconds' "
            "WHERE window_id=$1",
            window_id,
        )
    item = _reservation(generation, window_id)
    grant = await allocate(db, item, ttl=2 if boundary == "ttl" else 30)
    assert grant["decision"] == "dispatch"
    rows = await db.query_raw(
        "SELECT g.local_dispatch,(g.dispatch_expires_at<=w.window_ends_at) AS window_bound,"
        "(g.dispatch_expires_at<=g.created_at+INTERVAL '3 seconds') AS ttl_bound,"
        "(g.expires_at>g.dispatch_expires_at) AS retained_recovery "
        "FROM deltallm_accounting_grants g JOIN deltallm_accounting_grant_windows gw "
        "ON gw.grant_id=g.grant_id JOIN deltallm_accounting_budget_windows w "
        "ON w.window_id=gw.window_id WHERE g.grant_id=$1",
        grant["grant_id"],
    )
    assert rows[0]["local_dispatch"] is True
    assert rows[0]["window_bound"] is True
    assert rows[0]["retained_recovery"] is True
    if boundary == "ttl":
        assert rows[0]["ttl_bound"] is True
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))
    assert await _outstanding(db, generation) == 4


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (AccountingOutcome.COMPLETED, (Decimal("0.6"), Decimal(0), Decimal(0))),
        (AccountingOutcome.NOT_DISPATCHED, (Decimal(0), Decimal(0), Decimal(0))),
        (AccountingOutcome.UNCERTAIN, (Decimal(0), Decimal(0), Decimal(1))),
    ],
)
async def test_local_receipt_replay_and_unused_return_have_one_economic_effect(
    accounting_db, outcome, expected
):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    grant = await allocate(db, item)
    terminal = entry(item, grant, outcome=outcome)
    first = await finalize(db, generation, [terminal])
    assert first[0]["replayed"] is False
    assert await return_suffix(db, generation, grant, 1) == [{"returned_operations": 3}]
    assert await return_suffix(db, generation, grant, 1) == [{"returned_operations": 3}]
    assert await _settle_grants(db, generation) == 1
    replay = await finalize(db, generation, [terminal])
    assert replay[0]["replayed"] is True
    assert replay[0]["event_sequence"] == first[0]["event_sequence"]
    assert await _window(db, window_id) == expected
    assert await _outstanding(db, generation) == 0


async def test_unknown_owner_loss_keeps_full_unproven_allowance_provisional(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    grant = await allocate(db, _reservation(generation, window_id))
    assert await _settle_grants(db, generation) == 0
    await expire(db, grant)
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal(0), Decimal(0), Decimal(4))
    assert await _outstanding(db, generation) == 0
    assert await _settle_grants(db, generation) == 0


async def test_returned_suffix_cannot_be_claimed_after_return(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    grant = await allocate(db, item)
    assert await return_suffix(db, generation, grant, 1) == [{"returned_operations": 3}]
    with pytest.raises(Exception, match="accounting_permit_claim_item_shape"):
        await finalize(db, generation, [entry(item, grant, ordinal=3)])
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))
    assert await _outstanding(db, generation) == 4
    await finalize(db, generation, [entry(item, grant, ordinal=0)])
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal("0.6"), Decimal(0), Decimal(0))


async def test_terminal_receipt_can_finish_after_the_short_dispatch_deadline(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    grant = await allocate(db, item)
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET dispatch_expires_at=NOW()-INTERVAL '1 second' "
        "WHERE grant_id=$1",
        grant["grant_id"],
    )
    await finalize(db, generation, [entry(item, grant)])
    await return_suffix(db, generation, grant, 1)
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal("0.6"), Decimal(0), Decimal(0))


async def test_two_local_owners_cannot_allocate_more_than_one_shared_budget(accounting_db):
    clients, generation = accounting_db
    window_id = str(uuid4())
    await _create_window(clients[0], generation, window_id, limit="6")
    items = [_reservation(generation, window_id) for _ in clients]
    grants = await asyncio.gather(
        *(allocate(db, item) for db, item in zip(clients, items, strict=True))
    )
    assert sorted(grant["operation_limit"] for grant in grants) == [2, 4]
    assert await _window(clients[0], window_id) == (Decimal(0), Decimal(6), Decimal(0))
    await finalize(
        clients[0],
        generation,
        [entry(item, grant) for item, grant in zip(items, grants, strict=True)],
    )
    for grant in grants:
        await return_suffix(clients[0], generation, grant, 1)
    assert await _settle_grants(clients[0], generation) == 2
    assert await _window(clients[0], window_id) == (Decimal("1.2"), Decimal(0), Decimal(0))
    assert await _outstanding(clients[0], generation) == 0


async def test_zero_cost_local_suffix_return_does_not_lose_partition_slots(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id, allowance="0")
    grant = await allocate(db, item)
    await return_suffix(db, generation, grant, 1)
    terminal = entry(item, grant, outcome=AccountingOutcome.NOT_DISPATCHED)
    await finalize(db, generation, [terminal])
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal(0), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


async def test_receipt_horizon_funds_later_issues_without_extending_dispatch(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    fence = uuid4()
    grant = await allocate(db, item, fence=fence)
    rows = await db.query_raw(
        "SELECT (expires_at-dispatch_expires_at=$2::timestamptz-created_at) AS lifetime,"
        "(expires_at> $2::timestamptz) AS later_issue_funded,"
        "(expires_at<=created_at+INTERVAL '20 minutes') AS recovery_bound "
        "FROM deltallm_accounting_grants WHERE grant_id=$1",
        grant["grant_id"],
        item.expires_at.isoformat(),
    )
    assert rows == [{"lifetime": True, "later_issue_funded": True, "recovery_bound": True}]
    replay = await allocate(db, item, fence=fence)
    assert replay == grant
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))


@pytest.mark.parametrize(
    "field", ["grantee_id", "fence_token", "owner_token", "request_fingerprint"]
)
async def test_wrong_local_receipt_identity_cannot_create_a_charge(accounting_db, field):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    grant = await allocate(db, item)
    terminal = entry(item, grant)
    if field in {"grantee_id", "fence_token"}:
        terminal[field] = "another-owner" if field == "grantee_id" else str(uuid4())
    else:
        terminal["finalization"][field] = str(uuid4()) if field == "owner_token" else "0" * 64
    with pytest.raises(Exception, match="accounting_.*identity"):
        await finalize(db, generation, [terminal])
    assert await db.query_raw(
        "SELECT count(*)::integer AS operations FROM deltallm_billing_operations "
        "WHERE accounting_generation=$1",
        generation,
    ) == [{"operations": 0}]
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))


async def test_terminal_receipt_uses_the_original_expired_budget_period(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    grant = await allocate(db, item)
    await db.execute_raw(
        "UPDATE deltallm_accounting_budget_windows SET window_starts_at=NOW()-INTERVAL '10 minutes',"
        "window_ends_at=NOW()-INTERVAL '1 second' WHERE window_id=$1",
        window_id,
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET dispatch_expires_at=NOW()-INTERVAL '2 seconds' "
        "WHERE grant_id=$1",
        grant["grant_id"],
    )
    await finalize(db, generation, [entry(item, grant)])
    await return_suffix(db, generation, grant, 1)
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal("0.6"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


async def test_assigned_admission_keeps_local_fields_disabled_and_exact_settlement(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    await _repository(db).reserve_batch([item], expires_at=asyncio.get_running_loop().time() + 2)
    assert await db.query_raw(
        "SELECT local_dispatch,dispatch_expires_at,returned_operations "
        "FROM deltallm_accounting_grants WHERE generation=$1",
        generation,
    ) == [{"local_dispatch": False, "dispatch_expires_at": None, "returned_operations": 0}]
    await _repository(db).finalize_batch(
        [_finalization(item)], expires_at=asyncio.get_running_loop().time() + 2
    )
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal("0.6"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0
