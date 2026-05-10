from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from src.api.admin.endpoints.common import AuthScope
from src.batch.create import (
    BatchCreateSessionAdminService,
    BatchCreateArtifactStorageBackend,
    BatchCreatePromotionError,
    BatchCreateSessionCleanupConfig,
    BatchCreateSessionCleanupWorker,
    BatchCreateSessionCreate,
    BatchCreateSessionPromoter,
    BatchCreateSessionStatus,
    BatchCreateSessionStager,
    BatchCreateStagedRequest,
)
from src.batch.create.service import BatchCreateSessionService
from src.batch.cleanup import BatchCleanupConfig, BatchRetentionCleanupWorker
from src.batch.models import BATCH_JOB_STATUS_VALUES, BatchCompletionOutboxCreate, BatchItemCreate
from src.batch.models import encode_operator_failed_reason
from src.batch.repository import BatchRepository
from src.batch.service import BatchService
from src.batch.storage import LocalBatchArtifactStorage, S3BatchArtifactStorage
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.auth.roles import Permission
from src.models.responses import UserAPIKeyAuth

try:
    from prisma import Prisma
except Exception:  # pragma: no cover
    Prisma = None  # type: ignore[assignment]


DATABASE_URL = os.getenv("DATABASE_URL")
BATCH_JOB_STATUS_RECONCILIATION_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "prisma"
    / "migrations"
    / "20260424120000_batch_job_status_contract_reconciliation"
    / "migration.sql"
)


class _Upload:
    def __init__(self, payload: bytes, filename: str = "batch.jsonl") -> None:
        self.filename = filename
        self._payload = payload
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.modified_at: dict[tuple[str, str], datetime] = {}

    def upload_fileobj(self, fileobj, bucket: str, key: str, ExtraArgs=None) -> None:  # noqa: ANN001, N803
        del ExtraArgs
        self.objects[(bucket, key)] = fileobj.read()
        self.modified_at[(bucket, key)] = datetime.now(tz=UTC)

    def download_fileobj(self, bucket: str, key: str, fileobj) -> None:  # noqa: ANN001
        fileobj.write(self.objects[(bucket, key)])
        fileobj.seek(0)

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)
        self.modified_at.pop((Bucket, Key), None)

    def list_objects_v2(self, *, Bucket: str, Prefix: str, MaxKeys: int, ContinuationToken=None):  # noqa: ANN001, N803
        del ContinuationToken
        contents = []
        for (bucket, key), _payload in sorted(self.objects.items()):
            if bucket != Bucket or not key.startswith(Prefix):
                continue
            contents.append({"Key": key, "LastModified": self.modified_at[(bucket, key)]})
            if len(contents) >= MaxKeys:
                break
        return {"Contents": contents, "IsTruncated": False}


class _NoopBudgetService:
    async def check_budgets(self, **kwargs) -> None:  # noqa: ANN003
        del kwargs


class _NoopSpendTrackingService:
    async def log_spend(self, **kwargs) -> None:  # noqa: ANN003
        del kwargs

    async def log_request_failure(self, **kwargs) -> None:  # noqa: ANN003
        del kwargs


class _NoopPassiveHealthTracker:
    async def record_request_outcome(
        self,
        deployment_id: str,
        success: bool,
        error: str | None = None,
        *,
        exc: Exception | None = None,
    ) -> None:
        del deployment_id, success, error, exc


