from __future__ import annotations

from dataclasses import dataclass

from src.db.organization_deletion_records import (
    OrganizationDeletionJobRecord,
    OrganizationDeletionPlanRecord,
)


class OrganizationDeletionError(RuntimeError):
    code = "organization_deletion_error"


class OrganizationDeletionNotFoundError(OrganizationDeletionError):
    code = "organization_not_found"


class OrganizationDeletionValidationError(OrganizationDeletionError):
    code = "organization_deletion_invalid"


class OrganizationDeletionConflictError(OrganizationDeletionError):
    def __init__(self, message: str, *, code: str = "organization_deletion_conflict") -> None:
        self.code = code
        super().__init__(message)


class OrganizationDeletionUnavailableError(OrganizationDeletionError):
    code = "organization_deletion_unavailable"


class OrganizationDeletionRequestsDisabledError(OrganizationDeletionUnavailableError):
    code = "organization_deletion_requests_disabled"


@dataclass(frozen=True)
class OrganizationDeletionPlan:
    record: OrganizationDeletionPlanRecord
    plan_token: str
    recovery_window_hours: int
    requests_enabled: bool = False

    @property
    def can_request(self) -> bool:
        return (
            self.requests_enabled
            and self.record.lifecycle_state == "active"
            and not self.record.counts.has_blocking_dependencies
        )


@dataclass(frozen=True)
class OrganizationDeletionMutationResult:
    job: OrganizationDeletionJobRecord
    immediate_invalidation_succeeded: bool


__all__ = [
    "OrganizationDeletionConflictError",
    "OrganizationDeletionError",
    "OrganizationDeletionMutationResult",
    "OrganizationDeletionNotFoundError",
    "OrganizationDeletionPlan",
    "OrganizationDeletionRequestsDisabledError",
    "OrganizationDeletionUnavailableError",
    "OrganizationDeletionValidationError",
]
