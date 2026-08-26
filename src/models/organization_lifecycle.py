from __future__ import annotations

from dataclasses import dataclass


ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION = 2


@dataclass(frozen=True, slots=True)
class TeamOrganizationLifecycle:
    organization_id: str | None
    lifecycle_state: str


__all__ = ["ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION", "TeamOrganizationLifecycle"]
