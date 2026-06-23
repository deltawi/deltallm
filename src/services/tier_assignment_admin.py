from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.db.tiers import OrganizationTierAssignmentRecord
from src.services.tier_admin_errors import (
    TierAdminConflictError,
    TierAdminError,
    TierAdminNotFoundError,
    TierAdminUnavailableError,
    TierAdminValidationError,
)
from src.services.tier_assignment_admin_payloads import (
    normalize_assignment_create,
    normalize_assignment_patch,
)
from src.services.tier_assignment_admin_results import (
    TierAssignmentCreateResult,
    TierAssignmentDeleteResult,
    TierAssignmentUpdateResult,
)
from src.services.tier_assignment_cache_invalidation import (
    apply_best_effort_org_cache_invalidation,
    enqueue_org_tier_assignment_cache_invalidation,
)
from src.services.tier_assignment_admin_serialization import serialize_tier_assignment


class TierAssignmentAdminService:
    def __init__(
        self,
        repository: Any,
        *,
        cache_invalidation_service: Any | None = None,
        cache_invalidation_max_attempts: int = 10,
    ) -> None:
        self.repository = repository
        self.cache_invalidation_service = cache_invalidation_service
        self.cache_invalidation_max_attempts = max(1, int(cache_invalidation_max_attempts))

    async def list_assignments(
        self,
        *,
        organization_id: str,
        enabled: bool | None,
    ) -> dict[str, Any]:
        organization_id = await self.require_organization(organization_id)
        records = await self.repository.list_org_assignments(
            organization_id,
            enabled=enabled,
        )
        return {"data": [serialize_tier_assignment(record) for record in records]}

    async def create_assignment_with_cache_invalidation(
        self,
        *,
        organization_id: str,
        payload: Mapping[str, Any],
    ) -> TierAssignmentCreateResult:
        organization_id = self._normalize_organization_id(organization_id)
        await self._require_organization(organization_id)
        try:
            fields = normalize_assignment_create(payload)
            async with self._transaction() as tx:
                repository = self._repository_for_transaction(tx)
                record = await repository.upsert_org_assignment_in_current_transaction(
                    organization_id=organization_id,
                    **fields,
                )
                if record is None:
                    raise TierAdminNotFoundError("Tier assignment not found")
                cache_invalidation = await enqueue_org_tier_assignment_cache_invalidation(
                    tx,
                    organization_id=record.organization_id,
                    reason="organization_tier_assignment_create",
                    metadata={"assignment_id": record.assignment_id},
                    max_attempts=self.cache_invalidation_max_attempts,
                )
        except ValueError as exc:
            raise _assignment_admin_error(exc) from exc
        except TierAdminError:
            raise
        except Exception as exc:
            mapped_error = _assignment_storage_error(exc)
            if mapped_error is not None:
                raise mapped_error from exc
            raise

        cache_invalidation = await apply_best_effort_org_cache_invalidation(
            cache_invalidation,
            cache_invalidation_service=self.cache_invalidation_service,
            organization_id=record.organization_id,
            reason="organization_tier_assignment_create",
        )
        return TierAssignmentCreateResult(
            assignment=record,
            cache_invalidation=cache_invalidation,
        )

    async def update_assignment_with_cache_invalidation(
        self,
        *,
        organization_id: str,
        assignment_id: str,
        payload: Mapping[str, Any],
    ) -> TierAssignmentUpdateResult:
        organization_id = self._normalize_organization_id(organization_id)
        assignment_id = self._normalize_assignment_id(assignment_id)
        await self._require_organization(organization_id)
        try:
            async with self._transaction() as tx:
                repository = self._repository_for_transaction(tx)
                before = await repository.get_org_assignment_for_update(
                    assignment_id=assignment_id,
                    organization_id=organization_id,
                )
                if before is None:
                    raise TierAdminNotFoundError("Tier assignment not found")
                fields = normalize_assignment_patch(payload, existing=before)
                updated = await repository.upsert_org_assignment_in_current_transaction(
                    assignment_id=assignment_id,
                    organization_id=organization_id,
                    **fields,
                )
                if updated is None:
                    raise TierAdminNotFoundError("Tier assignment not found")
                cache_invalidation = await enqueue_org_tier_assignment_cache_invalidation(
                    tx,
                    organization_id=updated.organization_id,
                    reason="organization_tier_assignment_update",
                    metadata={"assignment_id": assignment_id},
                    max_attempts=self.cache_invalidation_max_attempts,
                )
        except ValueError as exc:
            raise _assignment_admin_error(exc) from exc
        except TierAdminError:
            raise
        except Exception as exc:
            mapped_error = _assignment_storage_error(exc)
            if mapped_error is not None:
                raise mapped_error from exc
            raise

        cache_invalidation = await apply_best_effort_org_cache_invalidation(
            cache_invalidation,
            cache_invalidation_service=self.cache_invalidation_service,
            organization_id=updated.organization_id,
            reason="organization_tier_assignment_update",
        )
        return TierAssignmentUpdateResult(
            before=before,
            assignment=updated,
            cache_invalidation=cache_invalidation,
        )

    async def delete_assignment_with_cache_invalidation(
        self,
        *,
        organization_id: str,
        assignment_id: str,
    ) -> TierAssignmentDeleteResult:
        organization_id = self._normalize_organization_id(organization_id)
        assignment_id = self._normalize_assignment_id(assignment_id)
        await self._require_organization(organization_id)
        try:
            async with self._transaction() as tx:
                repository = self._repository_for_transaction(tx)
                before = await repository.get_org_assignment_for_update(
                    assignment_id=assignment_id,
                    organization_id=organization_id,
                )
                if before is None:
                    raise TierAdminNotFoundError("Tier assignment not found")
                deleted = await repository.delete_org_assignment_for_org(
                    assignment_id=assignment_id,
                    organization_id=organization_id,
                )
                if not deleted:
                    raise TierAdminNotFoundError("Tier assignment not found")
                cache_invalidation = await enqueue_org_tier_assignment_cache_invalidation(
                    tx,
                    organization_id=organization_id,
                    reason="organization_tier_assignment_delete",
                    metadata={"assignment_id": assignment_id},
                    max_attempts=self.cache_invalidation_max_attempts,
                )
        except TierAdminError:
            raise
        except Exception as exc:
            mapped_error = _assignment_storage_error(exc)
            if mapped_error is not None:
                raise mapped_error from exc
            raise

        cache_invalidation = await apply_best_effort_org_cache_invalidation(
            cache_invalidation,
            cache_invalidation_service=self.cache_invalidation_service,
            organization_id=organization_id,
            reason="organization_tier_assignment_delete",
        )
        return TierAssignmentDeleteResult(
            before=before,
            response={
                "deleted": True,
                "organization_id": organization_id,
                "assignment_id": assignment_id,
            },
            cache_invalidation=cache_invalidation,
        )

    async def require_assignment_for_org(
        self,
        *,
        organization_id: str,
        assignment_id: str,
    ) -> OrganizationTierAssignmentRecord:
        organization_id = self._normalize_organization_id(organization_id)
        assignment_id = self._normalize_assignment_id(assignment_id)
        await self._require_organization(organization_id)
        record = await self.repository.get_org_assignment(assignment_id)
        if record is None or record.organization_id != organization_id:
            raise TierAdminNotFoundError("Tier assignment not found")
        return record

    async def require_organization(self, organization_id: str) -> str:
        organization_id = self._normalize_organization_id(organization_id)
        await self._require_organization(organization_id)
        return organization_id

    async def _require_organization(self, organization_id: str) -> None:
        exists = await self.repository.organization_exists_for_tier_assignment(organization_id)
        if not exists:
            raise TierAdminNotFoundError("Organization not found")

    def _transaction(self) -> Any:
        if not bool(getattr(self.repository, "supports_transactions", lambda: False)()):
            raise TierAdminUnavailableError("Tier assignment mutation requires transaction support")
        prisma = getattr(self.repository, "prisma", None)
        if prisma is None or not hasattr(prisma, "tx"):
            raise TierAdminUnavailableError("Tier assignment mutation requires transaction support")
        return prisma.tx()

    def _repository_for_transaction(self, tx: Any) -> Any:
        with_db = getattr(self.repository, "with_db", None)
        if with_db is None:
            raise TierAdminUnavailableError("Tier assignment mutation requires transaction support")
        return with_db(tx)

    @staticmethod
    def _normalize_organization_id(organization_id: str) -> str:
        normalized = str(organization_id or "").strip()
        if not normalized:
            raise TierAdminValidationError("organization_id is required")
        return normalized

    @staticmethod
    def _normalize_assignment_id(assignment_id: str) -> str:
        normalized = str(assignment_id or "").strip()
        if not normalized:
            raise TierAdminValidationError("assignment_id is required")
        return normalized


