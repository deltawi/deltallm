from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.db.tiers import OrganizationTierAssignmentRecord
from src.services.cache_invalidation import CacheInvalidationResult


@dataclass(frozen=True)
class TierAssignmentCreateResult:
    assignment: OrganizationTierAssignmentRecord
    cache_invalidation: CacheInvalidationResult


@dataclass(frozen=True)
class TierAssignmentUpdateResult:
    before: OrganizationTierAssignmentRecord
    assignment: OrganizationTierAssignmentRecord
    cache_invalidation: CacheInvalidationResult


@dataclass(frozen=True)
class TierAssignmentDeleteResult:
    before: OrganizationTierAssignmentRecord
    response: dict[str, Any]
    cache_invalidation: CacheInvalidationResult


__all__ = [
    "TierAssignmentCreateResult",
    "TierAssignmentDeleteResult",
    "TierAssignmentUpdateResult",
]
