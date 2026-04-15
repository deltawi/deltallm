from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from src.batch.repository import BatchRepository


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
    assert "VALUES ($1, $2, $3, $4, $5, $6::jsonb), ($7, $8, $9, $10, $11, $12::jsonb)" in prisma.sql
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
    assert prisma.params[2] == "queued"


@pytest.mark.asyncio
async def test_set_job_queued_casts_status_parameter_to_enum() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    queued = await repository.set_job_queued("batch-1", 2)

    assert queued is None
    assert 'status = $2::"DeltaLLM_BatchJobStatus"' in prisma.sql


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
    assert prisma.params[3] == "completed"


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
