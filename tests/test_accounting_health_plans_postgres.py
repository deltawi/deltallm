"""Health reads bounded keys even when settled receipts and metadata grow."""

import os

import pytest

from src.db.accounting_health import AccountingBacklogRepository
from src.db.accounting_calls import AccountingProtocolUnavailable
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes
from tests.test_accounting_journal_plans_postgres import seed_journal_history
from tests.test_accounting_journal_worker_postgres import pending
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_protocol_postgres import accounting_db as _accounting_db

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def seed_metadata(db, generation):
    first = generation * 100_000
    owned = list(range(first + 1, first + 10001))
    async with db.tx() as tx:
        await tx.execute_raw(
            "INSERT INTO deltallm_accounting_protocols(protocol_name,generation,writer_version,state,partition_count,max_outstanding_per_partition) SELECT 'primary',value,2,'prepared',1,1000 FROM unnest($1::bigint[]) value",
            owned,
        )
        await tx.execute_raw(
            "INSERT INTO deltallm_accounting_partitions(protocol_name,generation,partition_id,max_outstanding) SELECT 'primary',value,0,1000 FROM unnest($1::bigint[]) value",
            owned,
        )
        await tx.execute_raw(
            "INSERT INTO deltallm_accounting_terminal_capacity(protocol_name,generation,accounting_partition,max_entries) SELECT 'primary',value,0,1000 FROM unnest($1::bigint[]) value",
            owned,
        )
    return owned


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
async def test_actual_terminal_health_plan_does_not_scan_receipt_or_generation_history(
    accounting_db, planner
):
    clients, generation = accounting_db
    db = clients[0]
    await seed_journal_history(db, generation)
    owned = await seed_metadata(db, generation)
    try:
        await pending(db, generation)
        for relation in (
            "deltallm_accounting_protocols",
            "deltallm_accounting_partitions",
            "deltallm_accounting_terminal_capacity",
            "deltallm_accounting_terminal_journal",
        ):
            await db.execute_raw(f"ANALYZE {relation}")
        async with capture_accounting_plans(
            os.environ["DATABASE_URL"], planner=planner
        ) as captured:
            repo = AccountingBacklogRepository(captured, statement_budget_seconds=2)
            defaults = [
                await captured.query_raw("SHOW enable_seqscan"),
                await captured.query_raw("SHOW enable_bitmapscan"),
            ]
            for _ in range(6):
                value = await repo.snapshot(generation=generation, expires_at=deadline())
                assert value.pending_entries == value.outstanding_operations == 4
            assert defaults == [
                await captured.query_raw("SHOW enable_seqscan"),
                await captured.query_raw("SHOW enable_bitmapscan"),
            ]
        assert captured.errors == []
        observed = set()
        for entry in captured.plans:
            for node in nodes(entry.node):
                relation = node.get("Relation Name")
                if relation is None or not node["Actual Loops"]:
                    continue
                observed.add(relation)
                assert node["Node Type"] != "Seq Scan", entry.safe_report()
                assert node["Actual Rows"] <= 1, entry.safe_report()
                assert node["Actual Loops"] <= 64, entry.safe_report()
                assert node.get("Rows Removed by Filter", 0) == 0, entry.safe_report()
                assert node.get("Rows Removed by Index Recheck", 0) == 0, entry.safe_report()
        assert observed == {
            "pg_index",
            "deltallm_accounting_protocols",
            "deltallm_accounting_partitions",
            "deltallm_accounting_terminal_capacity",
            "deltallm_accounting_terminal_journal",
        }
    finally:
        for relation in (
            "deltallm_accounting_terminal_capacity",
            "deltallm_accounting_partitions",
            "deltallm_accounting_protocols",
        ):
            await db.execute_raw(
                f"DELETE FROM {relation} WHERE generation=ANY($1::bigint[])", owned
            )


async def test_missing_queue_head_index_fails_closed_and_transaction_restores_it(
    accounting_db,
):
    clients, generation = accounting_db
    db = clients[0]
    # Only this isolated test transaction loses the index. Error unwind restores it.
    with pytest.raises(AccountingProtocolUnavailable):
        async with db.tx() as transaction:
            await transaction.execute_raw("DROP INDEX deltallm_accounting_terminal_oldest_work_idx")
            await AccountingBacklogRepository(transaction, statement_budget_seconds=2).snapshot(
                generation=generation, expires_at=deadline()
            )
    restored = await db.query_raw(
        "SELECT indisvalid AND indisready AS usable FROM pg_index "
        "WHERE indexrelid='deltallm_accounting_terminal_oldest_work_idx'::regclass"
    )
    assert restored == [{"usable": True}]
    snapshot = await AccountingBacklogRepository(db, statement_budget_seconds=2).snapshot(
        generation=generation, expires_at=deadline()
    )
    assert snapshot.sampled_drained