class _NoopRouterStateBackend:
    async def increment_usage_counters(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs


class _AllowAllCallableTargetGrantService:
    def resolve_policy_allowlist(self, auth):  # noqa: ANN001
        del auth
        return SimpleNamespace(allowlist=None, authoritative=True, fallback_reason=None)


async def _connect_prisma() -> Any:
    if Prisma is None or not DATABASE_URL:  # pragma: no cover
        pytest.skip("DATABASE_URL and prisma client are required for DB-backed batch tests")
    client = Prisma(datasource={"url": DATABASE_URL})
    await client.connect()
    return client


async def _reset_batch_tables(db: Any) -> None:
    await db.execute_raw("DELETE FROM deltallm_batch_create_session")
    await db.execute_raw("DELETE FROM deltallm_batch_completion_outbox")
    await db.execute_raw("DELETE FROM deltallm_batch_item")
    await db.execute_raw("DELETE FROM deltallm_batch_job")
    await db.execute_raw("DELETE FROM deltallm_batch_file")


def _batch_job_status_reconciliation_sql() -> str:
    return BATCH_JOB_STATUS_RECONCILIATION_MIGRATION_PATH.read_text()


async def _execute_batch_job_status_reconciliation(db: Any) -> None:
    await db.execute_raw(_batch_job_status_reconciliation_sql())


async def _batch_job_status_column_contract(db: Any) -> dict[str, Any]:
    rows = await db.query_raw(
        """
        SELECT
            data_type,
            udt_name,
            column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'deltallm_batch_job'
          AND column_name = 'status'
        """
    )
    assert rows
    return dict(rows[0])


async def _enum_labels(db: Any, type_name: str) -> list[str]:
    rows = await db.query_raw(
        """
        SELECT e.enumlabel
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = $1
        ORDER BY e.enumsortorder
        """,
        type_name,
    )
    return [str(row["enumlabel"]) for row in rows]


async def _seed_raw_batch_job(db: Any, *, input_file_id: str, batch_id: str, status: str) -> None:
    column = await _batch_job_status_column_contract(db)
    status_udt_name = str(column.get("udt_name") or "")

    if status_udt_name == "text":
        status_value_sql = "$2"
    elif status_udt_name in {"DeltaLLM_BatchJobStatus", "DeltaLLM_BatchJobStatus_next"}:
        status_value_sql = f'$2::"{status_udt_name}"'
    else:  # pragma: no cover - defensive branch for unexpected schema drift in tests
        raise AssertionError(f"unexpected deltallm_batch_job.status type in test setup: {status_udt_name}")

    await db.execute_raw(
        f"""
        INSERT INTO deltallm_batch_job (
            batch_id, endpoint, status, execution_mode, input_file_id, total_items
        )
        VALUES (
            $1,
            '/v1/embeddings',
            {status_value_sql},
            'managed_internal',
            $3,
            0
        )
        """,
        batch_id,
        status,
        input_file_id,
    )


async def _assert_final_batch_job_status_contract(db: Any) -> None:
    column = await _batch_job_status_column_contract(db)
    assert column["udt_name"] == "DeltaLLM_BatchJobStatus"
    assert column["data_type"] == "USER-DEFINED"
    assert column["column_default"] is None
    assert await _enum_labels(db, "DeltaLLM_BatchJobStatus") == list(BATCH_JOB_STATUS_VALUES)
    assert await _enum_labels(db, "DeltaLLM_BatchJobStatus_next") == []


@pytest.fixture
async def batch_db():
    db = await _connect_prisma()
    try:
        rows = await db.query_raw(
            """
            SELECT
                to_regclass('public.deltallm_batch_job')::text AS batch_job,
                to_regclass('public.deltallm_batch_completion_outbox')::text AS batch_completion_outbox
            """
        )
        row = dict(rows[0]) if rows else {}
        if row.get("batch_job") is None or row.get("batch_completion_outbox") is None:
            pytest.skip("Batch tables are missing; run prisma migrate deploy before DB-backed batch tests")
        await _reset_batch_tables(db)
        yield db
    finally:
        await _reset_batch_tables(db)
        await db.disconnect()


@pytest.mark.asyncio
async def test_batch_job_status_column_uses_final_enum_contract(batch_db) -> None:
    await _assert_final_batch_job_status_contract(batch_db)


@pytest.mark.asyncio
async def test_batch_job_status_enum_query_shape_works_against_real_postgres(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        created_by_organization_id=None,
        expires_at=None,
        status="queued",
    )

    assert job is not None
    await _assert_final_batch_job_status_contract(batch_db)

    rows = await batch_db.query_raw(
        """
        SELECT batch_id, status
        FROM deltallm_batch_job
        WHERE status = $1::"DeltaLLM_BatchJobStatus"
        """,
        "queued",
    )

    assert [str(dict(row)["batch_id"]) for row in rows] == [job.batch_id]
    assert [str(dict(row)["status"]) for row in rows] == ["queued"]


@pytest.mark.asyncio
async def test_batch_job_status_reconciliation_converts_text_backed_column(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)

    try:
        await batch_db.execute_raw(
            """
            ALTER TABLE deltallm_batch_job
            ALTER COLUMN status DROP DEFAULT
            """
        )
        await batch_db.execute_raw(
            """
            ALTER TABLE deltallm_batch_job
            ALTER COLUMN status TYPE text
            USING (status::text)
            """
        )
        await _seed_raw_batch_job(
            batch_db,
            input_file_id=input_file_id,
            batch_id="batch-text-status",
            status="queued",
        )

        column = await _batch_job_status_column_contract(batch_db)
        assert column["udt_name"] == "text"

        await _execute_batch_job_status_reconciliation(batch_db)

        await _assert_final_batch_job_status_contract(batch_db)
    finally:
        await _reset_batch_tables(batch_db)
        await _execute_batch_job_status_reconciliation(batch_db)


@pytest.mark.asyncio
async def test_batch_job_status_reconciliation_converts_legacy_enum_shape(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)

    try:
        await batch_db.execute_raw(
            """
            CREATE TYPE "DeltaLLM_BatchJobStatus_legacy" AS ENUM (
                'validating',
                'queued',
                'in_progress',
                'finalizing',
                'completed',
                'failed',
                'cancelled',
                'expired'
            )
            """
        )
        await batch_db.execute_raw(
            """
            ALTER TABLE deltallm_batch_job
            ALTER COLUMN status DROP DEFAULT
            """
        )
        await batch_db.execute_raw(
            """
            ALTER TABLE deltallm_batch_job
            ALTER COLUMN status TYPE "DeltaLLM_BatchJobStatus_legacy"
            USING (status::text::"DeltaLLM_BatchJobStatus_legacy")
            """
        )
        await batch_db.execute_raw('DROP TYPE "DeltaLLM_BatchJobStatus"')
        await batch_db.execute_raw('ALTER TYPE "DeltaLLM_BatchJobStatus_legacy" RENAME TO "DeltaLLM_BatchJobStatus"')
        await _seed_raw_batch_job(
            batch_db,
            input_file_id=input_file_id,
            batch_id="batch-legacy-enum",
            status="queued",
        )

        assert await _enum_labels(batch_db, "DeltaLLM_BatchJobStatus") == [
            "validating",
            *list(BATCH_JOB_STATUS_VALUES),
        ]

        await _execute_batch_job_status_reconciliation(batch_db)

        await _assert_final_batch_job_status_contract(batch_db)
    finally:
        await _reset_batch_tables(batch_db)
        await _execute_batch_job_status_reconciliation(batch_db)


@pytest.mark.asyncio
async def test_batch_job_status_reconciliation_finishes_partial_next_type_cutover(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)

    try:
        await batch_db.execute_raw(
            """
            CREATE TYPE "DeltaLLM_BatchJobStatus_legacy" AS ENUM (
                'validating',
                'queued',
                'in_progress',
                'finalizing',
                'completed',
                'failed',
                'cancelled',
                'expired'
            )
            """
        )
        await batch_db.execute_raw(
            """
            ALTER TABLE deltallm_batch_job
            ALTER COLUMN status DROP DEFAULT
            """
        )
        await batch_db.execute_raw(
            """
            ALTER TABLE deltallm_batch_job
            ALTER COLUMN status TYPE "DeltaLLM_BatchJobStatus_legacy"
            USING (status::text::"DeltaLLM_BatchJobStatus_legacy")
            """
        )
        await batch_db.execute_raw('DROP TYPE "DeltaLLM_BatchJobStatus"')
        await batch_db.execute_raw('ALTER TYPE "DeltaLLM_BatchJobStatus_legacy" RENAME TO "DeltaLLM_BatchJobStatus"')
        await batch_db.execute_raw(
            """
            CREATE TYPE "DeltaLLM_BatchJobStatus_next" AS ENUM (
                'queued',
                'in_progress',
                'finalizing',
                'completed',
                'failed',
                'cancelled',
                'expired'
            )
            """
        )
        await batch_db.execute_raw(
            """
            ALTER TABLE deltallm_batch_job
            ALTER COLUMN status TYPE "DeltaLLM_BatchJobStatus_next"
            USING (status::text::"DeltaLLM_BatchJobStatus_next")
            """
        )
        await _seed_raw_batch_job(
            batch_db,
            input_file_id=input_file_id,
            batch_id="batch-partial-next-cutover",
            status="queued",
        )

        column = await _batch_job_status_column_contract(batch_db)
        assert column["udt_name"] == "DeltaLLM_BatchJobStatus_next"
        assert await _enum_labels(batch_db, "DeltaLLM_BatchJobStatus") == [
            "validating",
            *list(BATCH_JOB_STATUS_VALUES),
        ]
        assert await _enum_labels(batch_db, "DeltaLLM_BatchJobStatus_next") == list(BATCH_JOB_STATUS_VALUES)

        await _execute_batch_job_status_reconciliation(batch_db)

        await _assert_final_batch_job_status_contract(batch_db)
    finally:
        await _reset_batch_tables(batch_db)
        await _execute_batch_job_status_reconciliation(batch_db)


@pytest.mark.asyncio
async def test_batch_job_status_reconciliation_rejects_invalid_text_values(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)

    try:
        await batch_db.execute_raw(
            """
            ALTER TABLE deltallm_batch_job
            ALTER COLUMN status DROP DEFAULT
            """
        )
        await batch_db.execute_raw(
            """
            ALTER TABLE deltallm_batch_job
            ALTER COLUMN status TYPE text
            USING (status::text)
            """
        )
        await _seed_raw_batch_job(
            batch_db,
            input_file_id=input_file_id,
            batch_id="batch-invalid-text-status",
            status="broken",
        )

        with pytest.raises(Exception, match="invalid existing values"):
            await _execute_batch_job_status_reconciliation(batch_db)
    finally:
        await _reset_batch_tables(batch_db)
        await _execute_batch_job_status_reconciliation(batch_db)


async def _create_input_file(service: BatchService, *, auth: UserAPIKeyAuth, payload: bytes) -> str:
    created = await service.create_file(
        auth=auth,
        upload=_Upload(payload),  # type: ignore[arg-type]
        purpose="batch",
    )
    return str(created["id"])


async def _seed_batch_file(repository: BatchRepository) -> str:
    file_record = await repository.create_file(
        purpose="batch",
        filename="input.jsonl",
        bytes_size=16,
        storage_backend="local",
        storage_key="seed/input.jsonl",
        checksum="seed",
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
    )
    assert file_record is not None
    return file_record.file_id


@pytest.mark.asyncio
async def test_db_backed_backfill_repairs_complete_scheduler_aggregate_drift(batch_db) -> None:
    scheduler_column_rows = await batch_db.query_raw(
        """
        SELECT COUNT(*)::int AS scheduler_columns
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'deltallm_batch_job'
          AND column_name IN (
              'scheduler_version',
              'scheduling_model',
              'scheduling_model_group',
              'estimated_work_units',
              'remaining_work_units'
          )
        """
    )
    scheduler_columns = int(dict(scheduler_column_rows[0]).get("scheduler_columns") or 0)
    if scheduler_columns < 5:
        pytest.skip("Phase 1 scheduler columns are missing; run prisma migrate deploy first")

    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key=None,
        created_by_user_id=None,
        created_by_team_id="team-a",
        expires_at=None,
        status="queued",
        total_items=2,
        scheduler_version="fifo_v1",
        scheduling_model="m1",
        scheduling_model_group="m1",
        scheduling_endpoint="/v1/embeddings",
        tenant_scope_type="team",
        tenant_scope_id="team-a",
        service_tier="standard",
        estimated_work_units=1_000,
        remaining_work_units=1_000,
        size_class="m",
        scheduler_debug={"estimator_version": "v1"},
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(
                line_number=1,
                custom_id="c1",
                request_body={"model": "m1", "input": "a"},
                scheduling_model="m1",
                scheduling_model_group="m1",
                estimated_work_units=5,
            ),
            BatchItemCreate(
                line_number=2,
                custom_id="c2",
                request_body={"model": "m1", "input": "b"},
                scheduling_model="m1",
                scheduling_model_group="m1",
                estimated_work_units=5,
            ),
        ],
    )
    assert inserted == 2

    result = await repository.backfill_scheduler_dimensions(limit=10)

    rows = await batch_db.query_raw(
        """
        SELECT estimated_work_units, remaining_work_units, size_class
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        job.batch_id,
    )
    assert result == {"jobs": 1, "items": 0}
    assert rows == [{"estimated_work_units": 10, "remaining_work_units": 10, "size_class": "xs"}]


async def _seed_create_session_input_file(repository: BatchRepository) -> str:
    file_record = await repository.create_file(
        purpose="batch",
        filename="input.jsonl",
        bytes_size=32,
        storage_backend="local",
        storage_key="seed/input.jsonl",
        checksum="seed",
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
    )
    assert file_record is not None
    return file_record.file_id


async def _seed_staged_create_session(
    *,
    repository: BatchRepository,
    staging: BatchCreateArtifactStorageBackend,
    input_file_id: str,
    target_batch_id: str,
    request_count: int = 1,
    created_by_api_key: str = "key-a",
    created_by_team_id: str | None = None,
    created_by_organization_id: str | None = None,
    status: str = "staged",
) -> Any:
    requests = [
        BatchCreateStagedRequest(
            line_number=index,
            custom_id=f"req-{index}",
            request_body={"model": "m1", "input": f"hello-{index}"},
        )
        for index in range(1, request_count + 1)
    ]
    artifact = await staging.write_records(requests, filename=f"{target_batch_id}.jsonl")
    session = await repository.create_sessions.create_session(
        BatchCreateSessionCreate(
            target_batch_id=target_batch_id,
            endpoint="/v1/embeddings",
            input_file_id=input_file_id,
            staged_storage_backend=artifact.storage_backend,
            staged_storage_key=artifact.storage_key,
            staged_checksum=artifact.checksum,
            staged_bytes=artifact.bytes_size,
            expected_item_count=request_count,
            status=status,
            inferred_model="m1",
            created_by_api_key=created_by_api_key,
            created_by_team_id=created_by_team_id,
            created_by_organization_id=created_by_organization_id,
        )
    )
    assert session is not None
    return session


def _build_cutover_batch_service(
    *,
    repository: BatchRepository,
    storage,
    storage_registry: dict[str, Any] | None = None,
    idempotency_enabled: bool = False,
    max_pending_batches_per_scope: int = 20,
) -> BatchService:
    active_storage_registry = {"local": storage}
    if storage_registry:
        active_storage_registry.update(storage_registry)
    staging_backend = BatchCreateArtifactStorageBackend(
        storage=storage,
        storage_registry=active_storage_registry,
    )
    create_session_service = BatchCreateSessionService(
        repository=repository,
        create_session_repository=repository.create_sessions,
        stager=BatchCreateSessionStager(
            repository=repository.create_sessions,
            staging=staging_backend,
        ),
        promoter=BatchCreateSessionPromoter(
            repository=repository,
            staging=staging_backend,
            max_pending_batches_per_scope=max_pending_batches_per_scope,
        ),
        storage_registry=active_storage_registry,
        max_file_bytes=52_428_800,
        max_items_per_batch=10_000,
        max_line_bytes=1_048_576,
        storage_chunk_size=65_536,
        max_pending_batches_per_scope=max_pending_batches_per_scope,
        idempotency_enabled=idempotency_enabled,
    )
    return BatchService(
        repository=repository,
        storage=storage,
        storage_registry=active_storage_registry,
        create_session_service=create_session_service,
    )


@pytest.mark.asyncio
async def test_db_backed_concurrent_batch_create_enforces_pending_cap(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.create.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "artifacts"))
    service = _build_cutover_batch_service(
        repository=repository,
        storage=storage,
        max_pending_batches_per_scope=1,
    )
    auth = UserAPIKeyAuth(api_key="key-a")
    input_file_id = await _create_input_file(
        service,
        auth=auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )

    async def _create():
        return await service.create_embeddings_batch(
            auth=auth,
            input_file_id=input_file_id,
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
        )

    results = await asyncio.gather(_create(), _create(), return_exceptions=True)

    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert successes[0]["status"] == "validating"
    assert successes[0]["completion_window"] == "24h"
    assert successes[0]["errors"] is None
    assert len(failures) == 1
    assert isinstance(failures[0], HTTPException)
    assert failures[0].status_code == 429

    job_rows = await batch_db.query_raw("SELECT batch_id, status, total_items FROM deltallm_batch_job")
    item_rows = await batch_db.query_raw("SELECT item_id, batch_id FROM deltallm_batch_item")
    assert len(job_rows) == 1
    assert dict(job_rows[0])["status"] == "queued"
    assert int(dict(job_rows[0])["total_items"]) == 1
    assert len(item_rows) == 1
    assert str(dict(item_rows[0])["batch_id"]) == str(dict(job_rows[0])["batch_id"])


@pytest.mark.asyncio
async def test_db_backed_batch_create_rolls_back_after_insert_failure(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.create.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    class _FailingBatchRepository(BatchRepository):
        def __init__(self, prisma_client: Any, state: dict[str, int]) -> None:
            super().__init__(prisma_client)
            self.state = state

        def with_prisma(self, prisma_client: Any | None) -> "_FailingBatchRepository":
            return _FailingBatchRepository(prisma_client, self.state)

        async def create_items(self, batch_id: str, items: list[BatchItemCreate]) -> int:
            del batch_id, items
            self.state["calls"] += 1
            raise RuntimeError("simulated item insert failure")

    state = {"calls": 0}
    repository = _FailingBatchRepository(batch_db, state)
    storage = LocalBatchArtifactStorage(str(tmp_path / "artifacts"))
    service = _build_cutover_batch_service(repository=repository, storage=storage)
    auth = UserAPIKeyAuth(api_key="key-a")
    input_file_id = await _create_input_file(
        service,
        auth=auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )

    with pytest.raises(HTTPException) as exc:
        await service.create_embeddings_batch(
            auth=auth,
            input_file_id=input_file_id,
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
        )

    assert exc.value.status_code == 503
    assert "simulated item insert failure" in str(exc.value.detail)
    assert state["calls"] == 1
    job_rows = await batch_db.query_raw("SELECT batch_id FROM deltallm_batch_job")
    item_rows = await batch_db.query_raw("SELECT item_id FROM deltallm_batch_item")
    assert job_rows == []
    assert item_rows == []


@pytest.mark.asyncio
async def test_db_backed_batch_create_persists_organization_ownership(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.create.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "artifacts"))
    service = _build_cutover_batch_service(repository=repository, storage=storage)
    auth = UserAPIKeyAuth(api_key="key-a", organization_id="org-1")
    input_file_id = await _create_input_file(
        service,
        auth=auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )

    created = await service.create_embeddings_batch(
        auth=auth,
        input_file_id=input_file_id,
        endpoint="/v1/embeddings",
        metadata=None,
        completion_window=None,
    )

    file_rows = await batch_db.query_raw(
        """
        SELECT created_by_api_key, created_by_team_id, created_by_organization_id
        FROM deltallm_batch_file
        WHERE file_id = $1
        """,
        input_file_id,
    )
    job_rows = await batch_db.query_raw(
        """
        SELECT created_by_api_key, created_by_team_id, created_by_organization_id
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        str(created["id"]),
    )

    assert dict(file_rows[0]) == {
        "created_by_api_key": "key-a",
        "created_by_team_id": None,
        "created_by_organization_id": "org-1",
    }
    assert dict(job_rows[0]) == {
        "created_by_api_key": "key-a",
        "created_by_team_id": None,
        "created_by_organization_id": "org-1",
    }


