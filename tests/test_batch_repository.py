from __future__ import annotations

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
async def test_claim_next_job_includes_finalizing_jobs():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    job = await repository.claim_next_job(worker_id="worker-1", lease_seconds=120)

    assert job is None
    assert "'finalizing'" in prisma.sql
    assert "CASE WHEN status = 'finalizing' THEN 1 ELSE 0 END" in prisma.sql


@pytest.mark.asyncio
async def test_request_cancel_preserves_finalizing_status():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    job = await repository.request_cancel("batch-1")

    assert job is None
    assert "WHEN status = 'finalizing' THEN status" in prisma.sql


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
