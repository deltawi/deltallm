"""Native claims and commits use indexed keys after retained source history."""

import os

import pytest

from src.db.accounting_calls import AccountingProtocolUnavailable

from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes, seed_closed_accounting_history
from tests.test_accounting_local_lease_plans_postgres import seed_event_history
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_protocol_postgres import accounting_db as _accounting_db
from tests.test_accounting_read_model_postgres import next_page, repository, source

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def retained(db, generation):
    await seed_closed_accounting_history(db, generation)
    await seed_event_history(db, generation)
    # Checkpoint history must also be large. Four fresh cells cannot prove a
    # primary-key seek, since PostgreSQL can correctly prefer their tiny heap.
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_projection_checkpoints "
        "(projection_name,protocol_name,generation,accounting_partition) "
        "SELECT 'retained-report-'||value,'primary',$1,0 "
        "FROM generate_series(1,10000) value",
        generation,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_usage_facts_v2 "
        "(event_id,accounting_sequence,protocol_generation,source_sha256,operation_id,"
        "request_id,call_type,api_key,model,spend,spend_exact,start_time,end_time,source_occurred_at) "
        "SELECT event_id,sequence,generation,sha256('history'::bytea),operation_id,"
        "operation_id,'completion','retained-key','retained-model',0,0,NOW(),NOW(),NOW() "
        "FROM deltallm_accounting_events WHERE generation=$1",
        generation,
    )
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    await db.execute_raw(
        "UPDATE deltallm_accounting_projection_checkpoints SET last_sequence="
        "(SELECT max(sequence) FROM deltallm_accounting_events WHERE generation=$1) "
        "WHERE projection_name='accounting-read-model-v2' AND generation=$1 "
        "AND accounting_partition=0",
        generation,
    )
    for relation in (
        "deltallm_accounting_events",
        "deltallm_accounting_usage_facts_v2",
        "deltallm_accounting_projection_checkpoints",
        "deltallm_billing_operations",
    ):
        await db.execute_raw(f"ANALYZE {relation}")


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
async def test_actual_native_read_model_plans_do_not_scan_retained_history(
    accounting_db,
    planner,
):
    clients, generation = accounting_db
    db = clients[0]
    await retained(db, generation)
    await source(db, generation)
    relations = {
        "deltallm_accounting_events",
        "deltallm_billing_operations",
        "deltallm_accounting_projection_checkpoints",
        "deltallm_accounting_usage_facts_v2",
    }
    observed = set()
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        defaults = [
            await captured.query_raw("SHOW enable_seqscan"),
            await captured.query_raw("SHOW enable_bitmapscan"),
        ]
        repo = repository(captured)
        await repo.initialize(generation=generation, expires_at=deadline())
        for _ in range(6):
            progress = await repo.progress(generation=generation, expires_at=deadline())
            assert progress.pending_partitions == 1
        page = await next_page(repo, generation)
        assert len(page.sequences) == 4
        assert await repo.materialize(page, expires_at=deadline()) == 4
        assert await repo.materialize(page, expires_at=deadline()) == 0
        assert defaults == [
            await captured.query_raw("SHOW enable_seqscan"),
            await captured.query_raw("SHOW enable_bitmapscan"),
        ]
    assert captured.errors == []
    for entry in captured.plans:
        for node in nodes(entry.node):
            relation = node.get("Relation Name")
            if relation in relations and node["Actual Loops"]:
                observed.add(relation)
                assert node["Node Type"] not in {"Seq Scan", "Bitmap Heap Scan"}, {
                    "relation": relation,
                    "node": node["Node Type"],
                    "rows": node["Actual Rows"],
                    "filtered": node.get("Rows Removed by Filter", 0),
                    "query_sha256": entry.safe_report()["query_sha256"],
                }
                assert node["Actual Rows"] <= 4, entry.safe_report()
                assert node["Actual Loops"] <= 64, entry.safe_report()
                assert node.get("Rows Removed by Filter", 0) <= 1, {
                    "relation": relation,
                    "node": node["Node Type"],
                    "index": node.get("Index Name"),
                    "filtered": node.get("Rows Removed by Filter", 0),
                    "loops": node["Actual Loops"],
                    "query_sha256": entry.safe_report()["query_sha256"],
                }
                assert node.get("Rows Removed by Index Recheck", 0) == 0, entry.safe_report()
    assert relations <= observed


async def test_missing_operation_key_fails_closed_without_writing_any_read_effect(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    await source(db, generation)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    with pytest.raises(AccountingProtocolUnavailable):
        async with db.tx() as transaction:
            await transaction.execute_raw(
                "ALTER INDEX deltallm_billing_operations_pkey "
                "RENAME TO issue320_isolated_missing_operation_key"
            )
            await repository(transaction).materialize(page, expires_at=deadline())
    assert await repo.materialize(page, expires_at=deadline()) == 4