@pytest.mark.asyncio
async def test_db_backed_batch_create_cutover_returns_normal_batch_object_and_queued_job(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.create.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "cutover-artifacts"))
    service = _build_cutover_batch_service(repository=repository, storage=storage)
    auth = UserAPIKeyAuth(api_key="key-a")
    input_file_id = await _create_input_file(
        service,
        auth=auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )

    result = await service.create_embeddings_batch_result(
        auth=auth,
        input_file_id=input_file_id,
        endpoint="/v1/embeddings",
        metadata={"region": "eu"},
        completion_window=None,
    )

    assert result.response["id"]
    assert result.response["status"] == "validating"
    assert result.response["completion_window"] == "24h"
    assert result.response["errors"] is None
    assert result.audit_metadata["create_path"] == "create_session"
    assert result.audit_metadata["idempotency_resolution"] == "not_requested"

    session = await repository.create_sessions.get_session_by_target_batch_id(str(result.response["id"]))
    assert session is not None
    assert session.status == BatchCreateSessionStatus.COMPLETED

    job_rows = await batch_db.query_raw(
        """
        SELECT batch_id, status, total_items
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        str(result.response["id"]),
    )
    assert len(job_rows) == 1
    assert dict(job_rows[0])["status"] == "queued"
    assert int(dict(job_rows[0])["total_items"]) == 1

    all_job_rows = await batch_db.query_raw("SELECT status FROM deltallm_batch_job")
    assert {str(dict(row)["status"]) for row in all_job_rows} == {"queued"}


@pytest.mark.asyncio
async def test_db_backed_batch_create_cutover_reuses_same_batch_for_same_idempotency_key(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.create.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "cutover-idempotent-artifacts"))
    service = _build_cutover_batch_service(
        repository=repository,
        storage=storage,
        idempotency_enabled=True,
    )
    auth = UserAPIKeyAuth(api_key="key-a")
    input_file_id = await _create_input_file(
        service,
        auth=auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )

    first = await service.create_embeddings_batch_result(
        auth=auth,
        input_file_id=input_file_id,
        endpoint="/v1/embeddings",
        metadata={"region": "eu"},
        completion_window=None,
        idempotency_key="idem-1",
    )
    second = await service.create_embeddings_batch_result(
        auth=auth,
        input_file_id=input_file_id,
        endpoint="/v1/embeddings",
        metadata={"region": "eu"},
        completion_window=None,
        idempotency_key="idem-1",
    )

    assert first.response["id"] == second.response["id"]
    assert second.audit_metadata["idempotency_resolution"] == "existing"

    session = await repository.create_sessions.get_session_by_idempotency_key(
        idempotency_scope_key="api_key:key-a",
        idempotency_key="idem-1",
    )
    assert session is not None
    assert session.status == BatchCreateSessionStatus.COMPLETED
    assert session.promotion_attempt_count == 1

    session_rows = await batch_db.query_raw("SELECT session_id FROM deltallm_batch_create_session")
    job_rows = await batch_db.query_raw("SELECT batch_id FROM deltallm_batch_job")
    assert len(session_rows) == 1
    assert len(job_rows) == 1


@pytest.mark.asyncio
async def test_db_backed_batch_create_cutover_rejects_idempotency_payload_mismatch(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.create.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "cutover-idempotent-mismatch-artifacts"))
    service = _build_cutover_batch_service(
        repository=repository,
        storage=storage,
        idempotency_enabled=True,
    )
    auth = UserAPIKeyAuth(api_key="key-a")
    input_file_id = await _create_input_file(
        service,
        auth=auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )

    first = await service.create_embeddings_batch_result(
        auth=auth,
        input_file_id=input_file_id,
        endpoint="/v1/embeddings",
        metadata={"region": "eu"},
        completion_window=None,
        idempotency_key="idem-1",
    )

    with pytest.raises(HTTPException) as exc:
        await service.create_embeddings_batch_result(
            auth=auth,
            input_file_id=input_file_id,
            endpoint="/v1/embeddings",
            metadata={"region": "us"},
            completion_window=None,
            idempotency_key="idem-1",
        )

    assert exc.value.status_code == 409
    assert first.response["id"]

    session_rows = await batch_db.query_raw("SELECT session_id FROM deltallm_batch_create_session")
    job_rows = await batch_db.query_raw("SELECT batch_id FROM deltallm_batch_job")
    assert len(session_rows) == 1
    assert len(job_rows) == 1


@pytest.mark.asyncio
async def test_db_backed_batch_create_cutover_isolates_idempotency_by_team_within_same_org(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.create.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "cutover-team-idempotency-artifacts"))
    service = _build_cutover_batch_service(
        repository=repository,
        storage=storage,
        idempotency_enabled=True,
    )
    team_a_auth = UserAPIKeyAuth(api_key="key-a", team_id="team-a", organization_id="org-1")
    team_b_auth = UserAPIKeyAuth(api_key="key-b", team_id="team-b", organization_id="org-1")

    input_file_id_a = await _create_input_file(
        service,
        auth=team_a_auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )
    input_file_id_b = await _create_input_file(
        service,
        auth=team_b_auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )

    first = await service.create_embeddings_batch_result(
        auth=team_a_auth,
        input_file_id=input_file_id_a,
        endpoint="/v1/embeddings",
        metadata={"region": "eu"},
        completion_window=None,
        idempotency_key="shared-key",
    )
    second = await service.create_embeddings_batch_result(
        auth=team_b_auth,
        input_file_id=input_file_id_b,
        endpoint="/v1/embeddings",
        metadata={"region": "eu"},
        completion_window=None,
        idempotency_key="shared-key",
    )

    assert first.response["id"] != second.response["id"]

    session_rows = await batch_db.query_raw(
        """
        SELECT idempotency_scope_key, target_batch_id
        FROM deltallm_batch_create_session
        ORDER BY idempotency_scope_key ASC
        """
    )
    assert [dict(row) for row in session_rows] == [
        {"idempotency_scope_key": "team:team-a", "target_batch_id": str(first.response["id"])},
        {"idempotency_scope_key": "team:team-b", "target_batch_id": str(second.response["id"])},
    ]


@pytest.mark.asyncio
async def test_db_backed_batch_create_cutover_precheck_rejects_before_session_staging(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.create.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    repository = BatchRepository(batch_db)
    storage_root = tmp_path / "cutover-precheck-artifacts"
    storage = LocalBatchArtifactStorage(str(storage_root))
    service = _build_cutover_batch_service(
        repository=repository,
        storage=storage,
        max_pending_batches_per_scope=1,
    )
    auth = UserAPIKeyAuth(api_key="key-a")

    existing_input_file_id = await _seed_batch_file(repository)
    existing_job = await repository.create_job(
        batch_id="existing-batch",
        endpoint="/v1/embeddings",
        input_file_id=existing_input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        created_by_organization_id=None,
        execution_mode="managed_internal",
        status="queued",
        total_items=1,
    )
    assert existing_job is not None

    input_file_id = await _create_input_file(
        service,
        auth=auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )

    with pytest.raises(HTTPException) as exc:
        await service.create_embeddings_batch_result(
            auth=auth,
            input_file_id=input_file_id,
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
        )

    assert exc.value.status_code == 429

    session_rows = await batch_db.query_raw("SELECT session_id FROM deltallm_batch_create_session")
    job_rows = await batch_db.query_raw("SELECT batch_id FROM deltallm_batch_job ORDER BY batch_id ASC")
    assert session_rows == []
    assert [dict(row) for row in job_rows] == [{"batch_id": "existing-batch"}]

    staged_paths = [path for path in storage_root.rglob("*") if "batch-create-stage" in path.parts]
    assert staged_paths == []


@pytest.mark.asyncio
async def test_db_backed_create_session_repository_lifecycle_round_trip(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_create_session_input_file(repository)
    staging = BatchCreateArtifactStorageBackend(
        storage=LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts")),
    )
    artifact = await staging.write_records(
        [
            BatchCreateStagedRequest(
                line_number=1,
                custom_id="req-1",
                request_body={"model": "m1", "input": "hello"},
            )
        ],
        filename="session-1.jsonl",
    )

    created = await repository.create_sessions.create_session(
        BatchCreateSessionCreate(
            target_batch_id="batch-session-1",
            endpoint="/v1/embeddings",
            input_file_id=input_file_id,
            staged_storage_backend=artifact.storage_backend,
            staged_storage_key=artifact.storage_key,
            staged_checksum=artifact.checksum,
            staged_bytes=artifact.bytes_size,
            expected_item_count=1,
            created_by_api_key="key-a",
        )
    )
    assert created is not None
    assert created.status == "staged"

    failed = await repository.create_sessions.mark_session_failed_retryable(
        created.session_id,
        error_code="timeout",
        error_message="timed out",
        attempted_at=datetime.now(tz=UTC),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    assert failed is not None
    assert failed.status == "failed_retryable"
    assert failed.last_error_code == "timeout"

    fetched = await repository.create_sessions.get_session(created.session_id)
    assert fetched is not None
    assert fetched.status == "failed_retryable"


@pytest.mark.asyncio
async def test_db_backed_create_session_promotion_materializes_atomic_queued_batch(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    input_file_id = await _seed_create_session_input_file(repository)
    session = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=input_file_id,
        target_batch_id="batch-promote-1",
        request_count=2,
    )
    promoter = BatchCreateSessionPromoter(repository=repository, staging=staging)

    result = await promoter.promote_session(session.session_id)

    assert result.promoted is True
    assert result.batch_id == "batch-promote-1"
    assert result.job is not None
    assert result.job.status == "queued"
    assert result.job.total_items == 2

    session_rows = await batch_db.query_raw(
        """
        SELECT status, promotion_attempt_count, completed_at, last_attempt_at
        FROM deltallm_batch_create_session
        WHERE session_id = $1
        """,
        session.session_id,
    )
    job_rows = await batch_db.query_raw(
        """
        SELECT batch_id, status, total_items
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        result.batch_id,
    )
    item_rows = await batch_db.query_raw(
        """
        SELECT line_number, custom_id
        FROM deltallm_batch_item
        WHERE batch_id = $1
        ORDER BY line_number ASC
        """,
        result.batch_id,
    )

    assert dict(session_rows[0])["status"] == "completed"
    assert int(dict(session_rows[0])["promotion_attempt_count"]) == 1
    assert dict(session_rows[0])["completed_at"] is not None
    assert dict(session_rows[0])["last_attempt_at"] is not None
    assert dict(job_rows[0]) == {
        "batch_id": "batch-promote-1",
        "status": "queued",
        "total_items": 2,
    }
    assert [dict(row) for row in item_rows] == [
        {"line_number": 1, "custom_id": "req-1"},
        {"line_number": 2, "custom_id": "req-2"},
    ]


@pytest.mark.asyncio
async def test_db_backed_create_session_promotion_rolls_back_after_item_insert_failure(
    batch_db,
    tmp_path: Path,
) -> None:
    class _FailingPromotionRepository(BatchRepository):
        def __init__(self, prisma_client: Any, state: dict[str, int]) -> None:
            super().__init__(prisma_client)
            self.state = state

        def with_prisma(self, prisma_client: Any | None) -> "_FailingPromotionRepository":
            return _FailingPromotionRepository(prisma_client, self.state)

        async def create_items(self, batch_id: str, items: list[BatchItemCreate]) -> int:
            del batch_id, items
            self.state["calls"] += 1
            raise RuntimeError("simulated promotion item insert failure")

    state = {"calls": 0}
    repository = _FailingPromotionRepository(batch_db, state)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    input_file_id = await _seed_create_session_input_file(repository)
    session = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=input_file_id,
        target_batch_id="batch-promote-rollback",
    )
    promoter = BatchCreateSessionPromoter(repository=repository, staging=staging)

    with pytest.raises(BatchCreatePromotionError, match="Failed to promote batch create session") as exc:
        await promoter.promote_session(session.session_id)

    assert exc.value.retryable is True
    assert exc.value.code == "promotion_failed"
    assert state["calls"] == 1

    session_rows = await batch_db.query_raw(
        """
        SELECT status, promotion_attempt_count, last_error_code
        FROM deltallm_batch_create_session
        WHERE session_id = $1
        """,
        session.session_id,
    )
    job_rows = await batch_db.query_raw(
        "SELECT batch_id FROM deltallm_batch_job WHERE batch_id = $1",
        session.target_batch_id,
    )
    item_rows = await batch_db.query_raw(
        "SELECT item_id FROM deltallm_batch_item WHERE batch_id = $1",
        session.target_batch_id,
    )

    assert dict(session_rows[0]) == {
        "status": "failed_retryable",
        "promotion_attempt_count": 1,
        "last_error_code": "promotion_failed",
    }
    assert job_rows == []
    assert item_rows == []


@pytest.mark.asyncio
async def test_db_backed_create_session_promotion_enforces_pending_cap_per_scope(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    input_file_id = await _seed_create_session_input_file(repository)
    first = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=input_file_id,
        target_batch_id="batch-promote-cap-1",
        created_by_api_key="key-a",
    )
    second = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=input_file_id,
        target_batch_id="batch-promote-cap-2",
        created_by_api_key="key-a",
    )
    promoter = BatchCreateSessionPromoter(
        repository=repository,
        staging=staging,
        max_pending_batches_per_scope=1,
    )

    results = await asyncio.gather(
        promoter.promote_session(first.session_id),
        promoter.promote_session(second.session_id),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], BatchCreatePromotionError)
    assert failures[0].retryable is True
    assert failures[0].code == "pending_limit_exceeded"

    job_rows = await batch_db.query_raw(
        """
        SELECT batch_id, status
        FROM deltallm_batch_job
        ORDER BY batch_id ASC
        """
    )
    session_rows = await batch_db.query_raw(
        """
        SELECT session_id, status, promotion_attempt_count
        FROM deltallm_batch_create_session
        ORDER BY session_id ASC
        """
    )

    assert len(job_rows) == 1
    assert dict(job_rows[0])["status"] == "queued"
    status_by_session = {
        str(row["session_id"]): (str(row["status"]), int(row["promotion_attempt_count"]))
        for row in session_rows
    }
    assert status_by_session[first.session_id][0] in {"completed", "failed_retryable", "staged"}
    assert status_by_session[second.session_id][0] in {"completed", "failed_retryable", "staged"}
    assert sorted(status for status, _attempts in status_by_session.values()) in (
        ["completed", "failed_retryable"],
        ["completed", "staged"],
    )
    assert sorted(attempts for _status, attempts in status_by_session.values()) in ([0, 1], [1, 1])


