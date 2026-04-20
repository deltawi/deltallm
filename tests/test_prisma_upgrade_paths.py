from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.batch.create.models import BatchCreateSessionCreate, BatchCreateSessionStatus
from src.batch.models import (
    BatchCompletionOutboxCreate,
    BatchCompletionOutboxStatus,
    BatchItemCreate,
    BatchJobStatus,
)
from src.batch.repository import BatchRepository

try:
    from prisma import Prisma
except Exception:  # pragma: no cover
    Prisma = None  # type: ignore[assignment]


DATABASE_URL = os.getenv("DATABASE_URL")
SCENARIO = os.getenv("PRISMA_UPGRADE_PATH_SCENARIO", "").strip() or "unknown"
REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "prisma"
SCENARIO_METADATA_PATHS = {
    "legacy_v0_1_19_refusal": FIXTURE_DIR / "legacy_v0_1_19_metadata.json",
    "previous_release_v0_1_20_rc2_upgrade": FIXTURE_DIR / "previous_release_metadata.json",
}
LATEST_REPO_MIGRATION = max(
    path.name
    for path in (REPO_ROOT / "prisma" / "migrations").iterdir()
    if path.is_dir() and path.name[:1].isdigit()
)

_BATCH_TABLES = (
    ("batch_file", "deltallm_batch_file"),
    ("batch_job", "deltallm_batch_job"),
    ("batch_item", "deltallm_batch_item"),
    ("batch_completion_outbox", "deltallm_batch_completion_outbox"),
    ("batch_create_session", "deltallm_batch_create_session"),
)


def _scenario_fixture_metadata() -> dict[str, Any] | None:
    metadata_path = SCENARIO_METADATA_PATHS.get(SCENARIO)
    if metadata_path is None:
        return None
    return json.loads(metadata_path.read_text(encoding="utf-8"))


async def _connect_prisma() -> Any:
    if Prisma is None or not DATABASE_URL:  # pragma: no cover
        pytest.skip("DATABASE_URL and prisma client are required for upgrade-path smoke tests")
    client = Prisma(datasource={"url": DATABASE_URL})
    await client.connect()
    return client


async def _reset_batch_tables(db: Any) -> None:
    await db.execute_raw("DELETE FROM deltallm_batch_create_session")
    await db.execute_raw("DELETE FROM deltallm_batch_completion_outbox")
    await db.execute_raw("DELETE FROM deltallm_batch_item")
    await db.execute_raw("DELETE FROM deltallm_batch_job")
    await db.execute_raw("DELETE FROM deltallm_batch_file")


@pytest.fixture
async def upgrade_db() -> AsyncIterator[Any]:
    db = await _connect_prisma()
    try:
        yield db
    finally:
        await db.disconnect()


@pytest.fixture
async def clean_batch_repository(upgrade_db: Any) -> AsyncIterator[BatchRepository]:
    await _reset_batch_tables(upgrade_db)
    yield BatchRepository(prisma_client=upgrade_db)
    await _reset_batch_tables(upgrade_db)


@pytest.mark.asyncio
async def test_upgrade_path_schema_objects_exist(upgrade_db: Any) -> None:
    rows = await upgrade_db.query_raw(
        """
        SELECT
            to_regclass('public.deltallm_batch_file')::text AS batch_file,
            to_regclass('public.deltallm_batch_job')::text AS batch_job,
            to_regclass('public.deltallm_batch_item')::text AS batch_item,
            to_regclass('public.deltallm_batch_completion_outbox')::text AS batch_completion_outbox,
            to_regclass('public.deltallm_batch_create_session')::text AS batch_create_session,
            EXISTS (
                SELECT 1
                FROM pg_type
                WHERE typname = 'DeltaLLM_BatchJobStatus'
            ) AS has_batch_job_status_enum
        """
    )
    row = dict(rows[0]) if rows else {}

    for alias, relation_name in _BATCH_TABLES:
        assert row.get(alias) == relation_name
    assert row.get("has_batch_job_status_enum") is True


