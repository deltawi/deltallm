from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.batch.models import (
    BatchJobRecord,
    BatchJobStatus,
    BatchWebhookDeliveryStatus,
    BatchWebhookOutboxCreate,
    BatchWebhookOutboxRecord,
)
from src.batch.repositories.webhook_outbox_repository import BatchWebhookOutboxRepository
from src.batch.repository import BatchRepository
from src.batch.webhooks.events import build_batch_webhook_event


def _job(*, configured: bool = True) -> BatchJobRecord:
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    return BatchJobRecord(
        batch_id="batch-1",
        endpoint="/v1/embeddings",
        status=BatchJobStatus.COMPLETED,
        execution_mode="managed_internal",
        input_file_id="file-input",
        output_file_id="file-output",
        error_file_id=None,
        model="model-1",
        metadata={"customer_job_id": "job-1"},
        provider_batch_id=None,
        provider_status=None,
        provider_error=None,
        provider_last_sync_at=None,
        total_items=1,
        in_progress_items=0,
        completed_items=1,
        failed_items=0,
        cancelled_items=0,
        locked_by=None,
        lease_expires_at=None,
        cancel_requested_at=None,
        status_last_updated_at=now,
        created_by_api_key="key-1",
        created_by_user_id=None,
        created_by_team_id=None,
        created_at=now,
        started_at=now,
        completed_at=now,
        expires_at=None,
        webhook_config_ciphertext="v1.key.ciphertext" if configured else None,
        webhook_config_fingerprint="a" * 64 if configured else None,
    )


def _outbox_record(job: BatchJobRecord) -> BatchWebhookOutboxRecord:
    now = datetime(2026, 8, 5, 12, 30, tzinfo=UTC)
    event = build_batch_webhook_event(job, event_id="evt-existing", created_at=now)
    return BatchWebhookOutboxRecord(
        event_id=event.event_id,
        batch_id=job.batch_id,
        event_type=event.event_type,
        target_config_ciphertext=str(job.webhook_config_ciphertext),
        payload_json=event.payload_json,
        payload_sha256=event.payload_sha256,
        status=BatchWebhookDeliveryStatus.QUEUED,
        attempt_count=0,
        max_attempts=8,
        next_attempt_at=now,
        last_status_code=None,
        last_error=None,
        locked_by=None,
        lease_expires_at=None,
        created_at=now,
        updated_at=now,
        delivered_at=None,
    )


@pytest.mark.asyncio
async def test_webhook_outbox_repository_inserts_explicit_stable_event_id() -> None:
    now = datetime.now(tz=UTC)

    class _Prisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN201
            assert "ON CONFLICT (batch_id, event_type) DO NOTHING" in sql
            return [
                {
                    "event_id": params[0],
                    "batch_id": params[1],
                    "event_type": params[2],
                    "target_config_ciphertext": params[3],
                    "payload_json": params[4],
                    "payload_sha256": params[5],
                    "status": params[6],
                    "attempt_count": params[7],
                    "max_attempts": params[8],
                    "next_attempt_at": now,
                    "last_status_code": params[10],
                    "last_error": params[11],
                    "locked_by": None,
                    "lease_expires_at": None,
                    "created_at": now,
                    "updated_at": now,
                    "delivered_at": None,
                }
            ]

    job = _job()
    event = build_batch_webhook_event(job, event_id="evt-stable", created_at=now)
    repository = BatchWebhookOutboxRepository(_Prisma())

    inserted = await repository.insert_event(
        BatchWebhookOutboxCreate(
            event_id=event.event_id,
            batch_id=job.batch_id,
            event_type=event.event_type,
            target_config_ciphertext=str(job.webhook_config_ciphertext),
            payload_json=event.payload_json,
            payload_sha256=event.payload_sha256,
        )
    )

    assert inserted is not None
    assert inserted.event_id == "evt-stable"
    assert inserted.payload_json["id"] == "evt-stable"


class _TransactionManager:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    @asynccontextmanager
    async def tx(self):  # noqa: ANN201
        try:
            yield object()
        except Exception:
            self.rolled_back = True
            raise
        else:
            self.committed = True