@pytest.mark.asyncio
async def test_db_backed_create_session_soft_precheck_rejects_without_mutating_session(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    input_file_id = await _seed_create_session_input_file(repository)
    active_input_file_id = await _seed_batch_file(repository)
    active_job = await repository.create_job(
        batch_id="batch-active-cap",
        endpoint="/v1/embeddings",
        input_file_id=active_input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        created_by_organization_id=None,
        expires_at=None,
        status="queued",
        total_items=1,
    )
    assert active_job is not None

    session = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=input_file_id,
        target_batch_id="batch-precheck-cap",
        created_by_api_key="key-a",
    )
    promoter = BatchCreateSessionPromoter(
        repository=repository,
        staging=staging,
        max_pending_batches_per_scope=1,
        soft_precheck_enabled=True,
    )

    with pytest.raises(BatchCreatePromotionError, match="Active batch count exceeds") as exc:
        await promoter.promote_session(session.session_id)

    assert exc.value.retryable is True
    assert exc.value.code == "pending_limit_exceeded"

    session_rows = await batch_db.query_raw(
        """
        SELECT status, promotion_attempt_count, last_error_code, last_error_message
        FROM deltallm_batch_create_session
        WHERE session_id = $1
        """,
        session.session_id,
    )
    job_rows = await batch_db.query_raw(
        "SELECT batch_id FROM deltallm_batch_job WHERE batch_id = $1",
        session.target_batch_id,
    )

    assert dict(session_rows[0]) == {
        "status": "staged",
        "promotion_attempt_count": 0,
        "last_error_code": None,
        "last_error_message": None,
    }
    assert job_rows == []


@pytest.mark.asyncio
async def test_db_backed_create_session_promotion_is_idempotent_after_completion(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    input_file_id = await _seed_create_session_input_file(repository)
    session = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=input_file_id,
        target_batch_id="batch-promote-idempotent",
        request_count=3,
    )
    promoter = BatchCreateSessionPromoter(repository=repository, staging=staging)

    first = await promoter.promote_session(session.session_id)
    second = await promoter.promote_session(session.session_id)

    assert first.promoted is True
    assert second.promoted is False
    assert second.batch_id == first.batch_id

    job_rows = await batch_db.query_raw(
        "SELECT batch_id FROM deltallm_batch_job WHERE batch_id = $1",
        first.batch_id,
    )
    item_rows = await batch_db.query_raw(
        "SELECT item_id FROM deltallm_batch_item WHERE batch_id = $1",
        first.batch_id,
    )
    session_rows = await batch_db.query_raw(
        """
        SELECT status, promotion_attempt_count
        FROM deltallm_batch_create_session
        WHERE session_id = $1
        """,
        session.session_id,
    )

    assert len(job_rows) == 1
    assert len(item_rows) == 3
    assert dict(session_rows[0]) == {
        "status": "completed",
        "promotion_attempt_count": 1,
    }


