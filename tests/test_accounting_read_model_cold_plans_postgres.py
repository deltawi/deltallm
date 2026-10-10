"""Cold cached claims seek beyond finalized history without statistics refresh."""

import os
from uuid import uuid4

import asyncpg
import pytest

from src.db.accounting.reporting.accounting_read_model import AccountingReadModelRepository
from src.db.accounting.reporting.accounting_read_model_queries import CLAIM, PROGRESS, RECOVER_CLAIM
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes
from tests.test_accounting_local_leases_postgres import deadline

pytestmark = pytest.mark.postgres

# Connection-owned tables shadow the real relations and disappear on close.
# They preserve the exact claim's column/key types, without writing financial sinks.
COLD_TABLES = """
CREATE TEMP TABLE deltallm_accounting_protocols (
 protocol_name text,generation bigint,state text,partition_count integer,
 PRIMARY KEY(protocol_name,generation));
CREATE TEMP TABLE deltallm_accounting_projection_checkpoints (
 projection_name text,protocol_name text,generation bigint,accounting_partition integer,
 last_sequence bigint DEFAULT 0,lease_owner text,lease_token text,lease_expires_at timestamptz,
 last_error_code text,updated_at timestamptz DEFAULT now(),
 PRIMARY KEY(projection_name,protocol_name,generation,accounting_partition));
CREATE TEMP TABLE deltallm_accounting_events (
 protocol_name text,generation bigint,accounting_partition integer,sequence bigint,event_type text,
 payload_json jsonb,audit_envelope_json jsonb,created_at timestamptz DEFAULT now(),
 PRIMARY KEY(protocol_name,generation,accounting_partition,sequence));
CREATE INDEX cold_terminal_keys ON deltallm_accounting_events
 (protocol_name,generation,accounting_partition,sequence)
 WHERE event_type IN ('finalized','reconciled');
INSERT INTO deltallm_accounting_protocols VALUES ('primary',7,'active',64);
INSERT INTO deltallm_accounting_projection_checkpoints
 (projection_name,protocol_name,generation,accounting_partition)
 SELECT 'accounting-read-model-v2','primary',7,n FROM generate_series(0,63) n;
"""


async def cold_claim(repo):
    return await repo.claim(
        generation=7,
        worker_id="cold-plan",
        limit=256,
        lease_seconds=30,
        expires_at=deadline(),
    )


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
@pytest.mark.parametrize("analyzed_empty", [False, True])
@pytest.mark.parametrize("other_generation", [False, True])
async def test_cached_claim_does_not_join_all_finalized_history_before_analyze(
    planner, analyzed_empty, other_generation
):
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provision PostgreSQL")
        pytest.skip("DATABASE_URL is required")
    async with capture_accounting_plans(url, planner=planner) as captured:
        await captured._connection.execute(COLD_TABLES)
        if analyzed_empty:
            await captured._connection.execute("ANALYZE deltallm_accounting_events")
        repo = AccountingReadModelRepository(captured, statement_budget_seconds=0.25)
        defaults = [
            await captured.query_raw("SHOW enable_seqscan"),
            await captured.query_raw("SHOW enable_bitmapscan"),
            await captured.query_raw("SHOW jit"),
            await captured.query_raw("SHOW plan_cache_mode"),
        ]
        # Populate the prepared-statement cache while the source is empty.
        absent_token = str(uuid4())
        for _ in range(6):
            assert await cold_claim(repo) is None
            assert (
                await repo.progress(generation=7, expires_at=deadline())
            ).pending_partitions == 0
            rows = await captured.query_raw(
                RECOVER_CLAIM, "accounting-read-model-v2", 7, "cold-plan", absent_token, 256
            )
            assert rows[0]["accounting_partition"] is None
        await captured._connection.execute(
            "INSERT INTO deltallm_accounting_events "
            "SELECT 'primary',$1,(n%64)::integer,n,'finalized',"
            "jsonb_build_object('history',repeat('x',512)),'{}',now() "
            "FROM generate_series(1,40000) n",
            6 if other_generation else 7,
        )
        if other_generation:
            await captured._connection.execute(
                "INSERT INTO deltallm_accounting_events VALUES "
                "('primary',7,0,40000,'finalized','{}','{}',now())"
            )
        await captured._connection.execute(
            "UPDATE deltallm_accounting_projection_checkpoints SET last_sequence=40000"
        )
        await captured._connection.execute(
            "UPDATE deltallm_accounting_projection_checkpoints SET last_sequence=39999 "
            "WHERE accounting_partition=0"
        )
        # No ANALYZE/VACUUM: temporary tables also cannot get automatic cleanup.
        page = await cold_claim(repo)
        assert page.accounting_partition == 0 and page.sequences == (40000,)
        assert page.after_sequence == 39999 and page.generation == 7
        assert (await repo.progress(generation=7, expires_at=deadline())).pending_partitions == 1
        recovered = await captured.query_raw(
            RECOVER_CLAIM,
            "accounting-read-model-v2",
            7,
            page.worker_id,
            str(page.lease_token),
            256,
        )
        assert recovered[0]["sequences"] == [40000]
        assert await cold_claim(repo) is None
        assert defaults == [
            await captured.query_raw("SHOW enable_seqscan"),
            await captured.query_raw("SHOW enable_bitmapscan"),
            await captured.query_raw("SHOW jit"),
            await captured.query_raw("SHOW plan_cache_mode"),
        ]
    assert captured.errors == []
    claims = [entry for entry in captured.plans if "candidates AS MATERIALIZED" in entry.query]
    assert len(claims) == 8
    observed = False
    for entry in captured.plans:
        if not any(
            marker in entry.query
            for marker in (
                "candidates AS MATERIALIZED",
                "oldest_head_age_seconds",
                "owned AS MATERIALIZED",
            )
        ):
            continue
        for node in nodes(entry.node):
            if node.get("Relation Name") != "deltallm_accounting_events":
                continue
            observed |= node["Actual Loops"] > 0
            assert node["Actual Rows"] <= 1, entry.safe_report()
            assert node["Actual Loops"] <= 64, entry.safe_report()
            assert node.get("Rows Removed by Filter", 0) <= 1, entry.safe_report()
            assert node.get("Rows Removed by Join Filter", 0) <= 1, entry.safe_report()
            assert node.get("Local Hit Blocks", 0) <= 1024, entry.safe_report()
    assert observed


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
@pytest.mark.parametrize(
    "query,parameters",
    [
        (CLAIM, ("accounting-read-model-v2", 7, "cold-plan", str(uuid4()), 30, 256)),
        (PROGRESS, ("accounting-read-model-v2", 7)),
        (RECOVER_CLAIM, ("accounting-read-model-v2", 7, "cold-plan", str(uuid4()), 256)),
    ],
    ids=["claim", "progress", "recover"],
)
async def test_reporting_failure_restores_caller_plan_policy(planner, query, parameters):
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        connection = captured._connection
        await connection.execute(COLD_TABLES)
        settings = ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
        defaults = [await connection.fetchval("SHOW " + setting) for setting in settings]
        with pytest.raises(asyncpg.UndefinedColumnError, match="sequence"):
            async with connection.transaction():
                await connection.execute(
                    "ALTER TABLE pg_temp.deltallm_accounting_events DROP COLUMN sequence CASCADE"
                )
                await captured.query_raw(query, *parameters)
        assert defaults == [await connection.fetchval("SHOW " + setting) for setting in settings]
        assert await connection.fetchval("SELECT 1") == 1
    assert captured.errors == []
