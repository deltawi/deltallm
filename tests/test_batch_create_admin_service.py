from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from src.batch.create.admin_service import BatchCreateSessionAdminService
from src.batch.create.models import BatchCreateSessionRecord, BatchCreateSessionStatus
from src.batch.create.promoter import BatchCreatePromotionError, BatchCreatePromotionResult


def _session(*, status: str = BatchCreateSessionStatus.STAGED) -> BatchCreateSessionRecord:
    now = datetime.now(tz=UTC)
    return BatchCreateSessionRecord(
        session_id="session-1",
        target_batch_id="batch-1",
        status=status,
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        staged_storage_backend="local",
        staged_storage_key="batch-create-stage/2026/04/14/session-1.jsonl",
        staged_checksum="abc",
        staged_bytes=64,
        expected_item_count=1,
        inferred_model="m1",
        metadata=None,
        requested_service_tier=None,
        effective_service_tier=None,
        service_tier_source=None,
        scheduling_scope_key="team:team-1",
        priority_quota_scope_key="team:team-1",
        idempotency_scope_key=None,
        idempotency_key=None,
        last_error_code=None,
        last_error_message=None,
        promotion_attempt_count=0,
        created_by_api_key="key-a",
        created_by_user_id="user-1",
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
        created_at=now,
        completed_at=now if status == BatchCreateSessionStatus.COMPLETED else None,
        last_attempt_at=now,
        expires_at=None,
    )


class _RepositoryStub:
    def __init__(self, session: BatchCreateSessionRecord | None) -> None:
        self.session = session
        self.expire_calls: list[tuple[str, datetime, tuple[str, ...] | None]] = []
        self.summary_calls = 0

    async def get_session(self, session_id: str) -> BatchCreateSessionRecord | None:
        assert session_id == "session-1"
        return self.session

    async def mark_session_expired(
        self,
        session_id: str,
        *,
        expired_at: datetime,
        from_statuses: tuple[str, ...] | None = None,
    ) -> BatchCreateSessionRecord | None:
        self.expire_calls.append((session_id, expired_at, from_statuses))
        if self.session is None:
            return None
        self.session = BatchCreateSessionRecord(
            **{**self.session.__dict__, "status": BatchCreateSessionStatus.EXPIRED, "expires_at": expired_at}
        )
        return self.session

    async def summarize_statuses(self) -> dict[str, int]:
        self.summary_calls += 1
        return {
            "staged": 0,
            "completed": 0,
            "failed_retryable": 0,
            "failed_permanent": 0,
            "expired": 1 if self.session and self.session.status == BatchCreateSessionStatus.EXPIRED else 0,
        }


