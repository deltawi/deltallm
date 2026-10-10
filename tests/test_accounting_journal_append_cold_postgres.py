"""Cached append lookups must not scan retained terminal history."""

import json
import os
from uuid import uuid4

import asyncpg
import pytest

from src.db.accounting.journal.accounting_journal import AccountingJournalRepository
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes
from tests.test_accounting_journal_cold_plans_postgres import HISTORY
from tests.test_accounting_local_leases_postgres import allocation, deadline, owner, terminal
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _reservation,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db

COLD_TABLES = """
CREATE TEMP TABLE deltallm_accounting_terminal_journal
 (LIKE public.deltallm_accounting_terminal_journal INCLUDING ALL);
CREATE TEMP TABLE deltallm_accounting_terminal_payloads
 (LIKE public.deltallm_accounting_terminal_payloads INCLUDING ALL);
ALTER TABLE deltallm_accounting_terminal_payloads
 ADD CONSTRAINT cold_payload_journal_fkey FOREIGN KEY (journal_sequence)
 REFERENCES deltallm_accounting_terminal_journal(sequence) ON DELETE CASCADE;
ANALYZE deltallm_accounting_terminal_journal;
ANALYZE deltallm_accounting_terminal_payloads;
"""


def assert_append_plans(plans):
    observed = False
    for entry in plans:
        if "deltallm_accounting_append_terminal_journal(" in entry.query:
            continue
        for node in nodes(entry.node):
            if node.get("Relation Name") != "deltallm_accounting_terminal_journal":
                continue
            if not node["Actual Loops"]:
                continue
            observed = True
            report = json.dumps(
                {
                    "foreign_key": "FOR KEY SHARE OF x" in entry.query,
                    "prior_lookup": "WHERE j.operation_id=item->>'operation_id'" in entry.query,
                    **entry.safe_report(),
                }
            )
            assert node["Node Type"] != "Seq Scan", report
            assert node["Actual Rows"] <= 32, entry.safe_report()
            assert node["Actual Loops"] <= 32, entry.safe_report()
            assert node.get("Rows Removed by Filter", 0) <= 32, entry.safe_report()
            assert node.get("Rows Removed by Index Recheck", 0) <= 32, entry.safe_report()
            blocks = node.get("Local Hit Blocks", 0) + node.get("Local Read Blocks", 0)
            assert blocks <= 2048, entry.safe_report()
    assert observed


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
@pytest.mark.parametrize("analyzed", [False, True])
async def test_cold_cached_append_ignores_retained_history(accounting_db, planner, analyzed):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window, limit="1000000")
    items = [_reservation(generation, window) for _ in range(71)]
    grants = await owner(db).allocate_batch(
        [allocation(item) for item in items], expires_at=deadline()
    )
    values = [terminal(item, grant) for item, grant in zip(items, grants, strict=True)]
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        connection = captured._connection
        await connection.execute("SET auto_explain.log_min_duration=-1")
        await connection.execute(COLD_TABLES)
        repository = AccountingJournalRepository(captured, statement_budget_seconds=2)
        settings = ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
        defaults = [await connection.fetchval("SHOW " + setting) for setting in settings]
        # Compile this connection's function statements while history is small.
        for value in values[:7]:
            result = await repository.append_batch([value], expires_at=deadline())
            assert not result[0].replayed
        for index, (first, last) in enumerate(((1000001, 1040000), (1040001, 1300000))):
            await connection.execute(HISTORY.replace("1,40000", f"{first},{last}"))
            if analyzed:
                await connection.execute("ANALYZE deltallm_accounting_terminal_journal")
            captured.plans.clear()
            await connection.execute("SET auto_explain.log_min_duration=0")
            selected = values[7 + index * 32 : 39 + index * 32]
            actual = await repository.append_batch(selected, expires_at=deadline())
            replay = await repository.append_batch(selected, expires_at=deadline())
            await connection.execute("SET auto_explain.log_min_duration=-1")
            assert all(not receipt.replayed for receipt in actual)
            assert all(receipt.replayed for receipt in replay)
            assert [receipt.journal_sequence for receipt in actual] == [
                receipt.journal_sequence for receipt in replay
            ]
            assert defaults == [
                await connection.fetchval("SHOW " + setting) for setting in settings
            ]
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM deltallm_accounting_terminal_payloads"
                )
                == 7 + (index + 1) * 32
            )
            assert_append_plans(captured.plans)
    assert captured.errors == []


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
async def test_append_failure_preserves_caller_planner_policy(planner):
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        connection = captured._connection
        settings = ("enable_seqscan", "enable_bitmapscan", "jit", "plan_cache_mode")
        defaults = [await connection.fetchval("SHOW " + setting) for setting in settings]
        assert (
            await connection.fetchval(
                "SELECT 'plan_cache_mode=force_custom_plan'=ANY(proconfig) FROM pg_proc "
                "WHERE oid='deltallm_accounting_append_terminal_journal"
                "(bigint,jsonb,text[],text[])'::regprocedure"
            )
            is True
        )
        with pytest.raises(asyncpg.PostgresError, match="accounting_terminal_batch_shape"):
            async with connection.transaction():
                await connection.fetch(
                    "SELECT * FROM deltallm_accounting_append_terminal_journal"
                    "(1,'[]'::jsonb,ARRAY[]::text[],ARRAY[]::text[])"
                )
        assert defaults == [await connection.fetchval("SHOW " + setting) for setting in settings]
        assert await connection.fetchval("SELECT 1") == 1
    assert captured.errors == []
