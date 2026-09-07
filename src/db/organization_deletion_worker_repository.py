from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

from src.db.organization_deletion_cleanup_repository import CleanupPageResult
from src.db.organization_deletion_records import (
    ORGANIZATION_DELETION_JOB_COLUMNS_FROM_JOB_ALIAS,
    OrganizationDeletionJobRecord,
)
from src.db.organization_deletion_worker_errors import OrganizationDeletionClaimLost
from src.db.organization_deletion_worker_finalization import (
    OrganizationDeletionWorkerFinalizationMixin,
)


class OrganizationDeletionWorkerRepository(OrganizationDeletionWorkerFinalizationMixin):
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    def with_db(self, prisma_client: Any) -> OrganizationDeletionWorkerRepository:
        return OrganizationDeletionWorkerRepository(prisma_client)

    async def run_cleanup_page(
        self,
        job: OrganizationDeletionJobRecord,
        *,
        worker_id: str,
        lease_seconds: int,
        cleanup: Callable[[Any], Awaitable[CleanupPageResult]],
        next_phase: str,
        progress_key: str,
        release_claim: bool,
    ) -> tuple[bool, CleanupPageResult | None]:
        if self.prisma is None:
            return False, None
        async with self.prisma.tx() as tx:
            repository = self.with_db(tx)
            if not await repository._lock_fenced_job(
                tx,
                job,
                worker_id=worker_id,
                expected_phase=job.phase,
            ):
                return False, None
            result = await cleanup(tx)
            should_release = release_claim or not result.remaining
            target_phase = job.phase if result.remaining else next_phase
            rows = await tx.query_raw(
                """
                UPDATE deltallm_organizationdeletionjob
                SET phase = $4,
                    status = CASE WHEN $7::boolean THEN 'pending' ELSE 'processing' END,
                    progress = jsonb_set(
                        COALESCE(progress, '{}'::jsonb),
                        ARRAY[$5::text],
                        to_jsonb(
                            COALESCE((progress->>$5::text)::bigint, 0) + $6::bigint
                        ),
                        true
                    ),
                    next_attempt_at = CASE WHEN $7::boolean THEN NOW() ELSE next_attempt_at END,
                    locked_by = CASE WHEN $7::boolean THEN NULL ELSE locked_by END,
                    lease_expires_at = CASE
                        WHEN $7::boolean THEN NULL
                        ELSE clock_timestamp() + ($8 * INTERVAL '1 second')
                    END,
                    last_error_code = NULL,
                    last_error_detail = NULL,
                    updated_at = NOW()
                WHERE deletion_job_id = $1
                  AND status = 'processing'
                  AND phase = $9
                  AND locked_by = $2
                  AND claim_epoch = $3
                  AND lease_expires_at > clock_timestamp()
                RETURNING deletion_job_id
                """,
                job.deletion_job_id,
                worker_id,
                job.claim_epoch,
                target_phase,
                progress_key,
                max(0, int(result.processed)),
                should_release,
                max(5, int(lease_seconds)),
                job.phase,
            )
            if not rows:
                raise OrganizationDeletionClaimLost(
                    "organization deletion claim expired during cleanup"
                )
            return True, result

    async def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int,
    ) -> list[OrganizationDeletionJobRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            f"""
            WITH candidates AS (
                SELECT deletion_job_id
                FROM deltallm_organizationdeletionjob
                WHERE (
                    status IN ('pending', 'waiting') AND next_attempt_at <= NOW()
                ) OR (
                    status = 'processing' AND lease_expires_at < NOW()
                )
                ORDER BY next_attempt_at ASC, created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT $1
            )
            UPDATE deltallm_organizationdeletionjob j
            SET status = 'processing',
                locked_by = $2,
                lease_expires_at = NOW() + ($3 * INTERVAL '1 second'),
                claim_epoch = claim_epoch + 1,
                updated_at = NOW()
            FROM candidates c
            WHERE j.deletion_job_id = c.deletion_job_id
            RETURNING {ORGANIZATION_DELETION_JOB_COLUMNS_FROM_JOB_ALIAS}
            """,
            max(1, int(limit)),
            worker_id,
            max(5, int(lease_seconds)),
        )
        return [OrganizationDeletionJobRecord.from_row(dict(row)) for row in rows]

    async def settle_batch_wait(
        self,
        job: OrganizationDeletionJobRecord,
        *,
        worker_id: str,
        active_batches: int,
        requested_recheck_at: datetime,
    ) -> Literal["waiting", "advanced"]:
        if self.prisma is None:
            raise OrganizationDeletionClaimLost("organization deletion repository is unavailable")
        async with self.prisma.tx() as tx:
            rows = await tx.query_raw(
                """
                WITH decision AS (
                    SELECT clock_timestamp() AS decided_at
                )
                UPDATE deltallm_organizationdeletionjob AS job
                SET phase = CASE
                        WHEN $4::integer = 0
                             AND job.not_before_at <= decision.decided_at
                        THEN 'resolve_owned_assets'
                        ELSE job.phase
                    END,
                    status = CASE
                        WHEN $4::integer = 0
                             AND job.not_before_at <= decision.decided_at
                        THEN 'pending'
                        ELSE 'waiting'
                    END,
                    progress = COALESCE(job.progress, '{}'::jsonb) || jsonb_build_object(
                        'active_batches', $4::integer,
                        'recovery_window_elapsed', job.not_before_at <= decision.decided_at
                    ),
                    next_attempt_at = CASE
                        WHEN $4::integer = 0
                             AND job.not_before_at <= decision.decided_at
                        THEN decision.decided_at
                        WHEN $4::integer = 0
                        THEN LEAST($5::timestamptz, job.not_before_at)
                        ELSE $5::timestamptz
                    END,
                    locked_by = NULL,
                    lease_expires_at = NULL,
                    updated_at = decision.decided_at
                FROM decision
                WHERE job.deletion_job_id = $1
                  AND job.status = 'processing'
                  AND job.phase = 'wait_for_batches'
                  AND job.locked_by = $2
                  AND job.claim_epoch = $3
                  AND job.lease_expires_at > decision.decided_at
                RETURNING job.organization_id, job.phase
                """,
                job.deletion_job_id,
                worker_id,
                job.claim_epoch,
                max(0, int(active_batches)),
                requested_recheck_at,
            )
            if not rows:
                raise OrganizationDeletionClaimLost(
                    "organization deletion claim expired during batch wait"
                )
            if str(rows[0].get("phase") or "") != "resolve_owned_assets":
                return "waiting"
            organization_rows = await tx.query_raw(
                """
                UPDATE deltallm_organizationtable
                SET lifecycle_state = 'purging',
                    lifecycle_version = lifecycle_version + 1,
                    updated_at = NOW()
                WHERE organization_id = $1
                  AND deletion_job_id = $2
                  AND lifecycle_state = 'deletion_pending'
                RETURNING organization_id
                """,
                job.organization_id,
                job.deletion_job_id,
            )
            if not organization_rows:
                raise RuntimeError("organization could not enter irreversible deletion")
            await self.with_db(tx)._increment_lifecycle_generation()
            return "advanced"

    async def mark_retry(
        self,
        job: OrganizationDeletionJobRecord,
        *,
        worker_id: str,
        next_attempt_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_organizationdeletionjob
            SET status = 'pending',
                attempt_count = attempt_count + 1,
                next_attempt_at = $4::timestamptz,
                locked_by = NULL,
                lease_expires_at = NULL,
                last_error_code = $5,
                last_error_detail = LEFT($6, 512),
                updated_at = NOW()
            WHERE deletion_job_id = $1
              AND status = 'processing'
              AND locked_by = $2
              AND claim_epoch = $3
              AND lease_expires_at > clock_timestamp()
            RETURNING deletion_job_id
            """,
            job.deletion_job_id,
            worker_id,
            job.claim_epoch,
            next_attempt_at,
            error_code,
            error_detail,
        )
        return bool(rows)

    async def mark_failed(
        self,
        job: OrganizationDeletionJobRecord,
        *,
        worker_id: str,
        error_code: str,
        error_detail: str,
    ) -> bool:
        if self.prisma is None:
            return False
        async with self.prisma.tx() as tx:
            rows = await tx.query_raw(
                """
                UPDATE deltallm_organizationdeletionjob
                SET status = 'failed',
                    attempt_count = attempt_count + 1,
                    locked_by = NULL,
                    lease_expires_at = NULL,
                    last_error_code = $4,
                    last_error_detail = LEFT($5, 512),
                    updated_at = NOW()
                WHERE deletion_job_id = $1
                  AND status = 'processing'
                  AND locked_by = $2
                  AND claim_epoch = $3
                  AND lease_expires_at > clock_timestamp()
                RETURNING deletion_job_id
                """,
                job.deletion_job_id,
                worker_id,
                job.claim_epoch,
                error_code,
                error_detail,
            )
            if not rows:
                return False
            organization_rows = await tx.query_raw(
                """
                UPDATE deltallm_organizationtable
                SET lifecycle_state = 'deletion_failed',
                    lifecycle_version = lifecycle_version + 1,
                    updated_at = NOW()
                WHERE organization_id = $1 AND deletion_job_id = $2
                RETURNING organization_id
                """,
                job.organization_id,
                job.deletion_job_id,
            )
            if not organization_rows:
                raise RuntimeError("failed organization deletion target is missing")
            await self.with_db(tx)._increment_lifecycle_generation()
            await self._audit_failure(tx, job, error_code=error_code)
            return True

    async def _lock_fenced_job(
        self,
        tx: Any,
        job: OrganizationDeletionJobRecord,
        *,
        worker_id: str,
        expected_phase: str,
    ) -> bool:
        rows = await tx.query_raw(
            """
            SELECT deletion_job_id
            FROM deltallm_organizationdeletionjob
            WHERE deletion_job_id = $1 AND status = 'processing'
              AND phase = $4 AND locked_by = $2 AND claim_epoch = $3
              AND lease_expires_at > clock_timestamp()
            FOR UPDATE
            """,
            job.deletion_job_id,
            worker_id,
            job.claim_epoch,
            expected_phase,
        )
        return bool(rows)

    async def _increment_lifecycle_generation(self) -> None:
        if self.prisma is None:
            raise RuntimeError("organization lifecycle generation is unavailable")
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_organizationlifecyclegeneration
            SET generation = generation + 1, updated_at = NOW()
            WHERE singleton_id = 1
            """
        )


__all__ = ["OrganizationDeletionClaimLost", "OrganizationDeletionWorkerRepository"]
