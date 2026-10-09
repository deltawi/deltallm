"""Bulk local persistence uses indexed keys with representative retained history."""

import json
import os
from uuid import uuid4

import pytest

from src.billing.accounting.permits.accounting_local_leases import LocalPermitReturn
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import (
    nodes,
    seed_closed_accounting_history,
    seed_history,
)
from tests.test_accounting_local_leases_postgres import allocation, deadline, owner, terminal
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _reservation,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def seed_event_history(db, generation):
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_events "
        "(event_id,protocol_name,generation,accounting_partition,operation_id,component_id,"
        "event_type,outcome,payload_json,audit_envelope_json,occurred_at) "
        "SELECT 'lease-history-'||$1::text||'-'||value,'primary',$1::bigint,0,"
        "'allocator-op-'||$1::text||'-'||value,'provider','finalized','not_dispatched',"
        "'{}'::jsonb,'{}'::jsonb,NOW()-INTERVAL '1 hour' FROM generate_series(1,10000) value",
        generation,
    )
    await db.execute_raw("ANALYZE deltallm_accounting_events")


async def seed_reservation_history(db, generation, window):
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_grant_windows(grant_id,window_id,allocated_exact) "
        "SELECT 'allocator-grant-'||$1::text||'-'||value,$2,0 "
        "FROM generate_series(1,10000) value",
        generation,
        window,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_reservations "
        "(operation_id,window_id,grant_id,allowance_exact) "
        "SELECT 'allocator-op-'||$1::text||'-'||value,$2,"
        "'allocator-grant-'||$1::text||'-'||value,0 FROM generate_series(1,10000) value",
        generation,
        window,
    )
    await db.execute_raw("ANALYZE deltallm_accounting_reservations")
    await db.execute_raw("ANALYZE deltallm_accounting_grant_windows")


def assert_bounded_plans(captured, phase):
    assert captured.errors == []
    observed = set()
    for entry in captured.plans:
        for node in nodes(entry.node):
            relation = node.get("Relation Name")
            if (
                relation
                in {
                    "deltallm_accounting_budget_windows",
                    "deltallm_accounting_grants",
                    "deltallm_billing_operations",
                    "deltallm_accounting_events",
                    "deltallm_accounting_reservations",
                    "deltallm_accounting_grant_windows",
                }
                and node["Actual Loops"]
            ):
                observed.add(relation)
                assert node["Node Type"] != "Seq Scan", entry.safe_report()
                assert node["Actual Rows"] <= 45, entry.safe_report()
                assert node.get("Rows Removed by Filter", 0) <= 45, entry.safe_report()
    assert "deltallm_accounting_grants" in observed
    if phase == "allocate":
        assert "deltallm_accounting_budget_windows" in observed
    if phase == "finalize":
        assert {"deltallm_billing_operations", "deltallm_accounting_events"} <= observed


@pytest.mark.parametrize("phase", ["allocate", "return", "finalize"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_nested_local_persistence_does_not_scan_retained_history(
    accounting_db, phase, explicit
):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window, limit="100000")
    items = [_reservation(generation, window, explicit_window=explicit) for _ in range(6)]
    await seed_history(db, generation, items[0])
    await seed_closed_accounting_history(db, generation)
    await seed_event_history(db, generation)
    await seed_reservation_history(db, generation, window)
    allocations = [allocation(item) for item in items]
    grants = []
    if phase != "allocate":
        grants = await owner(db).allocate_batch(allocations, expires_at=deadline())
    async with capture_accounting_plans(os.environ["DATABASE_URL"]) as captured:
        repository = owner(captured)
        for index in range(6):
            if phase == "allocate":
                result = await repository.allocate_batch(
                    [allocations[index]], expires_at=deadline()
                )
                assert result[0].operation_limit == 4
            elif phase == "return":
                assert await repository.return_batch(
                    [LocalPermitReturn(grant=grants[index], first_unused_ordinal=0)],
                    expires_at=deadline(),
                ) == [4]
            else:
                result = await repository.finalize_batch(
                    [terminal(items[index], grants[index])], expires_at=deadline()
                )
                assert result[0].replayed is False
    assert_bounded_plans(captured, phase)
    print(
        json.dumps(
            {
                "phase": phase,
                "explicit": explicit,
                "plans": [entry.safe_report() for entry in captured.plans],
            },
            sort_keys=True,
        )
    )
