"""Actual cached terminal commit lookups stay bounded with cold retained history."""

import os
from uuid import uuid4

import asyncpg
import pytest

from src.billing.accounting_journal_claims import JournalClaim
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes
from tests.test_accounting_journal_cold_plans_postgres import COLD_TABLES, HISTORY
from tests.test_accounting_journal_worker_postgres import worker
from tests.test_accounting_local_leases_postgres import deadline

pytestmark = pytest.mark.postgres


def handle(generation, sequences):
    return JournalClaim(
        protocol_generation=generation,
        worker_id="cold-commit",
        lease_token=uuid4(),
        sequences=tuple(sequences),
    )


def assert_commit_plans(plans):
    observed = False
    for entry in plans:
        if "p_sequences" not in entry.query:
            continue
        for node in nodes(entry.node):
            if node.get("Relation Name") != "deltallm_accounting_terminal_journal":
                continue
            observed |= node["Actual Loops"] > 0
            assert node["Node Type"] != "Seq Scan", entry.safe_report()
            assert node["Actual Rows"] <= 64, entry.safe_report()
            assert node["Actual Loops"] <= 64, entry.safe_report()
            assert node.get("Rows Removed by Filter", 0) <= 64, entry.safe_report()
            assert node.get("Rows Removed by Index Recheck", 0) <= 64, entry.safe_report()
            blocks = node.get("Local Hit Blocks", 0) + node.get("Local Read Blocks", 0)
            assert blocks <= 2048, entry.safe_report()
    assert observed


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
@pytest.mark.parametrize("analyzed", [False, True])
@pytest.mark.parametrize("replay", [False, True])
async def test_cold_cached_terminal_commit_keys_ignore_retained_history(planner, analyzed, replay):
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        connection = captured._connection
        await connection.execute(COLD_TABLES)
        repo = worker(captured)
        defaults = [
            await connection.fetchval("SHOW " + setting)
            for setting in ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
        ]
        generation = 7 if replay else 8
        for _ in range(6):
            assert await repo.materialize(handle(generation, [1]), expires_at=deadline()) == 0
        for first, last in ((1, 40000), (40001, 300000)):
            await connection.execute(HISTORY.replace("1,40000", f"{first},{last}"))
            if analyzed:
                await connection.execute("ANALYZE deltallm_accounting_terminal_journal")
            captured.plans.clear()
            selected = handle(generation, range(last - 31, last + 1))
            assert await repo.materialize(selected, expires_at=deadline()) == (32 if replay else 0)
            await connection.execute("SET auto_explain.log_min_duration=-1")
            assert defaults == [
                await connection.fetchval("SHOW " + setting)
                for setting in ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
            ]
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM deltallm_accounting_terminal_journal WHERE status='completed'"
                )
                == last
            )
            assert (
                await connection.fetchval(
                    "SELECT pending_entries FROM deltallm_accounting_terminal_capacity"
                )
                == 32
            )
            assert_commit_plans(captured.plans)
            await connection.execute("SET auto_explain.log_min_duration=0")
    assert captured.errors == []


@pytest.mark.parametrize("missing_key", [False, True])
async def test_terminal_commit_failure_preserves_caller_policy_and_missing_key_denies(missing_key):
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner="generic") as captured:
        connection = captured._connection
        await connection.execute(COLD_TABLES)
        defaults = [
            await connection.fetchval("SHOW " + setting)
            for setting in ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
        ]
        if missing_key:
            drop = await connection.fetchval(
                "SELECT format('ALTER TABLE pg_temp.deltallm_accounting_terminal_journal "
                "DROP CONSTRAINT %I',conname) FROM pg_constraint "
                "WHERE conrelid='pg_temp.deltallm_accounting_terminal_journal'::regclass "
                "AND contype='p'"
            )
            await connection.execute(drop)
        reason = (
            "accounting_terminal_commit_key_index"
            if missing_key
            else "accounting_terminal_materialize_shape"
        )
        with pytest.raises(asyncpg.PostgresError, match=reason):
            async with connection.transaction():
                await connection.fetchval(
                    "SELECT deltallm_accounting_materialize_terminal_journal"
                    "(7,'error',gen_random_uuid(),$1::bigint[])",
                    [1] if missing_key else [],
                )
        assert defaults == [
            await connection.fetchval("SHOW " + setting)
            for setting in ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
        ]
    assert captured.errors == []
