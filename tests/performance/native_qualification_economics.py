"""Offline qualification checks compare native facts with every budget scope."""

from __future__ import annotations
import asyncio
from decimal import Decimal
from time import perf_counter
from prisma import Prisma
from src.billing.accounting.reporting.accounting_read_model_claims import READ_MODEL_PROJECTION

_STATE_SQL = """
SELECT
 (SELECT count(*)::bigint FROM deltallm_billing_operations
  WHERE accounting_generation=1 AND accounting_state IN ('reserved','provisional')) AS unsettled_operations,
 (SELECT count(*)::bigint FROM deltallm_accounting_grants
  WHERE generation=1 AND state<>'closed') AS open_grants,
 (SELECT count(*)::bigint FROM deltallm_accounting_terminal_journal
  WHERE generation=1 AND status<>'completed') AS terminal_pending,
 (SELECT count(*)::bigint FROM deltallm_accounting_projection_checkpoints c
  WHERE c.projection_name=$1 AND c.generation=1 AND EXISTS (
   SELECT 1 FROM deltallm_accounting_events e WHERE e.protocol_name=c.protocol_name
    AND e.generation=c.generation AND e.accounting_partition=c.accounting_partition
    AND e.sequence>c.last_sequence AND e.event_type IN ('finalized','reconciled'))) AS report_pending_partitions,
 (SELECT count(*)::bigint FROM deltallm_spend_ingestion_outbox
  WHERE status IN ('queued','retry','processing')) AS spend_pending,
 (SELECT count(*)::bigint FROM deltallm_audit_ingestion_outbox
  WHERE status IN ('queued','retry','processing')) AS audit_pending,
 (SELECT count(*)::bigint FROM deltallm_accounting_budget_windows
  WHERE committed_exact+reserved_exact+provisional_exact>limit_exact
     OR committed_exact<0 OR reserved_exact<0 OR provisional_exact<0) AS unsafe_windows,
 (SELECT count(*)::bigint FROM deltallm_accounting_usage_facts_v2) AS facts,
 (SELECT COALESCE(sum(spend_exact),0)::text FROM deltallm_accounting_usage_facts_v2) AS fact_charge,
 (SELECT count(*)::bigint FROM deltallm_spendlog_events) AS legacy_spend_rows
"""
_DRAIN_FIELDS = (
    "unsettled_operations",
    "open_grants",
    "terminal_pending",
    "report_pending_partitions",
    "spend_pending",
    "audit_pending",
    "unsafe_windows",
)


async def accounting_snapshot(db: Prisma) -> dict[str, int | str]:
    async with asyncio.timeout(5):
        rows = await db.query_raw(_STATE_SQL, READ_MODEL_PROJECTION)
    if len(rows) != 1:
        raise RuntimeError("Native accounting snapshot is incomplete")
    return {
        name: str(value) if name == "fact_charge" else int(value) for name, value in rows[0].items()
    }


async def wait_native_drain(db: Prisma, *, timeout: float = 180) -> dict[str, object]:
    started, samples = perf_counter(), []
    while True:
        state = await accounting_snapshot(db)
        samples.append({"offset_seconds": perf_counter() - started, **state})
        if state["unsafe_windows"]:
            raise RuntimeError("Qualification found unsafe budget state")
        if all(state[field] == 0 for field in _DRAIN_FIELDS):
            return {
                "passed": True,
                "seconds": perf_counter() - started,
                "samples": samples,
                "state": state,
            }
        if perf_counter() - started >= timeout:
            return {
                "passed": False,
                "seconds": perf_counter() - started,
                "samples": samples,
                "state": state,
            }
        await asyncio.sleep(2)


async def reconcile_native(
    db: Prisma, *, before: dict[str, int | str], successes: int, all_successful: bool
) -> dict[str, object]:
    after = await accounting_snapshot(db)
    async with asyncio.timeout(5):
        rows = await db.query_raw(
            "SELECT scope_type,committed_exact::text AS committed,"
            "reserved_exact::text AS reserved,provisional_exact::text AS provisional "
            "FROM deltallm_accounting_budget_windows "
            "WHERE protocol_name='primary' AND generation=1 ORDER BY scope_type LIMIT 65"
        )
    fact_charge = Decimal(str(after["fact_charge"]))
    expected_delta = Decimal(successes + 1) * Decimal("0.000007")  # Includes measure's precheck.
    actual_delta = fact_charge - Decimal(str(before["fact_charge"]))
    charge_matches = actual_delta == expected_delta if all_successful else None
    count_matches = (
        int(after["facts"]) - int(before["facts"]) == successes + 1 if all_successful else None
    )
    scopes = [
        {
            "scope_type": row["scope_type"],
            "committed": row["committed"],
            "reserved": row["reserved"],
            "provisional": row["provisional"],
            "matches_facts": Decimal(row["committed"]) == fact_charge,
        }
        for row in rows
    ]
    scope_matches = (
        bool(scopes)
        and {row["scope_type"] for row in scopes} == {"api_key", "user", "team", "organization"}
        and len(scopes) <= 64
        and all(
            row["matches_facts"]
            and Decimal(row["reserved"]) == 0
            and Decimal(row["provisional"]) == 0
            for row in scopes
        )
    )
    return {
        "passed": charge_matches is True
        and count_matches is True
        and scope_matches
        and after["unsafe_windows"] == 0
        and after["legacy_spend_rows"] == 0,
        "safe_budget_state": after["unsafe_windows"] == 0,
        "before": before,
        "after": after,
        "expected_exact_charge_delta": str(expected_delta),
        "actual_exact_charge_delta": str(actual_delta),
        "successful_charge_count_matches": count_matches,
        "successful_charge_amount_matches": charge_matches,
        "scope_matches": scope_matches,
        "scopes": scopes,
    }


async def native_storage_snapshot(db: Prisma) -> list[dict[str, object]]:
    """Collect append-heavy storage and vacuum state off the request path."""
    async with asyncio.timeout(10):
        return await db.query_raw(
            "SELECT relname,n_live_tup,n_dead_tup,autovacuum_count,autoanalyze_count,"
            "last_autovacuum,last_autoanalyze,pg_table_size(relid)::bigint AS table_bytes,"
            "pg_indexes_size(relid)::bigint AS index_bytes "
            "FROM pg_stat_user_tables WHERE relname LIKE 'deltallm_accounting_%' "
            "OR relname='deltallm_billing_operations' ORDER BY relname LIMIT 64"
        )
