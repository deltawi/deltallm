from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION = 2


class OrganizationLifecycleState(StrEnum):
    ACTIVE = "active"
    DELETION_PENDING = "deletion_pending"
    PURGING = "purging"
    DELETION_FAILED = "deletion_failed"


def parse_organization_lifecycle_state(value: object) -> OrganizationLifecycleState | None:
    try:
        return OrganizationLifecycleState(str(value or "").strip().lower())
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class TeamOrganizationLifecycle:
    organization_id: str | None
    lifecycle_state: str


__all__ = [
    "ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION",
    "OrganizationLifecycleState",
    "TeamOrganizationLifecycle",
    "parse_organization_lifecycle_state",
]
