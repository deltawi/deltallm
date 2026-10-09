from __future__ import annotations

from typing import Any

from src.db.tier_assignment_repository import TierAssignmentRepositoryMixin
from src.db.tier_bootstrap_repository import (
    TierBootstrapIdempotencyConflictError,
    TierBootstrapRepositoryMixin,
    TierBootstrapResult,
)
from src.db.tier_catalog_repository import TierCatalogRepositoryMixin
from src.db.tier_configuration_repository import (
    TierCapacityPoolMutationResult,
    TierCapacityPoolPage,
    TierConfigurationChildNotFoundError,
    TierConfigurationIdentityImmutableError,
    TierConfigurationMutationError,
    TierConfigurationPoolInUseError,
    TierConfigurationPoolReferenceError,
    TierConfigurationRepositoryMixin,
    TierConfigurationStaleError,
    TierConfigurationVersionNotDraftError,
    TierConfigurationVersionNotFoundError,
    TierModelPolicyMutationResult,
    TierModelPolicyBulkMutationResult,
    TierModelPolicyPage,
)
from src.db.tier_policy_repository import (
    TierPolicyRepositoryMixin,
    TierPolicyRepositoryUnavailableError,
)
from src.db.tier_records import (
    OrganizationTierAssignmentRecord,
    TierCapacityPoolRecord,
    TierCatalogVersionSummaryRecord,
    TierCreationRequestRecord,
    TierModelPolicyRecord,
    TierPolicyAssignmentRecord,
    TierPolicyLoadResult,
    TierRecord,
    TierVersionRecord,
)
from src.db.tier_version_clone_repository import TierVersionCloneRepositoryMixin
from src.db.tier_version_repository import (
    TierActivationActiveVersionChangedError,
    TierActivationConfigurationChangedError,
    TierVersionRepositoryMixin,
)


class TierRepository(
    TierCatalogRepositoryMixin,
    TierBootstrapRepositoryMixin,
    TierVersionRepositoryMixin,
    TierVersionCloneRepositoryMixin,
    TierConfigurationRepositoryMixin,
    TierPolicyRepositoryMixin,
    TierAssignmentRepositoryMixin,
):
    def __init__(self, prisma_client: Any | None = None, *, use_transactions: bool = True) -> None:
        self.prisma = prisma_client
        self._use_transactions = use_transactions

    def with_db(self, prisma_client: Any) -> TierRepository:
        return TierRepository(prisma_client, use_transactions=False)

    def supports_transactions(self) -> bool:
        return bool(
            self._use_transactions and self.prisma is not None and hasattr(self.prisma, "tx")
        )

    def require_transactions(self, operation: str) -> None:
        if not self.supports_transactions():
            raise RuntimeError(f"{operation} requires transaction support")


__all__ = [
    "OrganizationTierAssignmentRecord",
    "TierBootstrapIdempotencyConflictError",
    "TierBootstrapResult",
    "TierActivationActiveVersionChangedError",
    "TierActivationConfigurationChangedError",
    "TierCapacityPoolRecord",
    "TierCatalogVersionSummaryRecord",
    "TierCapacityPoolMutationResult",
    "TierCapacityPoolPage",
    "TierConfigurationChildNotFoundError",
    "TierConfigurationIdentityImmutableError",
    "TierConfigurationMutationError",
    "TierConfigurationPoolInUseError",
    "TierConfigurationPoolReferenceError",
    "TierConfigurationStaleError",
    "TierConfigurationVersionNotDraftError",
    "TierConfigurationVersionNotFoundError",
    "TierCreationRequestRecord",
    "TierModelPolicyRecord",
    "TierModelPolicyMutationResult",
    "TierModelPolicyBulkMutationResult",
    "TierModelPolicyPage",
    "TierPolicyAssignmentRecord",
    "TierPolicyLoadResult",
    "TierPolicyRepositoryUnavailableError",
    "TierRecord",
    "TierRepository",
    "TierVersionRecord",
]
