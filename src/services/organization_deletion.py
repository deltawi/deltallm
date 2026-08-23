from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.audit.actions import AuditAction
from src.db.cache_invalidation_outbox import CacheInvalidationOutboxRepository
from src.db.organization_deletion_records import OrganizationDeletionJobRecord
from src.db.organization_deletion_repository import OrganizationDeletionRepository
from src.db.organization_deletion_worker_repository import (
    OrganizationDeletionWorkerRepository,
)
from src.services.organization_deletion_audit import record_lifecycle_mutation_audit
from src.services.organization_deletion_request import OrganizationDeletionRequestWriter
from src.services.organization_deletion_tokens import (
    build_deletion_plan_token,
    build_deletion_request_hash,
)
from src.services.organization_deletion_types import (
    OrganizationDeletionConflictError,
    OrganizationDeletionError,
    OrganizationDeletionMutationResult,
    OrganizationDeletionNotFoundError,
    OrganizationDeletionPlan,
    OrganizationDeletionRequestsDisabledError,
    OrganizationDeletionUnavailableError,
    OrganizationDeletionValidationError,
)
from src.services.organization_lifecycle import OrganizationLifecycleAuthorizer

logger = logging.getLogger(__name__)

_RESTORABLE_PHASES = frozenset({"cancel_pending", "cancel_batches", "wait_for_batches"})


