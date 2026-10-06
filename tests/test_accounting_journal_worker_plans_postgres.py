"""Actual worker plans use bounded keys with retained accounting history."""

import os
from uuid import uuid4

import pytest

from src.billing.accounting_journal_claims import JournalFailure
from src.billing.accounting_protocol import AccountingOutcome
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes
from tests.test_accounting_journal_plans_postgres import seed_journal_history
from tests.test_accounting_journal_worker_postgres import pending, worker
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_local_lease_plans_postgres import (
    seed_event_history,
    seed_reservation_history,
)
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import _create_window, accounting_db as _accounting_db

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def seed_worker_history(db, generation):
    await seed_journal_history(db, generation)
    # Synthetic dead letters make payload history large without leaving old
    # entries eligible for claims. Keep their durable capacity charges visible.
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_journal SET status='failed',attempts=5,materialized_event_sequence=NULL,completed_at=NULL WHERE generation=$1",
        generation,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_terminal_payloads(journal_sequence,reservation_payload,finalization_payload) SELECT sequence,'{}','{}' FROM deltallm_accounting_terminal_journal WHERE generation=$1 AND status='failed'",
        generation,
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_protocols SET max_outstanding_per_partition=20000 WHERE generation=$1",
        generation,
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_partitions SET max_outstanding=20000 WHERE generation=$1",
        generation,
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_capacity SET max_entries=20000,pending_entries=10000,failed_entries=10000,pending_bytes=40000 WHERE generation=$1 AND accounting_partition=0",
        generation,
    )
    await db.execute_raw("ANALYZE deltallm_accounting_terminal_journal")
    await db.execute_raw("ANALYZE deltallm_accounting_terminal_payloads")
    window = str(uuid4())
    await _create_window(db, generation, window)
    await seed_event_history(db, generation)
    await seed_reservation_history(db, generation, window)


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("outcome", [AccountingOutcome.COMPLETED, AccountingOutcome.UNCERTAIN])
async def test_actual_claim_materialize_and_failure_plans_are_history_independent(
    accounting_db, planner, recover, outcome
):
    clients, generation = accounting_db
    db = clients[0]
    await seed_worker_history(db, generation)
    await pending(db, generation, outcome=outcome)
    relations = {
        "deltallm_accounting_grants",
        "deltallm_billing_operations",
        "deltallm_accounting_terminal_journal",
        "deltallm_accounting_terminal_payloads",
        "deltallm_accounting_grant_windows",
        "deltallm_accounting_events",
        "deltallm_accounting_reservations",
    }
    observed = set()
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        counted = CountingClient(captured)
        repo = worker(counted)
        for index in range(4):
            counted.lose_ack = recover
            claim = await repo.claim(
                generation=generation, worker_id=f"worker-{index}", limit=1, expires_at=deadline()
            )
            if index == 0:
                assert (
                    await repo.fail(claim, JournalFailure.PERSISTENCE, expires_at=deadline()) == 1
                )
                claim = await repo.claim(
                    generation=generation, worker_id="retry", limit=1, expires_at=deadline()
                )
            counted.lose_ack = recover
            assert await repo.materialize(claim, expires_at=deadline()) == 1
    assert captured.errors == []
    for entry in captured.plans:
        for node in nodes(entry.node):
            relation = node.get("Relation Name")
            if relation in relations and node["Actual Loops"]:
                observed.add(relation)
                assert node["Node Type"] != "Seq Scan", {
                    "query_sha256": entry.safe_report()["query_sha256"],
                    "relation": relation,
                    "rows": node["Actual Rows"],
                    "filtered": node.get("Rows Removed by Filter", 0),
                }
                assert node["Actual Rows"] <= 4, entry.safe_report()
                assert node.get("Rows Removed by Filter", 0) <= 4, entry.safe_report()
                assert node.get("Rows Removed by Index Recheck", 0) <= 4, entry.safe_report()
    if outcome is AccountingOutcome.COMPLETED:
        assert (
            relations - {"deltallm_accounting_grant_windows", "deltallm_accounting_reservations"}
            <= observed
        )
        assert "deltallm_accounting_grant_windows" not in observed
    else:
        assert relations <= observed


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
async def test_exhausted_capacity_lock_probes_only_selected_journal_keys(accounting_db, planner):
    clients, generation = accounting_db
    db = clients[0]
    await seed_worker_history(db, generation)
    _, _, _, accepted = await pending(db, generation)
    sequences = [ack.journal_sequence for ack in accepted]
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_journal SET attempts=5 WHERE sequence=ANY($1::bigint[])",
        sequences,
    )
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        claim = await worker(captured).claim(
            generation=generation, worker_id="exhausted-plan", limit=4, expires_at=deadline()
        )
    assert claim.sequences == ()
    assert captured.errors == []
    observed = False
    for entry in captured.plans:
        for node in nodes(entry.node):
            if (
                node.get("Relation Name") == "deltallm_accounting_terminal_journal"
                and node["Actual Loops"]
            ):
                observed = True
                assert node["Node Type"] != "Seq Scan", entry.safe_report()
                assert node["Actual Rows"] <= 4, entry.safe_report()
                assert node.get("Rows Removed by Filter", 0) <= 4, entry.safe_report()
                assert node.get("Rows Removed by Index Recheck", 0) <= 4, entry.safe_report()
    assert observed
    rows = await db.query_raw(
        "SELECT sum(pending_entries)::integer AS pending,sum(failed_entries)::integer AS failed "
        "FROM deltallm_accounting_terminal_capacity WHERE protocol_name='primary' AND generation=$1",
        generation,
    )
    assert rows[0]["pending"] == rows[0]["failed"] == 10004
