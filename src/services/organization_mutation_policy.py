from __future__ import annotations

from src.db.organization_mutation_guard import (
    OrganizationMutationGuardDatabase,
    OrganizationMutationGuardRepository,
)
from src.models.organization_lifecycle import OrganizationLifecycleState


class OrganizationMutationError(RuntimeError):
    code = "organization_mutation_error"


class OrganizationMutationNotFoundError(OrganizationMutationError):
    code = "organization_not_found"


class OrganizationMutationUnavailableError(OrganizationMutationError):
    code = "organization_lifecycle_unavailable"


class OrganizationMutationInactiveError(OrganizationMutationError):
    code = "organization_inactive"

    def __init__(
        self,
        *,
        organization_id: str,
        lifecycle_state: OrganizationLifecycleState,
    ) -> None:
        self.organization_id = organization_id
        self.lifecycle_state = lifecycle_state
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"organization {self.organization_id} is {self.lifecycle_state.value}"

    def detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": "Organization administrative changes are disabled",
            "lifecycle_state": self.lifecycle_state.value,
        }


class OrganizationMutationPolicy:
    """Authoritative policy for human/admin organization-scoped mutations."""

    def __init__(self, repository: OrganizationMutationGuardRepository) -> None:
        self.repository = repository

    @classmethod
    def for_database(
        cls,
        db: OrganizationMutationGuardDatabase,
    ) -> OrganizationMutationPolicy:
        return cls(OrganizationMutationGuardRepository(db))

    async def require_active(self, organization_id: str) -> None:
        normalized_id = str(organization_id or "").strip()
        if not normalized_id:
            raise OrganizationMutationNotFoundError("Organization not found")
        try:
            snapshot = await self.repository.lock_organization(normalized_id)
        except ValueError as exc:
            raise OrganizationMutationUnavailableError(
                "Organization lifecycle state is invalid"
            ) from exc
        except OrganizationMutationError:
            raise
        except Exception as exc:
            raise OrganizationMutationUnavailableError(
                "Organization lifecycle state could not be checked"
            ) from exc
        if snapshot is None:
            raise OrganizationMutationNotFoundError("Organization not found")
        if snapshot.lifecycle_state is not OrganizationLifecycleState.ACTIVE:
            raise OrganizationMutationInactiveError(
                organization_id=snapshot.organization_id,
                lifecycle_state=snapshot.lifecycle_state,
            )


__all__ = [
    "OrganizationMutationError",
    "OrganizationMutationInactiveError",
    "OrganizationMutationNotFoundError",
    "OrganizationMutationPolicy",
    "OrganizationMutationUnavailableError",
]
