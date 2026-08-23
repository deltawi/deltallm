from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from prisma import Prisma


@dataclass(frozen=True, slots=True)
class ConcurrentIndexSpec:
    migration_name: str
    name: str
    table: str
    columns: tuple[str, ...]

    @property
    def create_sql(self) -> str:
        columns = ", ".join(f'"{column}"' for column in self.columns)
        return (
            f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{self.name}" ON "{self.table}" ({columns})'
        )


CONCURRENT_INDEXES = (
    ConcurrentIndexSpec(
        "20260816120050_organization_deletion_lifecycle_index",
        "deltallm_organizationtable_lifecycle_state_idx",
        "deltallm_organizationtable",
        ("lifecycle_state", "updated_at"),
    ),
    ConcurrentIndexSpec(
        "20260816120100_organization_deletion_batch_file_index",
        "deltallm_batch_file_org_created_idx",
        "deltallm_batch_file",
        ("created_by_organization_id", "created_at"),
    ),
    ConcurrentIndexSpec(
        "20260816120200_organization_deletion_mcp_approval_index",
        "deltallm_mcpapproval_org_status_created_idx",
        "deltallm_mcpapprovalrequest",
        ("organization_id", "status", "created_at"),
    ),
    ConcurrentIndexSpec(
        "20260816120300_organization_deletion_prompt_log_org_index",
        "deltallm_promptrenderlog_org_created_idx",
        "deltallm_promptrenderlog",
        ("organization_id", "created_at"),
    ),
    ConcurrentIndexSpec(
        "20260816120400_organization_deletion_prompt_log_team_index",
        "deltallm_promptrenderlog_team_created_idx",
        "deltallm_promptrenderlog",
        ("team_id", "created_at"),
    ),
    ConcurrentIndexSpec(
        "20260816120500_organization_deletion_prompt_log_api_key_index",
        "deltallm_promptrenderlog_api_key_created_idx",
        "deltallm_promptrenderlog",
        ("api_key", "created_at"),
    ),
    ConcurrentIndexSpec(
        "20260816120600_organization_deletion_prompt_log_user_index",
        "deltallm_promptrenderlog_user_created_idx",
        "deltallm_promptrenderlog",
        ("user_id", "created_at"),
    ),
    ConcurrentIndexSpec(
        "20260816120700_organization_deletion_mcp_approval_scope_index",
        "deltallm_mcpapproval_scope_status_created_idx",
        "deltallm_mcpapprovalrequest",
        ("scope_type", "scope_id", "status", "created_at"),
    ),
    ConcurrentIndexSpec(
        "20260816120800_organization_deletion_mcp_approval_api_key_index",
        "deltallm_mcpapproval_api_key_created_idx",
        "deltallm_mcpapprovalrequest",
        ("requested_by_api_key", "created_at"),
    ),
    ConcurrentIndexSpec(
        "20260816120900_organization_deletion_mcp_approval_user_index",
        "deltallm_mcpapproval_user_created_idx",
        "deltallm_mcpapprovalrequest",
        ("requested_by_user", "created_at"),
    ),
    ConcurrentIndexSpec(
        "20260823120100_organization_deletion_webhook_index",
        "deltallm_batch_webhook_outbox_org_status_created_idx",
        "deltallm_batch_webhook_outbox",
        ("created_by_organization_id", "status", "created_at", "event_id"),
    ),
    ConcurrentIndexSpec(
        "20260823120200_organization_deletion_batch_job_user_index",
        "deltallm_batch_job_user_created_idx",
        "deltallm_batch_job",
        ("created_by_user_id", "created_at"),
    ),
    ConcurrentIndexSpec(
        "20260823120300_organization_deletion_batch_session_user_index",
        "deltallm_batch_create_session_user_status_created_idx",
        "deltallm_batch_create_session",
        ("created_by_user_id", "status", "created_at"),
    ),
)

REQUIRED_MIGRATIONS = frozenset(
    {
        "20260816120000_organization_deletion_lifecycle",
        "20260816121000_organization_deletion_lifecycle_validation",
        "20260816121100_organization_deletion_write_fences",
        "20260823120000_organization_deletion_invariant_repairs",
        "20260823120400_organization_deletion_invitation_removal_guard",
        *(spec.migration_name for spec in CONCURRENT_INDEXES),
    }
)

_BACKFILL_TABLES = (
    (
        "deltallm_batch_job",
        "batch_id",
        "deltallm_resolve_created_owner_organization(to_jsonb(record))",
    ),
    (
        "deltallm_batch_create_session",
        "session_id",
        "deltallm_resolve_created_owner_organization(to_jsonb(record))",
    ),
    (
        "deltallm_batch_file",
        "file_id",
        "deltallm_resolve_created_owner_organization(to_jsonb(record))",
    ),
    (
        "deltallm_batch_webhook_outbox",
        "event_id",
        "COALESCE(deltallm_resolve_created_owner_organization(to_jsonb(record)), "
        "source.created_by_organization_id)",
    ),
)


