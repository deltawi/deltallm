from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from src.db.organization_deletion_cleanup_repository import CleanupPageResult
from src.db.organization_deletion_records import (
    OrganizationDeletionFinalizationResult,
    OrganizationDeletionJobRecord,
)
from src.services.organization_deletion_worker import (
    OrganizationDeletionWorker,
    OrganizationDeletionWorkerConfig,
)


def _job(*, phase: str, not_before_at: datetime | None = None) -> OrganizationDeletionJobRecord:
    return OrganizationDeletionJobRecord(
        deletion_job_id="job-1",
        organization_id="org-1",
        status="processing",
        phase=phase,
        requested_by_account_id="account-1",
        idempotency_key="request-1",
        request_hash="hash",
        plan_token="plan",
        not_before_at=not_before_at or datetime.now(tz=UTC) + timedelta(hours=1),
        max_attempts=3,
        locked_by="worker-1",
        claim_epoch=2,
    )


class _WorkerRepository:
    def __init__(self, jobs: list[OrganizationDeletionJobRecord]) -> None:
        self.jobs = jobs
        self.transitions: list[tuple[str, str, dict[str, object]]] = []
        self.waits: list[dict[str, object]] = []
        self.retries: list[dict[str, object]] = []
        self.failures: list[dict[str, object]] = []
        self.finalized: list[str] = []
        self.finalization_result = OrganizationDeletionFinalizationResult.completed()
        self.cleanup_progress: dict[str, int] = {}

    async def claim_due(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        jobs, self.jobs = self.jobs, []
        return jobs

    async def advance_phase(self, job, *, next_phase, progress=None, **kwargs):  # noqa: ANN001, ANN003, ANN201
        del kwargs
        self.transitions.append((job.phase, next_phase, dict(progress or {})))
        return True

    async def run_cleanup_page(
        self,
        job,
        *,
        cleanup,
        next_phase,
        progress_key,
        release_claim,
        **kwargs,
    ):  # noqa: ANN001, ANN003, ANN201
        del kwargs
        result = await cleanup(self)
        total = self.cleanup_progress.get(progress_key, 0) + result.processed
        self.cleanup_progress[progress_key] = total
        if release_claim or not result.remaining:
            self.transitions.append(
                (
                    job.phase,
                    job.phase if result.remaining else next_phase,
                    {progress_key: total},
                )
            )
        return True, result

    async def mark_waiting(self, job, **kwargs):  # noqa: ANN001, ANN003, ANN201
        del job
        self.waits.append(dict(kwargs))
        return True

    async def mark_retry(self, job, **kwargs):  # noqa: ANN001, ANN003, ANN201
        del job
        self.retries.append(dict(kwargs))
        return True

    async def mark_failed(self, job, **kwargs):  # noqa: ANN001, ANN003, ANN201
        del job
        self.failures.append(dict(kwargs))
        return True

    async def finalize(self, job, **kwargs):  # noqa: ANN001, ANN003, ANN201
        del kwargs
        self.finalized.append(job.deletion_job_id)
        return self.finalization_result


class _CleanupRepository:
    def __init__(self) -> None:
        self.page_results: list[CleanupPageResult] = [CleanupPageResult(0, False)]
        self.active_batches = 0
        self.removable_state = False
        self.sensitive_history = False
        self.ambiguous_sensitive_records = False
        self.scoped_access = False
        self.owned_assets = False

    def with_db(self, _tx):  # noqa: ANN001, ANN201
        return self

    async def _page(self) -> CleanupPageResult:
        return self.page_results.pop(0)

    async def cancel_pending_page(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._page()

    async def request_batch_cancellation_page(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._page()

    async def delete_owned_assets_page(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._page()

    async def delete_sensitive_history_page(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._page()

    async def remove_scoped_access_page(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._page()

    async def revoke_credentials_page(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._page()

    async def remove_tenant_state_page(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._page()

    async def active_batch_count(self, organization_id: str) -> int:
        del organization_id
        return self.active_batches

    async def has_removable_state(self, organization_id: str) -> bool:
        del organization_id
        return self.removable_state

    async def has_owned_assets(self, organization_id: str) -> bool:
        del organization_id
        return self.owned_assets

    async def has_sensitive_history(self, organization_id: str) -> bool:
        del organization_id
        return self.sensitive_history

    async def has_ambiguous_sensitive_records(self, organization_id: str) -> bool:
        del organization_id
        return self.ambiguous_sensitive_records

    async def has_scoped_access(self, organization_id: str) -> bool:
        del organization_id
        return self.scoped_access


def _worker(
    job: OrganizationDeletionJobRecord,
) -> tuple[OrganizationDeletionWorker, _WorkerRepository, _CleanupRepository]:
    repository = _WorkerRepository([job])
    cleanup = _CleanupRepository()
    worker = OrganizationDeletionWorker(
        repository=repository,  # type: ignore[arg-type]
        cleanup_repository=cleanup,  # type: ignore[arg-type]
        worker_id="worker-1",
        config=OrganizationDeletionWorkerConfig(
            max_pages_per_claim=3,
            waiting_poll_seconds=1,
        ),
    )
    return worker, repository, cleanup


@pytest.mark.asyncio
async def test_worker_drains_bounded_pages_and_advances_phase() -> None:
    worker, repository, cleanup = _worker(_job(phase="cancel_pending"))
    cleanup.page_results = [CleanupPageResult(100, True), CleanupPageResult(3, False)]

    assert await worker.process_once() == 1

    assert repository.transitions == [
        ("cancel_pending", "cancel_batches", {"cancelled_pending_items": 103})
    ]


@pytest.mark.asyncio
async def test_worker_requeues_same_phase_at_page_budget() -> None:
    worker, repository, cleanup = _worker(_job(phase="revoke_credentials"))
    cleanup.page_results = [CleanupPageResult(100, True)] * 3

    await worker.process_once()

    assert repository.transitions == [
        ("revoke_credentials", "revoke_credentials", {"revoked_credentials": 300})
    ]


@pytest.mark.asyncio
async def test_worker_waits_for_recovery_deadline_and_batch_quiescence() -> None:
    worker, repository, cleanup = _worker(_job(phase="wait_for_batches"))
    cleanup.active_batches = 2

    await worker.process_once()

    assert repository.transitions == []
    assert repository.waits[0]["progress"] == {
        "active_batches": 2,
        "recovery_window_elapsed": False,
    }


@pytest.mark.asyncio
async def test_worker_enters_irreversible_phase_only_after_deadline() -> None:
    job = _job(
        phase="wait_for_batches",
        not_before_at=datetime.now(tz=UTC) - timedelta(seconds=1),
    )
    worker, repository, _cleanup = _worker(job)

    await worker.process_once()

    assert repository.transitions == [
        (
            "wait_for_batches",
            "resolve_owned_assets",
            {"active_batches": 0, "recovery_window_elapsed": True},
        )
    ]


@pytest.mark.asyncio
async def test_worker_delegates_final_inventory_to_fenced_repository() -> None:
    worker, repository, _cleanup = _worker(_job(phase="finalize"))
    repository.finalization_result = OrganizationDeletionFinalizationResult.retry_cleanup(
        "remove_tenant_state"
    )

    await worker.process_once()

    assert repository.finalized == ["job-1"]
    assert repository.transitions == []
    assert repository.failures == []


@pytest.mark.asyncio
async def test_worker_accepts_repository_owned_classification_block() -> None:
    worker, repository, _cleanup = _worker(replace(_job(phase="finalize"), attempt_count=2))
    repository.finalization_result = OrganizationDeletionFinalizationResult.blocked(
        "organization_deletion_ownership_classification_required"
    )

    await worker.process_once()

    assert repository.finalized == ["job-1"]
    assert repository.failures == []


@pytest.mark.asyncio
async def test_worker_completes_only_for_repository_completed_outcome() -> None:
    worker, repository, _cleanup = _worker(_job(phase="finalize"))

    await worker.process_once()

    assert repository.finalized == ["job-1"]
    assert repository.failures == []


@pytest.mark.asyncio
async def test_worker_persists_safe_error_and_honors_attempt_limit() -> None:
    worker, repository, cleanup = _worker(replace(_job(phase="cancel_pending"), attempt_count=2))

    async def _fail(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del args, kwargs
        raise RuntimeError("secret database detail")

    cleanup.cancel_pending_page = _fail  # type: ignore[method-assign]
    await worker.process_once()

    assert repository.retries == []
    assert repository.failures[0]["error_detail"] == "RuntimeError: phase execution failed"


@pytest.mark.asyncio
async def test_worker_readiness_requires_a_recent_successful_cycle() -> None:
    clock = [10.0]
    repository = _WorkerRepository([])
    worker = OrganizationDeletionWorker(
        repository=repository,  # type: ignore[arg-type]
        cleanup_repository=_CleanupRepository(),  # type: ignore[arg-type]
        worker_id="health-worker",
        config=OrganizationDeletionWorkerConfig(
            poll_interval_seconds=0.01,
            record_timeout_seconds=0.01,
        ),
        clock=lambda: clock[0],
    )

    assert worker.is_ready() is False
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0)
    assert worker.is_ready() is True

    clock[0] += 5.01
    assert worker.is_ready() is False
    worker.stop()
    await task
    assert worker.is_ready() is False


@pytest.mark.asyncio
async def test_worker_readiness_stays_fresh_during_bounded_multi_wave_work() -> None:
    clock = [10.0]
    jobs = [
        replace(_job(phase="cancel_pending"), deletion_job_id=f"job-{index}") for index in range(5)
    ]
    repository = _WorkerRepository(jobs)
    worker = OrganizationDeletionWorker(
        repository=repository,  # type: ignore[arg-type]
        cleanup_repository=_CleanupRepository(),  # type: ignore[arg-type]
        worker_id="health-worker",
        config=OrganizationDeletionWorkerConfig(
            max_batch_size=5,
            max_concurrency=2,
            poll_interval_seconds=5,
            record_timeout_seconds=45,
        ),
        clock=lambda: clock[0],
    )
    started = {job.deletion_job_id: asyncio.Event() for job in jobs}
    release = {job.deletion_job_id: asyncio.Event() for job in jobs}

    async def _controlled_phase(job: OrganizationDeletionJobRecord) -> None:
        started[job.deletion_job_id].set()
        await release[job.deletion_job_id].wait()

    worker._process_phase = _controlled_phase  # type: ignore[method-assign]
    worker._started = True
    task = asyncio.create_task(worker.process_once())

    await asyncio.gather(started["job-0"].wait(), started["job-1"].wait())
    clock[0] = 64.0
    assert worker.is_ready() is True
    release["job-0"].set()
    release["job-1"].set()

    await asyncio.gather(started["job-2"].wait(), started["job-3"].wait())
    clock[0] = 118.0
    assert worker.is_ready() is True
    release["job-2"].set()
    release["job-3"].set()

    await started["job-4"].wait()
    clock[0] = 172.0
    assert worker.is_ready() is True
    release["job-4"].set()
    assert await task == 5


@pytest.mark.asyncio
async def test_worker_readiness_fails_for_stuck_in_flight_record() -> None:
    clock = [10.0]
    repository = _WorkerRepository([_job(phase="cancel_pending")])
    worker = OrganizationDeletionWorker(
        repository=repository,  # type: ignore[arg-type]
        cleanup_repository=_CleanupRepository(),  # type: ignore[arg-type]
        worker_id="health-worker",
        config=OrganizationDeletionWorkerConfig(
            poll_interval_seconds=5,
            record_timeout_seconds=45,
        ),
        clock=lambda: clock[0],
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def _stuck_phase(_job: OrganizationDeletionJobRecord) -> None:
        started.set()
        await release.wait()

    worker._process_phase = _stuck_phase  # type: ignore[method-assign]
    worker._started = True
    task = asyncio.create_task(worker.process_once())
    await started.wait()

    clock[0] = 66.0
    assert worker.is_ready() is False
    release.set()
    assert await task == 1
