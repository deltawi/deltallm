"""An empty reporting frontier must not walk retained terminal index pages."""

import os

import pytest

from src.db.accounting_read_model import AccountingReadModelRepository
from src.db.accounting_read_model_queries import RECOVER_CLAIM
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_read_model_cold_plans_postgres import cold_claim

pytestmark = pytest.mark.postgres

HISTORY_INSERT_ROWS = 10_000

TABLES = """
CREATE TEMP TABLE deltallm_accounting_protocols (
 protocol_name text,generation bigint,state text,partition_count integer,
 PRIMARY KEY(protocol_name,generation));
CREATE TEMP TABLE deltallm_accounting_projection_checkpoints
 (LIKE public.deltallm_accounting_projection_checkpoints INCLUDING ALL);
CREATE TEMP TABLE deltallm_accounting_events
 (LIKE public.deltallm_accounting_events INCLUDING ALL);
INSERT INTO deltallm_accounting_protocols VALUES ('primary',7,'active',64);
INSERT INTO deltallm_accounting_projection_checkpoints
 (projection_name,protocol_name,generation,accounting_partition)
 SELECT 'accounting-read-model-v2','primary',7,n FROM generate_series(0,63) n;
"""


async def retain(captured, start, end, analyze):
    # Keep setup inserts inside the existing five-second command deadline.
    # Retain the full history and row width before any frontier query runs.
    for first in range(start, end + 1, HISTORY_INSERT_ROWS):
        await captured._connection.execute(
            "INSERT INTO deltallm_accounting_events "
            "(protocol_name,generation,accounting_partition,sequence,event_type,payload_json,"
            "audit_envelope_json,event_id,operation_id,component_id,occurred_at) "
            "SELECT 'primary',7,(n%64)::integer,n,'finalized',"
            "jsonb_build_object('history',repeat('x',512)),'{}','event-'||n,"
            "'operation-'||n,'test',now() FROM generate_series($1::bigint,$2::bigint) n",
            first,
            min(first + HISTORY_INSERT_ROWS - 1, end),
        )
    await captured._connection.execute(
        "UPDATE deltallm_accounting_projection_checkpoints SET last_sequence=$1,"
        "lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL",
        end,
    )
    if analyze:
        await captured._connection.execute("ANALYZE deltallm_accounting_events")
        await captured._connection.execute("ANALYZE deltallm_accounting_projection_checkpoints")


async def check_frontier(captured, repo, end):
    assert await cold_claim(repo) is None
    assert (await repo.progress(generation=7, expires_at=deadline())).pending_partitions == 0
    await captured._connection.execute(
        "UPDATE deltallm_accounting_projection_checkpoints SET last_sequence=$1 "
        "WHERE accounting_partition=$2",
        end - 1,
        end % 64,
    )
    page = await cold_claim(repo)
    assert page.sequences == (end,) and page.after_sequence == end - 1
    assert page.accounting_partition == end % 64
    recovered = await captured.query_raw(
        RECOVER_CLAIM,
        "accounting-read-model-v2",
        7,
        page.worker_id,
        str(page.lease_token),
        256,
    )
    assert len(recovered) == 1 and recovered[0]["sequences"] == [end]
    assert await cold_claim(repo) is None  # The unexpired lease still fences another claim.
    assert (await repo.progress(generation=7, expires_at=deadline())).pending_partitions == 1


def assert_bounded_event_work(plans):
    observed = False
    for entry in plans:
        if not any(
            marker in entry.query
            for marker in (
                "candidates AS MATERIALIZED",
                "oldest_head_age_seconds",
                "owned AS MATERIALIZED",
            )
        ):
            continue  # Seed writes are not reporting frontier work.
        event_nodes = [
            node
            for node in nodes(entry.node)
            if node.get("Relation Name") == "deltallm_accounting_events"
        ]
        blocks = sum(
            node.get("Local Hit Blocks", 0) + node.get("Local Read Blocks", 0)
            for node in event_nodes
        )
        # Returned rows alone miss a range scan that discards index pages.
        assert blocks <= 1024, {"event_blocks": blocks, **entry.safe_report()}
        assert entry.jit_functions == 0, entry.safe_report()
        for node in event_nodes:
            if not node["Actual Loops"]:
                continue
            observed = True
            assert node["Node Type"] in {"Index Scan", "Index Only Scan"}, entry.safe_report()
            assert node["Actual Rows"] <= 1, entry.safe_report()
            assert node["Actual Loops"] <= 64, entry.safe_report()
            assert node.get("Rows Removed by Filter", 0) <= 1, entry.safe_report()
            assert node.get("Rows Removed by Index Recheck", 0) == 0, entry.safe_report()
    assert observed


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
@pytest.mark.parametrize("analyze", [False, True])
async def test_actual_frontier_seeks_bound_blocks_with_production_indexes(planner, analyze):
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provision PostgreSQL")
        pytest.skip("DATABASE_URL is required")
    async with capture_accounting_plans(url, planner=planner) as captured:
        await captured._connection.execute(TABLES)
        repo = AccountingReadModelRepository(captured, statement_budget_seconds=2)
        for _ in range(6):
            assert await cold_claim(repo) is None
            await repo.progress(generation=7, expires_at=deadline())
        previous = 0
        for end in (40000, 300000):
            await retain(captured, previous + 1, end, analyze)
            await check_frontier(captured, repo, end)
            previous = end
    assert captured.errors == []
    assert_bounded_event_work(captured.plans)
