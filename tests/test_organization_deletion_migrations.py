from __future__ import annotations

import hashlib
from typing import Any

import pytest

from src.organization_deletion_migrations import (
    CONCURRENT_INDEXES,
    backfill_ownership,
    classify_index_rows,
    prepare_retry,
)


def _index_row(spec, *, valid: bool = True, ready: bool = True) -> dict[str, Any]:  # noqa: ANN001
    return {
        "index_name": spec.name,
        "table_name": spec.table,
        "columns": list(spec.columns),
        "indisvalid": valid,
        "indisready": ready,
    }


def test_concurrent_index_registry_checks_every_organization_deletion_index() -> None:
    assert len(CONCURRENT_INDEXES) == 13
    assert {spec.name for spec in CONCURRENT_INDEXES} == {
        "deltallm_organizationtable_lifecycle_state_idx",
        "deltallm_batch_file_org_created_idx",
        "deltallm_mcpapproval_org_status_created_idx",
        "deltallm_promptrenderlog_org_created_idx",
        "deltallm_promptrenderlog_team_created_idx",
        "deltallm_promptrenderlog_api_key_created_idx",
        "deltallm_promptrenderlog_user_created_idx",
        "deltallm_mcpapproval_scope_status_created_idx",
        "deltallm_mcpapproval_api_key_created_idx",
        "deltallm_mcpapproval_user_created_idx",
        "deltallm_batch_webhook_outbox_org_status_created_idx",
        "deltallm_batch_job_user_created_idx",
        "deltallm_batch_create_session_user_status_created_idx",
    }


def test_index_classification_repairs_only_matching_invalid_indexes() -> None:
    rows = [_index_row(spec) for spec in CONCURRENT_INDEXES]
    rows[2]["indisvalid"] = False
    rows.pop(4)

    missing, invalid = classify_index_rows(rows)

    assert [spec.name for spec in missing] == [CONCURRENT_INDEXES[4].name]
    assert invalid == [CONCURRENT_INDEXES[2].name]


def test_index_classification_fails_closed_on_name_collision() -> None:
    rows = [_index_row(spec) for spec in CONCURRENT_INDEXES]
    rows[0]["columns"] = ["updated_at", "lifecycle_state"]

    with pytest.raises(RuntimeError, match="unexpected definition"):
        classify_index_rows(rows)


@pytest.mark.asyncio
async def test_ownership_backfill_uses_bounded_idempotent_pages() -> None:
    class _Prisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):  # noqa: ANN201
            self.calls.append((sql, params))
            return [{"record_id": f"record-{len(self.calls)}"}]

    prisma = _Prisma()

    normalized = await backfill_ownership(prisma, page_size=10, max_pages=10)

    assert normalized == 4
    assert len(prisma.calls) == 4
    assert all("FOR UPDATE OF record SKIP LOCKED" in sql for sql, _ in prisma.calls)
    assert all(params == (10,) for _sql, params in prisma.calls)


@pytest.mark.asyncio
async def test_retry_preparation_drops_only_allowlisted_invalid_index_and_checks_checksum(
    tmp_path,
) -> None:  # noqa: ANN001
    failed_spec = CONCURRENT_INDEXES[2]
    migration_dir = tmp_path / failed_spec.migration_name
    migration_dir.mkdir()
    migration_sql = b"CREATE INDEX CONCURRENTLY test"
    (migration_dir / "migration.sql").write_bytes(migration_sql)

    class _Prisma:
        def __init__(self) -> None:
            self.dropped: list[str] = []

        async def query_raw(self, sql: str, *params):  # noqa: ANN201
            del params
            if "FROM pg_index" in sql:
                return [_index_row(spec, valid=spec != failed_spec) for spec in CONCURRENT_INDEXES]
            if "to_regclass" in sql:
                return [{"table_name": "_prisma_migrations"}]
            if "FROM _prisma_migrations" in sql:
                return [
                    {
                        "migration_name": failed_spec.migration_name,
                        "checksum": hashlib.sha256(migration_sql).hexdigest(),
                    }
                ]
            raise AssertionError(sql)

        async def execute_raw(self, sql: str, *params):  # noqa: ANN201
            del params
            self.dropped.append(sql)
            return 0

    prisma = _Prisma()

    failed = await prepare_retry(prisma, tmp_path)

    assert failed == [failed_spec.migration_name]
    assert prisma.dropped == [f'DROP INDEX CONCURRENTLY IF EXISTS "{failed_spec.name}"']
