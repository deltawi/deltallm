from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from src.batch.models import BatchJobStatus
from src.batch.repository import BatchRepository
from src.batch.repositories import job_repository as job_repository_module
from src.batch.scheduling import API_KEY_TENANT_SCOPE_PREFIX, MIXED_MODEL_GROUP, stable_tenant_scope_id


class _PrismaSpy:
    def __init__(self) -> None:
        self.sql = ""
        self.params = ()
        self.queries: list[str] = []

    async def query_raw(self, sql: str, *params):
        self.sql = sql
        self.params = params
        self.queries.append(sql)
        return []


def _job_row(**overrides):
    row = {
        "batch_id": "batch-1",
        "endpoint": "/v1/embeddings",
        "status": "queued",
        "execution_mode": "managed_internal",
        "input_file_id": "file-1",
        "model": "m1",
        "metadata": "{}",
        "total_items": 2,
        "created_by_api_key": None,
        "created_by_user_id": None,
        "created_by_team_id": None,
        "created_by_organization_id": None,
        "created_at": datetime.now(tz=UTC),
        "scheduler_version": "fifo_v1",
        "scheduling_model": "m1",
        "scheduling_model_group": "m1",
        "scheduling_endpoint": "/v1/embeddings",
        "tenant_scope_type": "anonymous",
        "tenant_scope_id": "anonymous",
        "service_tier": "standard",
        "estimated_work_units": 2,
        "remaining_work_units": 2,
        "size_class": "xs",
        "queue_entered_at": datetime.now(tz=UTC),
        "scheduler_debug": "{}",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_list_jobs_uses_or_scope_for_api_key_and_team():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    jobs = await repository.list_jobs(
        limit=20,
        created_by_api_key="key-1",
        created_by_team_id="team-1",
    )

    assert jobs == []
    assert "created_by_api_key" in prisma.sql
    assert "created_by_team_id" in prisma.sql
    assert " OR " in prisma.sql


@pytest.mark.asyncio
async def test_list_jobs_can_filter_by_organization_scope() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    jobs = await repository.list_jobs(
        limit=20,
        created_by_api_key="key-1",
        created_by_organization_id="org-1",
    )

    assert jobs == []
    assert "created_by_api_key" in prisma.sql
    assert "created_by_organization_id" in prisma.sql
    assert " OR " in prisma.sql


@pytest.mark.asyncio
async def test_claim_items_reclaims_expired_in_progress_rows():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    items = await repository.claim_items(
        batch_id="batch-1",
        worker_id="worker-1",
        limit=10,
        lease_seconds=120,
    )

    assert items == []
    assert "status = 'pending'" in prisma.sql
    assert "status = 'in_progress'" in prisma.sql
    assert "lease_expires_at < NOW()" in prisma.sql
    assert "not_before_at IS NULL OR not_before_at <= NOW()" in prisma.sql


@pytest.mark.asyncio
async def test_claim_items_honors_not_before_at_for_pending_rows() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    await repository.claim_items(
        batch_id="batch-1",
        worker_id="worker-1",
        limit=10,
        lease_seconds=120,
    )

    assert "status = 'pending'" in prisma.sql
    assert "AND (not_before_at IS NULL OR not_before_at <= NOW())" in prisma.sql
    assert "OR (status = 'in_progress' AND lease_expires_at < NOW())" in prisma.sql


@pytest.mark.asyncio
async def test_claim_next_work_uses_item_slice_claim_shape() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    claim = await repository.claim_next_work(
        worker_id="worker-1",
        max_items=10,
        max_work_units=25,
        lease_seconds=120,
    )

    assert claim is None
    assert "j.status IN ('queued', 'in_progress')" in prisma.sql
    assert "j.locked_by IS NULL OR j.lease_expires_at IS NULL OR j.lease_expires_at < NOW()" in prisma.sql
    assert "ORDER BY (j.last_scheduled_at IS NOT NULL) ASC" in prisma.sql
    assert "WITH selected_job AS" in prisma.sql
    assert "FOR KEY SHARE SKIP LOCKED" in prisma.sql
    # Single-job selection: no candidate_jobs CTE and no per-candidate seed lock.
    assert "WITH candidate_jobs AS" not in prisma.sql
    assert "claimable_jobs" not in prisma.sql
    assert "LIMIT GREATEST($2 - 1, 0)" not in prisma.sql
    # locked_items operates on the single selected_job.
    assert "i.status = 'pending'" in prisma.sql
    assert "i.not_before_at IS NULL OR i.not_before_at <= NOW()" in prisma.sql
    assert "FOR UPDATE SKIP LOCKED" in prisma.sql
    assert "LIMIT $2" in prisma.sql
    assert "SUM(estimated_work_units) OVER" in prisma.sql
    # locked_items LIMIT $2 enforces the max-items cap, eligible_items only
    # enforces the work-unit cap with claim_rank = 1 as the at-least-one fallback.
    assert "claim_rank <= $2" not in prisma.sql
    assert "cumulative_work_units <= $3 OR claim_rank = 1" in prisma.sql
    # updated_items must depend on updated_job for finalize-race symmetry.
    assert "FROM eligible_items s, updated_job uj" in prisma.sql
    assert "locked_by = $1" in prisma.sql
    assert "locked_by = NULL" in prisma.sql
    assert "first_claimed_at = COALESCE" in prisma.sql
    assert "queue_wait_observed" in prisma.sql


@pytest.mark.asyncio
async def test_claim_next_work_returns_none_when_lease_unparseable(caplog: pytest.LogCaptureFixture) -> None:
    class _BadLeasePrisma:
        async def query_raw(self, sql, *params):
            del sql, params
            return [
                {
                    "batch_id": "b-bad-lease",
                    "endpoint": "/v1/embeddings",
                    "model_group": "m1",
                    "tenant_scope_type": "api_key",
                    "tenant_scope_id": "tok",
                    "service_tier": "standard",
                    "size_class": "xs",
                    "queue_entered_at": None,
                    "previous_status": "queued",
                    "previous_first_claimed_at": None,
                    "queue_wait_observed": False,
                    "item_ids": ["item-1"],
                    "claimed_work_units": 1,
                    "lease_expires_at": "not-a-timestamp",
                    "reclaimed_items": 0,
                }
            ]

    repository = BatchRepository(prisma_client=_BadLeasePrisma())
    with caplog.at_level(logging.ERROR, logger="src.batch.repositories.job_repository"):
        claim = await repository.claim_next_work(
            worker_id="w1",
            max_items=4,
            max_work_units=8,
            lease_seconds=60,
        )

    assert claim is None
    assert "missing lease_expires_at" in caplog.text


@pytest.mark.asyncio
async def test_claim_next_finalization_uses_job_lease_only_for_finalizing_jobs() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    job = await repository.claim_next_finalization(worker_id="worker-1", lease_seconds=120)

    assert job is None
    assert "WHERE status = 'finalizing'" in prisma.sql
    assert "locked_by = $1" in prisma.sql
    assert "FOR UPDATE SKIP LOCKED" in prisma.sql
    assert "RETURNING j.*" in prisma.sql


@pytest.mark.asyncio
async def test_diagnose_empty_work_claim_uses_bounded_queue_state_probe() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    reason = await repository.diagnose_empty_work_claim()

    assert reason == "no_available_work"
    assert "WITH candidate_jobs AS" in prisma.sql
    assert "WHERE j.status IN ('queued', 'in_progress')" in prisma.sql
    assert "LIMIT 100" in prisma.sql
    assert "unleased_jobs AS" in prisma.sql
    assert "claimable_probe AS" in prisma.sql
    assert "FOR UPDATE SKIP LOCKED" in prisma.sql
    assert "EXISTS (" in prisma.sql
    assert "COUNT(*) FILTER" not in prisma.sql


@pytest.mark.asyncio
async def test_diagnose_empty_work_claim_classifies_future_retry_delay() -> None:
    class _DiagnosticPrisma:
        async def query_raw(self, sql: str, *params):
            del sql, params
            return [
                {
                    "active_jobs": 1,
                    "unleased_jobs": 1,
                    "pending_or_in_progress_items": 1,
                    "pending_items": 2,
                    "in_progress_items": 0,
                    "due_pending_items": 0,
                    "future_pending_items": 2,
                    "runnable_items": 0,
                    "claimable_items": 0,
                }
            ]

    repository = BatchRepository(prisma_client=_DiagnosticPrisma())

    assert await repository.diagnose_empty_work_claim() == "not_before_future"


@pytest.mark.asyncio
async def test_diagnose_empty_work_claim_prefers_lock_contention_for_mixed_due_and_future() -> None:
    class _DiagnosticPrisma:
        async def query_raw(self, sql: str, *params):
            del sql, params
            return [
                {
                    "active_jobs": 1,
                    "unleased_jobs": 1,
                    "pending_or_in_progress_items": 1,
                    "pending_items": 2,
                    "in_progress_items": 0,
                    "due_pending_items": 1,
                    "future_pending_items": 1,
                    "runnable_items": 0,
                    "claimable_items": 0,
                }
            ]

    repository = BatchRepository(prisma_client=_DiagnosticPrisma())

    assert await repository.diagnose_empty_work_claim() == "all_items_locked"


@pytest.mark.asyncio
async def test_release_claim_items_releases_only_owned_in_progress_rows() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    released = await repository.release_claim_items(item_ids=["item-1", "item-2"], worker_id="worker-1")

    assert released == 0
    assert "status = 'pending'" in prisma.sql
    assert "i.locked_by = $3" in prisma.sql
    assert "i.status = 'in_progress'" in prisma.sql
    assert "lease_expires_at = NULL" in prisma.sql


@pytest.mark.asyncio
async def test_claim_items_logs_reclaimed_expired_rows(caplog: pytest.LogCaptureFixture):
    class _ReclaimPrisma:
        async def query_raw(self, sql: str, *params):
            del sql, params
            return [
                {
                    "item_id": "item-1",
                    "batch_id": "batch-1",
                    "line_number": 1,
                    "custom_id": "custom-1",
                    "status": "in_progress",
                    "request_body": {"model": "m1"},
                    "response_body": None,
                    "error_body": None,
                    "usage": None,
                    "provider_cost": 0.0,
                    "billed_cost": 0.0,
                    "attempts": 2,
                    "last_error": None,
                    "locked_by": "worker-1",
                    "lease_expires_at": None,
                    "created_at": None,
                    "started_at": None,
                    "completed_at": None,
                    "previous_status": "in_progress",
                }
            ]

    repository = BatchRepository(prisma_client=_ReclaimPrisma())

    with caplog.at_level(logging.INFO):
        items = await repository.claim_items(
            batch_id="batch-1",
            worker_id="worker-1",
            limit=10,
            lease_seconds=120,
        )

    assert len(items) == 1
    assert "batch items reclaimed batch_id=batch-1 worker_id=worker-1 count=1" in caplog.text


@pytest.mark.asyncio
async def test_claim_next_job_includes_finalizing_jobs():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    job = await repository.claim_next_job(worker_id="worker-1", lease_seconds=120)

    assert job is None
    assert "'finalizing'" in prisma.sql
    assert "CASE WHEN status = 'finalizing' THEN 1 ELSE 0 END" in prisma.sql
    assert '::"DeltaLLM_BatchJobStatus"' in prisma.sql


@pytest.mark.asyncio
async def test_request_cancel_preserves_finalizing_status():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    job = await repository.request_cancel("batch-1")

    assert job is None
    assert "WHEN status = 'finalizing' THEN status" in prisma.sql
    assert '::"DeltaLLM_BatchJobStatus"' in prisma.sql


@pytest.mark.asyncio
async def test_reschedule_finalization_pushes_lease_forward():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    rescheduled = await repository.reschedule_finalization(
        batch_id="batch-1",
        worker_id="worker-1",
        retry_delay_seconds=60,
    )

    assert rescheduled is False
    assert "lease_expires_at = NOW() + ($3 || ' seconds')::interval" in prisma.sql
    assert "status = 'finalizing'" in prisma.sql


@pytest.mark.asyncio
async def test_retryable_mark_item_failed_uses_database_deadline_guard():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    updated = await repository.mark_item_failed(
        item_id="item-1",
        worker_id="worker-1",
        error_body={"message": "rate limited"},
        last_error="rate limited",
        retryable=True,
        retry_delay_seconds=60,
    )

    assert updated is False
    assert "FROM deltallm_batch_job j" in prisma.sql
    assert "j.batch_id = i.batch_id" in prisma.sql
    assert "NOW() + ($4 || ' seconds')::interval < j.expires_at" in prisma.sql


@pytest.mark.asyncio
async def test_release_items_for_retry_preserves_immediate_requeue_defaults():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    item_ids = await repository.release_items_for_retry(
        item_ids=["item-1"],
        worker_id="worker-1",
    )

    assert item_ids == []
    assert prisma.params == ("item-1", "worker-1", 0, None, None)
    assert "status = 'pending'" in prisma.sql
    assert "ELSE NULL" in prisma.sql
    assert "error_body = COALESCE($4::jsonb, i.error_body)" in prisma.sql
    assert "last_error = COALESCE($5, i.last_error)" in prisma.sql
    assert "j.batch_id = i.batch_id" in prisma.sql
    assert "NOW() + ($3 || ' seconds')::interval < j.expires_at" in prisma.sql


@pytest.mark.asyncio
async def test_release_items_for_retry_can_store_retry_metadata_and_delay():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    await repository.release_items_for_retry(
        item_ids=["item-1", "item-2"],
        worker_id="worker-1",
        retry_delay_seconds=30,
        error_body={"retry_category": "upstream_5xx"},
        last_error="upstream unavailable",
    )

    assert prisma.params[0:2] == ("item-1", "item-2")
    assert prisma.params[2] == "worker-1"
    assert prisma.params[3] == 30
    assert json.loads(prisma.params[4]) == {"retry_category": "upstream_5xx"}
    assert prisma.params[5] == "upstream unavailable"
    assert "WHEN $4 > 0 THEN NOW() + ($4 || ' seconds')::interval" in prisma.sql
    assert "i.locked_by = $3" in prisma.sql


@pytest.mark.asyncio
async def test_count_active_jobs_for_scope_prefers_team_scope():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    count = await repository.count_active_jobs_for_scope(
        created_by_api_key="key-1",
        created_by_team_id="team-1",
    )

    assert count == 0
    assert "created_by_team_id = $1" in prisma.sql
    assert "status NOT IN ('completed', 'failed', 'cancelled', 'expired')" in prisma.sql


@pytest.mark.asyncio
async def test_list_items_page_uses_line_number_cursor():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    items = await repository.list_items_page(
        batch_id="batch-1",
        limit=200,
        after_line_number=10,
    )

    assert items == []
    assert "line_number > $2" in prisma.sql
    assert "ORDER BY line_number ASC" in prisma.sql


@pytest.mark.asyncio
async def test_acquire_scope_advisory_lock_uses_postgres_advisory_lock():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    await repository.acquire_scope_advisory_lock(scope_type="team", scope_id="team-1")

    assert "pg_advisory_xact_lock" in prisma.sql
    assert "hashtext($1)" in prisma.sql
    assert "hashtext($2)" in prisma.sql


@pytest.mark.asyncio
async def test_create_items_uses_bulk_insert_statement():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    inserted = await repository.create_items(
        "batch-1",
        [
            type("Item", (), {"line_number": 1, "custom_id": "c1", "request_body": {"model": "m1"}})(),
            type("Item", (), {"line_number": 2, "custom_id": "c2", "request_body": {"model": "m2"}})(),
        ],
    )

    assert inserted == 0
    assert (
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10::timestamp), "
        "($11, $12, $13, $14, $15, $16::jsonb, $17, $18, $19, $20::timestamp)"
    ) in prisma.sql
    assert "ON CONFLICT (batch_id, line_number) DO NOTHING" in prisma.sql


