from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.batch.models import (
    BatchJobRecord,
    BatchJobStatus,
    BatchWebhookDeliveryStatus,
    BatchWebhookOutboxCreate,
    BatchWebhookOutboxRecord,
    BatchWebhookOwnershipConflictError,
)
from src.batch.repositories.webhook_outbox_repository import BatchWebhookOutboxRepository
from src.batch.repository import BatchRepository
from src.batch.webhooks.events import (
    batch_webhook_event_payload_sha256,
    build_batch_webhook_event,
)
from src.batch.worker_types import BATCH_ARTIFACT_VALIDATION_FAILED_PROVIDER_ERROR


def test_webhook_operations_migration_snapshots_ownership_and_adds_retention_index() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "prisma/migrations/20260806120000_batch_webhook_operations/migration.sql"
    )
    sql = migration.read_text(encoding="utf-8")

    assert 'ADD COLUMN "created_by_team_id" TEXT' in sql
    assert 'ADD COLUMN "created_by_organization_id" TEXT' in sql
    assert 'FROM "deltallm_batch_job" AS job' in sql
    assert 'WHERE job."batch_id" = webhook."batch_id"' in sql
    assert '"deltallm_batch_webhook_outbox_retention_idx"' in sql


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
        created_by_team_id="team-1",
        created_at=now,
        started_at=now,
        completed_at=now,
        expires_at=None,
        webhook_config_ciphertext="v1.key.ciphertext" if configured else None,
        webhook_config_fingerprint="a" * 64 if configured else None,
        created_by_organization_id="org-1",
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
        created_by_team_id=job.created_by_team_id,
        created_by_organization_id=job.created_by_organization_id,
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
                    "created_by_team_id": params[3],
                    "created_by_organization_id": params[4],
                    "target_config_ciphertext": params[5],
                    "payload_json": params[6],
                    "payload_sha256": params[7],
                    "status": params[8],
                    "attempt_count": params[9],
                    "max_attempts": params[10],
                    "next_attempt_at": now,
                    "last_status_code": params[12],
                    "last_error": params[13],
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
            created_by_team_id=job.created_by_team_id,
            created_by_organization_id=job.created_by_organization_id,
            target_config_ciphertext=str(job.webhook_config_ciphertext),
            payload_json=event.payload_json,
            payload_sha256=event.payload_sha256,
        )
    )

    assert inserted is not None
    assert inserted.event_id == "evt-stable"
    assert inserted.payload_json["id"] == "evt-stable"
    assert inserted.created_by_team_id == "team-1"
    assert inserted.created_by_organization_id == "org-1"


@pytest.mark.asyncio
async def test_webhook_outbox_repository_repairs_only_missing_ownership() -> None:
    source = _outbox_record(_job())
    original_updated_at = source.updated_at
    row = asdict(source)
    row.update(
        event_type=source.event_type.value,
        status=source.status.value,
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
    )

    class _Prisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN201
            assert "SET created_by_team_id = COALESCE" in sql
            assert "updated_at" not in sql
            assert params == ("batch-1", "batch.completed", "team-1", "org-1")
            return [row]

    repaired = await BatchWebhookOutboxRepository(_Prisma()).fill_missing_ownership(
        batch_id="batch-1",
        event_type="batch.completed",
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
    )

    assert repaired is not None
    assert repaired.created_by_team_id == "team-1"
    assert repaired.created_by_organization_id == "org-1"
    assert repaired.target_config_ciphertext == source.target_config_ciphertext
    assert repaired.payload_json == source.payload_json
    assert repaired.payload_sha256 == source.payload_sha256
    assert repaired.updated_at == original_updated_at


@pytest.mark.asyncio
async def test_webhook_outbox_repository_bulk_repairs_bounded_cleanup_page() -> None:
    class _Prisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN201
            assert "FROM deltallm_batch_job j" in sql
            assert "o.batch_id::text = ANY($1::text[])" in sql
            assert "o.created_by_team_id = j.created_by_team_id" in sql
            assert "o.created_by_organization_id = j.created_by_organization_id" in sql
            assert "updated_at" not in sql
            assert params == (["batch-1", "batch-2"],)
            return [{"event_id": "evt-1"}, {"event_id": "evt-2"}]

    repaired = await BatchWebhookOutboxRepository(_Prisma()).backfill_missing_ownership_for_batches(
        batch_ids=["batch-1", "batch-2"]
    )

    assert repaired == 2


