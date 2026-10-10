"""Recovery inspects a fixed indexed prefix with retained blocked history."""

import os
from uuid import uuid4

import pytest

from src.billing.accounting.permits.accounting_local_leases import LocalPermitReturn
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes
from tests.test_accounting_journal_plans_postgres import seed_journal_history
from tests.test_accounting_local_leases_postgres import allocation, deadline, owner
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _outstanding,
    _reservation,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
async def test_actual_settlement_scans_are_bounded_before_eligibility(accounting_db, planner):
    clients, generation = accounting_db
    db = clients[0]
    await seed_journal_history(db, generation)
    await db.execute_raw(
        "UPDATE deltallm_accounting_protocols SET max_outstanding_per_partition=20000 WHERE generation=$1",
        generation,
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_partitions SET max_outstanding=20000,outstanding_count=CASE WHEN partition_id=0 THEN 10000 ELSE 0 END WHERE generation=$1",
        generation,
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_capacity SET max_entries=20000,pending_entries=10000,pending_bytes=40000,failed_entries=10000 WHERE generation=$1 AND accounting_partition=0",
        generation,
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_journal SET status='failed',attempts=5,materialized_event_sequence=NULL,completed_at=NULL WHERE generation=$1",
        generation,
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET state='draining',reconciled_at=NULL WHERE generation=$1 AND grant_id LIKE 'allocator-grant-%'",
        generation,
    )
    await db.execute_raw("ANALYZE deltallm_accounting_grants")
    await db.execute_raw("ANALYZE deltallm_accounting_terminal_journal")
    window = str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window)
    grant = (await owner(db).allocate_batch([allocation(item)], expires_at=deadline()))[0]
    assert await owner(db).return_batch(
        [LocalPermitReturn(grant=grant, first_unused_ordinal=0)], expires_at=deadline()
    ) == [4]
    before = await _outstanding(db, generation)
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        for _ in range(6):
            rows = await captured.query_raw(
                "SELECT deltallm_accounting_reconcile_grants($1,4) AS count", generation
            )
            assert rows[0]["count"] == 0
    assert await _outstanding(db, generation) == before
    assert captured.errors == []
    observed = False
    for entry in captured.plans:
        for node in nodes(entry.node):
            if node.get("Relation Name") == "deltallm_accounting_grants" and node["Actual Loops"]:
                observed = True
                assert node["Node Type"] != "Seq Scan", entry.safe_report()
                assert node["Actual Rows"] <= 4, entry.safe_report()
                assert node.get("Rows Removed by Filter", 0) <= 4, entry.safe_report()
                assert node.get("Rows Removed by Index Recheck", 0) <= 4, entry.safe_report()
    assert observed
