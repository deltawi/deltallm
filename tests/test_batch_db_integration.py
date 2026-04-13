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

from src.batch.create import (
    BatchCreateArtifactStorageBackend,
    BatchCreatePromotionError,
    BatchCreateSessionCleanupConfig,
    BatchCreateSessionCleanupWorker,
    BatchCreateSessionCreate,
    BatchCreateSessionPromoter,
    BatchCreateSessionStager,
    BatchCreateStagedRequest,
)
from src.batch.cleanup import BatchCleanupConfig, BatchRetentionCleanupWorker
from src.batch.models import BatchCompletionOutboxCreate, BatchItemCreate
from src.batch.models import encode_operator_failed_reason
from src.batch.repository import BatchRepository
from src.batch.service import BatchService
from src.batch.storage import LocalBatchArtifactStorage, S3BatchArtifactStorage
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.models.responses import UserAPIKeyAuth

try:
    from prisma import Prisma
except Exception:  # pragma: no cover
    Prisma = None  # type: ignore[assignment]


DATABASE_URL = os.getenv("DATABASE_URL")


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
    async def record_request_outcome(self, deployment_id: str, success: bool, error: str | None = None) -> None:
        del deployment_id, success, error


class _NoopRouterStateBackend:
    async def increment_usage_counters(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs


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


@pytest.mark.asyncio
async def test_db_backed_concurrent_batch_create_enforces_pending_cap(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "artifacts"))
    service = BatchService(
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
    assert successes[0]["status"] == "queued"
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
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

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
    service = BatchService(repository=repository, storage=storage)
    auth = UserAPIKeyAuth(api_key="key-a")
    input_file_id = await _create_input_file(
        service,
        auth=auth,
        payload=b'{"custom_id":"c1","url":"/v1/embeddings","body":{"model":"m1","input":"hello"}}\n',
    )

    with pytest.raises(RuntimeError, match="simulated item insert failure"):
        await service.create_embeddings_batch(
            auth=auth,
            input_file_id=input_file_id,
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
        )

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
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

    repository = BatchRepository(batch_db)
    storage = LocalBatchArtifactStorage(str(tmp_path / "artifacts"))
    service = BatchService(repository=repository, storage=storage)
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

    assert list((tmp_path / "create-session-artifacts").rglob("*")) == []


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
    artifact = await staging.write_records(
        [
            BatchCreateStagedRequest(
                line_number=1,
                custom_id="req-orphan",
                request_body={"model": "m1", "input": "hello"},
            )
        ],
        filename="orphan.jsonl",
    )
    orphan_target = tmp_path / "create-session-artifacts" / artifact.storage_key
    old_time = datetime.now(tz=UTC) - timedelta(hours=2)
    os.utime(orphan_target, (old_time.timestamp(), old_time.timestamp()))

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
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

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
    active_service = BatchService(
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
    assert deleted_files == 2
    remaining_local_files = [path for path in (tmp_path / "local-artifacts").rglob("*") if path.is_file()]
    assert remaining_local_files == []
    assert s3_client.objects == {}
    remaining_jobs = await batch_db.query_raw("SELECT batch_id FROM deltallm_batch_job WHERE batch_id = $1", batch_id)
    remaining_files = await batch_db.query_raw("SELECT file_id FROM deltallm_batch_file")
    assert remaining_jobs == []
    assert remaining_files == []


@pytest.mark.asyncio
async def test_db_backed_grouped_embedding_execution_preserves_item_and_batch_totals(
    batch_db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("src.batch.service.ensure_batch_model_allowed", lambda *args, **kwargs: None)

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
    service = BatchService(repository=repository, storage=storage)
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