@pytest.mark.asyncio
async def test_db_backed_create_session_promotion_handles_large_item_counts(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    input_file_id = await _seed_create_session_input_file(repository)
    session = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=input_file_id,
        target_batch_id="batch-promote-large",
        request_count=401,
    )
    promoter = BatchCreateSessionPromoter(
        repository=repository,
        staging=staging,
        insert_chunk_size=250,
    )

    result = await promoter.promote_session(session.session_id)

    item_rows = await batch_db.query_raw(
        """
        SELECT COUNT(*) AS total
        FROM deltallm_batch_item
        WHERE batch_id = $1
        """,
        result.batch_id,
    )
    job_rows = await batch_db.query_raw(
        """
        SELECT total_items, status
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        result.batch_id,
    )

    assert result.promoted is True
    assert int(dict(item_rows[0])["total"]) == 401
    assert dict(job_rows[0]) == {
        "total_items": 401,
        "status": "queued",
    }


@pytest.mark.asyncio
async def test_db_backed_create_session_promotion_is_not_claimable_before_commit(
    batch_db,
    tmp_path: Path,
) -> None:
    class _BlockingPromotionRepository(BatchRepository):
        def __init__(self, prisma_client: Any, entered: asyncio.Event, release: asyncio.Event) -> None:
            super().__init__(prisma_client)
            self.entered = entered
            self.release = release

        def with_prisma(self, prisma_client: Any | None) -> "_BlockingPromotionRepository":
            return _BlockingPromotionRepository(prisma_client, self.entered, self.release)

        async def create_items(self, batch_id: str, items: list[BatchItemCreate]) -> int:
            inserted = await super().create_items(batch_id, items)
            self.entered.set()
            await self.release.wait()
            return inserted

    entered = asyncio.Event()
    release = asyncio.Event()
    repository = _BlockingPromotionRepository(batch_db, entered, release)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    input_file_id = await _seed_create_session_input_file(repository)
    session = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=input_file_id,
        target_batch_id="batch-promote-claim-race",
    )
    promoter = BatchCreateSessionPromoter(repository=repository, staging=staging)

    promotion_task = asyncio.create_task(promoter.promote_session(session.session_id))
    await entered.wait()

    claimed_before_commit = await BatchRepository(batch_db).claim_next_job(worker_id="worker-before")
    assert claimed_before_commit is None

    release.set()
    result = await promotion_task
    claimed_after_commit = await BatchRepository(batch_db).claim_next_job(worker_id="worker-after")

    assert result.promoted is True
    assert claimed_after_commit is not None
    assert claimed_after_commit.batch_id == result.batch_id


@pytest.mark.asyncio
async def test_db_backed_create_session_cleanup_deletes_expired_session_artifact_without_touching_batch_file(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_create_session_input_file(repository)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    artifact = await staging.write_records(
        [
            BatchCreateStagedRequest(
                line_number=1,
                custom_id="req-1",
                request_body={"model": "m1", "input": "hello"},
            )
        ],
        filename="session-1.jsonl",
    )

    session = await repository.create_sessions.create_session(
        BatchCreateSessionCreate(
            target_batch_id="batch-session-1",
            endpoint="/v1/embeddings",
            input_file_id=input_file_id,
            staged_storage_backend=artifact.storage_backend,
            staged_storage_key=artifact.storage_key,
            staged_checksum=artifact.checksum,
            staged_bytes=artifact.bytes_size,
            expected_item_count=1,
            status="failed_retryable",
            created_by_api_key="key-a",
            last_error_code="timeout",
            last_error_message="timed out",
            last_attempt_at=datetime.now(tz=UTC) - timedelta(hours=1),
            expires_at=datetime.now(tz=UTC) - timedelta(minutes=1),
        )
    )
    assert session is not None

    worker = BatchCreateSessionCleanupWorker(
        repository=repository.create_sessions,
        staging=staging,
        config=BatchCreateSessionCleanupConfig(interval_seconds=0.01, scan_limit=10),
    )

    deleted_sessions, deleted_artifacts = await worker.process_once()

    assert deleted_sessions == 1
    assert deleted_artifacts == 1
    assert not (tmp_path / "create-session-artifacts" / artifact.storage_key).exists()

    session_rows = await batch_db.query_raw(
        "SELECT session_id FROM deltallm_batch_create_session WHERE session_id = $1",
        session.session_id,
    )
    file_rows = await batch_db.query_raw(
        "SELECT file_id FROM deltallm_batch_file WHERE file_id = $1",
        input_file_id,
    )

    assert session_rows == []
    assert len(file_rows) == 1


@pytest.mark.asyncio
async def test_db_backed_session_stager_compensates_artifact_on_session_insert_conflict(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_create_session_input_file(repository)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    stager = BatchCreateSessionStager(
        repository=repository.create_sessions,
        staging=staging,
    )
    existing = await repository.create_sessions.create_session(
        BatchCreateSessionCreate(
            target_batch_id="batch-session-1",
            endpoint="/v1/embeddings",
            input_file_id=input_file_id,
            staged_storage_backend="local",
            staged_storage_key="existing/session-1.jsonl",
            staged_checksum="seed",
            staged_bytes=16,
            expected_item_count=1,
            created_by_api_key="key-a",
        )
    )
    assert existing is not None

    with pytest.raises(Exception):
        await stager.stage_session(
            records=[
                BatchCreateStagedRequest(
                    line_number=1,
                    custom_id="req-1",
                    request_body={"model": "m1", "input": "hello"},
                )
            ],
            filename="duplicate-session.jsonl",
            build_session=lambda artifact: BatchCreateSessionCreate(
                target_batch_id="batch-session-1",
                endpoint="/v1/embeddings",
                input_file_id=input_file_id,
                staged_storage_backend=artifact.storage_backend,
                staged_storage_key=artifact.storage_key,
                staged_checksum=artifact.checksum,
                staged_bytes=artifact.bytes_size,
                expected_item_count=1,
                created_by_api_key="key-a",
            ),
        )

    assert [path for path in (tmp_path / "create-session-artifacts").rglob("*") if path.is_file()] == []


@pytest.mark.asyncio
async def test_db_backed_create_session_cleanup_uses_status_retention_when_expires_at_is_null(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_create_session_input_file(repository)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    old = datetime.now(tz=UTC) - timedelta(days=2)

    completed_artifact = await staging.write_records(
        [
            BatchCreateStagedRequest(
                line_number=1,
                custom_id="req-completed",
                request_body={"model": "m1", "input": "hello"},
            )
        ],
        filename="completed.jsonl",
    )
    retryable_artifact = await staging.write_records(
        [
            BatchCreateStagedRequest(
                line_number=1,
                custom_id="req-retryable",
                request_body={"model": "m1", "input": "hello"},
            )
        ],
        filename="retryable.jsonl",
    )
    staged_artifact = await staging.write_records(
        [
            BatchCreateStagedRequest(
                line_number=1,
                custom_id="req-staged",
                request_body={"model": "m1", "input": "hello"},
            )
        ],
        filename="staged.jsonl",
    )

    completed = await repository.create_sessions.create_session(
        BatchCreateSessionCreate(
            target_batch_id="batch-session-completed",
            endpoint="/v1/embeddings",
            input_file_id=input_file_id,
            staged_storage_backend=completed_artifact.storage_backend,
            staged_storage_key=completed_artifact.storage_key,
            staged_checksum=completed_artifact.checksum,
            staged_bytes=completed_artifact.bytes_size,
            expected_item_count=1,
            status="completed",
            created_by_api_key="key-a",
            completed_at=old,
        )
    )
    retryable = await repository.create_sessions.create_session(
        BatchCreateSessionCreate(
            target_batch_id="batch-session-retryable",
            endpoint="/v1/embeddings",
            input_file_id=input_file_id,
            staged_storage_backend=retryable_artifact.storage_backend,
            staged_storage_key=retryable_artifact.storage_key,
            staged_checksum=retryable_artifact.checksum,
            staged_bytes=retryable_artifact.bytes_size,
            expected_item_count=1,
            status="failed_retryable",
            created_by_api_key="key-a",
            last_error_code="timeout",
            last_error_message="timed out",
            last_attempt_at=old,
        )
    )
    staged_session = await repository.create_sessions.create_session(
        BatchCreateSessionCreate(
            target_batch_id="batch-session-staged",
            endpoint="/v1/embeddings",
            input_file_id=input_file_id,
            staged_storage_backend=staged_artifact.storage_backend,
            staged_storage_key=staged_artifact.storage_key,
            staged_checksum=staged_artifact.checksum,
            staged_bytes=staged_artifact.bytes_size,
            expected_item_count=1,
            status="staged",
            created_by_api_key="key-a",
        )
    )
    assert completed is not None
    assert retryable is not None
    assert staged_session is not None

    worker = BatchCreateSessionCleanupWorker(
        repository=repository.create_sessions,
        staging=staging,
        config=BatchCreateSessionCleanupConfig(
            interval_seconds=0.01,
            scan_limit=10,
            completed_retention_seconds=3600,
            retryable_retention_seconds=3600,
            failed_retention_seconds=3600,
        ),
    )

    deleted_sessions, deleted_artifacts = await worker.process_once()

    assert deleted_sessions == 2
    assert deleted_artifacts == 2
    assert not (tmp_path / "create-session-artifacts" / completed_artifact.storage_key).exists()
    assert not (tmp_path / "create-session-artifacts" / retryable_artifact.storage_key).exists()
    assert (tmp_path / "create-session-artifacts" / staged_artifact.storage_key).exists()

    completed_rows = await batch_db.query_raw(
        "SELECT session_id FROM deltallm_batch_create_session WHERE session_id = $1",
        completed.session_id,
    )
    retryable_rows = await batch_db.query_raw(
        "SELECT session_id FROM deltallm_batch_create_session WHERE session_id = $1",
        retryable.session_id,
    )
    staged_rows = await batch_db.query_raw(
        "SELECT session_id FROM deltallm_batch_create_session WHERE session_id = $1",
        staged_session.session_id,
    )

    assert completed_rows == []
    assert retryable_rows == []
    assert len(staged_rows) == 1


@pytest.mark.asyncio
async def test_db_backed_create_session_cleanup_delete_skips_refreshed_candidate(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_create_session_input_file(repository)
    staging = BatchCreateArtifactStorageBackend(
        storage=LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts")),
    )
    artifact = await staging.write_records(
        [
            BatchCreateStagedRequest(
                line_number=1,
                custom_id="req-1",
                request_body={"model": "m1", "input": "hello"},
            )
        ],
        filename="refreshable.jsonl",
    )
    old_attempt = datetime.now(tz=UTC) - timedelta(days=2)
    session = await repository.create_sessions.create_session(
        BatchCreateSessionCreate(
            target_batch_id="batch-session-refreshable",
            endpoint="/v1/embeddings",
            input_file_id=input_file_id,
            staged_storage_backend=artifact.storage_backend,
            staged_storage_key=artifact.storage_key,
            staged_checksum=artifact.checksum,
            staged_bytes=artifact.bytes_size,
            expected_item_count=1,
            status="failed_retryable",
            created_by_api_key="key-a",
            last_error_code="timeout",
            last_error_message="timed out",
            last_attempt_at=old_attempt,
        )
    )
    assert session is not None

    candidates = await repository.create_sessions.list_cleanup_candidates(
        now=datetime.now(tz=UTC),
        completed_before=datetime.now(tz=UTC) - timedelta(hours=1),
        retryable_before=datetime.now(tz=UTC) - timedelta(hours=1),
        failed_before=datetime.now(tz=UTC) - timedelta(hours=1),
        limit=10,
    )
    assert [candidate.session_id for candidate in candidates] == [session.session_id]

    refreshed = await repository.create_sessions.mark_session_failed_retryable(
        session.session_id,
        error_code="retrying",
        error_message="retried",
        attempted_at=datetime.now(tz=UTC),
        expires_at=None,
    )
    assert refreshed is not None

    deleted = await repository.create_sessions.delete_cleanup_candidate(candidates[0])

    assert deleted is None
    fetched = await repository.create_sessions.get_session(session.session_id)
    assert fetched is not None
    assert fetched.last_error_code == "retrying"


@pytest.mark.asyncio
async def test_db_backed_create_session_cleanup_deletes_old_unreferenced_orphan_artifact(
    batch_db,
    tmp_path: Path,
) -> None:
    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage)
    old_time = datetime.now(tz=UTC) - timedelta(hours=2)
    orphan_key = (
        f"batch-create-stage/{old_time:%Y/%m/%d}/"
        f"{old_time:%Y%m%dT%H%M%S%fZ}-manual-orphan.jsonl"
    )
    orphan_target = tmp_path / "create-session-artifacts" / orphan_key
    orphan_target.parent.mkdir(parents=True, exist_ok=True)
    orphan_target.write_text(
        '{"line_number":1,"custom_id":"req-orphan","request_body":{"model":"m1","input":"hello"}}\n',
        encoding="utf-8",
    )

    worker = BatchCreateSessionCleanupWorker(
        repository=repository.create_sessions,
        staging=staging,
        config=BatchCreateSessionCleanupConfig(
            interval_seconds=0.01,
            scan_limit=10,
            orphan_grace_seconds=3600,
        ),
    )

    deleted_sessions, deleted_artifacts = await worker.process_once()

    assert deleted_sessions == 0
    assert deleted_artifacts == 1
    assert not orphan_target.exists()


@pytest.mark.asyncio
async def test_db_backed_batch_create_session_status_check_rejects_invalid_rows(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_create_session_input_file(repository)

    with pytest.raises(Exception):
        await batch_db.execute_raw(
            """
            INSERT INTO deltallm_batch_create_session (
                session_id, target_batch_id, status, endpoint, input_file_id,
                staged_storage_backend, staged_storage_key, staged_bytes, expected_item_count
            )
            VALUES (
                'session-invalid-status',
                'batch-invalid-status',
                'broken',
                '/v1/embeddings',
                $1,
                'local',
                'batch-create-stage/invalid.jsonl',
                16,
                1
            )
            """,
            input_file_id,
        )


@pytest.mark.asyncio
async def test_db_backed_batch_job_status_check_rejects_invalid_rows(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)

    with pytest.raises(Exception):
        await batch_db.execute_raw(
            """
            INSERT INTO deltallm_batch_job (
                batch_id, endpoint, status, execution_mode, input_file_id, total_items
            )
            VALUES (
                'batch-invalid-job-status',
                '/v1/embeddings',
                'broken',
                'managed_internal',
                $1,
                0
            )
            """,
            input_file_id,
        )

    with pytest.raises(Exception):
        await batch_db.execute_raw(
            """
            INSERT INTO deltallm_batch_job (
                batch_id, endpoint, status, execution_mode, input_file_id, total_items
            )
            VALUES (
                'batch-invalid-job-status-validating',
                '/v1/embeddings',
                'validating',
                'managed_internal',
                $1,
                0
            )
            """,
            input_file_id,
        )


@pytest.mark.asyncio
async def test_db_backed_admin_expire_marks_retryable_session_expired_and_deletes_artifact(batch_db, tmp_path: Path) -> None:
    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage, storage_registry={"local": storage})
    session = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=await _seed_create_session_input_file(repository),
        target_batch_id="batch-admin-expire",
        request_count=1,
        created_by_api_key="key-a",
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
        status=BatchCreateSessionStatus.FAILED_RETRYABLE,
    )
    service = BatchCreateSessionAdminService(
        repository=repository.create_sessions,
        promoter=SimpleNamespace(),  # type: ignore[arg-type]
        staging=staging,
    )

    artifact_path = tmp_path / "create-session-artifacts" / session.staged_storage_key
    assert artifact_path.exists()

    result = await service.expire_session(session.session_id)

    assert result.session.status == BatchCreateSessionStatus.EXPIRED
    assert result.artifact_deleted is True
    refreshed = await repository.create_sessions.get_session(session.session_id)
    assert refreshed is not None
    assert refreshed.status == BatchCreateSessionStatus.EXPIRED
    assert not artifact_path.exists()


@pytest.mark.asyncio
async def test_db_backed_admin_expire_rejects_completed_session_without_touching_batch(batch_db, tmp_path: Path) -> None:
    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage, storage_registry={"local": storage})
    input_file_id = await _seed_create_session_input_file(repository)
    batch = await repository.create_job(
        batch_id="batch-admin-completed",
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
        status="queued",
        total_items=1,
    )
    assert batch is not None
    staged_session = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=input_file_id,
        target_batch_id=batch.batch_id,
        request_count=1,
        created_by_api_key="key-a",
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
        status=BatchCreateSessionStatus.STAGED,
    )
    completed = await repository.create_sessions.mark_session_completed(
        staged_session.session_id,
        completed_at=datetime.now(tz=UTC),
        expires_at=None,
        from_statuses=(BatchCreateSessionStatus.STAGED,),
    )
    assert completed is not None
    service = BatchCreateSessionAdminService(
        repository=repository.create_sessions,
        promoter=SimpleNamespace(),  # type: ignore[arg-type]
        staging=staging,
    )

    with pytest.raises(HTTPException) as exc:
        await service.expire_session(completed.session_id)

    assert exc.value.status_code == 400
    refreshed_session = await repository.create_sessions.get_session(completed.session_id)
    refreshed_batch = await repository.get_job(batch.batch_id)
    assert refreshed_session is not None
    assert refreshed_session.status == BatchCreateSessionStatus.COMPLETED
    assert refreshed_batch is not None
    assert refreshed_batch.batch_id == batch.batch_id


@pytest.mark.asyncio
async def test_db_backed_batch_create_session_admin_scope_blocks_other_team(
    client,
    test_app,
    batch_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "create-session-artifacts"))
    staging = BatchCreateArtifactStorageBackend(storage=storage, storage_registry={"local": storage})
    session = await _seed_staged_create_session(
        repository=repository,
        staging=staging,
        input_file_id=await _seed_create_session_input_file(repository),
        target_batch_id="batch-admin-scope",
        request_count=1,
        created_by_api_key="key-a",
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
        status=BatchCreateSessionStatus.FAILED_RETRYABLE,
    )
    test_app.state.prisma_manager = type("Prisma", (), {"client": batch_db})()

    class _AdminServiceStub:
        async def expire_session(self, session_id: str):  # noqa: ANN201
            raise AssertionError(f"expire_session should not be called for forbidden session: {session_id}")

    test_app.state.batch_create_session_admin_service = _AdminServiceStub()

    monkeypatch.setattr(
        "src.api.admin.endpoints.batch_create_sessions.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=False,
            team_ids=["team-2"],
            team_permissions_by_id={"team-2": {Permission.KEY_READ, Permission.KEY_UPDATE}},
        ),
    )

    response = await client.post(
        f"/ui/api/batch-create-sessions/{session.session_id}/expire",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_db_backed_claim_items_are_disjoint_under_contention(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(line_number=1, custom_id="c1", request_body={"model": "m1", "input": "a"}),
            BatchItemCreate(line_number=2, custom_id="c2", request_body={"model": "m1", "input": "b"}),
            BatchItemCreate(line_number=3, custom_id="c3", request_body={"model": "m1", "input": "c"}),
            BatchItemCreate(line_number=4, custom_id="c4", request_body={"model": "m1", "input": "d"}),
        ],
    )
    assert inserted == 4
    queued = await repository.set_job_queued(job.batch_id, inserted)
    assert queued is not None

    db_one = await _connect_prisma()
    db_two = await _connect_prisma()
    try:
        repo_one = BatchRepository(db_one)
        repo_two = BatchRepository(db_two)
        first, second = await asyncio.gather(
            repo_one.claim_items(batch_id=job.batch_id, worker_id="w1", limit=3, lease_seconds=120),
            repo_two.claim_items(batch_id=job.batch_id, worker_id="w2", limit=3, lease_seconds=120),
        )
    finally:
        await db_one.disconnect()
        await db_two.disconnect()

    first_lines = {item.line_number for item in first}
    second_lines = {item.line_number for item in second}
    assert first_lines
    assert second_lines
    assert first_lines.isdisjoint(second_lines)
    assert first_lines | second_lines == {1, 2, 3, 4}


@pytest.mark.asyncio
async def test_db_backed_claim_next_work_allows_multiple_slices_from_same_job(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(line_number=1, custom_id="c1", request_body={"model": "m1", "input": "a"}),
            BatchItemCreate(line_number=2, custom_id="c2", request_body={"model": "m1", "input": "b"}),
            BatchItemCreate(line_number=3, custom_id="c3", request_body={"model": "m1", "input": "c"}),
            BatchItemCreate(line_number=4, custom_id="c4", request_body={"model": "m1", "input": "d"}),
        ],
    )
    assert inserted == 4
    await repository.set_job_queued(job.batch_id, inserted)

    db_one = await _connect_prisma()
    db_two = await _connect_prisma()
    try:
        repo_one = BatchRepository(db_one)
        repo_two = BatchRepository(db_two)
        first, second = await asyncio.gather(
            repo_one.claim_next_work(
                worker_id="w1",
                max_items=2,
                max_work_units=100,
                lease_seconds=120,
            ),
            repo_two.claim_next_work(
                worker_id="w2",
                max_items=2,
                max_work_units=100,
                lease_seconds=120,
            ),
        )
    finally:
        await db_one.disconnect()
        await db_two.disconnect()

    assert first is not None
    assert second is not None
    first_items = await repository.load_claim_items(first.item_ids)
    second_items = await repository.load_claim_items(second.item_ids)
    first_lines = {item.line_number for item in first_items}
    second_lines = {item.line_number for item in second_items}
    assert first_lines
    assert second_lines
    assert first_lines.isdisjoint(second_lines)
    assert first_lines | second_lines == {1, 2, 3, 4}


@pytest.mark.asyncio
async def test_db_backed_claim_next_work_respects_not_before_and_large_first_item(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(
                line_number=1,
                custom_id="future",
                request_body={"model": "m1", "input": "future"},
                estimated_work_units=1,
                not_before_at=datetime.now(tz=UTC) + timedelta(hours=1),
            ),
            BatchItemCreate(
                line_number=2,
                custom_id="large",
                request_body={"model": "m1", "input": "large"},
                estimated_work_units=10,
            ),
            BatchItemCreate(
                line_number=3,
                custom_id="small",
                request_body={"model": "m1", "input": "small"},
                estimated_work_units=1,
            ),
        ],
    )
    assert inserted == 3
    await repository.set_job_queued(job.batch_id, inserted)

    claim = await repository.claim_next_work(
        worker_id="w1",
        max_items=3,
        max_work_units=5,
        lease_seconds=120,
    )

    assert claim is not None
    assert claim.claimed_work_units == 10
    claimed_items = await repository.load_claim_items(claim.item_ids)
    assert [item.line_number for item in claimed_items] == [2]
    assert claimed_items[0].status == "in_progress"
    future_items = await repository.list_items(job.batch_id)
    assert [item.status for item in future_items if item.line_number == 1] == ["pending"]


@pytest.mark.asyncio
async def test_db_backed_claim_next_work_rotates_to_unscheduled_small_job(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    large = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    small = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-b",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert large is not None
    assert small is not None
    large_count = await repository.create_items(
        large.batch_id,
        [
            BatchItemCreate(line_number=1, custom_id="large-1", request_body={"model": "m1", "input": "a"}),
            BatchItemCreate(line_number=2, custom_id="large-2", request_body={"model": "m1", "input": "b"}),
            BatchItemCreate(line_number=3, custom_id="large-3", request_body={"model": "m1", "input": "c"}),
            BatchItemCreate(line_number=4, custom_id="large-4", request_body={"model": "m1", "input": "d"}),
        ],
    )
    small_count = await repository.create_items(
        small.batch_id,
        [BatchItemCreate(line_number=1, custom_id="small-1", request_body={"model": "m1", "input": "small"})],
    )
    assert large_count == 4
    assert small_count == 1
    await repository.set_job_queued(large.batch_id, large_count)
    await repository.set_job_queued(small.batch_id, small_count)

    first = await repository.claim_next_work(
        worker_id="w1",
        max_items=2,
        max_work_units=100,
        lease_seconds=120,
    )
    assert first is not None
    assert first.batch_id == large.batch_id

    second = await repository.claim_next_work(
        worker_id="w2",
        max_items=2,
        max_work_units=100,
        lease_seconds=120,
    )
    assert second is not None
    assert second.batch_id == small.batch_id


@pytest.mark.asyncio
async def test_db_backed_claim_next_work_returns_none_when_oldest_items_row_locked(batch_db) -> None:
    """selected_job picks the FIFO-oldest job under FOR KEY SHARE, then the
    locked_items LATERAL acquires FOR UPDATE SKIP LOCKED on its items. When
    the oldest job's job-row is unlocked but all of its items are row-locked
    by another transaction, the LATERAL produces zero rows and the claim
    returns None — even though a later job has unlocked items. This is the
    documented LIMIT 1 trade-off versus the prior multi-candidate seed
    design; the next worker poll picks up the work after the lock releases.

    The empty-claim diagnostic reports system-wide state, not LIMIT 1
    contention: with another job's items still claimable, it correctly
    returns "no_available_work" (the catch-all) rather than
    "all_items_locked" (which would imply *all* items everywhere are
    locked). That single-job case is covered by
    test_db_backed_diagnose_empty_work_claim_reports_all_items_locked.
    """
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    oldest = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    next_job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-b",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert oldest is not None
    assert next_job is not None
    assert await repository.create_items(
        oldest.batch_id,
        [BatchItemCreate(line_number=1, custom_id="oldest-1", request_body={"model": "m1", "input": "a"})],
    ) == 1
    assert await repository.create_items(
        next_job.batch_id,
        [BatchItemCreate(line_number=1, custom_id="next-1", request_body={"model": "m1", "input": "b"})],
    ) == 1
    await repository.set_job_queued(oldest.batch_id, 1)
    await repository.set_job_queued(next_job.batch_id, 1)

    lock_db = await _connect_prisma()
    claim_db = await _connect_prisma()
    try:
        async with lock_db.tx(timeout=10000) as tx:
            locked = await tx.query_raw(
                """
                SELECT item_id
                FROM deltallm_batch_item
                WHERE batch_id = $1
                FOR UPDATE
                """,
                oldest.batch_id,
            )
            assert locked

            claim_repo = BatchRepository(claim_db)
            claim = await claim_repo.claim_next_work(
                worker_id="w1",
                max_items=1,
                max_work_units=100,
                lease_seconds=120,
            )
    finally:
        await claim_db.disconnect()
        await lock_db.disconnect()

    assert claim is None

    # After the lock holder commits, the next claim picks the oldest job.
    follow_up = await repository.claim_next_work(
        worker_id="w1",
        max_items=1,
        max_work_units=100,
        lease_seconds=120,
    )
    assert follow_up is not None
    assert follow_up.batch_id == oldest.batch_id


@pytest.mark.asyncio
async def test_db_backed_claim_next_work_skips_job_row_locked_oldest_job(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    oldest = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    next_job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-b",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert oldest is not None
    assert next_job is not None
    assert await repository.create_items(
        oldest.batch_id,
        [BatchItemCreate(line_number=1, custom_id="oldest-1", request_body={"model": "m1", "input": "a"})],
    ) == 1
    assert await repository.create_items(
        next_job.batch_id,
        [BatchItemCreate(line_number=1, custom_id="next-1", request_body={"model": "m1", "input": "b"})],
    ) == 1
    await repository.set_job_queued(oldest.batch_id, 1)
    await repository.set_job_queued(next_job.batch_id, 1)

    lock_db = await _connect_prisma()
    claim_db = await _connect_prisma()
    try:
        async with lock_db.tx(timeout=10000) as tx:
            locked = await tx.query_raw(
                """
                SELECT batch_id
                FROM deltallm_batch_job
                WHERE batch_id = $1
                FOR UPDATE
                """,
                oldest.batch_id,
            )
            assert locked

            claim = await BatchRepository(claim_db).claim_next_work(
                worker_id="w1",
                max_items=1,
                max_work_units=100,
                lease_seconds=120,
            )
    finally:
        await claim_db.disconnect()
        await lock_db.disconnect()

    assert claim is not None
    assert claim.batch_id == next_job.batch_id


@pytest.mark.asyncio
async def test_db_backed_claim_next_work_skips_live_job_lease(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    leased = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    next_job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-b",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert leased is not None
    assert next_job is not None
    assert await repository.create_items(
        leased.batch_id,
        [BatchItemCreate(line_number=1, custom_id="leased-1", request_body={"model": "m1", "input": "a"})],
    ) == 1
    assert await repository.create_items(
        next_job.batch_id,
        [BatchItemCreate(line_number=1, custom_id="next-1", request_body={"model": "m1", "input": "b"})],
    ) == 1
    await repository.set_job_queued(leased.batch_id, 1)
    await repository.set_job_queued(next_job.batch_id, 1)
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_job
        SET locked_by = 'job-fifo-worker',
            lease_expires_at = NOW() + INTERVAL '10 minutes'
        WHERE batch_id = $1
        """,
        leased.batch_id,
    )

    claim = await repository.claim_next_work(
        worker_id="w1",
        max_items=1,
        max_work_units=100,
        lease_seconds=120,
    )

    assert claim is not None
    assert claim.batch_id == next_job.batch_id