def classify_index_rows(rows: Sequence[Any]) -> tuple[list[ConcurrentIndexSpec], list[str]]:
    by_name = {str(dict(row).get("index_name") or ""): dict(row) for row in rows}
    missing: list[ConcurrentIndexSpec] = []
    invalid: list[str] = []
    for spec in CONCURRENT_INDEXES:
        row = by_name.get(spec.name)
        if row is None:
            missing.append(spec)
            continue
        table = str(row.get("table_name") or "")
        columns = tuple(str(value) for value in (row.get("columns") or []))
        if table != spec.table or columns != spec.columns:
            raise RuntimeError(f"index {spec.name} has an unexpected definition")
        if not bool(row.get("indisvalid")) or not bool(row.get("indisready")):
            invalid.append(spec.name)
    return missing, invalid


async def _collect_index_rows(db: Any) -> list[Any]:
    names = [spec.name for spec in CONCURRENT_INDEXES]
    rows = await db.query_raw(
        """
        SELECT index_class.relname AS index_name,
               table_class.relname AS table_name,
               index_meta.indisvalid,
               index_meta.indisready,
               array_agg(attribute.attname ORDER BY key.ordinality) AS columns
        FROM pg_index index_meta
        JOIN pg_class index_class ON index_class.oid = index_meta.indexrelid
        JOIN pg_class table_class ON table_class.oid = index_meta.indrelid
        JOIN pg_namespace namespace ON namespace.oid = index_class.relnamespace
        CROSS JOIN LATERAL unnest(index_meta.indkey)
            WITH ORDINALITY AS key(attnum, ordinality)
        JOIN pg_attribute attribute
          ON attribute.attrelid = table_class.oid
         AND attribute.attnum = key.attnum
        WHERE namespace.nspname = current_schema()
          AND index_class.relname = ANY($1::text[])
        GROUP BY index_class.relname, table_class.relname,
                 index_meta.indisvalid, index_meta.indisready
        """,
        names,
    )
    return list(rows)


async def _failed_known_migrations(db: Any, migrations_root: Path) -> list[str]:
    table_rows = await db.query_raw(
        "SELECT to_regclass(current_schema() || '._prisma_migrations')::text AS table_name"
    )
    if not table_rows or not dict(table_rows[0]).get("table_name"):
        return []
    rows = await db.query_raw(
        """
        SELECT migration_name, checksum
        FROM _prisma_migrations
        WHERE migration_name = ANY($1::text[])
          AND finished_at IS NULL
          AND rolled_back_at IS NULL
        """,
        [spec.migration_name for spec in CONCURRENT_INDEXES],
    )
    failed: list[str] = []
    for raw_row in rows:
        row = dict(raw_row)
        migration_name = str(row.get("migration_name") or "")
        migration_path = migrations_root / migration_name / "migration.sql"
        if not migration_path.is_file():
            raise RuntimeError(f"known failed migration is missing locally: {migration_name}")
        checksum = hashlib.sha256(migration_path.read_bytes()).hexdigest()
        if checksum != str(row.get("checksum") or ""):
            raise RuntimeError(f"known failed migration checksum changed: {migration_name}")
        failed.append(migration_name)
    return failed