@pytest.mark.asyncio
async def test_webhook_outbox_repository_rejects_cleanup_ownership_conflicts() -> None:
    class _Prisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN201
            assert "IS DISTINCT FROM" in sql
            assert "JOIN deltallm_batch_job j" in sql
            assert params == (["batch-1", "batch-2"],)
            return [{"conflict_count": 1}]

    with pytest.raises(BatchWebhookOwnershipConflictError) as exc_info:
        await BatchWebhookOutboxRepository(_Prisma()).assert_ownership_matches_jobs_for_batches(
            batch_ids=["batch-1", "batch-2"]
        )

    assert exc_info.value.conflict_count == 1


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
            self.query_calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):  # noqa: ANN201
            self.query_calls.append((sql, params))
            if "WITH due AS" in sql or "WITH exhausted AS" in sql:
                return [row]
            return [{"event_id": params[0]}]

    prisma = _Prisma()
    repository = BatchWebhookOutboxRepository(prisma)

    claimed = await repository.claim_due(worker_id="worker-1", lease_seconds=30, limit=10)
    assert len(claimed) == 1
    assert claimed[0].attempt_count == 2
    claim_sql, claim_params = prisma.query_calls[0]
    assert "FOR UPDATE SKIP LOCKED" in claim_sql
    assert "webhook.attempt_count < webhook.max_attempts" in claim_sql
    assert "organization.lifecycle_state = 'active'" in claim_sql
    assert "WHEN webhook.created_by_organization_id IS NOT NULL" in claim_sql
    assert "WHEN webhook.created_by_team_id IS NOT NULL" in claim_sql
    assert claim_params == (10, "worker-1", 30)

    terminalized = await repository.fail_exhausted_expired_leases(limit=7)
    assert len(terminalized) == 1
    terminal_sql, terminal_params = prisma.query_calls[1]
    assert "max_attempts_exhausted_after_lease_expiry" in terminal_sql
    assert "FOR UPDATE SKIP LOCKED" in terminal_sql
    assert terminal_params == (7,)

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

    transition_calls = prisma.query_calls[2:]
    assert all("AND attempt_count = $3" in sql for sql, _params in transition_calls)
    retry_params = transition_calls[1][1]
    assert retry_params[4] == "http_retryable_status"


@pytest.mark.asyncio
async def test_webhook_replay_is_failed_only_and_preserves_immutable_material() -> None:
    source = _outbox_record(_job())
    source.status = BatchWebhookDeliveryStatus.QUEUED
    source.attempt_count = 0
    source.last_status_code = None
    source.last_error = None
    row = asdict(source)
    row["status"] = "queued"
    row["previous_attempt_count"] = 8

    class _Prisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN201
            assert params == (source.event_id, source.batch_id)
            assert "AND status = 'failed'" in sql
            assert "attempt_count = 0" in sql
            assert "payload_json =" not in sql
            assert "payload_sha256 =" not in sql
            assert "target_config_ciphertext =" not in sql
            assert "event_id =" not in sql.split("SET", 1)[1].split("FROM", 1)[0]
            return [row]

    result = await BatchWebhookOutboxRepository(_Prisma()).replay_failed(
        batch_id=source.batch_id,
        event_id=source.event_id,
    )

    assert result is not None
    assert result.previous_attempt_count == 8
    assert result.record.event_id == source.event_id
    assert result.record.payload_json == source.payload_json
    assert result.record.payload_sha256 == source.payload_sha256
    assert result.record.target_config_ciphertext == source.target_config_ciphertext
    assert result.record.status == BatchWebhookDeliveryStatus.QUEUED


