from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.batch.create import (
    BatchCreatePromotionResult,
    BatchCreateSessionRecord,
    BatchCreateSessionStatus,
)
from src.batch.create.service import BatchCreateSessionService, BatchCreateSessionServiceResult
from src.batch.models import BatchJobRecord, BatchJobStatus
from src.batch.service import BatchService
from src.models.responses import UserAPIKeyAuth


def _job(*, batch_id: str = "batch-1") -> BatchJobRecord:
    now = datetime.now(tz=UTC)
    return BatchJobRecord(
        batch_id=batch_id,
        endpoint="/v1/embeddings",
        status=BatchJobStatus.QUEUED,
        execution_mode="managed_internal",
        input_file_id="file-1",
        output_file_id=None,
        error_file_id=None,
        model="m1",
        metadata=None,
        provider_batch_id=None,
        provider_status=None,
        provider_error=None,
        provider_last_sync_at=None,
        total_items=1,
        in_progress_items=0,
        completed_items=0,
        failed_items=0,
        cancelled_items=0,
        locked_by=None,
        lease_expires_at=None,
        cancel_requested_at=None,
        status_last_updated_at=now,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        expires_at=None,
        created_by_organization_id=None,
    )


def _session(
    *,
    status: str = BatchCreateSessionStatus.COMPLETED,
    input_file_id: str = "file-1",
    metadata: dict | None = None,
) -> BatchCreateSessionRecord:
    now = datetime.now(tz=UTC)
    return BatchCreateSessionRecord(
        session_id="session-1",
        target_batch_id="batch-1",
        status=status,
        endpoint="/v1/embeddings",
        input_file_id=input_file_id,
        staged_storage_backend="local",
        staged_storage_key="batch-create-stage/session-1.jsonl",
        staged_checksum="abc",
        staged_bytes=12,
        expected_item_count=1,
        inferred_model="m1",
        metadata=metadata,
        requested_service_tier=None,
        effective_service_tier=None,
        service_tier_source=None,
        scheduling_scope_key="api_key:key-a",
        priority_quota_scope_key="api_key:key-a",
        idempotency_scope_key="api_key:key-a",
        idempotency_key="idem-1",
        last_error_code=None,
        last_error_message=None,
        promotion_attempt_count=0,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        created_by_organization_id=None,
        created_at=now,
        completed_at=now if status == BatchCreateSessionStatus.COMPLETED else None,
        last_attempt_at=now,
        expires_at=None,
    )


class _FailIfLoadedRepo:
    def __init__(self, *, job: BatchJobRecord) -> None:
        self._job = job

    async def get_file(self, file_id: str):  # noqa: ANN201
        raise AssertionError(f"get_file should not be called for matching idempotent retry: {file_id}")

    async def get_job(self, batch_id: str):  # noqa: ANN201
        assert batch_id == self._job.batch_id
        return self._job


class _PromoterStub:
    def __init__(self, *, result: BatchCreatePromotionResult) -> None:
        self.result = result
        self.calls: list[str] = []

    async def promote_session(self, session_id: str) -> BatchCreatePromotionResult:
        self.calls.append(session_id)
        return self.result


