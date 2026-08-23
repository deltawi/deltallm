from __future__ import annotations

from typing import Any

from src.audit.actions import AuditAction
from src.db.cache_invalidation_outbox import CacheInvalidationOutboxRepository
from src.db.organization_deletion_final_inventory import (
    ORGANIZATION_DELETION_FINAL_INVENTORY_SQL,
)
from src.db.organization_deletion_records import (
    OrganizationDeletionFinalizationResult,
    OrganizationDeletionJobRecord,
)
from src.db.organization_deletion_worker_errors import OrganizationDeletionClaimLost
from src.db.repositories import AuditEventRecord, AuditRepository


class OrganizationDeletionWorkerFinalizationMixin:
    """Final lifecycle mutations shared by the worker repository facade."""

    prisma: Any | None

    async def finalize(
        self,
        job: OrganizationDeletionJobRecord,
        *,
        worker_id: str,
    ) -> OrganizationDeletionFinalizationResult:
        if self.prisma is None:
            return OrganizationDeletionFinalizationResult.claim_lost()
        async with self.prisma.tx() as tx:
            if not await self._lock_fenced_job(
                tx,
                job,
                worker_id=worker_id,
                expected_phase="finalize",
            ):
                return OrganizationDeletionFinalizationResult.claim_lost()
            organization = await tx.query_raw(
                """
                SELECT organization_id
                FROM deltallm_organizationtable
                WHERE organization_id = $1
                  AND deletion_job_id = $2
                  AND lifecycle_state = 'purging'
                FOR UPDATE
                """,
                job.organization_id,
                job.deletion_job_id,
            )
            if not organization:
                raise RuntimeError("organization finalization target is missing")
            inventory = await tx.query_raw(
                ORGANIZATION_DELETION_FINAL_INVENTORY_SQL,
                job.organization_id,
            )
            blocker = str(inventory[0].get("blocker") or "") if inventory else ""
            if blocker == "blocked_ownership_classification":
                await self._mark_ownership_blocked(tx, job, worker_id=worker_id)
                return OrganizationDeletionFinalizationResult.blocked(
                    "organization_deletion_ownership_classification_required"
                )
            if blocker:
                await self._reroute_from_finalization(
                    tx,
                    job,
                    worker_id=worker_id,
                    next_phase=blocker,
                )
                return OrganizationDeletionFinalizationResult.retry_cleanup(blocker)
            tombstones = await tx.query_raw(
                """
                INSERT INTO deltallm_organizationtombstone (
                    organization_id, deletion_job_id, deleted_at
                ) VALUES ($1, $2, NOW())
                ON CONFLICT (organization_id) DO UPDATE
                SET deleted_at = deltallm_organizationtombstone.deleted_at
                WHERE deltallm_organizationtombstone.deletion_job_id = EXCLUDED.deletion_job_id
                RETURNING organization_id
                """,
                job.organization_id,
                job.deletion_job_id,
            )
            if not tombstones:
                raise RuntimeError("organization tombstone ownership mismatch")
            deleted = await tx.query_raw(
                """
                DELETE FROM deltallm_organizationtable
                WHERE organization_id = $1 AND deletion_job_id = $2
                RETURNING organization_id
                """,
                job.organization_id,
                job.deletion_job_id,
            )
            if not deleted:
                raise RuntimeError("organization finalization target is missing")
            await self.with_db(tx)._increment_lifecycle_generation()
            await self._enqueue_completion_invalidation(tx, job)
            await self._audit_completion(tx, job)
            completed = await tx.query_raw(
                """
                UPDATE deltallm_organizationdeletionjob
                SET status = 'completed', phase = 'completed',
                    completed_at = NOW(), updated_at = NOW(),
                    locked_by = NULL, lease_expires_at = NULL,
                    last_error_code = NULL, last_error_detail = NULL
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
            )
            if not completed:
                raise OrganizationDeletionClaimLost(
                    "organization deletion claim expired during finalization"
                )
            return OrganizationDeletionFinalizationResult.completed()

    @staticmethod
    async def _reroute_from_finalization(
        tx: Any,
        job: OrganizationDeletionJobRecord,
        *,
        worker_id: str,
        next_phase: str,
    ) -> None:
        rows = await tx.query_raw(
            """
            UPDATE deltallm_organizationdeletionjob
            SET status = 'pending', phase = $4, next_attempt_at = NOW(),
                locked_by = NULL, lease_expires_at = NULL,
                last_error_code = NULL, last_error_detail = NULL,
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
            next_phase,
        )
        if not rows:
            raise OrganizationDeletionClaimLost(
                "organization deletion claim expired during final inventory"
            )

    async def _mark_ownership_blocked(
        self,
        tx: Any,
        job: OrganizationDeletionJobRecord,
        *,
        worker_id: str,
    ) -> None:
        error_code = "organization_deletion_ownership_classification_required"
        rows = await tx.query_raw(
            """
            UPDATE deltallm_organizationdeletionjob
            SET status = 'failed', attempt_count = attempt_count + 1,
                locked_by = NULL, lease_expires_at = NULL,
                last_error_code = $4,
                last_error_detail = 'Tenant record ownership requires classification',
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
        )
        if not rows:
            raise OrganizationDeletionClaimLost(
                "organization deletion claim expired during final inventory"
            )
        updated = await tx.query_raw(
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
        if not updated:
            raise RuntimeError("blocked organization deletion target is missing")
        await self.with_db(tx)._increment_lifecycle_generation()
        await self._audit_failure(tx, job, error_code=error_code)

    async def retry_failed(
        self,
        *,
        organization_id: str,
        deletion_job_id: str,
        retried_by_account_id: str | None,
    ) -> bool:
        if self.prisma is None:
            return False
        async with self.prisma.tx() as tx:
            rows = await tx.query_raw(
                """
                UPDATE deltallm_organizationdeletionjob
                SET status = 'pending', attempt_count = 0,
                    next_attempt_at = NOW(), last_error_code = NULL,
                    last_error_detail = NULL, updated_at = NOW()
                WHERE organization_id = $1 AND deletion_job_id = $2
                  AND status = 'failed'
                RETURNING deletion_job_id
                """,
                organization_id,
                deletion_job_id,
            )
            if not rows:
                return False
            organization_rows = await tx.query_raw(
                """
                UPDATE deltallm_organizationtable
                SET lifecycle_state = CASE
                        WHEN deletion_not_before_at > NOW() THEN 'deletion_pending'
                        ELSE 'purging'
                    END,
                    lifecycle_version = lifecycle_version + 1,
                    updated_at = NOW()
                WHERE organization_id = $1 AND deletion_job_id = $2
                RETURNING organization_id
                """,
                organization_id,
                deletion_job_id,
            )
            if not organization_rows:
                raise RuntimeError("failed organization deletion target is missing")
            await self.with_db(tx)._increment_lifecycle_generation()
            invalidation = await CacheInvalidationOutboxRepository(tx).enqueue(
                scope_type="organization",
                scope_id=organization_id,
                reason="organization_deletion_retried",
                metadata={"deletion_job_id": deletion_job_id},
            )
            if invalidation is None:
                raise RuntimeError("retry invalidation could not be enqueued")
            await AuditRepository(tx).create_event(
                AuditEventRecord(
                    event_id="",
                    action=AuditAction.ADMIN_ORGANIZATION_DELETION_RETRY.value,
                    organization_id=organization_id,
                    actor_type=("platform_account" if retried_by_account_id else "master_key"),
                    actor_id=retried_by_account_id,
                    resource_type="organization_deletion_job",
                    resource_id=deletion_job_id,
                    status="success",
                )
            )
            return True

    @staticmethod
    async def _enqueue_completion_invalidation(
        tx: Any,
        job: OrganizationDeletionJobRecord,
    ) -> None:
        record = await CacheInvalidationOutboxRepository(tx).enqueue(
            scope_type="organization",
            scope_id=job.organization_id,
            reason="organization_deletion_completed",
            metadata={"deletion_job_id": job.deletion_job_id},
        )
        if record is None:
            raise RuntimeError("completion invalidation could not be enqueued")

    @staticmethod
    async def _audit_completion(tx: Any, job: OrganizationDeletionJobRecord) -> None:
        await AuditRepository(tx).create_event(
            AuditEventRecord(
                event_id="",
                action=AuditAction.SYSTEM_ORGANIZATION_DELETION_COMPLETE.value,
                organization_id=job.organization_id,
                actor_type="system",
                actor_id="organization_deletion_worker",
                resource_type="organization_deletion_job",
                resource_id=job.deletion_job_id,
                status="success",
                metadata={"phase": "completed"},
            )
        )

    @staticmethod
    async def _audit_failure(
        tx: Any,
        job: OrganizationDeletionJobRecord,
        *,
        error_code: str,
    ) -> None:
        await AuditRepository(tx).create_event(
            AuditEventRecord(
                event_id="",
                action=AuditAction.SYSTEM_ORGANIZATION_DELETION_FAILED.value,
                organization_id=job.organization_id,
                actor_type="system",
                actor_id="organization_deletion_worker",
                resource_type="organization_deletion_job",
                resource_id=job.deletion_job_id,
                status="error",
                error_code=error_code,
                metadata={"phase": job.phase},
            )
        )


__all__ = ["OrganizationDeletionWorkerFinalizationMixin"]