def _assignment_admin_error(exc: ValueError) -> TierAdminError:
    detail = str(exc)
    lowered = detail.lower()
    if "one active primary" in lowered:
        return TierAdminConflictError(detail)
    if "active tier version" in lowered:
        return TierAdminConflictError(detail)
    if "existing tier" in lowered or "existing tier version" in lowered:
        return TierAdminNotFoundError(detail)
    return TierAdminValidationError(detail)


def _assignment_storage_error(exc: Exception) -> TierAdminError | None:
    message = str(exc).lower()
    if (
        "deltallm_orgtierassignment_primary_no_overlap" in message
        or "exclusion constraint" in message
    ):
        return TierAdminConflictError(
            "organization can only have one active primary tier assignment"
        )
    if "foreign key" in message or "foreign_key_violation" in message:
        if "organization" in message:
            return TierAdminNotFoundError("Organization not found")
        if "tier_version" in message or "version_matches_tier" in message:
            return TierAdminValidationError(
                "tier_version_id must reference an existing tier version for tier_id"
            )
        if "tier" in message:
            return TierAdminNotFoundError("tier_id must reference an existing tier")
    return None


__all__ = [
    "TierAssignmentCreateResult",
    "TierAssignmentAdminService",
    "TierAssignmentDeleteResult",
    "TierAssignmentUpdateResult",
    "serialize_tier_assignment",
]
