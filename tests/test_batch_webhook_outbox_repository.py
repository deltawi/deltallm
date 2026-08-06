from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
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
from src.batch.webhooks.events import (
    batch_webhook_event_payload_sha256,
    build_batch_webhook_event,
)
from src.batch.worker_types import BATCH_ARTIFACT_VALIDATION_FAILED_PROVIDER_ERROR


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


@pytest.mark.asyncio
async def test_webhook_delivery_repository_claims_and_fences_every_transition() -> None:
    source = _outbox_record(_job())
    now = datetime.now(tz=UTC)
    row = asdict(source)
    row.update(
        status="processing",
        attempt_count=2,
        locked_by="worker-1",
        lease_expires_at=now + timedelta(seconds=30),
        updated_at=now,
    )

    class _Prisma:
        def __init__(self) -> None:
            self.execute_calls: list[str] = []
            self.query_calls: list[tuple[str, tuple[object, ...]]] = []

        async def execute_raw(self, sql: str, *params) -> int:  # noqa: ANN002
            assert not params
            self.execute_calls.append(sql)
            return 1

        async def query_raw(self, sql: str, *params):  # noqa: ANN201
            self.query_calls.append((sql, params))
            if "WITH due AS" in sql:
                return [row]
            return [{"event_id": params[0]}]

    prisma = _Prisma()
    repository = BatchWebhookOutboxRepository(prisma)

    claimed = await repository.claim_due(worker_id="worker-1", lease_seconds=30, limit=10)
    assert len(claimed) == 1
    assert claimed[0].attempt_count == 2
    assert "max_attempts_exhausted_after_lease_expiry" in prisma.execute_calls[0]
    claim_sql, claim_params = prisma.query_calls[0]
    assert "FOR UPDATE SKIP LOCKED" in claim_sql
    assert "attempt_count < max_attempts" in claim_sql
    assert claim_params == (10, "worker-1", 30)

    assert await repository.renew_lease(
        source.event_id,
        worker_id="worker-1",
        attempt_count=2,
        lease_seconds=30,
    )
    assert await repository.mark_retrying(
        source.event_id,
        worker_id="worker-1",
        attempt_count=2,
        status_code=503,
        error="  http_retryable_status\n",
        next_attempt_at=now,
    )
    assert await repository.mark_failed(
        source.event_id,
        worker_id="worker-1",
        attempt_count=2,
        status_code=400,
        error="http_permanent_status",
    )
    assert await repository.mark_delivered(
        source.event_id,
        worker_id="worker-1",
        attempt_count=2,
        status_code=204,
    )

    transition_calls = prisma.query_calls[1:]
    assert all("AND attempt_count = $3" in sql for sql, _params in transition_calls)
    retry_params = transition_calls[1][1]
    assert retry_params[4] == "http_retryable_status"


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


class _CurrentTransactionClient:
    def __init__(self) -> None:
        self.tx_called = False

    def is_transaction(self) -> bool:
        return True

    def tx(self):  # noqa: ANN201
        self.tx_called = True
        raise AssertionError("an existing transaction must not open a nested transaction")


class _JobRepository:
    def __init__(
        self,
        job: BatchJobRecord | None,
        *,
        observe_error: Exception | None = None,
    ) -> None:
        self.job = job
        self.observe_error = observe_error
        self.calls: list[dict[str, object]] = []
        self.observed: list[str] = []
        self.locked: list[str] = []

    async def attach_artifacts_and_finalize(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        return self.job

    async def get_job_for_update(self, batch_id: str) -> BatchJobRecord | None:
        self.locked.append(batch_id)
        return self.job if self.job is not None and self.job.batch_id == batch_id else None

    async def set_webhook_config_if_unset(
        self,
        *,
        batch_id: str,
        webhook_config_ciphertext: str,
        webhook_config_fingerprint: str,
    ) -> BatchJobRecord | None:
        if self.job is None or self.job.batch_id != batch_id:
            return None
        if (
            self.job.webhook_config_ciphertext is not None
            or self.job.webhook_config_fingerprint is not None
        ):
            return None
        self.job.webhook_config_ciphertext = webhook_config_ciphertext
        self.job.webhook_config_fingerprint = webhook_config_fingerprint
        return self.job

    def observe_finalization(self, job: BatchJobRecord) -> None:
        self.observed.append(job.batch_id)
        if self.observe_error is not None:
            raise self.observe_error


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
    observe_error: Exception | None = None,
) -> tuple[BatchRepository, _TransactionManager, _JobRepository, _JobRepository]:
    transaction_manager = _TransactionManager()
    transactional_jobs = _JobRepository(job)
    transactional_repository = BatchRepository()
    transactional_repository.jobs = transactional_jobs  # type: ignore[assignment]
    transactional_repository.webhook_outbox = outbox  # type: ignore[assignment]
    transactional_repository.webhook_max_attempts = 8
    repository = BatchRepository()
    repository.prisma = transaction_manager
    observed_jobs = _JobRepository(None, observe_error=observe_error)
    repository.jobs = observed_jobs  # type: ignore[assignment]
    repository.with_prisma = lambda _tx: transactional_repository  # type: ignore[method-assign]
    return repository, transaction_manager, observed_jobs, transactional_jobs


