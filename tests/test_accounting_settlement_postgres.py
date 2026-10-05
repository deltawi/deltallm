"""Recovery limits inspected grants without changing conservative money effects."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from tests.test_accounting_local_leases_postgres import allocation, deadline, owner
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _outstanding,
    _reservation,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def seed_grants(db, generation, window, *, count, blocked=False, prefix=None):
    prefix = prefix or str(uuid4())
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_grants(grant_id,protocol_name,generation,"
        "grantee_id,subject_key,accounting_partition,state,dispatch_mode,local_dispatch,"
        "dispatch_expires_at,fence_token,unit_allowance_exact,allocated_exact,operation_limit,expires_at) "
        "SELECT $3||'-'||value,'primary',$1,'recovery-fixture',repeat('a',64),0,"
        "CASE WHEN $4::boolean THEN 'draining' ELSE 'active' END,'preissued',TRUE,"
        "NOW()-INTERVAL '3 seconds',gen_random_uuid(),1,1,1,"
        "NOW()-CASE WHEN $4::boolean THEN INTERVAL '2 seconds' ELSE INTERVAL '1 second' END "
        "FROM generate_series(1,$2::integer) value",
        generation,
        count,
        prefix,
        blocked,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_grant_windows(grant_id,window_id,allocated_exact) SELECT grant_id,$2,1 FROM deltallm_accounting_grants WHERE generation=$1 AND grant_id LIKE $3||'-%'",
        generation,
        window,
        prefix,
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_budget_windows SET reserved_exact=reserved_exact+$2::integer WHERE window_id=$1",
        window,
        count,
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_partitions SET outstanding_count=outstanding_count+$2::integer WHERE generation=$1 AND partition_id=0",
        generation,
        count,
    )
    if blocked:
        await db.execute_raw(
            "INSERT INTO deltallm_accounting_terminal_capacity(generation,accounting_partition,max_entries,pending_entries,pending_bytes,failed_entries) VALUES ($1,0,1000,$2::integer,$2::integer*4,$2::integer)",
            generation,
            count,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_accounting_terminal_journal(generation,operation_id,grant_id,grantee_id,fence_token,permit_ordinal,accounting_partition,allowance_exact,outcome,reservation_sha256,finalization_sha256,payload_bytes,status,attempts) SELECT generation,grant_id,grant_id,grantee_id,fence_token,0,0,1,'uncertain',sha256('reservation'::bytea),sha256('finalization'::bytea),4,'failed',5 FROM deltallm_accounting_grants WHERE generation=$1 AND grant_id LIKE $2||'-%'",
            generation,
            prefix,
        )


async def settle(db, generation, limit):
    rows = await db.query_raw(
        "SELECT deltallm_accounting_reconcile_grants($1,$2::integer) AS count", generation, limit
    )
    return rows[0]["count"]


async def create_window(db, generation):
    window = str(uuid4())
    await _create_window(db, generation, window, limit="100000")
    return window


@pytest.mark.parametrize("limit", [1, 4, 256])
async def test_expiry_and_close_each_have_a_candidate_cap_and_exact_owner_loss_balances(
    accounting_db, limit
):
    clients, generation = accounting_db
    db = clients[0]
    window = await create_window(db, generation)
    await seed_grants(db, generation, window, count=300)
    assert await settle(db, generation, limit) == limit
    states = await db.query_raw(
        "SELECT state,count(*)::integer AS count FROM deltallm_accounting_grants WHERE generation=$1 GROUP BY state",
        generation,
    )
    assert {row["state"]: row["count"] for row in states} == {
        "active": 300 - limit,
        "closed": limit,
    }
    assert await _window(db, window) == (Decimal(0), Decimal(300 - limit), Decimal(limit))
    assert await _outstanding(db, generation) == 300 - limit
    next_count = min(limit, 300 - limit)
    assert await settle(db, generation, limit) == next_count
    assert await _window(db, window) == (
        Decimal(0),
        Decimal(300 - limit - next_count),
        Decimal(limit + next_count),
    )


@pytest.mark.parametrize("reset_cursor", [False, True])
async def test_blocked_prefix_cannot_starve_later_work_and_cursor_loss_cannot_release_money(
    accounting_db, reset_cursor
):
    clients, generation = accounting_db
    db = clients[0]
    window = await create_window(db, generation)
    await seed_grants(db, generation, window, count=8, blocked=True)
    await seed_grants(db, generation, window, count=1)
    assert [await settle(db, generation, 2) for _ in range(3)] == [0, 0, 0]
    assert await _window(db, window) == (Decimal(0), Decimal(9), Decimal(0))
    if reset_cursor:
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_recovery_cursors WHERE protocol_name='primary' AND generation=$1",
            generation,
        )
        expected = [0, 0, 0, 0, 1]
    else:
        expected = [0, 1]
    assert [await settle(db, generation, 2) for _ in expected] == expected
    assert await _window(db, window) == (Decimal(0), Decimal(8), Decimal(1))
    assert await _outstanding(db, generation) == 8
    rows = await db.query_raw(
        "SELECT count(*)::integer AS count FROM deltallm_accounting_terminal_journal WHERE generation=$1 AND status='failed'",
        generation,
    )
    assert rows[0]["count"] == 8


async def test_two_recovery_owners_and_foreground_funding_do_not_double_close_or_overspend(
    accounting_db,
):
    clients, generation = accounting_db
    db = clients[0]
    window = await create_window(db, generation)
    await seed_grants(db, generation, window, count=32)
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_recovery_cursors(protocol_name,generation) VALUES ('primary',$1)",
        generation,
    )
    results = await asyncio.gather(
        settle(clients[0], generation, 4),
        settle(clients[1], generation, 4),
        owner(db).allocate_batch(
            [allocation(_reservation(generation, window))], expires_at=deadline()
        ),
    )
    closed = results[0] + results[1]
    assert 0 < closed <= 8 and results[2][0].operation_limit == 4
    assert await _window(db, window) == (Decimal(0), Decimal(36 - closed), Decimal(closed))
    assert await _outstanding(db, generation) == 36 - closed
    for _ in range(8):
        values = await asyncio.gather(*(settle(client, generation, 4) for client in clients))
        closed += sum(values)
    assert closed == 32
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(32))
    assert await _outstanding(db, generation) == 4


async def test_locked_cursor_skips_recovery_and_keeps_charges_for_the_next_owner(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window = await create_window(db, generation)
    await seed_grants(db, generation, window, count=4)
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_recovery_cursors(protocol_name,generation) VALUES ('primary',$1)",
        generation,
    )
    async with db.tx() as tx:
        await tx.query_raw(
            "SELECT * FROM deltallm_accounting_recovery_cursors WHERE protocol_name='primary' AND generation=$1 FOR UPDATE",
            generation,
        )
        assert await settle(clients[1], generation, 4) == 0
        assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
    assert await settle(clients[1], generation, 4) == 4
    assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(4))


async def test_unknown_generation_retains_the_previous_empty_recovery_contract(accounting_db):
    clients, _ = accounting_db
    assert await settle(clients[0], -1, 4) == 0
