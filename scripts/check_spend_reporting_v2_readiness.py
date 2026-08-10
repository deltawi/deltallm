from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from prisma import Prisma


REQUIRED_MIGRATIONS = {
    "20260810120000_spend_log_cursor_indexes",
    "20260810140000_spend_owner_scope",
    "20260810150000_spend_owner_scope_index",
}
REQUIRED_COLUMNS = {
    "deltallm_spendlog_events.owner_account_id",
    "deltallm_batch_job.created_by_owner_account_id",
    "deltallm_batch_job.created_by_owner_snapshot_complete",
    "deltallm_batch_create_session.created_by_owner_account_id",
    "deltallm_batch_create_session.created_by_owner_snapshot_complete",
}
REQUIRED_BOOLEAN_DEFAULT_FALSE_COLUMNS = {
    "deltallm_batch_job.created_by_owner_snapshot_complete",
    "deltallm_batch_create_session.created_by_owner_snapshot_complete",
}
REQUIRED_INDEXES = {
    "deltallm_spendlog_events_org_time_id_idx",
    "deltallm_spendlog_events_time_id_idx",
    "deltallm_spendlog_events_owner_time_id_idx",
}
REQUIRED_INDEX_COLUMNS = {
    "deltallm_spendlog_events_org_time_id_idx": (
        "organization_id",
        "start_time",
        "id",
    ),
    "deltallm_spendlog_events_time_id_idx": ("start_time", "id"),
    "deltallm_spendlog_events_owner_time_id_idx": (
        "owner_account_id",
        "start_time",
        "id",
    ),
}
REQUIRED_TRIGGERS = {
    "deltallm_spend_event_owner_snapshot",
    "deltallm_batch_job_owner_snapshot",
    "deltallm_batch_create_session_owner_snapshot",
}


def _row_value(row: Any, key: str) -> Any:
    return dict(row).get(key)


def build_readiness_report(
    *,
    migration_rows: list[Any],
    column_rows: list[Any],
    index_rows: list[Any],
    trigger_rows: list[Any],
) -> dict[str, Any]:
    migrations = {
        str(_row_value(row, "migration_name"))
        for row in migration_rows
        if _row_value(row, "finished_at") is not None
        and _row_value(row, "rolled_back_at") is None
    }
    columns: set[str] = set()
    for row in column_rows:
        name = f'{_row_value(row, "table_name")}.{_row_value(row, "column_name")}'
        if name in REQUIRED_BOOLEAN_DEFAULT_FALSE_COLUMNS:
            default = str(_row_value(row, "column_default") or "").lower()
            if (
                str(_row_value(row, "data_type") or "").lower() != "boolean"
                or str(_row_value(row, "is_nullable") or "").upper() != "NO"
                or not default.startswith("false")
            ):
                continue
        columns.add(name)
    indexes: set[str] = set()
    for row in index_rows:
        name = str(_row_value(row, "index_name"))
        definition = (
            str(_row_value(row, "index_definition") or "")
            .lower()
            .replace('"', "")
        )
        indexed_expression = definition.rsplit("(", 1)[-1].split(")", 1)[0]
        indexed_columns = tuple(
            part.strip().split()[0]
            for part in indexed_expression.split(",")
            if part.strip()
        )
        required_columns = REQUIRED_INDEX_COLUMNS.get(name, ())
        definition_matches = bool(required_columns) and indexed_columns == required_columns
        if (
            bool(_row_value(row, "indisvalid"))
            and bool(_row_value(row, "indisready"))
            and definition_matches
        ):
            indexes.add(name)
    triggers = {
        str(_row_value(row, "trigger_name"))
        for row in trigger_rows
        if str(_row_value(row, "tgenabled") or "D") != "D"
    }

    checks = {
        "migrations": sorted(REQUIRED_MIGRATIONS - migrations),
        "columns": sorted(REQUIRED_COLUMNS - columns),
        "valid_indexes": sorted(REQUIRED_INDEXES - indexes),
        "enabled_triggers": sorted(REQUIRED_TRIGGERS - triggers),
    }
    ready = all(not missing for missing in checks.values())
    return {
        "ready": ready,
        "missing": checks,
        "note": (
            "Database compatibility is ready. Confirm every gateway and batch worker runs "
            "reporting-v2 code before enabling spend_reporting_v2_enabled."
            if ready
            else "Database compatibility is incomplete. Keep spend_reporting_v2_enabled false."
        ),
    }


async def collect_readiness(db: Any) -> dict[str, Any]:
    migration_rows = await db.query_raw(
        """
        SELECT migration_name, finished_at, rolled_back_at
        FROM _prisma_migrations
        WHERE migration_name IN (
            '20260810120000_spend_log_cursor_indexes',
            '20260810140000_spend_owner_scope',
            '20260810150000_spend_owner_scope_index'
        )
        """
    )
    column_rows = await db.query_raw(
        """
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND (table_name, column_name) IN (
              ('deltallm_spendlog_events', 'owner_account_id'),
              ('deltallm_batch_job', 'created_by_owner_account_id'),
              ('deltallm_batch_job', 'created_by_owner_snapshot_complete'),
              ('deltallm_batch_create_session', 'created_by_owner_account_id'),
              ('deltallm_batch_create_session', 'created_by_owner_snapshot_complete')
          )
        """
    )
    index_rows = await db.query_raw(
        """
        SELECT index_class.relname AS index_name,
               index_meta.indisvalid,
               index_meta.indisready,
               pg_get_indexdef(index_class.oid) AS index_definition
        FROM pg_class AS index_class
        JOIN pg_index AS index_meta ON index_meta.indexrelid = index_class.oid
        JOIN pg_namespace AS namespace ON namespace.oid = index_class.relnamespace
        WHERE namespace.nspname = current_schema()
          AND index_class.relname IN (
              'deltallm_spendlog_events_org_time_id_idx',
              'deltallm_spendlog_events_time_id_idx',
              'deltallm_spendlog_events_owner_time_id_idx'
          )
        """
    )
    trigger_rows = await db.query_raw(
        """
        SELECT trigger_meta.tgname AS trigger_name, trigger_meta.tgenabled
        FROM pg_trigger AS trigger_meta
        JOIN pg_class AS table_class ON table_class.oid = trigger_meta.tgrelid
        JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
        WHERE namespace.nspname = current_schema()
          AND NOT trigger_meta.tgisinternal
          AND trigger_meta.tgname IN (
              'deltallm_spend_event_owner_snapshot',
              'deltallm_batch_job_owner_snapshot',
              'deltallm_batch_create_session_owner_snapshot'
          )
        """
    )
    return build_readiness_report(
        migration_rows=list(migration_rows),
        column_rows=list(column_rows),
        index_rows=list(index_rows),
        trigger_rows=list(trigger_rows),
    )


async def run_check(database_url: str) -> dict[str, Any]:
    db = Prisma(datasource={"url": database_url})
    await db.connect()
    try:
        return await collect_readiness(db)
    finally:
        await db.disconnect()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check database readiness before enabling scoped usage reporting v2."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL URL; defaults to DATABASE_URL.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.database_url:
        print("DATABASE_URL or --database-url is required", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(run_check(str(args.database_url)))
    except Exception as exc:
        print(f"Spend reporting readiness check failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