@pytest.mark.asyncio
async def test_db_backed_claim_next_work_skips_item_with_future_not_before_after_retry(batch_db) -> None:
    """A retryable failure sets not_before_at into the future via
    mark_item_failed(retryable=True, retry_delay_seconds>0). The next
    work-slice claim must skip that item (predicate excludes future
    not_before_at) and pick the next eligible one in line order.
    """
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(line_number=1, custom_id="c1", request_body={"model": "m1", "input": "a"}),
            BatchItemCreate(line_number=2, custom_id="c2", request_body={"model": "m1", "input": "b"}),
        ],
    )
    assert inserted == 2
    await repository.set_job_queued(job.batch_id, inserted)

    first = await repository.claim_next_work(
        worker_id="w1",
        max_items=1,
        max_work_units=100,
        lease_seconds=120,
    )
    assert first is not None
    assert len(first.item_ids) == 1
    retried_item_id = first.item_ids[0]

    # Mark the claimed item as a retryable failure with a 1h backoff.
    marked = await repository.mark_item_failed(
        item_id=retried_item_id,
        worker_id="w1",
        error_body={"message": "transient upstream"},
        last_error="transient upstream",
        retryable=True,
        retry_delay_seconds=3600,
    )
    assert marked is True

    second = await repository.claim_next_work(
        worker_id="w1",
        max_items=2,
        max_work_units=100,
        lease_seconds=120,
    )

    assert second is not None
    second_items = await repository.load_claim_items(second.item_ids)
    # Retried item is in the future; only line 2 should be returned.
    assert [item.line_number for item in second_items] == [2]
    assert retried_item_id not in second.item_ids


