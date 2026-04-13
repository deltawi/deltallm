from __future__ import annotations

from datetime import UTC, datetime, timedelta
import pytest

from src.batch.create.models import BatchCreateSessionRecord, BatchCreateSessionStatus, BatchCreateStagedRequest
from src.batch.create.promoter import BatchCreatePromotionError, BatchCreateSessionPromoter
from src.batch.models import BatchJobRecord


def _session(*, status: str) -> BatchCreateSessionRecord:
    now = datetime.now(tz=UTC)
    return BatchCreateSessionRecord(
        session_id="session-1",
        target_batch_id="batch-1",
        status=status,
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        staged_storage_backend="local",
        staged_storage_key="batch-create-stage/2026/04/13/session-1.jsonl",
        staged_checksum="checksum-1",
        staged_bytes=128,
        expected_item_count=1,
        inferred_model="m1",
        metadata={"source": "test"},
        requested_service_tier=None,
        effective_service_tier=None,
        service_tier_source=None,
        scheduling_scope_key=None,
        priority_quota_scope_key=None,
        idempotency_scope_key=None,
        idempotency_key=None,
        last_error_code=None,
        last_error_message=None,
        promotion_attempt_count=0,
        created_by_api_key="key-1",
        created_by_user_id="user-1",
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
        created_at=now,
        completed_at=now if status == BatchCreateSessionStatus.COMPLETED else None,
        last_attempt_at=None,
        expires_at=None,
    )


def _job() -> BatchJobRecord:
    now = datetime.now(tz=UTC)
    return BatchJobRecord(
        batch_id="batch-1",
        endpoint="/v1/embeddings",
        status="queued",
        execution_mode="managed_internal",
        input_file_id="file-1",
        output_file_id=None,
        error_file_id=None,
        model="m1",
        metadata={"source": "test"},
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
        created_by_api_key="key-1",
        created_by_user_id="user-1",
        created_by_team_id="team-1",
        created_at=now,
        started_at=None,
        completed_at=None,
        expires_at=None,
        created_by_organization_id="org-1",
    )


class _SessionRepo:
    def __init__(self, session: BatchCreateSessionRecord) -> None:
        self.session = session
        self.failed_retryable_calls: list[dict[str, object]] = []
        self.failed_permanent_calls: list[dict[str, object]] = []

    async def get_session(self, session_id: str) -> BatchCreateSessionRecord | None:
        return self.session if session_id == self.session.session_id else None

    async def mark_session_failed_retryable(self, session_id: str, **kwargs):  # noqa: ANN003
        self.failed_retryable_calls.append({"session_id": session_id, **kwargs})
        return self.session

    async def mark_session_failed_permanent(self, session_id: str, **kwargs):  # noqa: ANN003
        self.failed_permanent_calls.append({"session_id": session_id, **kwargs})
        return self.session


class _TxSessionRepo(_SessionRepo):
    async def get_session_for_update(self, session_id: str) -> BatchCreateSessionRecord | None:
        return await self.get_session(session_id)

    async def mark_session_completed(self, session_id: str, **kwargs):  # noqa: ANN003
        del kwargs
        return self.session if session_id == self.session.session_id else None


class _FakeTxManager:
    def __init__(self, tx_client: object) -> None:
        self.tx_client = tx_client

    async def __aenter__(self) -> object:
        return self.tx_client

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        del exc_type, exc, tb


class _FakePrisma:
    def __init__(self, tx_client: object) -> None:
        self.tx_client = tx_client
        self.tx_calls: list[dict[str, object]] = []

    def tx(self, *, max_wait, timeout):  # noqa: ANN001
        self.tx_calls.append({"max_wait": max_wait, "timeout": timeout})
        return _FakeTxManager(self.tx_client)