class _JobRepository:
    def __init__(self, job: BatchJobRecord | None) -> None:
        self.job = job
        self.calls: list[dict[str, object]] = []
        self.observed: list[str] = []

    async def attach_artifacts_and_finalize(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        return self.job

    def observe_finalization(self, job: BatchJobRecord) -> None:
        self.observed.append(job.batch_id)


class _OutboxRepository:
    def __init__(
        self,
        *,
        inserted: object | None = SimpleNamespace(),
        existing: BatchWebhookOutboxRecord | None = None,
        error: Exception | None = None,
    ) -> None:
        self.inserted = inserted
        self.existing = existing
        self.error = error
        self.events: list[BatchWebhookOutboxCreate] = []

    async def insert_event(self, event: BatchWebhookOutboxCreate):  # noqa: ANN201
        self.events.append(event)
        if self.error is not None:
            raise self.error
        return self.inserted

    async def get_by_batch_and_event_type(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return self.existing


def _aggregate_repository(
    *,
    job: BatchJobRecord | None,
    outbox: _OutboxRepository,
) -> tuple[BatchRepository, _TransactionManager, _JobRepository]:
    transaction_manager = _TransactionManager()
    transactional_jobs = _JobRepository(job)
    transactional_repository = SimpleNamespace(
        jobs=transactional_jobs,
        webhook_outbox=outbox,
        webhook_max_attempts=8,
    )
    repository = BatchRepository()
    repository.prisma = transaction_manager
    observed_jobs = _JobRepository(None)
    repository.jobs = observed_jobs  # type: ignore[assignment]
    repository.with_prisma = lambda _tx: transactional_repository  # type: ignore[method-assign]
    return repository, transaction_manager, observed_jobs


@pytest.mark.asyncio
async def test_atomic_finalization_enqueues_configured_event_then_publishes_metric() -> None:
    outbox = _OutboxRepository()
    repository, transaction, observed_jobs = _aggregate_repository(job=_job(), outbox=outbox)

    finalized = await repository.attach_artifacts_and_finalize(
        batch_id="batch-1",
        output_file_id="file-output",
        error_file_id=None,
        final_status=BatchJobStatus.COMPLETED,
        worker_id="worker-1",
    )

    assert finalized is not None
    assert transaction.committed is True
    assert transaction.rolled_back is False
    assert len(outbox.events) == 1
    assert outbox.events[0].payload_json["data"]["batch"]["metadata"] == {
        "customer_job_id": "job-1"
    }
    assert observed_jobs.observed == ["batch-1"]


@pytest.mark.asyncio
async def test_atomic_finalization_rolls_back_when_outbox_insert_fails() -> None:
    outbox = _OutboxRepository(error=RuntimeError("outbox unavailable"))
    repository, transaction, observed_jobs = _aggregate_repository(job=_job(), outbox=outbox)

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await repository.attach_artifacts_and_finalize(
            batch_id="batch-1",
            output_file_id="file-output",
            error_file_id=None,
            final_status=BatchJobStatus.COMPLETED,
            worker_id="worker-1",
        )

    assert transaction.committed is False
    assert transaction.rolled_back is True
    assert observed_jobs.observed == []


@pytest.mark.asyncio
async def test_atomic_finalization_skips_outbox_for_legacy_batch() -> None:
    outbox = _OutboxRepository(error=AssertionError("outbox must not be called"))
    repository, transaction, observed_jobs = _aggregate_repository(
        job=_job(configured=False),
        outbox=outbox,
    )

    finalized = await repository.attach_artifacts_and_finalize(
        batch_id="batch-1",
        output_file_id="file-output",
        error_file_id=None,
        final_status=BatchJobStatus.COMPLETED,
        worker_id="worker-1",
    )

    assert finalized is not None
    assert transaction.committed is True
    assert outbox.events == []
    assert observed_jobs.observed == ["batch-1"]


@pytest.mark.asyncio
async def test_atomic_finalization_fence_loss_creates_no_event_or_metric() -> None:
    outbox = _OutboxRepository(error=AssertionError("outbox must not be called"))
    repository, transaction, observed_jobs = _aggregate_repository(job=None, outbox=outbox)

    finalized = await repository.attach_artifacts_and_finalize(
        batch_id="batch-1",
        output_file_id="file-output",
        error_file_id=None,
        final_status=BatchJobStatus.COMPLETED,
        worker_id="stale-worker",
    )

    assert finalized is None
    assert transaction.committed is True
    assert outbox.events == []
    assert observed_jobs.observed == []


@pytest.mark.asyncio
async def test_atomic_finalization_accepts_verified_existing_logical_event() -> None:
    job = _job()
    existing = _outbox_record(job)
    outbox = _OutboxRepository(inserted=None, existing=existing)
    repository, transaction, observed_jobs = _aggregate_repository(job=job, outbox=outbox)

    finalized = await repository.attach_artifacts_and_finalize(
        batch_id="batch-1",
        output_file_id="file-output",
        error_file_id=None,
        final_status=BatchJobStatus.COMPLETED,
        worker_id="worker-1",
    )

    assert finalized is not None
    assert transaction.committed is True
    assert observed_jobs.observed == ["batch-1"]


@pytest.mark.asyncio
async def test_atomic_finalization_rejects_conflicting_existing_event() -> None:
    job = _job()
    existing = _outbox_record(job)
    existing.payload_json["data"]["batch"]["status"] = "failed"
    outbox = _OutboxRepository(inserted=None, existing=existing)
    repository, transaction, observed_jobs = _aggregate_repository(job=job, outbox=outbox)

    with pytest.raises(RuntimeError, match="conflicts with terminal outcome"):
        await repository.attach_artifacts_and_finalize(
            batch_id="batch-1",
            output_file_id="file-output",
            error_file_id=None,
            final_status=BatchJobStatus.COMPLETED,
            worker_id="worker-1",
        )

    assert transaction.rolled_back is True
    assert observed_jobs.observed == []