class OrganizationDeletionService:
    def __init__(
        self,
        *,
        repository: OrganizationDeletionRepository,
        cache_invalidation_service: Any | None,
        lifecycle_authorizer: OrganizationLifecycleAuthorizer | None = None,
        worker_repository: OrganizationDeletionWorkerRepository | None = None,
        recovery_window_hours: int = 168,
        max_attempts: int = 20,
        requests_enabled: bool = False,
    ) -> None:
        self.repository = repository
        self.cache_invalidation_service = cache_invalidation_service
        self.lifecycle_authorizer = lifecycle_authorizer
        self.worker_repository = worker_repository or OrganizationDeletionWorkerRepository(
            repository.prisma
        )
        self.recovery_window_hours = max(1, int(recovery_window_hours))
        self.max_attempts = max(1, int(max_attempts))
        self.requests_enabled = bool(requests_enabled)
        self.request_writer = OrganizationDeletionRequestWriter(
            repository,
            max_attempts=self.max_attempts,
        )

    async def preview(self, organization_id: str) -> OrganizationDeletionPlan:
        organization_id = self._normalize_id(organization_id, "organization_id")
        try:
            record = await self.repository.get_plan(organization_id)
        except Exception as exc:
            raise OrganizationDeletionUnavailableError(
                "Organization deletion impact is unavailable"
            ) from exc
        if record is None:
            raise OrganizationDeletionNotFoundError("Organization not found")
        return OrganizationDeletionPlan(
            record=record,
            plan_token=build_deletion_plan_token(record),
            recovery_window_hours=self.recovery_window_hours,
            requests_enabled=self.requests_enabled,
        )

    async def request_deletion(
        self,
        *,
        organization_id: str,
        confirmation_name: str,
        plan_token: str,
        idempotency_key: str,
        requested_by_account_id: str | None,
        options: dict[str, object] | None = None,
    ) -> OrganizationDeletionMutationResult:
        if not self.requests_enabled:
            raise OrganizationDeletionRequestsDisabledError(
                "Organization deletion requests are disabled during staged rollout"
            )
        organization_id = self._normalize_id(organization_id, "organization_id")
        confirmation_name = self._normalize_id(confirmation_name, "confirmation_name")
        plan_token = self._normalize_id(plan_token, "plan_token")
        idempotency_key = self._normalize_id(idempotency_key, "Idempotency-Key")
        if len(idempotency_key) > 200:
            raise OrganizationDeletionValidationError("Idempotency-Key is too long")
        normalized_options = dict(options or {})
        request_hash = build_deletion_request_hash(
            organization_id=organization_id,
            confirmation_name=confirmation_name,
            plan_token=plan_token,
            options=normalized_options,
        )

        existing = await self._existing_idempotent_job(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if existing is not None:
            return OrganizationDeletionMutationResult(
                job=existing,
                immediate_invalidation_succeeded=False,
            )

        not_before_at = datetime.now(tz=UTC) + timedelta(hours=self.recovery_window_hours)
        job = await self.request_writer.persist(
            organization_id=organization_id,
            confirmation_name=confirmation_name,
            plan_token=plan_token,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            requested_by_account_id=requested_by_account_id,
            options=normalized_options,
            not_before_at=not_before_at,
        )

        immediate = await self._invalidate_now(
            organization_id,
            reason="organization_deletion_requested",
        )
        return OrganizationDeletionMutationResult(
            job=job,
            immediate_invalidation_succeeded=immediate,
        )

    async def get_job(
        self,
        *,
        organization_id: str,
        deletion_job_id: str,
    ) -> OrganizationDeletionJobRecord:
        organization_id = self._normalize_id(organization_id, "organization_id")
        deletion_job_id = self._normalize_id(deletion_job_id, "deletion_job_id")
        try:
            job = await self.repository.get_job(
                organization_id=organization_id,
                deletion_job_id=deletion_job_id,
            )
        except Exception as exc:
            raise OrganizationDeletionUnavailableError(
                "Organization deletion status is unavailable"
            ) from exc
        if job is None:
            raise OrganizationDeletionNotFoundError("Organization deletion request not found")
        return job

    async def restore(
        self,
        *,
        organization_id: str,
        deletion_job_id: str,
        restored_by_account_id: str | None = None,
    ) -> OrganizationDeletionMutationResult:
        organization_id = self._normalize_id(organization_id, "organization_id")
        deletion_job_id = self._normalize_id(deletion_job_id, "deletion_job_id")
        try:
            async with self._transaction() as tx:
                tx_repository = self.repository.with_db(tx)
                job = await tx_repository.get_job(
                    organization_id=organization_id,
                    deletion_job_id=deletion_job_id,
                    for_update=True,
                )
                if job is None:
                    raise OrganizationDeletionNotFoundError(
                        "Organization deletion request not found"
                    )
                self._require_restorable(job)
                organization = await tx_repository.get_organization_for_update(organization_id)
                if organization is None:
                    raise OrganizationDeletionConflictError(
                        "Organization has already been permanently deleted",
                        code="organization_deletion_restore_closed",
                    )
                restored = await tx_repository.restore_organization(
                    organization_id=organization_id,
                    deletion_job_id=deletion_job_id,
                )
                job_restored = await tx_repository.mark_job_restored(
                    deletion_job_id=deletion_job_id
                )
                if not restored or not job_restored:
                    raise OrganizationDeletionConflictError(
                        "Organization can no longer be restored",
                        code="organization_deletion_restore_closed",
                    )
                await tx_repository.increment_lifecycle_generation()
                await self._enqueue_invalidation(
                    tx,
                    organization_id=organization_id,
                    reason="organization_deletion_restored",
                    deletion_job_id=deletion_job_id,
                )
                await record_lifecycle_mutation_audit(
                    tx,
                    action=AuditAction.ADMIN_ORGANIZATION_DELETION_RESTORE,
                    job=job,
                    actor_id=restored_by_account_id,
                    before_state=str(organization.get("lifecycle_state") or "deletion_pending"),
                    after_state="active",
                )
                restored_job = await tx_repository.get_job(
                    organization_id=organization_id,
                    deletion_job_id=deletion_job_id,
                )
                if restored_job is None:
                    raise OrganizationDeletionUnavailableError(
                        "Restored deletion request could not be read"
                    )
        except OrganizationDeletionError:
            raise
        except Exception as exc:
            raise OrganizationDeletionUnavailableError(
                "Organization could not be restored"
            ) from exc

        immediate = await self._invalidate_now(
            organization_id,
            reason="organization_deletion_restored",
        )
        return OrganizationDeletionMutationResult(
            job=restored_job,
            immediate_invalidation_succeeded=immediate,
        )

    async def retry_failed(
        self,
        *,
        organization_id: str,
        deletion_job_id: str,
        retried_by_account_id: str | None = None,
    ) -> OrganizationDeletionMutationResult:
        job = await self.get_job(
            organization_id=organization_id,
            deletion_job_id=deletion_job_id,
        )
        if job.status != "failed":
            raise OrganizationDeletionConflictError(
                "Only failed organization deletion requests can be retried",
                code="organization_deletion_retry_not_allowed",
            )
        try:
            retried = await self.worker_repository.retry_failed(
                organization_id=organization_id,
                deletion_job_id=deletion_job_id,
                retried_by_account_id=retried_by_account_id,
            )
            if not retried:
                raise OrganizationDeletionConflictError(
                    "Organization deletion can no longer be retried",
                    code="organization_deletion_retry_not_allowed",
                )
            refreshed = await self.get_job(
                organization_id=organization_id,
                deletion_job_id=deletion_job_id,
            )
        except OrganizationDeletionError:
            raise
        except Exception as exc:
            raise OrganizationDeletionUnavailableError(
                "Organization deletion retry could not be scheduled"
            ) from exc
        immediate = await self._invalidate_now(
            organization_id,
            reason="organization_deletion_retried",
        )
        return OrganizationDeletionMutationResult(
            job=refreshed,
            immediate_invalidation_succeeded=immediate,
        )

    async def _existing_idempotent_job(
        self,
        *,
        organization_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> OrganizationDeletionJobRecord | None:
        try:
            existing = await self.repository.get_job_by_idempotency_key(
                organization_id=organization_id,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            raise OrganizationDeletionUnavailableError(
                "Organization deletion idempotency state is unavailable"
            ) from exc
        if existing is not None:
            self.request_writer.require_matching_request(existing, request_hash)
        return existing

    @staticmethod
    def _require_restorable(job: OrganizationDeletionJobRecord) -> None:
        now = datetime.now(tz=UTC)
        if (
            job.status not in {"pending", "processing", "waiting", "failed"}
            or job.phase not in _RESTORABLE_PHASES
            or job.not_before_at is None
            or now >= job.not_before_at
        ):
            raise OrganizationDeletionConflictError(
                "Organization can no longer be restored",
                code="organization_deletion_restore_closed",
            )

    async def _enqueue_invalidation(
        self,
        tx: Any,
        *,
        organization_id: str,
        reason: str,
        deletion_job_id: str,
    ) -> None:
        record = await CacheInvalidationOutboxRepository(tx).enqueue(
            scope_type="organization",
            scope_id=organization_id,
            reason=reason,
            metadata={"deletion_job_id": deletion_job_id},
        )
        if record is None:
            raise OrganizationDeletionUnavailableError(
                "Authentication invalidation could not be scheduled"
            )

    async def _invalidate_now(self, organization_id: str, *, reason: str) -> bool:
        lifecycle_succeeded = True
        if self.lifecycle_authorizer is not None:
            try:
                await self.lifecycle_authorizer.refresh_generation()
                await self.lifecycle_authorizer.remember_state(
                    organization_id,
                    "active" if reason == "organization_deletion_restored" else "deletion_pending",
                )
            except Exception:
                lifecycle_succeeded = False
                logger.exception(
                    "best-effort organization lifecycle snapshot refresh failed",
                    extra={"organization_id": organization_id, "reason": reason},
                )
        service = self.cache_invalidation_service
        if service is None:
            return False
        invalidate = getattr(service, "invalidate_organization_cache_now", None)
        if not callable(invalidate):
            return False
        try:
            result = await invalidate(organization_id, reason=reason)
        except Exception:
            logger.exception(
                "best-effort organization lifecycle cache invalidation failed",
                extra={"organization_id": organization_id, "reason": reason},
            )
            return False
        return lifecycle_succeeded and bool(getattr(result, "invalidated", False))

    def _transaction(self) -> Any:
        if not self.repository.supports_transactions():
            raise OrganizationDeletionUnavailableError(
                "Organization deletion requires transaction support"
            )
        prisma = self.repository.prisma
        if prisma is None:
            raise OrganizationDeletionUnavailableError(
                "Organization deletion requires transaction support"
            )
        return prisma.tx()

    @staticmethod
    def _normalize_id(value: str, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise OrganizationDeletionValidationError(f"{field_name} is required")
        return normalized


__all__ = [
    "OrganizationDeletionConflictError",
    "OrganizationDeletionError",
    "OrganizationDeletionMutationResult",
    "OrganizationDeletionNotFoundError",
    "OrganizationDeletionPlan",
    "OrganizationDeletionService",
    "OrganizationDeletionUnavailableError",
    "OrganizationDeletionValidationError",
]