class _FakeRepository:
    def __init__(
        self,
        *,
        session_repo: _SessionRepo,
        job: BatchJobRecord | None = None,
        tx_repository: _FakeRepository | None = None,
        active_jobs: int = 0,
    ) -> None:
        self.create_sessions = session_repo
        self._job = job
        self._tx_repository = tx_repository
        self._active_jobs = active_jobs
        self.lock_calls: list[tuple[str, str]] = []
        self.prisma = _FakePrisma(object()) if tx_repository is None else None
        if tx_repository is not None:
            self.prisma = _FakePrisma(tx_repository)

    def with_prisma(self, prisma_client: object):  # noqa: ANN001
        assert self._tx_repository is not None
        assert prisma_client is self._tx_repository
        return self._tx_repository

    async def get_job(self, batch_id: str) -> BatchJobRecord | None:
        return self._job if self._job is not None and self._job.batch_id == batch_id else None

    async def acquire_scope_advisory_lock(self, *, scope_type: str, scope_id: str) -> None:
        self.lock_calls.append((scope_type, scope_id))

    async def count_active_jobs_for_scope(self, *, created_by_api_key=None, created_by_team_id=None) -> int:  # noqa: ANN001
        del created_by_api_key, created_by_team_id
        return self._active_jobs

    async def create_job(self, **kwargs):  # noqa: ANN003
        raise AssertionError("create_job should not be reached in this test")

    async def create_items(self, batch_id: str, items):  # noqa: ANN001
        del batch_id, items
        raise AssertionError("create_items should not be reached in this test")


class _FakeStaging:
    def __init__(self, *, records: list[BatchCreateStagedRequest] | None = None) -> None:
        self.read_calls = 0
        self.records = records or []

    def read_records(self, artifact):  # noqa: ANN001
        async def _iter():
            self.read_calls += 1
            for record in self.records:
                yield record

        return _iter()


class _SuccessfulTxRepository(_FakeRepository):
    def __init__(self, *, session_repo: _TxSessionRepo, active_jobs: int = 0) -> None:
        super().__init__(session_repo=session_repo, active_jobs=active_jobs)
        self.created_jobs: list[dict[str, object]] = []
        self.created_items: list[tuple[str, list[BatchCreateStagedRequest]]] = []

    async def create_job(self, **kwargs):  # noqa: ANN003
        self.created_jobs.append(dict(kwargs))
        return _job()

    async def create_items(self, batch_id: str, items):  # noqa: ANN001
        staged_items = [
            BatchCreateStagedRequest(
                line_number=int(item.line_number),
                custom_id=str(item.custom_id),
                request_body=dict(item.request_body),
            )
            for item in items
        ]
        self.created_items.append((batch_id, staged_items))
        return len(items)


@pytest.mark.asyncio
async def test_promote_completed_session_returns_existing_job_without_restaging() -> None:
    session_repo = _SessionRepo(_session(status=BatchCreateSessionStatus.COMPLETED))
    repository = _FakeRepository(session_repo=session_repo, job=_job())
    staging = _FakeStaging()
    promoter = BatchCreateSessionPromoter(repository=repository, staging=staging)

    result = await promoter.promote_session("session-1")

    assert result.promoted is False
    assert result.batch_id == "batch-1"
    assert result.job is not None
    assert staging.read_calls == 0
    assert session_repo.failed_retryable_calls == []
    assert session_repo.failed_permanent_calls == []


@pytest.mark.asyncio
async def test_promote_session_rejects_unpromotable_status_without_mutating_session() -> None:
    session_repo = _SessionRepo(_session(status=BatchCreateSessionStatus.FAILED_PERMANENT))
    repository = _FakeRepository(session_repo=session_repo)
    promoter = BatchCreateSessionPromoter(repository=repository, staging=_FakeStaging())

    with pytest.raises(BatchCreatePromotionError, match="not promotable") as exc:
        await promoter.promote_session("session-1")

    assert exc.value.retryable is False
    assert exc.value.code == "session_not_promotable"
    assert session_repo.failed_retryable_calls == []
    assert session_repo.failed_permanent_calls == []