class _NeverCalledStager:
    async def stage_session(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError("stage_session should not be called")


@pytest.mark.asyncio
async def test_create_session_service_resolves_matching_existing_session_before_loading_input_file() -> None:
    existing_session = _session(status=BatchCreateSessionStatus.COMPLETED)
    job = _job(batch_id=existing_session.target_batch_id)

    async def _get_session_by_idempotency_key(**kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return existing_session

    promoter = _PromoterStub(
        result=BatchCreatePromotionResult(
            session_id=existing_session.session_id,
            batch_id=job.batch_id,
            promoted=False,
            job=job,
        )
    )
    service = BatchCreateSessionService(
        repository=_FailIfLoadedRepo(job=job),  # type: ignore[arg-type]
        create_session_repository=SimpleNamespace(
            get_session_by_idempotency_key=_get_session_by_idempotency_key
        ),  # type: ignore[arg-type]
        stager=SimpleNamespace(),  # type: ignore[arg-type]
        promoter=promoter,  # type: ignore[arg-type]
        storage_registry={},
        max_file_bytes=1024,
        max_items_per_batch=100,
        max_line_bytes=1024,
        storage_chunk_size=128,
        idempotency_enabled=True,
    )

    result = await service.create_embeddings_batch(
        auth=UserAPIKeyAuth(api_key="key-a"),
        input_file_id="file-1",
        endpoint="/v1/embeddings",
        metadata=None,
        completion_window=None,
        idempotency_key="idem-1",
    )

    assert result.job.batch_id == "batch-1"
    assert result.audit_metadata["idempotency_resolution"] == "existing"
    assert promoter.calls == ["session-1"]


@pytest.mark.asyncio
async def test_create_session_service_rejects_mismatched_existing_idempotent_request() -> None:
    existing_session = _session(metadata={"region": "eu"})

    async def _get_session_by_idempotency_key(**kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return existing_session

    service = BatchCreateSessionService(
        repository=SimpleNamespace(),  # type: ignore[arg-type]
        create_session_repository=SimpleNamespace(
            get_session_by_idempotency_key=_get_session_by_idempotency_key
        ),  # type: ignore[arg-type]
        stager=SimpleNamespace(),  # type: ignore[arg-type]
        promoter=SimpleNamespace(),  # type: ignore[arg-type]
        storage_registry={},
        max_file_bytes=1024,
        max_items_per_batch=100,
        max_line_bytes=1024,
        storage_chunk_size=128,
        idempotency_enabled=True,
    )

    with pytest.raises(HTTPException) as exc:
        await service.create_embeddings_batch(
            auth=UserAPIKeyAuth(api_key="key-a"),
            input_file_id="file-1",
            endpoint="/v1/embeddings",
            metadata={"region": "us"},
            completion_window=None,
            idempotency_key="idem-1",
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_batch_service_create_result_delegates_to_bound_create_session_service() -> None:
    class _CreateSessionServiceStub:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create_embeddings_batch(self, **kwargs):  # noqa: ANN003, ANN201
            self.calls.append(kwargs)
            return BatchCreateSessionServiceResult(
                job=_job(batch_id="batch-delegated"),
                audit_metadata={"create_path": "create_session", "idempotency_resolution": "created"},
            )

    create_session_service = _CreateSessionServiceStub()
    batch_service = BatchService(
        repository=SimpleNamespace(),  # type: ignore[arg-type]
        storage=SimpleNamespace(backend_name="local"),  # type: ignore[arg-type]
        create_session_service=create_session_service,  # type: ignore[arg-type]
    )

    result = await batch_service.create_embeddings_batch_result(
        auth=UserAPIKeyAuth(api_key="key-a"),
        input_file_id="file-1",
        endpoint="/v1/embeddings",
        metadata=None,
        completion_window=None,
        idempotency_key="idem-1",
    )

    assert result.response["id"] == "batch-delegated"
    assert result.response["status"] == "queued"
    assert result.audit_metadata["create_path"] == "create_session"
    assert create_session_service.calls[0]["idempotency_key"] == "idem-1"


@pytest.mark.asyncio
async def test_create_session_service_retries_existing_permanent_failure_as_client_error() -> None:
    existing_session = _session(status=BatchCreateSessionStatus.FAILED_PERMANENT)
    existing_session.last_error_code = "item_count_mismatch"
    existing_session.last_error_message = "stored mismatch"

    async def _get_session_by_idempotency_key(**kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return existing_session

    service = BatchCreateSessionService(
        repository=SimpleNamespace(),  # type: ignore[arg-type]
        create_session_repository=SimpleNamespace(
            get_session_by_idempotency_key=_get_session_by_idempotency_key
        ),  # type: ignore[arg-type]
        stager=SimpleNamespace(),  # type: ignore[arg-type]
        promoter=SimpleNamespace(),  # type: ignore[arg-type]
        storage_registry={},
        max_file_bytes=1024,
        max_items_per_batch=100,
        max_line_bytes=1024,
        storage_chunk_size=128,
        idempotency_enabled=True,
    )

    with pytest.raises(HTTPException) as exc:
        await service.create_embeddings_batch(
            auth=UserAPIKeyAuth(api_key="key-a"),
            input_file_id="file-1",
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
            idempotency_key="idem-1",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "stored mismatch"


def test_create_session_service_uses_team_scope_for_idempotency_and_api_key_scope_for_org_only_pending_limit() -> None:
    service = BatchCreateSessionService(
        repository=SimpleNamespace(),  # type: ignore[arg-type]
        create_session_repository=SimpleNamespace(),  # type: ignore[arg-type]
        stager=SimpleNamespace(),  # type: ignore[arg-type]
        promoter=SimpleNamespace(),  # type: ignore[arg-type]
        storage_registry={},
        max_file_bytes=1024,
        max_items_per_batch=100,
        max_line_bytes=1024,
        storage_chunk_size=128,
        idempotency_enabled=True,
    )

    assert service._idempotency_scope_key(  # noqa: SLF001
        UserAPIKeyAuth(api_key="key-a", team_id="team-1", organization_id="org-1")
    ) == "team:team-1"
    assert service._session_scope_key(  # noqa: SLF001
        UserAPIKeyAuth(api_key="key-a", team_id="team-1", organization_id="org-1")
    ) == "team:team-1"
    assert service._idempotency_scope_key(  # noqa: SLF001
        UserAPIKeyAuth(api_key="key-a", organization_id="org-1")
    ) == "organization:org-1"
    assert service._session_scope_key(  # noqa: SLF001
        UserAPIKeyAuth(api_key="key-a", organization_id="org-1")
    ) == "api_key:key-a"


@pytest.mark.asyncio
async def test_create_session_service_precheck_rejects_before_staging_new_request() -> None:
    file_record = SimpleNamespace(
        file_id="file-1",
        created_by_api_key="key-a",
        created_by_team_id=None,
        created_by_organization_id=None,
        bytes=32,
        storage_backend="local",
        storage_key="input/file-1.jsonl",
    )

    async def _get_session_by_idempotency_key(**kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return None

    async def _get_file(file_id: str):  # noqa: ANN201
        assert file_id == "file-1"
        return file_record

    async def _count_active_jobs_for_scope(**kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return 1

    service = BatchCreateSessionService(
        repository=SimpleNamespace(
            get_file=_get_file,
            count_active_jobs_for_scope=_count_active_jobs_for_scope,
        ),  # type: ignore[arg-type]
        create_session_repository=SimpleNamespace(
            get_session_by_idempotency_key=_get_session_by_idempotency_key
        ),  # type: ignore[arg-type]
        stager=_NeverCalledStager(),  # type: ignore[arg-type]
        promoter=SimpleNamespace(),  # type: ignore[arg-type]
        storage_registry={"local": SimpleNamespace()},
        max_file_bytes=1024,
        max_items_per_batch=100,
        max_line_bytes=1024,
        storage_chunk_size=128,
        max_pending_batches_per_scope=1,
        idempotency_enabled=True,
    )

    with pytest.raises(HTTPException) as exc:
        await service.create_embeddings_batch(
            auth=UserAPIKeyAuth(api_key="key-a"),
            input_file_id="file-1",
            endpoint="/v1/embeddings",
            metadata=None,
            completion_window=None,
            idempotency_key="idem-1",
        )

    assert exc.value.status_code == 429