@pytest.mark.asyncio
async def test_expired_file_gc_excludes_files_referenced_by_create_sessions() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    files = await repository.files.list_expired_unreferenced_files(now=datetime.now(tz=UTC), limit=50)

    assert files == []
    assert "FROM deltallm_batch_create_session s" in prisma.sql
    assert "WHERE s.input_file_id = f.file_id" in prisma.sql


@pytest.mark.asyncio
async def test_create_job_rejects_invalid_status_before_sql() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    with pytest.raises(ValueError, match="batch job status"):
        await repository.create_job(
            endpoint="/v1/embeddings",
            input_file_id="file-1",
            model="m1",
            metadata=None,
            created_by_api_key="key-1",
            created_by_user_id=None,
            created_by_team_id=None,
            expires_at=None,
            status="broken",
        )

    assert prisma.queries == []


@pytest.mark.asyncio
async def test_create_job_defaults_to_queued_status() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        model="m1",
        metadata=None,
        created_by_api_key="key-1",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )

    assert '::"DeltaLLM_BatchJobStatus"' in prisma.sql
    assert prisma.params[2] == BatchJobStatus.QUEUED.value
    assert len(prisma.params) == 25
    assert "scheduler_version, scheduling_model, scheduling_model_group" in prisma.sql


@pytest.mark.asyncio
async def test_create_job_hashes_api_key_tenant_scope_id() -> None:
    class _CreateJobPrisma(_PrismaSpy):
        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            self.queries.append(sql)
            return [
                {
                    "batch_id": params[0],
                    "endpoint": params[1],
                    "status": params[2],
                    "execution_mode": params[3],
                    "input_file_id": params[4],
                    "model": params[5],
                    "metadata": params[6],
                    "total_items": params[7],
                    "scheduler_version": params[8],
                    "scheduling_model": params[9],
                    "scheduling_model_group": params[10],
                    "scheduling_endpoint": params[11],
                    "tenant_scope_type": params[12],
                    "tenant_scope_id": params[13],
                    "service_tier": params[14],
                    "estimated_work_units": params[15],
                    "remaining_work_units": params[16],
                    "size_class": params[17],
                    "queue_entered_at": params[18],
                    "scheduler_debug": params[19],
                    "created_by_api_key": params[20],
                    "created_at": datetime.now(tz=UTC),
                }
            ]

    prisma = _CreateJobPrisma()
    repository = BatchRepository(prisma_client=prisma)

    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        model="m1",
        metadata=None,
        created_by_api_key="sk-test-secret",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )

    assert job is not None
    assert job.tenant_scope_type == "api_key"
    assert job.tenant_scope_id is not None
    assert job.tenant_scope_id.startswith("api_key_sha256:")
    assert "sk-test-secret" not in job.tenant_scope_id