@pytest.mark.asyncio
async def test_db_backed_claim_next_work_does_not_strand_items_when_job_finalizing(batch_db) -> None:
    """Race-symmetry guard for the updated_items / updated_job CTE dependency.

    If the only candidate job is no longer ('queued', 'in_progress'), the SQL must
    leave its items untouched. updated_items is wired off updated_job, so an empty
    updated_job (status filter fails, EvalPlanQual or otherwise) means no item is
    flipped to 'in_progress' and the worker correctly observes claim=None.
    """
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [BatchItemCreate(line_number=1, custom_id="c1", request_body={"model": "m1", "input": "a"})],
    )
    assert inserted == 1
    await repository.set_job_queued(job.batch_id, inserted)
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_job
        SET status = 'finalizing'::"DeltaLLM_BatchJobStatus",
            status_last_updated_at = NOW()
        WHERE batch_id = $1
        """,
        job.batch_id,
    )

    claim = await repository.claim_next_work(
        worker_id="w1",
        max_items=4,
        max_work_units=16,
        lease_seconds=120,
    )

    assert claim is None
    items = await repository.list_items(job.batch_id)
    assert [item.status for item in items] == ["pending"]
    assert all(item.locked_by is None for item in items)
    assert all(item.lease_expires_at is None for item in items)


@pytest.mark.asyncio
async def test_db_backed_diagnose_empty_work_claim_reports_not_before_future(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(
                line_number=1,
                custom_id="future-1",
                request_body={"model": "m1", "input": "a"},
                not_before_at=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        ],
    )
    assert inserted == 1
    await repository.set_job_queued(job.batch_id, inserted)

    assert await repository.claim_next_work(
        worker_id="w1",
        max_items=1,
        max_work_units=100,
        lease_seconds=120,
    ) is None
    assert await repository.diagnose_empty_work_claim() == "not_before_future"


@pytest.mark.asyncio
async def test_db_backed_diagnose_empty_work_claim_reports_all_items_locked(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [BatchItemCreate(line_number=1, custom_id="locked-1", request_body={"model": "m1", "input": "a"})],
    )
    assert inserted == 1
    await repository.set_job_queued(job.batch_id, inserted)

    lock_db = await _connect_prisma()
    diagnostic_db = await _connect_prisma()
    try:
        async with lock_db.tx(timeout=10000) as tx:
            locked = await tx.query_raw(
                """
                SELECT item_id
                FROM deltallm_batch_item
                WHERE batch_id = $1
                FOR UPDATE
                """,
                job.batch_id,
            )
            assert locked

            reason = await BatchRepository(diagnostic_db).diagnose_empty_work_claim()
    finally:
        await diagnostic_db.disconnect()
        await lock_db.disconnect()

    assert reason == "all_items_locked"


@pytest.mark.asyncio
async def test_db_backed_diagnose_empty_work_claim_reports_locked_for_mixed_due_and_future(
    batch_db,
) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(line_number=1, custom_id="locked-1", request_body={"model": "m1", "input": "a"}),
            BatchItemCreate(
                line_number=2,
                custom_id="future-1",
                request_body={"model": "m1", "input": "b"},
                not_before_at=datetime.now(tz=UTC) + timedelta(hours=1),
            ),
        ],
    )
    assert inserted == 2
    await repository.set_job_queued(job.batch_id, inserted)

    lock_db = await _connect_prisma()
    diagnostic_db = await _connect_prisma()
    try:
        async with lock_db.tx(timeout=10000) as tx:
            locked = await tx.query_raw(
                """
                SELECT item_id
                FROM deltallm_batch_item
                WHERE batch_id = $1
                  AND line_number = 1
                FOR UPDATE
                """,
                job.batch_id,
            )
            assert locked

            reason = await BatchRepository(diagnostic_db).diagnose_empty_work_claim()
    finally:
        await diagnostic_db.disconnect()
        await lock_db.disconnect()

    assert reason == "all_items_locked"


@pytest.mark.asyncio
async def test_db_backed_expired_item_can_be_reclaimed_after_crash(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [BatchItemCreate(line_number=1, custom_id="c1", request_body={"model": "m1", "input": "a"})],
    )
    assert inserted == 1
    await repository.set_job_queued(job.batch_id, inserted)

    first_claim = await repository.claim_items(batch_id=job.batch_id, worker_id="w1", limit=1, lease_seconds=120)
    assert len(first_claim) == 1
    item = first_claim[0]
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_item
        SET lease_expires_at = NOW() - INTERVAL '1 second'
        WHERE item_id = $1
        """,
        item.item_id,
    )

    reclaimed = await repository.claim_items(batch_id=job.batch_id, worker_id="w2", limit=1, lease_seconds=120)
    assert [row.item_id for row in reclaimed] == [item.item_id]
    updated = await repository.mark_item_completed(
        item_id=item.item_id,
        worker_id="w2",
        response_body={"object": "list", "data": [{"index": 0, "embedding": [0.1]}]},
        usage={"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        provider_cost=0.01,
        billed_cost=0.01,
    )
    assert updated is True

    refreshed = await repository.refresh_job_progress(job.batch_id)
    assert refreshed is not None
    assert refreshed.completed_items == 1
    assert refreshed.status == "finalizing"


@pytest.mark.asyncio
async def test_db_backed_bulk_completion_is_all_or_nothing_when_any_item_is_no_longer_owned(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(line_number=1, custom_id="c1", request_body={"model": "m1", "input": "a"}),
            BatchItemCreate(line_number=2, custom_id="c2", request_body={"model": "m1", "input": "b"}),
        ],
    )
    assert inserted == 2
    await repository.set_job_queued(job.batch_id, inserted)

    claimed = await repository.claim_items(batch_id=job.batch_id, worker_id="w1", limit=2, lease_seconds=120)
    assert [item.line_number for item in claimed] == [1, 2]

    second_item_id = claimed[1].item_id
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_item
        SET locked_by = 'w2'
        WHERE item_id = $1
        """,
        second_item_id,
    )

    updated = await repository.mark_items_completed_bulk(
        items=[
            {
                "item_id": claimed[0].item_id,
                "response_body": {"object": "list", "data": [{"index": 0, "embedding": [0.1]}]},
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
                "provider_cost": 0.01,
                "billed_cost": 0.01,
            },
            {
                "item_id": claimed[1].item_id,
                "response_body": {"object": "list", "data": [{"index": 0, "embedding": [0.2]}]},
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
                "provider_cost": 0.01,
                "billed_cost": 0.01,
            },
        ],
        worker_id="w1",
    )

    assert updated is False
    rows = await batch_db.query_raw(
        """
        SELECT line_number, status, locked_by
        FROM deltallm_batch_item
        WHERE batch_id = $1
        ORDER BY line_number ASC
        """,
        job.batch_id,
    )
    assert [dict(row) for row in rows] == [
        {"line_number": 1, "status": "in_progress", "locked_by": "w1"},
        {"line_number": 2, "status": "in_progress", "locked_by": "w2"},
    ]


@pytest.mark.asyncio
async def test_db_backed_bulk_completion_with_outbox_persists_completed_items_and_outbox_rows(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id="user-1",
        created_by_team_id="team-1",
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(line_number=1, custom_id="c1", request_body={"model": "m1", "input": "a"}),
            BatchItemCreate(line_number=2, custom_id="c2", request_body={"model": "m1", "input": "b"}),
        ],
    )
    assert inserted == 2
    await repository.set_job_queued(job.batch_id, inserted)

    claimed = await repository.claim_items(batch_id=job.batch_id, worker_id="w1", limit=2, lease_seconds=120)
    assert [item.line_number for item in claimed] == [1, 2]

    items = [
        {
            "item_id": claimed[0].item_id,
            "response_body": {"object": "list", "data": [{"index": 0, "embedding": [0.1]}]},
            "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            "provider_cost": 0.01,
            "billed_cost": 0.01,
            "outbox_payload": {
                "batch_id": job.batch_id,
                "item_id": claimed[0].item_id,
                "model": "m1",
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
                "billed_cost": 0.01,
            },
            "outbox_max_attempts": 7,
        },
        {
            "item_id": claimed[1].item_id,
            "response_body": {"object": "list", "data": [{"index": 0, "embedding": [0.2]}]},
            "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
            "provider_cost": 0.02,
            "billed_cost": 0.02,
            "outbox_payload": {
                "batch_id": job.batch_id,
                "item_id": claimed[1].item_id,
                "model": "m1",
                "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
                "billed_cost": 0.02,
            },
            "outbox_max_attempts": 7,
        },
    ]

    result = await repository.complete_items_with_outbox_bulk(items=items, worker_id="w1")

    assert result == "completed"
    item_rows = await batch_db.query_raw(
        """
        SELECT line_number, item_id, status, locked_by
        FROM deltallm_batch_item
        WHERE batch_id = $1
        ORDER BY line_number ASC
        """,
        job.batch_id,
    )
    assert [dict(row) for row in item_rows] == [
        {"line_number": 1, "item_id": claimed[0].item_id, "status": "completed", "locked_by": None},
        {"line_number": 2, "item_id": claimed[1].item_id, "status": "completed", "locked_by": None},
    ]

    outbox_rows = await batch_db.query_raw(
        """
        SELECT item_id, status, attempt_count, max_attempts
        FROM deltallm_batch_completion_outbox
        WHERE batch_id = $1
        ORDER BY item_id ASC
        """,
        job.batch_id,
    )
    expected_outbox_rows = sorted(
        [
            {"item_id": claimed[0].item_id, "status": "queued", "attempt_count": 0, "max_attempts": 7},
            {"item_id": claimed[1].item_id, "status": "queued", "attempt_count": 0, "max_attempts": 7},
        ],
        key=lambda row: row["item_id"],
    )
    assert [dict(row) for row in outbox_rows] == expected_outbox_rows


@pytest.mark.asyncio
async def test_db_backed_bulk_completion_with_outbox_reports_already_completed_after_prior_commit(batch_db) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id="user-1",
        created_by_team_id="team-1",
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(line_number=1, custom_id="c1", request_body={"model": "m1", "input": "a"}),
            BatchItemCreate(line_number=2, custom_id="c2", request_body={"model": "m1", "input": "b"}),
        ],
    )
    assert inserted == 2
    await repository.set_job_queued(job.batch_id, inserted)

    claimed = await repository.claim_items(batch_id=job.batch_id, worker_id="w1", limit=2, lease_seconds=120)
    items = [
        {
            "item_id": claimed[0].item_id,
            "response_body": {"object": "list", "data": [{"index": 0, "embedding": [0.1]}]},
            "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            "provider_cost": 0.01,
            "billed_cost": 0.01,
            "outbox_payload": {"batch_id": job.batch_id, "item_id": claimed[0].item_id},
            "outbox_max_attempts": 5,
        },
        {
            "item_id": claimed[1].item_id,
            "response_body": {"object": "list", "data": [{"index": 0, "embedding": [0.2]}]},
            "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
            "provider_cost": 0.02,
            "billed_cost": 0.02,
            "outbox_payload": {"batch_id": job.batch_id, "item_id": claimed[1].item_id},
            "outbox_max_attempts": 5,
        },
    ]

    first_result = await repository.complete_items_with_outbox_bulk(items=items, worker_id="w1")
    second_result = await repository.complete_items_with_outbox_bulk(items=items, worker_id="w1")

    assert first_result == "completed"
    assert second_result == "already_completed"

    outbox_rows = await batch_db.query_raw(
        """
        SELECT COUNT(*)::int AS count
        FROM deltallm_batch_completion_outbox
        WHERE batch_id = $1
        """,
        job.batch_id,
    )
    assert int(dict(outbox_rows[0])["count"]) == 2


@pytest.mark.asyncio
async def test_db_backed_completion_outbox_reclaims_expired_processing_and_enforces_owner_cas(batch_db) -> None:
    repository = BatchRepository(batch_db)
    completion_ids = await repository.enqueue_completion_outbox_many(
        [
            BatchCompletionOutboxCreate(
                batch_id="b1",
                item_id="i1",
                payload_json={"request_id": "batch:b1:i1", "item_id": "i1"},
            )
        ]
    )
    assert len(completion_ids) == 1
    completion_id = completion_ids[0]

    claimed = await repository.claim_completion_outbox_due(worker_id="w1", lease_seconds=30, limit=10)

    assert len(claimed) == 1
    assert claimed[0].completion_id == completion_id
    assert claimed[0].locked_by == "w1"
    assert claimed[0].status == "processing"
    assert await repository.mark_completion_outbox_sent(completion_id, worker_id="w2") is False

    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_completion_outbox
        SET lease_expires_at = NOW() - INTERVAL '1 second'
        WHERE completion_id = $1
        """,
        completion_id,
    )

    reclaimed = await repository.claim_completion_outbox_due(worker_id="w2", lease_seconds=30, limit=10)

    assert len(reclaimed) == 1
    assert reclaimed[0].completion_id == completion_id
    assert reclaimed[0].locked_by == "w2"
    assert reclaimed[0].attempt_count == 2
    assert await repository.mark_completion_outbox_sent(completion_id, worker_id="w2") is True

    rows = await batch_db.query_raw(
        """
        SELECT status, locked_by, lease_expires_at, processed_at
        FROM deltallm_batch_completion_outbox
        WHERE completion_id = $1
        """,
        completion_id,
    )
    assert [dict(row) for row in rows] == [
        {
            "status": "sent",
            "locked_by": None,
            "lease_expires_at": None,
            "processed_at": rows[0]["processed_at"],
        }
    ]


@pytest.mark.asyncio
async def test_db_backed_finalization_retry_survives_restart(batch_db, tmp_path: Path) -> None:
    class _FailOnceStorage:
        backend_name = "local"

        def __init__(self, delegate: LocalBatchArtifactStorage) -> None:
            self.delegate = delegate
            self.fail_next_error_artifact = True

        async def write_lines_stream(self, *, purpose: str, filename: str, lines):  # noqa: ANN001
            if purpose == "batch_error" and self.fail_next_error_artifact:
                self.fail_next_error_artifact = False
                raise RuntimeError("simulated artifact failure")
            return await self.delegate.write_lines_stream(purpose=purpose, filename=filename, lines=lines)

        async def delete(self, storage_key: str) -> None:
            await self.delegate.delete(storage_key)

    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(line_number=1, custom_id="c1", request_body={"model": "m1", "input": "a"}),
            BatchItemCreate(line_number=2, custom_id="c2", request_body={"model": "m1", "input": "b"}),
        ],
    )
    assert inserted == 2
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_job
        SET status = 'finalizing',
            total_items = 2,
            completed_items = 1,
            failed_items = 1,
            in_progress_items = 0,
            cancelled_items = 0,
            started_at = NOW()
        WHERE batch_id = $1
        """,
        job.batch_id,
    )
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_item
        SET status = 'completed',
            response_body = $2::jsonb,
            usage = $3::jsonb,
            completed_at = NOW()
        WHERE batch_id = $1
          AND line_number = 1
        """,
        job.batch_id,
        json.dumps({"object": "list", "data": [{"index": 0, "embedding": [0.1]}]}),
        json.dumps({"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1}),
    )
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_item
        SET status = 'failed',
            error_body = $2::jsonb,
            last_error = 'boom',
            completed_at = NOW()
        WHERE batch_id = $1
          AND line_number = 2
        """,
        job.batch_id,
        json.dumps({"message": "boom"}),
    )

    storage = _FailOnceStorage(LocalBatchArtifactStorage(str(tmp_path / "artifacts")))
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repository,
        storage=storage,  # type: ignore[arg-type]
        config=BatchWorkerConfig(worker_id="w1", finalization_retry_delay_seconds=60),
    )

    did_work = await worker.process_once()
    assert did_work is True

    after_failure_rows = await batch_db.query_raw(
        """
        SELECT status, output_file_id, error_file_id, lease_expires_at, locked_by
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        job.batch_id,
    )
    after_failure = dict(after_failure_rows[0])
    assert after_failure["status"] == "finalizing"
    assert after_failure["output_file_id"] is None
    assert after_failure["error_file_id"] is None
    assert after_failure["locked_by"] is None
    assert after_failure["lease_expires_at"] is not None

    file_rows = await batch_db.query_raw("SELECT file_id FROM deltallm_batch_file WHERE purpose IN ('batch_output', 'batch_error')")
    assert file_rows == []

    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_job
        SET lease_expires_at = NOW() - INTERVAL '1 second'
        WHERE batch_id = $1
        """,
        job.batch_id,
    )

    did_work = await worker.process_once()
    assert did_work is True

    finalized_rows = await batch_db.query_raw(
        """
        SELECT status, output_file_id, error_file_id
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        job.batch_id,
    )
    finalized = dict(finalized_rows[0])
    assert finalized["status"] == "completed"
    assert finalized["output_file_id"] is not None
    assert finalized["error_file_id"] is not None