async def prepare_retry(db: Any, migrations_root: Path) -> list[str]:
    rows = await _collect_index_rows(db)
    _missing, invalid = classify_index_rows(rows)
    for name in invalid:
        # name is sourced exclusively from the static registry above.
        await db.execute_raw(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
    return await _failed_known_migrations(db, migrations_root)


async def ensure_indexes(db: Any) -> None:
    rows = await _collect_index_rows(db)
    missing, invalid = classify_index_rows(rows)
    if invalid:
        raise RuntimeError(f"invalid organization-deletion indexes remain: {', '.join(invalid)}")
    for spec in missing:
        await db.execute_raw(spec.create_sql)


def _backfill_sql(table: str, id_column: str, resolution: str) -> str:
    source_join = ""
    if table == "deltallm_batch_webhook_outbox":
        source_join = "LEFT JOIN deltallm_batch_job source ON source.batch_id = record.batch_id"
    return f"""
        WITH candidates AS MATERIALIZED (
            SELECT record.{id_column} AS record_id,
                   {resolution} AS organization_id
            FROM {table} record
            {source_join}
            WHERE record.created_by_organization_id IS NULL
              AND {resolution} IS NOT NULL
            ORDER BY record.{id_column}
            FOR UPDATE OF record SKIP LOCKED
            LIMIT $1
        )
        UPDATE {table} record
        SET created_by_organization_id = candidate.organization_id
        FROM candidates candidate
        WHERE record.{id_column} = candidate.record_id
        RETURNING record.{id_column}
    """


async def backfill_ownership(db: Any, *, page_size: int, max_pages: int) -> int:
    normalized = 0
    bounded_page_size = max(1, min(int(page_size), 1_000))
    bounded_max_pages = max(1, int(max_pages))
    pages = 0
    for table, id_column, resolution in _BACKFILL_TABLES:
        sql = _backfill_sql(table, id_column, resolution)
        while pages < bounded_max_pages:
            rows = await db.query_raw(sql, bounded_page_size)
            pages += 1
            normalized += len(rows)
            if len(rows) < bounded_page_size:
                break
        else:
            raise RuntimeError("organization ownership backfill page limit reached; rerun the job")
    return normalized


async def verify_readiness(db: Any) -> dict[str, object]:
    migration_rows = await db.query_raw(
        """
        SELECT migration_name, finished_at, rolled_back_at
        FROM _prisma_migrations
        WHERE migration_name = ANY($1::text[])
        """,
        sorted(REQUIRED_MIGRATIONS),
    )
    finished = {
        str(dict(row).get("migration_name") or "")
        for row in migration_rows
        if dict(row).get("finished_at") is not None and dict(row).get("rolled_back_at") is None
    }
    missing_migrations = sorted(REQUIRED_MIGRATIONS - finished)
    index_rows = await _collect_index_rows(db)
    missing_indexes, invalid_indexes = classify_index_rows(index_rows)
    unresolved_ownership = await _count_resolvable_missing_ownership(db)
    report: dict[str, object] = {
        "ready": (
            not missing_migrations
            and not missing_indexes
            and not invalid_indexes
            and unresolved_ownership == 0
        ),
        "missing_migrations": missing_migrations,
        "missing_indexes": [spec.name for spec in missing_indexes],
        "invalid_indexes": invalid_indexes,
        "resolvable_missing_ownership": unresolved_ownership,
    }
    return report


async def _with_database(database_url: str, action: Any) -> Any:
    db = Prisma(datasource={"url": database_url})
    await db.connect()
    try:
        return await action(db)
    finally:
        await db.disconnect()


async def _count_resolvable_missing_ownership(db: Any) -> int:
    unresolved = 0
    for table, _id_column, resolution in _BACKFILL_TABLES:
        source_join = ""
        if table == "deltallm_batch_webhook_outbox":
            source_join = "LEFT JOIN deltallm_batch_job source ON source.batch_id = record.batch_id"
        rows = await db.query_raw(
            f"""
            SELECT COUNT(*)::int AS unresolved
            FROM {table} record
            {source_join}
            WHERE record.created_by_organization_id IS NULL
              AND {resolution} IS NOT NULL
            """
        )
        unresolved += int(dict(rows[0]).get("unresolved") or 0) if rows else 0
    return unresolved


def _run_prisma(schema: Path, database_url: str, *args: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    subprocess.run(
        ["prisma", *args, f"--schema={schema}"],
        check=True,
        env=environment,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deploy and verify organization-deletion database invariants."
    )
    parser.add_argument("command", choices=("deploy", "verify"), nargs="?", default="deploy")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--schema", type=Path, default=Path("prisma/schema.prisma"))
    parser.add_argument("--backfill-page-size", type=int, default=500)
    parser.add_argument("--backfill-max-pages", type=int, default=1_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.database_url:
        print("DATABASE_URL or --database-url is required", file=sys.stderr)
        return 2
    schema = args.schema.resolve()
    migrations_root = schema.parent / "migrations"
    database_url = str(args.database_url)
    try:
        if args.command == "deploy":
            failed = asyncio.run(
                _with_database(
                    database_url,
                    lambda db: prepare_retry(db, migrations_root),
                )
            )
            for migration_name in failed:
                _run_prisma(
                    schema,
                    database_url,
                    "migrate",
                    "resolve",
                    "--rolled-back",
                    migration_name,
                )
            _run_prisma(schema, database_url, "migrate", "deploy")
            normalized = asyncio.run(
                _with_database(
                    database_url,
                    lambda db: _finish_deploy(
                        db,
                        page_size=args.backfill_page_size,
                        max_pages=args.backfill_max_pages,
                    ),
                )
            )
            print(f"Normalized {normalized} legacy batch ownership record(s).")
        report = asyncio.run(_with_database(database_url, verify_readiness))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Organization-deletion migration failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready"] else 1


async def _finish_deploy(db: Any, *, page_size: int, max_pages: int) -> int:
    await ensure_indexes(db)
    return await backfill_ownership(db, page_size=page_size, max_pages=max_pages)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CONCURRENT_INDEXES",
    "REQUIRED_MIGRATIONS",
    "ConcurrentIndexSpec",
    "backfill_ownership",
    "classify_index_rows",
    "ensure_indexes",
    "main",
    "prepare_retry",
    "verify_readiness",
]
