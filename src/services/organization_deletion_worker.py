from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Awaitable, Callable

from src.db.organization_deletion_cleanup_repository import (
    CleanupPageResult,
    OrganizationDeletionCleanupRepository,
)
from src.db.organization_deletion_records import OrganizationDeletionJobRecord
from src.db.organization_deletion_worker_repository import (
    OrganizationDeletionClaimLost,
    OrganizationDeletionWorkerRepository,
)
from src.metrics.organization_deletion import (
    deltallm_organization_deletion_claims_metric,
    deltallm_organization_deletion_jobs_metric,
    deltallm_organization_deletion_phase_latency_metric,
    deltallm_organization_deletion_phase_metric,
)
from src.services.organization_lifecycle import OrganizationLifecycleAuthorizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OrganizationDeletionWorkerConfig:
    poll_interval_seconds: float = 5.0
    max_batch_size: int = 5
    max_concurrency: int = 2
    lease_seconds: int = 60
    record_timeout_seconds: float = 45.0
    page_size: int = 100
    max_pages_per_claim: int = 10
    waiting_poll_seconds: float = 10.0
    retry_initial_seconds: int = 5
    retry_max_seconds: int = 300


@dataclass(frozen=True, slots=True)
class OrganizationDeletionWorkerHealth:
    started: bool
    fresh: bool
    last_success_age_seconds: float | None
    last_error: str | None
    in_flight_count: int


