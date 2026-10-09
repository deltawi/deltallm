"""Presence has 64 leased cells, strict fences, and bounded indexed reads."""

from decimal import Decimal
import os
from uuid import uuid4

import pytest

from src.billing.accounting_presence import ProjectionLease, ProjectionPresencePublisher
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_presence import AccountingPresenceRepository
from src.telemetry.lifecycle import WorkerState
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_presence import Processing
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _window,
    _outstanding,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def acquire(repo, generation):
    return await repo.acquire(
        generation=generation, owner_token=uuid4(), lease_seconds=10, expires_at=deadline()
    )


async def test_presence_is_fixed_sized_and_never_changes_money(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    repo = AccountingPresenceRepository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    await repo.initialize(generation=generation, expires_at=deadline())
    values = [await acquire(repo, generation) for _ in range(64)]
    assert len({value.slot for value in values}) == 64
    assert await acquire(repo, generation) is None
    assert (await repo.snapshot(generation=generation, expires_at=deadline())).ready_slots == 0
    for value in values:
        assert await repo.publish(value, ready=True, lease_seconds=10, expires_at=deadline())
    snapshot = await repo.snapshot(generation=generation, expires_at=deadline())
    assert snapshot.present_slots == snapshot.ready_slots == 64
    assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0
    with pytest.raises(Exception):
        await db.execute_raw(
            "INSERT INTO deltallm_accounting_projection_presence(generation,slot) VALUES ($1,64)",
            generation,
        )


async def test_expired_and_replaced_owner_cannot_publish_or_release_another_lease(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    repo = AccountingPresenceRepository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    original = await acquire(repo, generation)
    assert await repo.publish(original, ready=True, lease_seconds=10, expires_at=deadline())
    await db.execute_raw(
        "UPDATE deltallm_accounting_projection_presence SET expires_at=NOW()-INTERVAL '1 second' "
        "WHERE generation=$1 AND slot=$2",
        generation,
        original.slot,
    )
    assert (await repo.snapshot(generation=generation, expires_at=deadline())).ready_slots == 0
    assert not await repo.publish(original, ready=True, lease_seconds=10, expires_at=deadline())
    replacement = await acquire(repo, generation)
    assert original.slot == replacement.slot and original.owner_token != replacement.owner_token
    assert not await repo.release(original, expires_at=deadline())
    assert not await repo.publish(original, ready=True, lease_seconds=10, expires_at=deadline())
    assert await repo.publish(replacement, ready=True, lease_seconds=10, expires_at=deadline())
    wrong = ProjectionLease(
        generation=generation + 1, slot=replacement.slot, owner_token=replacement.owner_token
    )
    assert not await repo.release(wrong, expires_at=deadline())
    assert (await repo.snapshot(generation=generation, expires_at=deadline())).ready_slots == 1


async def test_publisher_needs_actual_ready_processing_and_closes_its_lease(accounting_db):
    clients, generation = accounting_db
    repo = AccountingPresenceRepository(clients[0])
    processing = Processing(WorkerState.DISABLED)
    runtime = ProjectionPresencePublisher(repo, generation=generation, processing=processing)
    await runtime.start(expires_at=deadline())
    with pytest.raises(AccountingProtocolUnavailable):
        await runtime.observe(expires_at=deadline())
    assert (await repo.snapshot(generation=generation, expires_at=deadline())).ready_slots == 0
    processing.state = WorkerState.READY
    await runtime.observe(expires_at=deadline())
    assert (await repo.snapshot(generation=generation, expires_at=deadline())).ready_slots == 1
    await runtime.close(expires_at=deadline())
    assert (await repo.snapshot(generation=generation, expires_at=deadline())).ready_slots == 0
    with pytest.raises(AccountingProtocolUnavailable):
        await repo.snapshot(generation=generation + 1, expires_at=deadline())


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
async def test_presence_actual_plan_uses_only_fixed_slot_primary_keys(accounting_db, planner):
    clients, generation = accounting_db
    db = clients[0]
    owned = list(range(generation * 100_000 + 1, generation * 100_000 + 10001))
    try:
        await db.execute_raw(
            "INSERT INTO deltallm_accounting_protocols(protocol_name,generation,writer_version,state,"
            "partition_count,max_outstanding_per_partition) SELECT 'primary',value,2,'prepared',1,1000 "
            "FROM unnest($1::bigint[]) value",
            owned,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_accounting_projection_presence(generation,slot) "
            "SELECT value,0 FROM unnest($1::bigint[]) value",
            owned,
        )
        repo = AccountingPresenceRepository(db, statement_budget_seconds=2)
        await repo.initialize(generation=generation, expires_at=deadline())
        lease = await acquire(repo, generation)
        assert await repo.publish(lease, ready=True, lease_seconds=10, expires_at=deadline())
        await db.execute_raw("ANALYZE deltallm_accounting_protocols")
        await db.execute_raw("ANALYZE deltallm_accounting_projection_presence")
        async with capture_accounting_plans(
            os.environ["DATABASE_URL"], planner=planner
        ) as captured:
            native = AccountingPresenceRepository(captured, statement_budget_seconds=2)
            for _ in range(6):
                assert (
                    await native.snapshot(generation=generation, expires_at=deadline())
                ).ready_slots == 1
        assert captured.errors == []
        observed = set()
        for entry in captured.plans:
            for node in nodes(entry.node):
                relation = node.get("Relation Name")
                if relation is None or not node["Actual Loops"]:
                    continue
                observed.add(relation)
                assert node["Node Type"] != "Seq Scan", entry.safe_report()
                assert node["Actual Rows"] <= 1 and node["Actual Loops"] <= 64, entry.safe_report()
                assert node.get("Rows Removed by Filter", 0) == 0, entry.safe_report()
                assert node.get("Rows Removed by Index Recheck", 0) == 0, entry.safe_report()
        assert observed == {
            "deltallm_accounting_protocols",
            "deltallm_accounting_projection_presence",
        }
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_protocols WHERE generation=ANY($1::bigint[])", owned
        )