@pytest.mark.asyncio
async def test_promote_session_short_circuits_before_staging_when_soft_precheck_hits_pending_limit() -> None:
    session = _session(status=BatchCreateSessionStatus.STAGED)
    tx_session_repo = _TxSessionRepo(session)
    tx_repository = _FakeRepository(session_repo=tx_session_repo, active_jobs=1)
    repository = _FakeRepository(
        session_repo=_SessionRepo(session),
        tx_repository=tx_repository,
        active_jobs=1,
    )
    promoter = BatchCreateSessionPromoter(
        repository=repository,
        staging=_FakeStaging(
            records=[
                BatchCreateStagedRequest(
                    line_number=1,
                    custom_id="req-1",
                    request_body={"model": "m1", "input": "hello"},
                )
            ]
        ),
        max_pending_batches_per_scope=1,
    )
    metric_calls: list[tuple[str, str]] = []
    from src.batch.create import promoter as promoter_module

    def _capture_metric(*, action: str, status: str) -> None:
        metric_calls.append((action, status))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(promoter_module, "increment_batch_create_session_action", _capture_metric)

    try:
        with pytest.raises(BatchCreatePromotionError, match="Active batch count exceeds") as exc:
            await promoter.promote_session("session-1")

        assert exc.value.retryable is True
        assert exc.value.code == "pending_limit_exceeded"
        assert tx_repository.lock_calls == []
        assert repository.prisma is not None
        assert repository.prisma.tx_calls == []
        assert promoter.staging.read_calls == 0
        assert repository.create_sessions.failed_retryable_calls == []
        assert repository.create_sessions.failed_permanent_calls == []
        assert metric_calls == [("promotion_precheck", "rejected")]
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_promote_session_uses_configured_tx_timings_and_locked_recheck() -> None:
    session = _session(status=BatchCreateSessionStatus.STAGED)
    tx_repository = _SuccessfulTxRepository(session_repo=_TxSessionRepo(session), active_jobs=0)
    repository = _FakeRepository(
        session_repo=_SessionRepo(session),
        tx_repository=tx_repository,
        active_jobs=0,
    )
    staging = _FakeStaging(
        records=[
            BatchCreateStagedRequest(
                line_number=1,
                custom_id="req-1",
                request_body={"model": "m1", "input": "hello"},
            )
        ]
    )
    promoter = BatchCreateSessionPromoter(
        repository=repository,
        staging=staging,
        tx_max_wait_seconds=2.5,
        tx_timeout_seconds=45.0,
    )

    result = await promoter.promote_session("session-1")

    assert result.promoted is True
    assert result.batch_id == "batch-1"
    assert staging.read_calls == 1
    assert tx_repository.lock_calls == [("team", "team-1")]
    assert repository.prisma is not None
    assert repository.prisma.tx_calls == [
        {
            "max_wait": timedelta(seconds=2.5),
            "timeout": timedelta(seconds=45.0),
        }
    ]
    assert tx_repository.created_jobs[0]["status"] == "queued"


@pytest.mark.asyncio
async def test_promote_session_marks_retryable_failure_when_locked_recheck_hits_pending_limit() -> None:
    session = _session(status=BatchCreateSessionStatus.STAGED)
    tx_repository = _SuccessfulTxRepository(session_repo=_TxSessionRepo(session), active_jobs=1)
    repository = _FakeRepository(
        session_repo=_SessionRepo(session),
        tx_repository=tx_repository,
        active_jobs=0,
    )
    staging = _FakeStaging(
        records=[
            BatchCreateStagedRequest(
                line_number=1,
                custom_id="req-1",
                request_body={"model": "m1", "input": "hello"},
            )
        ]
    )
    promoter = BatchCreateSessionPromoter(
        repository=repository,
        staging=staging,
        max_pending_batches_per_scope=1,
    )

    with pytest.raises(BatchCreatePromotionError, match="Active batch count exceeds") as exc:
        await promoter.promote_session("session-1")

    assert exc.value.retryable is True
    assert exc.value.code == "pending_limit_exceeded"
    assert staging.read_calls == 1
    assert tx_repository.lock_calls == [("team", "team-1")]
    assert len(repository.create_sessions.failed_retryable_calls) == 1