@pytest.mark.asyncio
async def test_atomic_finalization_enqueues_configured_event_then_publishes_metric() -> None:
    outbox = _OutboxRepository()
    repository, transaction, observed_jobs, transactional_jobs = _aggregate_repository(
        job=_job(),
        outbox=outbox,
    )

    finalized = await repository.attach_artifacts_and_finalize(
        batch_id="batch-1",
        output_file_id="file-output",
        error_file_id=None,
        final_status=BatchJobStatus.COMPLETED,
        worker_id="worker-1",
        terminal_provider_error=BATCH_ARTIFACT_VALIDATION_FAILED_PROVIDER_ERROR,
    )

    assert finalized is not None
    assert transaction.committed is True
    assert transaction.rolled_back is False
    assert len(outbox.events) == 1
    assert outbox.events[0].payload_json["data"]["batch"]["metadata"] == {
        "customer_job_id": "job-1"
    }
    assert observed_jobs.observed == ["batch-1"]
    assert transactional_jobs.calls == [
        {
            "batch_id": "batch-1",
            "output_file_id": "file-output",
            "error_file_id": None,
            "final_status": BatchJobStatus.COMPLETED,
            "worker_id": "worker-1",
            "terminal_provider_error": BATCH_ARTIFACT_VALIDATION_FAILED_PROVIDER_ERROR,
        }
    ]