@pytest.mark.asyncio
async def test_claim_next_job_observes_queue_wait_only_for_first_queued_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ClaimPrisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN001
            del sql, params
            return [
                {
                    "batch_id": "batch-1",
                    "endpoint": "/v1/embeddings",
                    "status": "in_progress",
                    "execution_mode": "managed_internal",
                    "input_file_id": "file-1",
                    "model": "m1",
                    "metadata": "{}",
                    "total_items": 1,
                    "created_by_api_key": "key-1",
                    "created_at": datetime.now() - timedelta(seconds=30),
                    "queue_entered_at": datetime.now() - timedelta(seconds=20),
                    "scheduling_model_group": "m1",
                    "service_tier": "standard",
                    "size_class": "xs",
                    "previous_status": "queued",
                    "previous_first_claimed_at": None,
                }
            ]

    observations: list[dict[str, object]] = []
    monkeypatch.setattr(
        job_repository_module,
        "observe_batch_queue_wait",
        lambda **kwargs: observations.append(kwargs),
    )
    repository = BatchRepository(prisma_client=_ClaimPrisma())

    job = await repository.claim_next_job(worker_id="worker-1", lease_seconds=120)

    assert job is not None
    assert observations
    assert observations[0]["model_group"] == "m1"
    assert float(observations[0]["wait_seconds"]) >= 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous_status", "previous_first_claimed_at"),
    [
        ("in_progress", None),
        ("finalizing", None),
        ("queued", datetime.now(tz=UTC)),
    ],
)
async def test_claim_next_job_skips_queue_wait_for_reclaims_and_repeated_claims(
    monkeypatch: pytest.MonkeyPatch,
    previous_status: str,
    previous_first_claimed_at: datetime | None,
) -> None:
    class _ClaimPrisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN001
            del sql, params
            return [
                {
                    "batch_id": "batch-1",
                    "endpoint": "/v1/embeddings",
                    "status": "in_progress",
                    "execution_mode": "managed_internal",
                    "input_file_id": "file-1",
                    "model": "m1",
                    "metadata": "{}",
                    "total_items": 1,
                    "created_by_api_key": "key-1",
                    "created_at": datetime.now(tz=UTC),
                    "queue_entered_at": datetime.now(tz=UTC) - timedelta(seconds=20),
                    "scheduling_model_group": "m1",
                    "service_tier": "standard",
                    "size_class": "xs",
                    "previous_status": previous_status,
                    "previous_first_claimed_at": previous_first_claimed_at,
                }
            ]

    observations: list[dict[str, object]] = []
    monkeypatch.setattr(
        job_repository_module,
        "observe_batch_queue_wait",
        lambda **kwargs: observations.append(kwargs),
    )
    repository = BatchRepository(prisma_client=_ClaimPrisma())

    await repository.claim_next_job(worker_id="worker-1", lease_seconds=120)

    assert observations == []