@pytest.mark.asyncio
async def test_upgrade_path_records_prisma_migration_history(upgrade_db: Any) -> None:
    rows = await upgrade_db.query_raw(
        """
        SELECT
            COUNT(*)::int AS migration_count,
            MAX(migration_name)::text AS latest_migration_name
        FROM _prisma_migrations
        """
    )
    row = dict(rows[0]) if rows else {}
    migration_count = int(row.get("migration_count") or 0)
    latest_migration_name = row.get("latest_migration_name")

    assert migration_count > 0
    assert latest_migration_name == LATEST_REPO_MIGRATION

    metadata = _scenario_fixture_metadata()
    if SCENARIO == "previous_release_v0_1_20_rc2_upgrade":
        assert metadata is not None
        assert metadata["latest_fixture_migration"] != LATEST_REPO_MIGRATION
        assert migration_count > int(metadata["migration_count"])

        watermark_rows = await upgrade_db.query_raw(
            """
            SELECT
                SUM(CASE WHEN migration_name = $1 THEN 1 ELSE 0 END)::int AS fixture_latest_count,
                SUM(CASE WHEN migration_name = $2 THEN 1 ELSE 0 END)::int AS repo_latest_count
            FROM _prisma_migrations
            """,
            metadata["latest_fixture_migration"],
            LATEST_REPO_MIGRATION,
        )
        watermark_row = dict(watermark_rows[0]) if watermark_rows else {}
        assert int(watermark_row.get("fixture_latest_count") or 0) == 1
        assert int(watermark_row.get("repo_latest_count") or 0) == 1


@pytest.mark.asyncio
async def test_upgrade_path_seeded_fixture_rows_survive(upgrade_db: Any) -> None:
    metadata = _scenario_fixture_metadata()
    if metadata is None:
        pytest.skip("Scenario does not restore a historical fixture with seeded rows")

    seed_rows = metadata["seed_rows"]

    if SCENARIO == "legacy_v0_1_19_refusal":
        rows = await upgrade_db.query_raw(
            """
            SELECT
                (SELECT filename::text FROM deltallm_batch_file WHERE file_id = $1) AS batch_file_name,
                (SELECT status::text FROM deltallm_batch_job WHERE batch_id = $2) AS batch_job_status,
                (SELECT status::text FROM deltallm_batch_item WHERE item_id = $3) AS batch_item_status,
                (SELECT status::text FROM deltallm_batch_completion_outbox WHERE completion_id = $4) AS completion_status
            """,
            seed_rows["batch_file_id"],
            seed_rows["batch_job_id"],
            seed_rows["batch_item_id"],
            seed_rows["completion_id"],
        )
        row = dict(rows[0]) if rows else {}

        assert row.get("batch_file_name") == "legacy-v019-input.jsonl"
        assert row.get("batch_job_status") == BatchJobStatus.QUEUED
        assert row.get("batch_item_status") == "completed"
        assert row.get("completion_status") == BatchCompletionOutboxStatus.QUEUED
        return

    if SCENARIO == "previous_release_v0_1_20_rc2_upgrade":
        rows = await upgrade_db.query_raw(
            """
            SELECT
                (SELECT filename::text FROM deltallm_batch_file WHERE file_id = $1) AS batch_file_name,
                (SELECT filename::text FROM deltallm_batch_file WHERE file_id = $2) AS session_file_name,
                (SELECT status::text FROM deltallm_batch_job WHERE batch_id = $3) AS batch_job_status,
                (SELECT status::text FROM deltallm_batch_item WHERE item_id = $4) AS batch_item_status,
                (SELECT status::text FROM deltallm_batch_completion_outbox WHERE completion_id = $5) AS completion_status,
                (SELECT status::text FROM deltallm_batch_create_session WHERE session_id = $6) AS create_session_status
            """,
            seed_rows["batch_file_id"],
            seed_rows["session_file_id"],
            seed_rows["batch_job_id"],
            seed_rows["batch_item_id"],
            seed_rows["completion_id"],
            seed_rows["create_session_id"],
        )
        row = dict(rows[0]) if rows else {}

        assert row.get("batch_file_name") == "prev-release-input.jsonl"
        assert row.get("session_file_name") == "prev-release-session.jsonl"
        assert row.get("batch_job_status") == BatchJobStatus.FINALIZING
        assert row.get("batch_item_status") == "completed"
        assert row.get("completion_status") == BatchCompletionOutboxStatus.QUEUED
        assert row.get("create_session_status") == BatchCreateSessionStatus.STAGED
        return

    pytest.fail(f"Unhandled historical fixture scenario: {SCENARIO}")