@pytest.mark.asyncio
async def test_atomic_finalization_uses_authoritative_returned_status_for_event() -> None:
    job = _job()
    job.status = BatchJobStatus.CANCELLED
    outbox = _OutboxRepository()
    repository, transaction, observed_jobs, _ = _aggregate_repository(
        job=job,
        outbox=outbox,
    )

    finalized = await repository.attach_artifacts_and_finalize(
        batch_id=job.batch_id,
        output_file_id=job.output_file_id,
        error_file_id=job.error_file_id,
        final_status=BatchJobStatus.COMPLETED,
        worker_id="worker-1",
    )

    assert finalized is job
    assert transaction.committed is True
    assert observed_jobs.observed == [job.batch_id]
    assert len(outbox.events) == 1
    assert outbox.events[0].event_type.value == "batch.cancelled"
    assert outbox.events[0].payload_json["data"]["batch"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_atomic_finalization_ignores_metric_failure_after_commit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    outbox = _OutboxRepository()
    repository, transaction, observed_jobs, _ = _aggregate_repository(
        job=_job(),
        outbox=outbox,
        observe_error=RuntimeError("metrics unavailable"),
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
    assert transaction.rolled_back is False
    assert len(outbox.events) == 1
    assert observed_jobs.observed == ["batch-1"]
    assert "batch finalization metric publish failed batch_id=batch-1" in caplog.text


@pytest.mark.asyncio
async def test_atomic_finalization_reuses_current_transaction_and_defers_metric() -> None:
    job = _job()
    outbox = _OutboxRepository()
    jobs = _JobRepository(job)
    transaction_client = _CurrentTransactionClient()
    repository = BatchRepository()
    repository.prisma = transaction_client
    repository.jobs = jobs  # type: ignore[assignment]
    repository.webhook_outbox = outbox  # type: ignore[assignment]

    finalized = await repository.attach_artifacts_and_finalize(
        batch_id=job.batch_id,
        output_file_id=job.output_file_id,
        error_file_id=job.error_file_id,
        final_status=job.status,
        worker_id="worker-1",
    )

    assert finalized is job
    assert transaction_client.tx_called is False
    assert len(outbox.events) == 1
    assert jobs.observed == []

    repository.publish_finalization_metric_after_commit(finalized)

    assert jobs.observed == [job.batch_id]


@pytest.mark.asyncio
async def test_webhook_reconciliation_repairs_terminal_job_and_enqueues_in_current_transaction() -> None:
    job = _job(configured=False)
    jobs = _JobRepository(job)
    outbox = _OutboxRepository()
    transaction_client = _CurrentTransactionClient()
    repository = BatchRepository(transaction_client)
    repository.jobs = jobs  # type: ignore[assignment]
    repository.webhook_outbox = outbox  # type: ignore[assignment]

    reconciled = await repository.reconcile_job_webhook_config(
        batch_id=job.batch_id,
        webhook_config_ciphertext="v1.key.recovered",
        webhook_config_fingerprint="b" * 64,
    )

    assert reconciled is job
    assert transaction_client.tx_called is False
    assert jobs.locked == [job.batch_id]
    assert job.webhook_config_ciphertext == "v1.key.recovered"
    assert job.webhook_config_fingerprint == "b" * 64
    assert len(outbox.events) == 1
    assert outbox.events[0].event_type.value == "batch.completed"
    assert outbox.events[0].target_config_ciphertext == "v1.key.recovered"


@pytest.mark.asyncio
async def test_webhook_reconciliation_rejects_conflicting_job_configuration() -> None:
    job = _job()
    jobs = _JobRepository(job)
    outbox = _OutboxRepository(error=AssertionError("outbox must not be called"))
    repository = BatchRepository(_CurrentTransactionClient())
    repository.jobs = jobs  # type: ignore[assignment]
    repository.webhook_outbox = outbox  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="conflicts with existing job"):
        await repository.reconcile_job_webhook_config(
            batch_id=job.batch_id,
            webhook_config_ciphertext="v1.other.ciphertext",
            webhook_config_fingerprint="c" * 64,
        )

    assert outbox.events == []


@pytest.mark.asyncio
async def test_atomic_finalization_rolls_back_when_outbox_insert_fails() -> None:
    outbox = _OutboxRepository(error=RuntimeError("outbox unavailable"))
    repository, transaction, observed_jobs, _ = _aggregate_repository(job=_job(), outbox=outbox)

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
    repository, transaction, observed_jobs, _ = _aggregate_repository(
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
    repository, transaction, observed_jobs, _ = _aggregate_repository(job=None, outbox=outbox)

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
    repository, transaction, observed_jobs, _ = _aggregate_repository(job=job, outbox=outbox)

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


@pytest.mark.parametrize(
    "terminal_status",
    [BatchJobStatus.FAILED, BatchJobStatus.EXPIRED],
)
@pytest.mark.asyncio
async def test_webhook_reconciliation_accepts_immutable_terminal_snapshot_after_timestamp_drift(
    terminal_status: BatchJobStatus,
) -> None:
    job = _job()
    job.status = terminal_status
    job.completed_items = 0
    job.failed_items = 1 if terminal_status is BatchJobStatus.FAILED else 0
    existing = _outbox_record(job)
    snapshot_timestamp = existing.payload_json["data"]["batch"][f"{terminal_status.value}_at"]
    job.status_last_updated_at += timedelta(hours=1)
    outbox = _OutboxRepository(inserted=None, existing=existing)
    jobs = _JobRepository(job)
    repository = BatchRepository(_CurrentTransactionClient())
    repository.jobs = jobs  # type: ignore[assignment]
    repository.webhook_outbox = outbox  # type: ignore[assignment]

    reconciled = await repository.reconcile_job_webhook_config(
        batch_id=job.batch_id,
        webhook_config_ciphertext=str(job.webhook_config_ciphertext),
        webhook_config_fingerprint=str(job.webhook_config_fingerprint),
    )

    assert reconciled is job
    assert jobs.locked == [job.batch_id]
    assert len(outbox.events) == 1
    assert existing.payload_json["data"]["batch"][f"{terminal_status.value}_at"] == snapshot_timestamp
    assert snapshot_timestamp != int(job.status_last_updated_at.timestamp())


@pytest.mark.asyncio
async def test_atomic_finalization_rejects_conflicting_existing_event() -> None:
    job = _job()
    existing = _outbox_record(job)
    existing.payload_json["data"]["batch"]["status"] = "failed"
    existing.payload_sha256 = batch_webhook_event_payload_sha256(existing.payload_json)
    outbox = _OutboxRepository(inserted=None, existing=existing)
    repository, transaction, observed_jobs, _ = _aggregate_repository(job=job, outbox=outbox)

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


@pytest.mark.parametrize(
    "conflict",
    ["target", "batch_identity", "payload_hash"],
)
@pytest.mark.asyncio
async def test_atomic_finalization_rejects_invalid_existing_event_identity_or_integrity(
    conflict: str,
) -> None:
    job = _job()
    existing = _outbox_record(job)
    if conflict == "target":
        existing.target_config_ciphertext = "v1.other.ciphertext"
    elif conflict == "batch_identity":
        existing.payload_json["data"]["batch"]["id"] = "batch-other"
        existing.payload_sha256 = batch_webhook_event_payload_sha256(existing.payload_json)
    else:
        existing.payload_json["data"]["batch"]["metadata"] = {"tampered": True}
    outbox = _OutboxRepository(inserted=None, existing=existing)
    repository, transaction, observed_jobs, _ = _aggregate_repository(job=job, outbox=outbox)

    with pytest.raises(RuntimeError, match="conflicts with terminal outcome"):
        await repository.attach_artifacts_and_finalize(
            batch_id=job.batch_id,
            output_file_id=job.output_file_id,
            error_file_id=job.error_file_id,
            final_status=job.status,
            worker_id="worker-1",
        )

    assert transaction.rolled_back is True
    assert observed_jobs.observed == []
