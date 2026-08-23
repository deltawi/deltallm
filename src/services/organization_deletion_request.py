from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any

from src.audit.actions import AuditAction
from src.db.cache_invalidation_outbox import CacheInvalidationOutboxRepository
from src.db.organization_deletion_records import OrganizationDeletionJobRecord
from src.db.organization_deletion_repository import OrganizationDeletionRepository
from src.services.organization_deletion_audit import record_lifecycle_mutation_audit
from src.services.organization_deletion_tokens import (
    build_deletion_plan_snapshot,
    build_deletion_plan_token,
)
from src.services.organization_deletion_types import (
    OrganizationDeletionConflictError,
    OrganizationDeletionError,
    OrganizationDeletionNotFoundError,
    OrganizationDeletionUnavailableError,
    OrganizationDeletionValidationError,
)


class OrganizationDeletionRequestWriter:
    def __init__(
        self,
        repository: OrganizationDeletionRepository,
        *,
        max_attempts: int,
    ) -> None:
        self.repository = repository
        self.max_attempts = max(1, int(max_attempts))

    async def persist(
        self,
        *,
        organization_id: str,
        confirmation_name: str,
        plan_token: str,
        idempotency_key: str,
        request_hash: str,
        requested_by_account_id: str | None,
        options: dict[str, object],
        not_before_at: datetime,
    ) -> OrganizationDeletionJobRecord:
        try:
            async with self._transaction() as tx:
                return await self._create_in_transaction(
                    tx,
                    organization_id=organization_id,
                    confirmation_name=confirmation_name,
                    plan_token=plan_token,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    requested_by_account_id=requested_by_account_id,
                    options=options,
                    not_before_at=not_before_at,
                )
        except OrganizationDeletionError:
            raise
        except Exception as exc:
            if self.looks_like_unique_conflict(exc):
                existing = await self.repository.get_job_by_idempotency_key(
                    organization_id=organization_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    self.require_matching_request(existing, request_hash)
                    return existing
                raise OrganizationDeletionConflictError(
                    "Organization deletion is already in progress",
                    code="organization_deletion_active_job",
                ) from exc
            raise OrganizationDeletionUnavailableError(
                "Organization deletion could not be scheduled"
            ) from exc

    async def _create_in_transaction(
        self,
        tx: Any,
        *,
        organization_id: str,
        confirmation_name: str,
        plan_token: str,
        idempotency_key: str,
        request_hash: str,
        requested_by_account_id: str | None,
        options: dict[str, object],
        not_before_at: datetime,
    ) -> OrganizationDeletionJobRecord:
        repository = self.repository.with_db(tx)
        existing = await repository.get_job_by_idempotency_key(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            self.require_matching_request(existing, request_hash)
            return existing
        organization = await repository.get_organization_for_update(organization_id)
        if organization is None:
            raise OrganizationDeletionNotFoundError("Organization not found")
        existing = await repository.get_job_by_idempotency_key(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            self.require_matching_request(existing, request_hash)
            return existing
        self.require_deletable_organization(
            organization,
            confirmation_name=confirmation_name,
        )
        current_plan = await repository.get_plan(organization_id)
        if current_plan is None:
            raise OrganizationDeletionNotFoundError("Organization not found")
        if not hmac.compare_digest(build_deletion_plan_token(current_plan), plan_token):
            raise OrganizationDeletionConflictError(
                "Organization deletion impact changed; refresh the preview",
                code="organization_deletion_plan_stale",
            )
        if current_plan.counts.ambiguous_sensitive_records > 0:
            raise OrganizationDeletionConflictError(
                "Sensitive records have conflicting or unattributed organization ownership; "
                "classify them before deletion",
                code="organization_deletion_ambiguous_sensitive_records",
            )
        if current_plan.counts.unresolved_batch_ownership_records > 0:
            raise OrganizationDeletionConflictError(
                "Batch records require durable organization ownership before deletion",
                code="organization_deletion_unresolved_batch_ownership",
            )
        if (
            current_plan.counts.external_mcp_dependencies > 0
            or current_plan.counts.external_prompt_dependencies > 0
            or current_plan.counts.external_route_group_dependencies > 0
        ):
            raise OrganizationDeletionConflictError(
                "Organization-owned assets are still used outside this organization; "
                "transfer or unbind them before deletion",
                code="organization_deletion_asset_dependencies",
            )
        job = await repository.create_job(
            organization_id=organization_id,
            requested_by_account_id=requested_by_account_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            plan_token=plan_token,
            plan_snapshot=build_deletion_plan_snapshot(current_plan),
            options=options,
            not_before_at=not_before_at,
            max_attempts=self.max_attempts,
        )
        await self._activate_deletion(tx, repository, job, not_before_at=not_before_at)
        return job

    async def _activate_deletion(
        self,
        tx: Any,
        repository: OrganizationDeletionRepository,
        job: OrganizationDeletionJobRecord,
        *,
        not_before_at: datetime,
    ) -> None:
        transitioned = await repository.mark_organization_deletion_pending(
            organization_id=job.organization_id,
            deletion_job_id=job.deletion_job_id,
            not_before_at=not_before_at,
        )
        if not transitioned:
            raise OrganizationDeletionConflictError(
                "Organization deletion is already in progress",
                code="organization_deletion_active_job",
            )
        await repository.increment_lifecycle_generation()
        record = await CacheInvalidationOutboxRepository(tx).enqueue(
            scope_type="organization",
            scope_id=job.organization_id,
            reason="organization_deletion_requested",
            metadata={"deletion_job_id": job.deletion_job_id},
        )
        if record is None:
            raise OrganizationDeletionUnavailableError(
                "Authentication invalidation could not be scheduled"
            )
        await record_lifecycle_mutation_audit(
            tx,
            action=AuditAction.ADMIN_ORGANIZATION_DELETION_REQUEST,
            job=job,
            actor_id=job.requested_by_account_id,
            before_state="active",
            after_state="deletion_pending",
        )

    def _transaction(self) -> Any:
        if not self.repository.supports_transactions() or self.repository.prisma is None:
            raise OrganizationDeletionUnavailableError(
                "Organization deletion requires transaction support"
            )
        return self.repository.prisma.tx()

    @staticmethod
    def require_matching_request(
        existing: OrganizationDeletionJobRecord,
        request_hash: str,
    ) -> None:
        if not hmac.compare_digest(existing.request_hash, request_hash):
            raise OrganizationDeletionConflictError(
                "Idempotency-Key was already used with a different request",
                code="organization_deletion_idempotency_conflict",
            )

    @staticmethod
    def require_deletable_organization(
        organization: dict[str, Any],
        *,
        confirmation_name: str,
    ) -> None:
        if str(organization.get("lifecycle_state") or "active") != "active":
            raise OrganizationDeletionConflictError(
                "Organization deletion is already in progress",
                code="organization_deletion_active_job",
            )
        expected = str(
            organization.get("organization_name") or organization.get("organization_id") or ""
        ).strip()
        if not expected or not hmac.compare_digest(expected, confirmation_name):
            raise OrganizationDeletionValidationError(
                "confirmation_name must exactly match the organization name"
            )

    @staticmethod
    def looks_like_unique_conflict(exc: Exception) -> bool:
        message = str(exc).lower()
        return "unique" in message or "duplicate" in message


__all__ = ["OrganizationDeletionRequestWriter"]