class OrganizationDeletionWorker:
    def __init__(
        self,
        *,
        repository: OrganizationDeletionWorkerRepository,
        cleanup_repository: OrganizationDeletionCleanupRepository,
        worker_id: str,
        config: OrganizationDeletionWorkerConfig | None = None,
        lifecycle_authorizer: OrganizationLifecycleAuthorizer | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.repository = repository
        self.cleanup_repository = cleanup_repository
        self.worker_id = str(worker_id or "").strip() or "organization-deletion-worker"
        self.config = config or OrganizationDeletionWorkerConfig()
        self.lifecycle_authorizer = lifecycle_authorizer
        self._clock = clock
        self._stopped = False
        self._started = False
        self._last_repository_success_at: float | None = None
        self._last_progress_at: float | None = None
        self._in_flight_started_at: dict[str, float] = {}
        self._last_cycle_error: str | None = "not_started"

    def stop(self) -> None:
        self._stopped = True

    def health_snapshot(self) -> OrganizationDeletionWorkerHealth:
        healthy_timestamps = tuple(
            value
            for value in (self._last_repository_success_at, self._last_progress_at)
            if value is not None
        )
        last_healthy_at = max(healthy_timestamps) if healthy_timestamps else None
        age = max(0.0, self._clock() - last_healthy_at) if last_healthy_at is not None else None
        freshness_limit = max(
            5.0,
            self.config.record_timeout_seconds + (2 * self.config.poll_interval_seconds),
        )
        in_flight_stale = any(
            max(0.0, self._clock() - started_at) > freshness_limit
            for started_at in self._in_flight_started_at.values()
        )
        return OrganizationDeletionWorkerHealth(
            started=self._started,
            fresh=bool(
                self._started
                and not self._stopped
                and age is not None
                and age <= freshness_limit
                and not in_flight_stale
            ),
            last_success_age_seconds=age,
            last_error=self._last_cycle_error,
            in_flight_count=len(self._in_flight_started_at),
        )

    def is_ready(self) -> bool:
        return self.health_snapshot().fresh

    async def run(self) -> None:
        self._started = True
        try:
            while not self._stopped:
                try:
                    processed = await self.process_once()
                except Exception as exc:
                    self._last_cycle_error = type(exc).__name__
                    logger.exception(
                        "organization deletion worker cycle failed",
                        extra={"worker_id": self.worker_id},
                    )
                    processed = 0
                if processed == 0:
                    await asyncio.sleep(self.config.poll_interval_seconds)
        finally:
            self._started = False

    async def process_once(self) -> int:
        jobs = await self.repository.claim_due(
            worker_id=self.worker_id,
            lease_seconds=self.config.lease_seconds,
            limit=self.config.max_batch_size,
        )
        now = self._clock()
        self._last_repository_success_at = now
        self._last_progress_at = now
        self._last_cycle_error = None
        if not jobs:
            return 0
        deltallm_organization_deletion_claims_metric.inc(len(jobs))
        semaphore = asyncio.Semaphore(max(1, min(self.config.max_concurrency, len(jobs))))

        async def _run(job: OrganizationDeletionJobRecord) -> None:
            async with semaphore:
                self._in_flight_started_at[job.deletion_job_id] = self._clock()
                self._last_progress_at = self._clock()
                try:
                    await self._process_safely(job)
                finally:
                    self._in_flight_started_at.pop(job.deletion_job_id, None)
                    self._last_progress_at = self._clock()

        await asyncio.gather(*(_run(job) for job in jobs))
        return len(jobs)

    async def _process_safely(self, job: OrganizationDeletionJobRecord) -> None:
        started_at = monotonic()
        outcome = "advanced"
        try:
            async with asyncio.timeout(self.config.record_timeout_seconds):
                await self._process_phase(job)
        except OrganizationDeletionClaimLost:
            outcome = "claim_lost"
            logger.warning(
                "organization deletion claim lost",
                extra={
                    "deletion_job_id": job.deletion_job_id,
                    "organization_id": job.organization_id,
                    "phase": job.phase,
                },
            )
        except Exception as exc:
            outcome = "error"
            logger.exception(
                "organization deletion phase failed",
                extra={
                    "deletion_job_id": job.deletion_job_id,
                    "organization_id": job.organization_id,
                    "phase": job.phase,
                },
            )
            await self._handle_failure(job, exc)
        finally:
            deltallm_organization_deletion_phase_metric.labels(
                phase=job.phase,
                outcome=outcome,
            ).inc()
            deltallm_organization_deletion_phase_latency_metric.labels(
                phase=job.phase,
            ).observe(monotonic() - started_at)

    async def _process_phase(self, job: OrganizationDeletionJobRecord) -> None:
        handlers: dict[str, Callable[[OrganizationDeletionJobRecord], Awaitable[None]]] = {
            "cancel_pending": self._cancel_pending,
            "cancel_batches": self._cancel_batches,
            "wait_for_batches": self._wait_for_batches,
            "resolve_owned_assets": self._resolve_owned_assets,
            "purge_sensitive_history": self._purge_sensitive_history,
            "remove_scoped_access": self._remove_scoped_access,
            "revoke_credentials": self._revoke_credentials,
            "remove_tenant_state": self._remove_tenant_state,
            "finalize": self._finalize,
        }
        handler = handlers.get(job.phase)
        if handler is None:
            raise RuntimeError(f"unsupported organization deletion phase: {job.phase}")
        await handler(job)

    async def _cancel_pending(self, job: OrganizationDeletionJobRecord) -> None:
        await self._run_paged_phase(
            job,
            cleanup=lambda repository: repository.cancel_pending_page(
                job.organization_id,
                page_size=self.config.page_size,
            ),
            next_phase="cancel_batches",
            progress_key="cancelled_pending_items",
        )

    async def _cancel_batches(self, job: OrganizationDeletionJobRecord) -> None:
        await self._run_paged_phase(
            job,
            cleanup=lambda repository: repository.request_batch_cancellation_page(
                job.organization_id,
                page_size=self.config.page_size,
            ),
            next_phase="wait_for_batches",
            progress_key="batch_cancellation_requests",
        )

    async def _wait_for_batches(self, job: OrganizationDeletionJobRecord) -> None:
        active_batches = await self.cleanup_repository.active_batch_count(job.organization_id)
        now = datetime.now(tz=UTC)
        deadline_reached = job.not_before_at is not None and now >= job.not_before_at
        if active_batches > 0 or not deadline_reached:
            await self.repository.mark_waiting(
                job,
                worker_id=self.worker_id,
                next_attempt_at=now + timedelta(seconds=self.config.waiting_poll_seconds),
                progress={
                    "active_batches": active_batches,
                    "recovery_window_elapsed": deadline_reached,
                },
            )
            return
        await self.repository.advance_phase(
            job,
            worker_id=self.worker_id,
            next_phase="resolve_owned_assets",
            progress={"active_batches": 0, "recovery_window_elapsed": True},
            mark_organization_purging=True,
        )

    async def _resolve_owned_assets(self, job: OrganizationDeletionJobRecord) -> None:
        await self._run_paged_phase(
            job,
            cleanup=lambda repository: repository.delete_owned_assets_page(
                job.organization_id,
                page_size=self.config.page_size,
            ),
            next_phase="purge_sensitive_history",
            progress_key="deleted_owned_assets",
        )

    async def _purge_sensitive_history(self, job: OrganizationDeletionJobRecord) -> None:
        await self._run_paged_phase(
            job,
            cleanup=lambda repository: repository.delete_sensitive_history_page(
                job.organization_id,
                page_size=self.config.page_size,
            ),
            next_phase="remove_scoped_access",
            progress_key="deleted_sensitive_records",
        )

    async def _remove_scoped_access(self, job: OrganizationDeletionJobRecord) -> None:
        await self._run_paged_phase(
            job,
            cleanup=lambda repository: repository.remove_scoped_access_page(
                job.organization_id,
                page_size=self.config.page_size,
            ),
            next_phase="revoke_credentials",
            progress_key="removed_scoped_access",
        )

    async def _revoke_credentials(self, job: OrganizationDeletionJobRecord) -> None:
        await self._run_paged_phase(
            job,
            cleanup=lambda repository: repository.revoke_credentials_page(
                job.organization_id,
                page_size=self.config.page_size,
            ),
            next_phase="remove_tenant_state",
            progress_key="revoked_credentials",
        )

    async def _remove_tenant_state(self, job: OrganizationDeletionJobRecord) -> None:
        await self._run_paged_phase(
            job,
            cleanup=lambda repository: repository.remove_tenant_state_page(
                job.organization_id,
                deletion_job_id=job.deletion_job_id,
                page_size=self.config.page_size,
            ),
            next_phase="finalize",
            progress_key="removed_tenant_records",
        )

    async def _finalize(self, job: OrganizationDeletionJobRecord) -> None:
        result = await self.repository.finalize(job, worker_id=self.worker_id)
        if result.outcome == "completed":
            deltallm_organization_deletion_jobs_metric.labels(outcome="completed").inc()
        if result.outcome == "completed" and self.lifecycle_authorizer is not None:
            await self.lifecycle_authorizer.invalidate(job.organization_id)

    async def _run_paged_phase(
        self,
        job: OrganizationDeletionJobRecord,
        *,
        cleanup: Callable[[OrganizationDeletionCleanupRepository], Awaitable[CleanupPageResult]],
        next_phase: str,
        progress_key: str,
    ) -> None:
        page_budget = max(1, self.config.max_pages_per_claim)
        for page_index in range(page_budget):
            committed, result = await self.repository.run_cleanup_page(
                job,
                worker_id=self.worker_id,
                lease_seconds=self.config.lease_seconds,
                cleanup=lambda tx: cleanup(self.cleanup_repository.with_db(tx)),
                next_phase=next_phase,
                progress_key=progress_key,
                release_claim=page_index + 1 >= page_budget,
            )
            if not committed or result is None or not result.remaining:
                return

    async def _handle_failure(
        self,
        job: OrganizationDeletionJobRecord,
        exc: Exception,
    ) -> None:
        next_attempt = job.attempt_count + 1
        error_code = f"organization_deletion_{job.phase}_failed"
        safe_detail = f"{type(exc).__name__}: phase execution failed"
        if next_attempt >= job.max_attempts:
            await self.repository.mark_failed(
                job,
                worker_id=self.worker_id,
                error_code=error_code,
                error_detail=safe_detail,
            )
            deltallm_organization_deletion_jobs_metric.labels(outcome="failed").inc()
            return
        delay = min(
            self.config.retry_max_seconds,
            self.config.retry_initial_seconds * (2 ** max(0, next_attempt - 1)),
        )
        await self.repository.mark_retry(
            job,
            worker_id=self.worker_id,
            next_attempt_at=datetime.now(tz=UTC) + timedelta(seconds=delay),
            error_code=error_code,
            error_detail=safe_detail,
        )
        deltallm_organization_deletion_jobs_metric.labels(outcome="retry").inc()


__all__ = ["OrganizationDeletionWorker", "OrganizationDeletionWorkerConfig"]
