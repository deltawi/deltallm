"""Actual nested append and recovery plans stay independent of retained history."""

import os
from uuid import uuid4

import pytest

from src.db.accounting_journal import AccountingJournalRepository
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes, seed_closed_accounting_history
from tests.test_accounting_local_leases_postgres import allocation, deadline, owner, terminal
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _reservation,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def seed_journal_history(db, generation):
    await seed_closed_accounting_history(db, generation)
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_terminal_capacity(generation,accounting_partition,max_entries) VALUES ($1,0,1000)",
        generation,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_terminal_journal(generation,operation_id,grant_id,grantee_id,fence_token,permit_ordinal,accounting_partition,allowance_exact,outcome,reservation_sha256,finalization_sha256,payload_bytes,status,materialized_event_sequence,completed_at) "
        "SELECT $1,'journal-history-'||$1::text||'-'||value,'allocator-grant-'||$1::text||'-'||value,'history',gen_random_uuid(),0,0,0,'not_dispatched',sha256('reservation'::bytea),sha256('finalization'::bytea),4,'completed',1,NOW() FROM generate_series(1,10000) value",
        generation,
    )
    await db.execute_raw("ANALYZE deltallm_accounting_terminal_journal")


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
@pytest.mark.parametrize("recover", [False, True])
async def test_nested_terminal_append_and_recovery_key_probes(accounting_db, planner, recover):
    clients, generation = accounting_db
    db = clients[0]
    await seed_journal_history(db, generation)
    window = str(uuid4())
    await _create_window(db, generation, window, limit="100000")
    items = [_reservation(generation, window) for _ in range(6)]
    grants = await owner(db).allocate_batch(
        [allocation(item) for item in items], expires_at=deadline()
    )
    relations = {
        "deltallm_accounting_grants",
        "deltallm_billing_operations",
        "deltallm_accounting_terminal_journal",
    }
    observed = set()
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        counted = CountingClient(captured)
        repository = AccountingJournalRepository(counted, statement_budget_seconds=2)
        for item, grant in zip(items, grants, strict=True):
            counted.lose_ack = recover
            result = await repository.append_batch([terminal(item, grant)], expires_at=deadline())
            assert result[0].replayed is recover
    assert counted.calls == 6 * (1 + recover)
    assert captured.errors == []
    for entry in captured.plans:
        for node in nodes(entry.node):
            relation = node.get("Relation Name")
            if relation in relations and node["Actual Loops"]:
                observed.add(relation)
                assert node["Node Type"] != "Seq Scan", entry.safe_report()
                assert node["Actual Rows"] <= 1, entry.safe_report()
                assert node.get("Rows Removed by Filter", 0) <= 1, entry.safe_report()
                assert node.get("Rows Removed by Index Recheck", 0) <= 1, entry.safe_report()
    assert relations <= observed
