"""Cold cached terminal claims stay bounded without a statistics refresh."""

import os

import asyncpg
import pytest

from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes
from tests.test_accounting_journal_worker_postgres import worker
from tests.test_accounting_local_leases_postgres import deadline

pytestmark = pytest.mark.postgres

COLD_TABLES = """
CREATE TEMP TABLE deltallm_accounting_terminal_journal
 (LIKE public.deltallm_accounting_terminal_journal INCLUDING ALL);
CREATE TEMP TABLE deltallm_accounting_terminal_capacity
 (LIKE public.deltallm_accounting_terminal_capacity INCLUDING ALL);
INSERT INTO deltallm_accounting_terminal_capacity
 (generation,accounting_partition,max_entries,pending_entries,pending_bytes)
 VALUES(7,0,1000,32,128);
"""
HISTORY = """
INSERT INTO deltallm_accounting_terminal_journal(sequence,generation,operation_id,grant_id,
 grantee_id,fence_token,permit_ordinal,accounting_partition,allowance_exact,outcome,
 reservation_sha256,finalization_sha256,payload_bytes,status,materialized_event_sequence,completed_at)
 SELECT n,7,'history-'||n,'grant-'||n,'history',gen_random_uuid(),0,0,0,'not_dispatched',
 sha256('reservation'::bytea),sha256('finalization'::bytea),4,'completed',1,now()
 FROM generate_series(1,40000) n;
"""
SELECTED = """
INSERT INTO deltallm_accounting_terminal_journal(sequence,generation,operation_id,grant_id,
 grantee_id,fence_token,permit_ordinal,accounting_partition,allowance_exact,outcome,
 reservation_sha256,finalization_sha256,payload_bytes,status,attempts,
 lease_owner,lease_token,lease_expires_at)
 SELECT n,7,'selected-'||n,'selected-grant-'||n,'probe',gen_random_uuid(),0,0,0,'not_dispatched',
 sha256('reservation'::bytea),sha256('finalization'::bytea),4,$1,$2,
 CASE WHEN $1='processing' THEN 'expired' END,
 CASE WHEN $1='processing' THEN gen_random_uuid() END,
 CASE WHEN $1='processing' THEN now()-interval '1 second' END
 FROM generate_series(40001,40032) n;
"""


async def claim(repo):
    return await repo.claim(generation=7, worker_id="cold-claim", limit=32, expires_at=deadline())


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
@pytest.mark.parametrize("selected", [None, "pending", "processing", "exhausted"])
async def test_cold_cached_terminal_claim_never_scans_completed_history(planner, selected):
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provision PostgreSQL")
        pytest.skip("DATABASE_URL is required")
    async with capture_accounting_plans(url, planner=planner) as captured:
        connection = captured._connection
        await connection.execute(COLD_TABLES)
        repo = worker(captured)
        defaults = [
            await connection.fetchval("SHOW " + setting)
            for setting in ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
        ]
        for _ in range(6):
            assert (await claim(repo)).sequences == ()
        await connection.execute(HISTORY)
        if selected is not None:
            await connection.execute(
                SELECTED,
                "processing" if selected == "processing" else "pending",
                5 if selected == "exhausted" else 0,
            )
        captured.plans.clear()
        # No ANALYZE/VACUUM; these connection-owned tables cannot auto-clean.
        actual = await claim(repo)
        expected = tuple(range(40001, 40033)) if selected in {"pending", "processing"} else ()
        assert actual.sequences == expected
        assert defaults == [
            await connection.fetchval("SHOW " + setting)
            for setting in ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
        ]
        if selected == "exhausted":
            assert (
                await connection.fetchval(
                    "SELECT failed_entries FROM deltallm_accounting_terminal_capacity"
                )
                == 32
            )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM deltallm_accounting_terminal_journal WHERE status='completed'"
            )
            == 40000
        )
    assert captured.errors == []
    observed = False
    for entry in captured.plans:
        if "deltallm_accounting_claim_terminal_journal" in entry.query or entry.query.startswith(
            "SELECT count(*)"
        ):
            continue
        for node in nodes(entry.node):
            if node.get("Relation Name") != "deltallm_accounting_terminal_journal":
                continue
            observed |= node["Actual Loops"] > 0
            assert node["Node Type"] != "Seq Scan", entry.safe_report()
            assert node["Actual Rows"] <= 64, entry.safe_report()
            assert node.get("Rows Removed by Filter", 0) <= 64, entry.safe_report()
            assert node.get("Local Hit Blocks", 0) <= 2048, entry.safe_report()
    assert observed


@pytest.mark.parametrize("missing_key", [False, True])
async def test_terminal_claim_failure_restores_caller_and_missing_key_fails_closed(missing_key):
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner="generic") as captured:
        connection = captured._connection
        await connection.execute(COLD_TABLES)
        defaults = [
            await connection.fetchval("SHOW " + setting)
            for setting in ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
        ]
        if missing_key:
            # The statement is generated from the constraint of this owned temp table only.
            drop = await connection.fetchval(
                "SELECT format('ALTER TABLE pg_temp.deltallm_accounting_terminal_journal "
                "DROP CONSTRAINT %I',conname) FROM pg_constraint "
                "WHERE conrelid='pg_temp.deltallm_accounting_terminal_journal'::regclass "
                "AND contype='p'"
            )
            await connection.execute(drop)
        reason = (
            "accounting_terminal_claim_key_index"
            if missing_key
            else "accounting_terminal_claim_shape"
        )
        with pytest.raises(asyncpg.PostgresError, match=reason):
            async with connection.transaction():
                await connection.fetch(
                    "SELECT * FROM deltallm_accounting_claim_terminal_journal"
                    "(7,'error',gen_random_uuid(),30,0)"
                )
        assert defaults == [
            await connection.fetchval("SHOW " + setting)
            for setting in ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
        ]
    assert captured.errors == []