class _PromoterStub:
    def __init__(self, result: BatchCreatePromotionResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    async def promote_session(self, session_id: str) -> BatchCreatePromotionResult:
        self.calls.append(session_id)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class _StagingStub:
    def __init__(self, *, fail_delete: bool = False) -> None:
        self.fail_delete = fail_delete
        self.deleted: list[tuple[str, str]] = []

    async def delete(self, artifact) -> None:  # noqa: ANN001
        self.deleted.append((artifact.storage_backend, artifact.storage_key))
        if self.fail_delete:
            raise RuntimeError("delete failed")


@pytest.mark.asyncio
async def test_admin_retry_rejects_completed_session() -> None:
    repository = _RepositoryStub(_session(status=BatchCreateSessionStatus.COMPLETED))
    service = BatchCreateSessionAdminService(
        repository=repository,  # type: ignore[arg-type]
        promoter=_PromoterStub(result=None),  # type: ignore[arg-type]
        staging=_StagingStub(),  # type: ignore[arg-type]
    )

    with pytest.raises(HTTPException) as exc:
        await service.retry_session("session-1")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_admin_retry_uses_promoter_and_returns_refreshed_session() -> None:
    completed = _session(status=BatchCreateSessionStatus.COMPLETED)

    class _RefreshingRepository(_RepositoryStub):
        def __init__(self) -> None:
            super().__init__(_session(status=BatchCreateSessionStatus.FAILED_RETRYABLE))
            self.get_calls = 0

        async def get_session(self, session_id: str) -> BatchCreateSessionRecord | None:
            self.get_calls += 1
            if self.get_calls >= 2:
                self.session = completed
            return await super().get_session(session_id)

    repository = _RefreshingRepository()
    promoter = _PromoterStub(
        result=BatchCreatePromotionResult(session_id="session-1", batch_id="batch-1", promoted=True, job=None)
    )
    service = BatchCreateSessionAdminService(
        repository=repository,  # type: ignore[arg-type]
        promoter=promoter,  # type: ignore[arg-type]
        staging=_StagingStub(),  # type: ignore[arg-type]
    )

    result = await service.retry_session("session-1")

    assert promoter.calls == ["session-1"]
    assert result.session.status == BatchCreateSessionStatus.COMPLETED
    assert result.promotion.promoted is True


@pytest.mark.asyncio
async def test_admin_retry_maps_promotion_error_to_http() -> None:
    repository = _RepositoryStub(_session(status=BatchCreateSessionStatus.FAILED_RETRYABLE))
    promoter = _PromoterStub(
        error=BatchCreatePromotionError(
            "Active batch count exceeds embeddings_batch_max_pending_batches_per_scope (20)",
            code="pending_limit_exceeded",
            retryable=True,
        )
    )
    service = BatchCreateSessionAdminService(
        repository=repository,  # type: ignore[arg-type]
        promoter=promoter,  # type: ignore[arg-type]
        staging=_StagingStub(),  # type: ignore[arg-type]
    )

    with pytest.raises(HTTPException) as exc:
        await service.retry_session("session-1")

    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_admin_expire_marks_session_expired_and_deletes_artifact() -> None:
    repository = _RepositoryStub(_session(status=BatchCreateSessionStatus.FAILED_PERMANENT))
    staging = _StagingStub()
    service = BatchCreateSessionAdminService(
        repository=repository,  # type: ignore[arg-type]
        promoter=_PromoterStub(result=None),  # type: ignore[arg-type]
        staging=staging,  # type: ignore[arg-type]
    )

    result = await service.expire_session("session-1")

    assert result.session.status == BatchCreateSessionStatus.EXPIRED
    assert result.artifact_deleted is True
    assert repository.expire_calls[0][2] == (
        BatchCreateSessionStatus.STAGED,
        BatchCreateSessionStatus.FAILED_RETRYABLE,
        BatchCreateSessionStatus.FAILED_PERMANENT,
    )
    assert staging.deleted == [("local", "batch-create-stage/2026/04/14/session-1.jsonl")]


@pytest.mark.asyncio
async def test_admin_expire_rejects_completed_session() -> None:
    repository = _RepositoryStub(_session(status=BatchCreateSessionStatus.COMPLETED))
    service = BatchCreateSessionAdminService(
        repository=repository,  # type: ignore[arg-type]
        promoter=_PromoterStub(result=None),  # type: ignore[arg-type]
        staging=_StagingStub(),  # type: ignore[arg-type]
    )

    with pytest.raises(HTTPException) as exc:
        await service.expire_session("session-1")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_admin_expire_preserves_expired_row_when_artifact_delete_fails() -> None:
    repository = _RepositoryStub(_session(status=BatchCreateSessionStatus.STAGED))
    service = BatchCreateSessionAdminService(
        repository=repository,  # type: ignore[arg-type]
        promoter=_PromoterStub(result=None),  # type: ignore[arg-type]
        staging=_StagingStub(fail_delete=True),  # type: ignore[arg-type]
    )

    result = await service.expire_session("session-1")

    assert result.session.status == BatchCreateSessionStatus.EXPIRED
    assert result.artifact_deleted is False