@pytest.mark.asyncio
async def test_upgrade_path_batch_repository_accepts_current_status_enum(
    clean_batch_repository: BatchRepository,
) -> None:
    repository = clean_batch_repository
    batch_file = await repository.create_file(
        purpose="batch",
        filename=f"{SCENARIO}-input.jsonl",
        bytes_size=24,
        storage_backend="local",
        storage_key=f"{SCENARIO}/input.jsonl",
        checksum="upgrade-path",
        created_by_api_key="key-upgrade-path",
        created_by_user_id=None,
        created_by_team_id=None,
    )

    assert batch_file is not None

    created = await repository.create_job(
        batch_id=f"{SCENARIO}-batch-job",
        endpoint="/v1/embeddings",
        input_file_id=batch_file.file_id,
        model="text-embedding-3-small",
        metadata={"scenario": SCENARIO},
        created_by_api_key="key-upgrade-path",
        created_by_user_id=None,
        created_by_team_id=None,
        status=BatchJobStatus.FINALIZING,
        total_items=2,
    )

    assert created is not None
    assert created.status == BatchJobStatus.FINALIZING

    rows = await repository.prisma.query_raw(  # type: ignore[union-attr]
        """
        SELECT status
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        created.batch_id,
    )
    assert rows
    assert rows[0]["status"] == BatchJobStatus.FINALIZING


@pytest.mark.asyncio
async def test_upgrade_path_completion_outbox_and_create_session_tables_are_usable(
    clean_batch_repository: BatchRepository,
) -> None:
    repository = clean_batch_repository
    batch_file = await repository.create_file(
        purpose="batch",
        filename=f"{SCENARIO}-workload.jsonl",
        bytes_size=48,
        storage_backend="local",
        storage_key=f"{SCENARIO}/workload.jsonl",
        checksum="upgrade-path-workload",
        created_by_api_key="key-upgrade-path",
        created_by_user_id=None,
        created_by_team_id=None,
    )

    assert batch_file is not None

    job = await repository.create_job(
        batch_id=f"{SCENARIO}-outbox-batch",
        endpoint="/v1/embeddings",
        input_file_id=batch_file.file_id,
        model="text-embedding-3-small",
        metadata={"scenario": SCENARIO, "surface": "completion_outbox"},
        created_by_api_key="key-upgrade-path",
        created_by_user_id=None,
        created_by_team_id=None,
        status=BatchJobStatus.QUEUED,
        total_items=1,
    )

    assert job is not None

    created_items = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(
                line_number=1,
                custom_id=f"{SCENARIO}-item-1",
                request_body={"model": "text-embedding-3-small", "input": "hello"},
            )
        ],
    )
    assert created_items == 1

    item_rows = await repository.prisma.query_raw(  # type: ignore[union-attr]
        """
        SELECT item_id
        FROM deltallm_batch_item
        WHERE batch_id = $1
        ORDER BY line_number ASC
        """,
        job.batch_id,
    )
    assert item_rows
    item_id = str(item_rows[0]["item_id"])

    completion_ids = await repository.enqueue_completion_outbox_many(
        [
            BatchCompletionOutboxCreate(
                batch_id=job.batch_id,
                item_id=item_id,
                payload_json={"batch_id": job.batch_id, "item_id": item_id, "scenario": SCENARIO},
                status=BatchCompletionOutboxStatus.QUEUED,
            )
        ]
    )
    assert len(completion_ids) == 1

    outbox_rows = await repository.prisma.query_raw(  # type: ignore[union-attr]
        """
        SELECT status
        FROM deltallm_batch_completion_outbox
        WHERE completion_id = $1
        """,
        completion_ids[0],
    )
    assert outbox_rows
    assert outbox_rows[0]["status"] == BatchCompletionOutboxStatus.QUEUED

    session = await repository.create_sessions.create_session(
        BatchCreateSessionCreate(
            target_batch_id=f"{SCENARIO}-create-session-batch",
            endpoint="/v1/embeddings",
            input_file_id=batch_file.file_id,
            staged_storage_backend="local",
            staged_storage_key=f"{SCENARIO}/staged.jsonl",
            staged_checksum="upgrade-path-stage",
            staged_bytes=128,
            expected_item_count=1,
            status=BatchCreateSessionStatus.STAGED,
            inferred_model="text-embedding-3-small",
            metadata={"scenario": SCENARIO},
            created_by_api_key="key-upgrade-path",
            created_by_user_id=None,
            created_by_team_id=None,
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )
    )

    assert session is not None
    assert session.status == BatchCreateSessionStatus.STAGED

    session_rows = await repository.prisma.query_raw(  # type: ignore[union-attr]
        """
        SELECT status
        FROM deltallm_batch_create_session
        WHERE session_id = $1
        """,
        session.session_id,
    )
    assert session_rows
    assert session_rows[0]["status"] == BatchCreateSessionStatus.STAGED