@pytest.mark.asyncio
async def test_webhook_summary_zero_fills_statuses_and_tracks_oldest_active_age() -> None:
    class _Prisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN201, ARG002
            assert "GROUP BY status" in sql
            assert "attempt_count < max_attempts" in sql
            assert "next_attempt_at <= NOW()" in sql
            assert "lease_expires_at < NOW()" in sql
            return [
                {
                    "status": "queued",
                    "count": 2,
                    "oldest_age_seconds": 45.5,
                    "due_count": 1,
                },
                {
                    "status": "processing",
                    "count": 2,
                    "oldest_age_seconds": 30,
                    "due_count": 1,
                },
                {
                    "status": "failed",
                    "count": 1,
                    "oldest_age_seconds": 0,
                    "due_count": 0,
                },
            ]

    summary = await BatchWebhookOutboxRepository(_Prisma()).summarize()

    assert summary.counts[BatchWebhookDeliveryStatus.QUEUED] == 2
    assert summary.counts[BatchWebhookDeliveryStatus.PROCESSING] == 2
    assert summary.counts[BatchWebhookDeliveryStatus.DELIVERED] == 0
    assert summary.counts[BatchWebhookDeliveryStatus.FAILED] == 1
    assert summary.oldest_pending_age_seconds == 45.5
    assert summary.due_count == 2


@pytest.mark.asyncio
async def test_webhook_retention_deletes_only_bounded_terminal_rows() -> None:
    cutoff = datetime(2026, 7, 1, tzinfo=UTC)

    class _Prisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN201
            assert "status IN ('delivered', 'failed')" in sql
            assert "FOR UPDATE SKIP LOCKED" in sql
            assert "DELETE FROM deltallm_batch_webhook_outbox" in sql
            assert params == (cutoff, 1000)
            return [{"status": "delivered"}, {"status": "failed"}, {"status": "failed"}]

    deleted = await BatchWebhookOutboxRepository(_Prisma()).delete_terminal_before(
        cutoff=cutoff,
        limit=10_000,
    )

    assert deleted == {"delivered": 1, "failed": 2}


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
        self.ownership_repairs: list[dict[str, object]] = []

    async def insert_event(self, event: BatchWebhookOutboxCreate):  # noqa: ANN201
        self.events.append(event)
        if self.error is not None:
            raise self.error
        return self.inserted

    async def get_by_batch_and_event_type(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return self.existing

    async def fill_missing_ownership(self, **kwargs):  # noqa: ANN003, ANN201
        self.ownership_repairs.append(kwargs)
        if self.existing is None:
            return None
        if self.existing.created_by_team_id is None:
            self.existing.created_by_team_id = kwargs["created_by_team_id"]
        if self.existing.created_by_organization_id is None:
            self.existing.created_by_organization_id = kwargs["created_by_organization_id"]
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
    assert outbox.events[0].created_by_team_id == "team-1"
    assert outbox.events[0].created_by_organization_id == "org-1"
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
async def test_webhook_reconciliation_repairs_terminal_job_and_enqueues_in_current_transaction() -> (
    None
):
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


@pytest.mark.asyncio
async def test_atomic_finalization_repairs_legacy_existing_event_ownership() -> None:
    job = _job()
    existing = _outbox_record(job)
    original_updated_at = existing.updated_at
    existing.created_by_team_id = None
    existing.created_by_organization_id = None
    outbox = _OutboxRepository(inserted=None, existing=existing)
    repository, transaction, observed_jobs, _ = _aggregate_repository(job=job, outbox=outbox)

    finalized = await repository.attach_artifacts_and_finalize(
        batch_id=job.batch_id,
        output_file_id=job.output_file_id,
        error_file_id=job.error_file_id,
        final_status=job.status,
        worker_id="worker-1",
    )

    assert finalized is job
    assert transaction.committed is True
    assert observed_jobs.observed == [job.batch_id]
    assert existing.created_by_team_id == job.created_by_team_id
    assert existing.created_by_organization_id == job.created_by_organization_id
    assert existing.updated_at == original_updated_at
    assert outbox.ownership_repairs == [
        {
            "batch_id": job.batch_id,
            "event_type": existing.event_type,
            "created_by_team_id": job.created_by_team_id,
            "created_by_organization_id": job.created_by_organization_id,
        }
    ]


@pytest.mark.asyncio
async def test_atomic_finalization_rejects_non_null_ownership_conflict() -> None:
    job = _job()
    existing = _outbox_record(job)
    existing.created_by_team_id = "different-team"
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

    assert existing.created_by_team_id == "different-team"
    assert transaction.rolled_back is True
    assert observed_jobs.observed == []


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
    assert (
        existing.payload_json["data"]["batch"][f"{terminal_status.value}_at"] == snapshot_timestamp
    )
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