@pytest.mark.asyncio
async def test_set_job_queued_casts_status_parameter_to_enum() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    queued = await repository.set_job_queued("batch-1", 2)

    assert queued is None
    assert 'status = $2::"DeltaLLM_BatchJobStatus"' in prisma.sql


@pytest.mark.asyncio
async def test_set_job_queued_repairs_missing_api_key_tenant_scope_id() -> None:
    class _SetQueuedPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self.first_row = _job_row(
                created_by_api_key="sk-test-secret",
                tenant_scope_type="api_key",
                tenant_scope_id=None,
            )

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if len(self.calls) == 1:
                return [self.first_row]
            repaired = dict(self.first_row)
            repaired["tenant_scope_id"] = params[1]
            return [repaired]

    prisma = _SetQueuedPrisma()
    repository = BatchRepository(prisma_client=prisma)

    queued = await repository.set_job_queued("batch-1", 2)

    expected_scope_id = stable_tenant_scope_id(scope_type="api_key", scope_id="sk-test-secret")
    assert queued is not None
    assert queued.tenant_scope_id == expected_scope_id
    assert len(prisma.calls) == 2
    assert prisma.calls[1][1][1] == expected_scope_id
    assert "tenant_scope_id NOT LIKE $3" in prisma.calls[1][0]