@pytest.mark.asyncio
async def test_db_backed_operator_marked_failed_batch_finalizes_as_failed(batch_db, tmp_path: Path) -> None:
    repository = BatchRepository(batch_db)
    input_file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        model="m1",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        created_by_organization_id="org-1",
        expires_at=None,
    )
    assert job is not None
    inserted = await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(line_number=1, custom_id="c1", request_body={"model": "m1", "input": "a"}),
            BatchItemCreate(line_number=2, custom_id="c2", request_body={"model": "m1", "input": "b"}),
        ],
    )
    assert inserted == 2
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_job
        SET status = 'finalizing',
            total_items = 2,
            completed_items = 1,
            failed_items = 1,
            in_progress_items = 0,
            cancelled_items = 0,
            provider_error = $2,
            started_at = NOW()
        WHERE batch_id = $1
        """,
        job.batch_id,
        encode_operator_failed_reason("manual stop"),
    )
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_item
        SET status = 'completed',
            response_body = $2::jsonb,
            usage = $3::jsonb,
            completed_at = NOW()
        WHERE batch_id = $1
          AND line_number = 1
        """,
        job.batch_id,
        json.dumps({"object": "list", "data": [{"index": 0, "embedding": [0.1]}]}),
        json.dumps({"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1}),
    )
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_item
        SET status = 'failed',
            error_body = $2::jsonb,
            last_error = 'manual stop',
            completed_at = NOW()
        WHERE batch_id = $1
          AND line_number = 2
        """,
        job.batch_id,
        json.dumps({"message": "manual stop"}),
    )

    storage = LocalBatchArtifactStorage(str(tmp_path / "artifacts"))
    worker = BatchExecutorWorker(
        app=SimpleNamespace(state=SimpleNamespace()),
        repository=repository,
        storage=storage,
        config=BatchWorkerConfig(worker_id="w1"),
    )

    did_work = await worker.process_once()
    assert did_work is True

    finalized_rows = await batch_db.query_raw(
        """
        SELECT status, output_file_id, error_file_id, provider_error
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        job.batch_id,
    )
    finalized = dict(finalized_rows[0])
    assert finalized["status"] == "failed"
    assert finalized["output_file_id"] is not None
    assert finalized["error_file_id"] is not None
    artifact_rows = await batch_db.query_raw(
        """
        SELECT file_id, created_by_organization_id
        FROM deltallm_batch_file
        WHERE file_id IN ($1, $2)
        ORDER BY file_id ASC
        """,
        finalized["output_file_id"],
        finalized["error_file_id"],
    )
    assert [dict(row)["created_by_organization_id"] for row in artifact_rows] == ["org-1", "org-1"]

    service = BatchService(repository=repository, storage=storage)
    org_auth = UserAPIKeyAuth(api_key="key-b", organization_id="org-1")
    output_content = await service.get_file_content(file_id=str(finalized["output_file_id"]), auth=org_auth)
    error_content = await service.get_file_content(file_id=str(finalized["error_file_id"]), auth=org_auth)
    assert output_content
    assert error_content


@pytest.mark.asyncio
async def test_db_backed_shared_storage_flow_uses_recorded_backends_end_to_end(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.create.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    async def _fake_execute_embedding(request, payload, deployment):  # noqa: ANN001
        del request, payload, deployment
        return {
            "object": "list",
            "data": [{"index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    repository = BatchRepository(batch_db)
    local_storage = LocalBatchArtifactStorage(str(tmp_path / "local-artifacts"))
    s3_client = _FakeS3Client()
    s3_storage = S3BatchArtifactStorage(bucket="batch-bucket", prefix="prefix", client=s3_client)
    storage_registry = {"local": local_storage, "s3": s3_storage}
    legacy_service = BatchService(
        repository=repository,
        storage=local_storage,
        storage_registry=storage_registry,
    )
    active_service = _build_cutover_batch_service(
        repository=repository,
        storage=s3_storage,
        storage_registry=storage_registry,
    )
    auth = UserAPIKeyAuth(api_key="key-a")
    input_file_id = await _create_input_file(
        legacy_service,
        auth=auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )

    created = await active_service.create_embeddings_batch(
        auth=auth,
        input_file_id=input_file_id,
        endpoint="/v1/embeddings",
        metadata=None,
        completion_window=None,
    )
    batch_id = str(created["id"])

    deployment_obj = SimpleNamespace(
        deployment_id="dep-1",
        deltallm_params={"model": "m1", "api_base": "http://localhost"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):  # noqa: ANN001
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(
            self,
            *,
            primary_deployment,
            model_group,
            execute,
            return_deployment=False,
            **kwargs,
        ):
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    worker = BatchExecutorWorker(
        app=SimpleNamespace(
            state=SimpleNamespace(
                router=_Router(),
                failover_manager=_Failover(),
                spend_tracking_service=_NoopSpendTrackingService(),
                budget_service=_NoopBudgetService(),
                passive_health_tracker=_NoopPassiveHealthTracker(),
                router_state_backend=_NoopRouterStateBackend(),
                callable_target_grant_service=_AllowAllCallableTargetGrantService(),
            )
        ),
        repository=repository,
        storage=s3_storage,
        config=BatchWorkerConfig(worker_id="w-shared"),
    )
    did_work = await worker.process_once()
    assert did_work is True

    job_rows = await batch_db.query_raw(
        """
        SELECT input_file_id, output_file_id, error_file_id, status
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        batch_id,
    )
    job_row = dict(job_rows[0])
    assert job_row["status"] == "completed"
    assert job_row["input_file_id"] == input_file_id
    assert job_row["output_file_id"] is not None
    assert job_row["error_file_id"] is None

    file_rows = await batch_db.query_raw(
        """
        SELECT file_id, purpose, storage_backend, storage_key
        FROM deltallm_batch_file
        ORDER BY purpose ASC
        """
    )
    file_by_purpose = {str(dict(row)["purpose"]): dict(row) for row in file_rows}
    assert file_by_purpose["batch"]["storage_backend"] == "local"
    assert file_by_purpose["batch_output"]["storage_backend"] == "s3"
    assert await local_storage.read_bytes(str(file_by_purpose["batch"]["storage_key"]))
    assert await s3_storage.read_bytes(str(file_by_purpose["batch_output"]["storage_key"]))

    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_job
        SET expires_at = NOW() - INTERVAL '1 second',
            completed_at = NOW() - INTERVAL '1 second'
        WHERE batch_id = $1
        """,
        batch_id,
    )
    await batch_db.execute_raw(
        """
        UPDATE deltallm_batch_file
        SET expires_at = NOW() - INTERVAL '1 second'
        WHERE file_id IN ($1, $2)
        """,
        input_file_id,
        job_row["output_file_id"],
    )

    cleanup = BatchRetentionCleanupWorker(
        repository=repository,
        storage=s3_storage,
        storage_registry=storage_registry,
        config=BatchCleanupConfig(scan_limit=20),
    )
    deleted_jobs, deleted_files = await cleanup.process_once()

    assert deleted_jobs == 1
    assert deleted_files == 1
    remaining_local_files = [path for path in (tmp_path / "local-artifacts").rglob("*") if path.is_file()]
    assert remaining_local_files == [tmp_path / "local-artifacts" / str(file_by_purpose["batch"]["storage_key"])]
    assert len(s3_client.objects) == 1
    remaining_s3_key = next(iter(s3_client.objects.keys()))[1]
    assert remaining_s3_key.startswith("prefix/batch-create-stage/")
    remaining_jobs = await batch_db.query_raw("SELECT batch_id FROM deltallm_batch_job WHERE batch_id = $1", batch_id)
    remaining_files = await batch_db.query_raw("SELECT file_id, purpose FROM deltallm_batch_file")
    assert remaining_jobs == []
    assert [dict(row) for row in remaining_files] == [
        {"file_id": input_file_id, "purpose": "batch"},
    ]


@pytest.mark.asyncio
async def test_db_backed_grouped_embedding_execution_preserves_item_and_batch_totals(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.create.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    async def _fake_execute_embedding(request, payload, deployment):  # noqa: ANN001
        del request, deployment
        inputs = payload.input if isinstance(payload.input, list) else [payload.input]
        return {
            "object": "list",
            "data": [
                {"index": index, "embedding": [float(index), float(index) + 0.1]}
                for index, _ in enumerate(inputs)
            ],
            "model": "provider-embedding-model",
            "usage": {"prompt_tokens": 4 * len(inputs), "total_tokens": 4 * len(inputs)},
        }

    monkeypatch.setattr("src.batch.worker._execute_embedding", _fake_execute_embedding)

    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "artifacts"))
    service = _build_cutover_batch_service(repository=repository, storage=storage)
    auth = UserAPIKeyAuth(api_key="key-a")
    input_file_id = await _create_input_file(
        service,
        auth=auth,
        payload=(
            b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"aaaa"}}\n'
            b'{"custom_id":"c2","url":"/v1/embeddings","body":{"model":"m1","input":"bbbb"}}\n'
            b'{"custom_id":"c3","url":"/v1/embeddings","body":{"model":"m1","input":"cccc"}}\n'
        ),
    )

    created = await service.create_embeddings_batch(
        auth=auth,
        input_file_id=input_file_id,
        endpoint="/v1/embeddings",
        metadata=None,
        completion_window=None,
    )
    batch_id = str(created["id"])

    deployment_obj = SimpleNamespace(
        deployment_id="dep-1",
        deltallm_params={"model": "m1", "api_base": "http://localhost"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={
            "upstream_max_batch_inputs": 2,
            "batch_input_cost_per_token": 0.0005,
            "batch_output_cost_per_token": 0.0,
        },
    )

    class _Router:
        def resolve_model_group(self, model: str) -> str:
            return model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "dep-1"

        def require_deployment(self, model_group: str, deployment: str):  # noqa: ANN001
            del model_group, deployment
            return deployment_obj

    class _Failover:
        async def execute_with_failover(
            self,
            *,
            primary_deployment,
            model_group,
            execute,
            return_deployment=False,
            **kwargs,
        ):
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    worker = BatchExecutorWorker(
        app=SimpleNamespace(
            state=SimpleNamespace(
                router=_Router(),
                failover_manager=_Failover(),
                spend_tracking_service=_NoopSpendTrackingService(),
                budget_service=_NoopBudgetService(),
                passive_health_tracker=_NoopPassiveHealthTracker(),
                router_state_backend=_NoopRouterStateBackend(),
                callable_target_grant_service=_AllowAllCallableTargetGrantService(),
            )
        ),
        repository=repository,
        storage=storage,
        config=BatchWorkerConfig(worker_id="w-microbatch", worker_concurrency=1),
    )

    did_work = await worker.process_once()
    assert did_work is True

    item_rows = await batch_db.query_raw(
        """
        SELECT line_number, status, usage, provider_cost, billed_cost
        FROM deltallm_batch_item
        WHERE batch_id = $1
        ORDER BY line_number ASC
        """,
        batch_id,
    )
    items = [dict(row) for row in item_rows]
    assert [item["status"] for item in items] == ["completed", "completed", "completed"]
    assert [item["usage"] for item in items] == [
        {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4},
        {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4},
        {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4},
    ]

    total_provider_cost = sum(float(item["provider_cost"]) for item in items)
    total_billed_cost = sum(float(item["billed_cost"]) for item in items)
    assert total_provider_cost == pytest.approx(0.012)
    assert total_billed_cost == pytest.approx(0.006)

    cost_rows = await batch_db.query_raw(
        """
        SELECT COALESCE(SUM(provider_cost), 0) AS total_provider_cost,
               COALESCE(SUM(billed_cost), 0) AS total_billed_cost
        FROM deltallm_batch_item
        WHERE batch_id = $1
        """,
        batch_id,
    )
    cost_row = dict(cost_rows[0])
    assert float(cost_row["total_provider_cost"]) == pytest.approx(total_provider_cost)
    assert float(cost_row["total_billed_cost"]) == pytest.approx(total_billed_cost)

    job_rows = await batch_db.query_raw(
        """
        SELECT status, completed_items, failed_items, output_file_id, error_file_id
        FROM deltallm_batch_job
        WHERE batch_id = $1
        """,
        batch_id,
    )
    job_row = dict(job_rows[0])
    assert job_row["status"] == "completed"
    assert int(job_row["completed_items"] or 0) == 3
    assert int(job_row["failed_items"] or 0) == 0
    assert job_row["output_file_id"] is not None
    assert job_row["error_file_id"] is None