@pytest.mark.asyncio
async def test_set_job_queued_repairs_raw_api_key_tenant_scope_id() -> None:
    class _SetQueuedPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self.first_row = _job_row(
                created_by_api_key="sk-created-secret",
                tenant_scope_type="api_key",
                tenant_scope_id="sk-existing-raw-secret",
            )

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if len(self.calls) == 1:
                return [self.first_row]
            repaired = dict(self.first_row)
            repaired["tenant_scope_id"] = params[1]
            return [repaired]

    prisma = _SetQueuedPrisma()
    repository = BatchRepository(prisma_client=prisma)

    queued = await repository.set_job_queued("batch-1", 2)

    expected_scope_id = stable_tenant_scope_id(
        scope_type="api_key",
        scope_id="sk-existing-raw-secret",
    )
    assert queued is not None
    assert queued.tenant_scope_id == expected_scope_id
    assert len(prisma.calls) == 2
    assert "sk-existing-raw-secret" not in queued.tenant_scope_id
    assert prisma.calls[1][1][2] == f"{API_KEY_TENANT_SCOPE_PREFIX}%"


@pytest.mark.asyncio
async def test_set_job_queued_skips_tenant_scope_repair_for_team_scope() -> None:
    class _SetQueuedPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            return [_job_row(created_by_team_id="team-1", tenant_scope_type="team", tenant_scope_id="team-1")]

    prisma = _SetQueuedPrisma()
    repository = BatchRepository(prisma_client=prisma)

    queued = await repository.set_job_queued("batch-1", 2)

    assert queued is not None
    assert queued.tenant_scope_id == "team-1"
    assert len(prisma.calls) == 1


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_repairs_active_rows_with_estimator_and_lock() -> None:
    class _Router:
        def resolve_model_group(self, model_name: str) -> str:
            assert model_name == "m1"
            return "group-m1"

    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return [
                    {
                        "item_id": "item-1",
                        "request_body": {"model": "m1", "input": "a" * 257},
                        "scheduling_model": None,
                        "scheduling_model_group": None,
                        "estimated_work_units": 1,
                        "endpoint": "/v1/embeddings",
                        "job_model": "m1",
                        "line_number": 1,
                    }
                ]
            if "WITH payload(item_id" in sql:
                return [{"item_id": params[0]}]
            if "WITH field_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-1",
                        "endpoint": "/v1/embeddings",
                        "model": "m1",
                        "created_by_organization_id": None,
                        "created_by_team_id": None,
                        "created_by_api_key": "sk-created-secret",
                        "created_by_user_id": None,
                        "tenant_scope_type": "api_key",
                        "tenant_scope_id": "sk-existing-raw-secret",
                        "service_tier": "standard",
                        "estimated_work_units": 0,
                        "remaining_work_units": 0,
                        "total_items": 2,
                        "created_at": datetime.now(tz=UTC),
                        "item_estimated_work_units": 2,
                        "item_remaining_work_units": 2,
                        "missing_dimension_items": 0,
                        "distinct_models": 1,
                        "distinct_model_groups": 0,
                        "item_scheduling_model": "m1",
                        "item_scheduling_model_group": None,
                    }
                ]
            return [{"batch_id": params[0]}]

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma, model_group_resolver=_Router())

    result = await repository.backfill_scheduler_dimensions(limit=500)

    expected_scope_id = stable_tenant_scope_id(
        scope_type="api_key",
        scope_id="sk-existing-raw-secret",
    )
    assert result == {"jobs": 1, "items": 1}
    item_select_sql, item_select_params = prisma.calls[1]
    item_update_sql, item_update_params = prisma.calls[2]
    select_sql, select_params = prisma.calls[3]
    update_sql, update_params = prisma.calls[4]
    assert "JOIN deltallm_batch_job j ON j.batch_id = i.batch_id" in item_select_sql
    assert "j.status IN ('queued', 'in_progress', 'finalizing')" in item_select_sql
    assert "FOR UPDATE OF i SKIP LOCKED" in item_select_sql
    assert item_select_params == (500,)
    assert "NULLIF(i.scheduling_model, '')" in item_update_sql
    assert "WITH payload(item_id, scheduling_model, scheduling_model_group, estimated_work_units)" in item_update_sql
    assert item_update_params[1] == "m1"
    assert item_update_params[2] == "group-m1"
    assert item_update_params[3] == 2
    assert "field_candidate_jobs AS" in select_sql
    assert "aggregate_candidate_jobs AS" in select_sql
    assert "aggregate_drift_scan_jobs AS" in select_sql
    assert "aggregate_drift_item_stats AS" in select_sql
    assert "aggregate_drift_candidate_jobs AS" in select_sql
    assert "candidate_jobs AS" in select_sql
    assert "WITH scanned_jobs AS" not in select_sql
    assert "JOIN candidate_jobs s ON s.batch_id = i.batch_id" in select_sql
    assert "status IN ('queued', 'in_progress', 'finalizing')" in select_sql
    assert "missing_dimension_items" in select_sql
    assert "ci.missing_dimension_items = 0" in select_sql
    assert "j.scheduler_debug->>'estimator_version' IS DISTINCT FROM $3" in select_sql
    assert "j.estimated_work_units IS DISTINCT FROM COALESCE(j.total_items, 0)" not in select_sql
    assert "ci.estimated_work_units IS DISTINCT FROM ci.derived_estimated_work_units" in select_sql
    assert "FOR UPDATE OF j SKIP LOCKED" in select_sql
    assert select_params == (500, 5000, "v1", f"{API_KEY_TENANT_SCOPE_PREFIX}%")
    assert update_params[5] == "api_key"
    assert update_params[6] == expected_scope_id
    assert update_params[8] == 2
    assert update_params[9] == 2
    assert update_params[3] == "group-m1"
    assert json.loads(str(update_params[12]))["estimator_version"] == "v1"
    assert update_params[13] == f"{API_KEY_TENANT_SCOPE_PREFIX}%"
    assert "scheduling_model = $3" in update_sql
    assert "tenant_scope_id NOT LIKE $14" in update_sql
    assert "estimated_work_units = GREATEST($9, 0)" in update_sql
    assert "remaining_work_units = GREATEST($10, 0)" in update_sql
    assert "status IN ('queued', 'in_progress', 'finalizing')" in update_sql
    assert "'completed'" not in update_sql


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_uses_targeted_candidates_before_job_repair_limit() -> None:
    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return []
            if "WITH field_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-later",
                        "endpoint": "/v1/embeddings",
                        "model": "m1",
                        "created_by_organization_id": None,
                        "created_by_team_id": "team-1",
                        "created_by_api_key": None,
                        "created_by_user_id": None,
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "service_tier": "standard",
                        "estimated_work_units": 0,
                        "remaining_work_units": 0,
                        "total_items": 1,
                        "created_at": datetime.now(tz=UTC),
                        "item_estimated_work_units": 1,
                        "item_remaining_work_units": 1,
                        "missing_dimension_items": 0,
                        "distinct_models": 1,
                        "distinct_model_groups": 1,
                        "item_scheduling_model": "m1",
                        "item_scheduling_model_group": "m1",
                    }
                ]
            if "UPDATE deltallm_batch_job" in sql:
                return [{"batch_id": params[0]}]
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.backfill_scheduler_dimensions(limit=1)

    select_sql, select_params = next(
        (sql, params) for sql, params in prisma.calls if "WITH field_candidate_jobs AS" in sql
    )
    assert result == {"jobs": 1, "items": 0}
    assert select_params == (1, 10, "v1", f"{API_KEY_TENANT_SCOPE_PREFIX}%")
    assert "field_candidate_jobs AS" in select_sql
    assert "aggregate_candidate_jobs AS" in select_sql
    assert "aggregate_drift_candidate_jobs AS" in select_sql
    assert "candidate_jobs AS" in select_sql
    assert "WITH scanned_jobs AS" not in select_sql
    assert "LIMIT $2" in select_sql
    assert "ci.missing_dimension_items = 0" in select_sql
    assert "FOR UPDATE OF j SKIP LOCKED" in select_sql


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_repairs_complete_job_aggregate_drift() -> None:
    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return []
            if "WITH field_candidate_jobs AS" in sql and "aggregate_drift_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-1",
                        "endpoint": "/v1/embeddings",
                        "model": "m1",
                        "created_by_organization_id": None,
                        "created_by_team_id": "team-1",
                        "created_by_api_key": None,
                        "created_by_user_id": None,
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "service_tier": "standard",
                        "estimated_work_units": 1_000,
                        "remaining_work_units": 1_000,
                        "size_class": "m",
                        "total_items": 2,
                        "created_at": datetime.now(tz=UTC),
                        "item_count": 2,
                        "item_estimated_work_units": 10,
                        "item_remaining_work_units": 4,
                        "missing_dimension_items": 0,
                        "distinct_models": 1,
                        "distinct_model_groups": 1,
                        "item_scheduling_model": "m1",
                        "item_scheduling_model_group": "m1",
                    }
                ]
            if "UPDATE deltallm_batch_job" in sql:
                return [{"batch_id": params[0]}]
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.backfill_scheduler_dimensions(limit=500)

    assert result == {"jobs": 1, "items": 0}
    select_sql, _select_params = next(
        (sql, params) for sql, params in prisma.calls if "WITH field_candidate_jobs AS" in sql
    )
    update_sql, update_params = next(
        (sql, params) for sql, params in prisma.calls if "UPDATE deltallm_batch_job" in sql
    )
    assert "ci.estimated_work_units IS DISTINCT FROM ci.derived_estimated_work_units" in select_sql
    assert "ci.remaining_work_units IS DISTINCT FROM ci.derived_remaining_work_units" in select_sql
    assert "ci.size_class IS DISTINCT FROM" in select_sql
    assert "aggregate_drift_scan_jobs AS" in select_sql
    assert "aggregate_drift_item_stats AS" in select_sql
    assert "JOIN aggregate_drift_scan_jobs s ON s.batch_id = i.batch_id" in select_sql
    assert "dis.missing_dimension_items = 0" in select_sql
    assert "j.estimated_work_units IS DISTINCT FROM dis.estimated_work_units" in select_sql
    assert "FROM aggregate_drift_candidate_jobs" in select_sql
    assert update_params[8] == 10
    assert update_params[9] == 4
    assert update_params[10] == "xs"
    assert "estimated_work_units = GREATEST($9, 0)" in update_sql


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_skips_job_until_all_items_are_normalized() -> None:
    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return []
            if "WITH field_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-1",
                        "endpoint": "/v1/embeddings",
                        "model": "m1",
                        "created_by_organization_id": None,
                        "created_by_team_id": "team-1",
                        "created_by_api_key": None,
                        "created_by_user_id": None,
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "service_tier": "standard",
                        "estimated_work_units": 1,
                        "remaining_work_units": 1,
                        "total_items": 10_000,
                        "created_at": datetime.now(tz=UTC),
                        "item_estimated_work_units": 500,
                        "item_remaining_work_units": 500,
                        "missing_dimension_items": 9_500,
                        "distinct_models": 1,
                        "distinct_model_groups": 1,
                        "item_scheduling_model": "m1",
                        "item_scheduling_model_group": "m1",
                    }
                ]
            if "UPDATE deltallm_batch_job" in sql:
                raise AssertionError("job aggregate must not be stamped from partial item data")
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.backfill_scheduler_dimensions(limit=500)

    assert result == {"jobs": 0, "items": 0}
    assert not any("UPDATE deltallm_batch_job" in sql for sql, _params in prisma.calls)


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_marks_legacy_mixed_model_jobs_from_items() -> None:
    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return []
            if "WITH field_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-1",
                        "endpoint": "/v1/chat/completions",
                        "model": "legacy-job-model",
                        "created_by_organization_id": None,
                        "created_by_team_id": "team-1",
                        "created_by_api_key": None,
                        "created_by_user_id": None,
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "service_tier": "standard",
                        "estimated_work_units": 1,
                        "remaining_work_units": 1,
                        "total_items": 2,
                        "created_at": datetime.now(tz=UTC),
                        "item_estimated_work_units": 9,
                        "item_remaining_work_units": 7,
                        "missing_dimension_items": 0,
                        "distinct_models": 2,
                        "distinct_model_groups": 2,
                        "item_scheduling_model": "model-a",
                        "item_scheduling_model_group": "group-a",
                    }
                ]
            if "UPDATE deltallm_batch_job" in sql:
                return [{"batch_id": params[0]}]
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.backfill_scheduler_dimensions(limit=500)

    assert result == {"jobs": 1, "items": 0}
    update_sql, update_params = next(
        (sql, params) for sql, params in prisma.calls if "UPDATE deltallm_batch_job" in sql
    )
    assert update_params[2] == MIXED_MODEL_GROUP
    assert update_params[3] == MIXED_MODEL_GROUP
    assert update_params[8] == 9
    assert update_params[9] == 7
    assert json.loads(str(update_params[12]))["mixed_model"] is True
    assert "scheduling_model = $3" in update_sql


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_skips_when_lock_is_held() -> None:
    class _LockedTx:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            return [{"acquired": False}]

    class _Prisma:
        def __init__(self) -> None:
            self.tx_client = _LockedTx()
            self.tx_entered = False

        @asynccontextmanager
        async def tx(self):
            self.tx_entered = True
            yield self.tx_client

        async def query_raw(self, sql: str, *params):
            del sql, params
            raise AssertionError("backfill should use the transaction client")

    prisma = _Prisma()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.backfill_scheduler_dimensions(limit=500)

    assert result == {"jobs": 0, "items": 0, "skipped": 1}
    assert prisma.tx_entered is True
    assert len(prisma.tx_client.calls) == 1
    assert "pg_try_advisory_xact_lock" in prisma.tx_client.calls[0][0]


@pytest.mark.asyncio
async def test_refresh_job_progress_casts_status_case_to_enum() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    refreshed = await repository.refresh_job_progress("batch-1")

    assert refreshed is None
    assert "WHEN s.pending_items = 0 AND s.in_progress_items = 0 THEN 'finalizing'" in prisma.sql
    assert '::"DeltaLLM_BatchJobStatus"' in prisma.sql


@pytest.mark.asyncio
async def test_attach_artifacts_and_finalize_casts_status_parameter_to_enum() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    finalized = await repository.attach_artifacts_and_finalize(
        batch_id="batch-1",
        output_file_id="out-1",
        error_file_id="err-1",
        final_status="completed",
    )

    assert finalized is None
    assert 'status = $4::"DeltaLLM_BatchJobStatus"' in prisma.sql
    assert prisma.params[3] == BatchJobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_attach_artifacts_and_finalize_rejects_invalid_status_before_sql() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    with pytest.raises(ValueError, match="batch job status"):
        await repository.attach_artifacts_and_finalize(
            batch_id="batch-1",
            output_file_id=None,
            error_file_id=None,
            final_status="broken",
        )

    assert prisma.queries == []
