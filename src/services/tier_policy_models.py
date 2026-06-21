from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping


FrozenMetadata = Mapping[str, Any]
FrozenPricing = Mapping[str, float]


@dataclass(frozen=True, slots=True)
class TierPolicySource:
    assignment_id: str
    organization_id: str
    tier_id: str
    tier_key: str | None
    tier_version_id: str
    tier_version_number: int | None
    assignment_type: str
    assignment_weight: int
    model_policy_id: str | None = None
    model_policy_priority: int = 0


@dataclass(frozen=True, slots=True)
class TierPolicyLimits:
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    rph_limit: int | None = None
    rpd_limit: int | None = None
    tpd_limit: int | None = None
    max_parallel_requests: int | None = None
    batch_rpm_limit: int | None = None
    batch_tpm_limit: int | None = None


@dataclass(frozen=True, slots=True)
class CompiledTierModelPolicy:
    organization_id: str
    callable_key: str
    access_mode: str
    source: TierPolicySource
    limits: TierPolicyLimits
    pricing: FrozenPricing
    capacity_pool_key: str | None = None
    metadata: FrozenMetadata | None = None


@dataclass(frozen=True, slots=True)
class CompiledTierPricingPolicy:
    organization_id: str
    callable_key: str
    mode: str
    pricing: FrozenPricing
    source: TierPolicySource


@dataclass(frozen=True, slots=True)
class CompiledTierRateLimitDescriptor:
    scope: str
    entity_id: str
    limit: int
    amount_kind: str
    window_seconds: int
    mode: str = "sync"


@dataclass(frozen=True, slots=True)
class CompiledTierCapacityPoolPolicy:
    pool_key: str
    callable_key: str
    rpm_capacity: int | None
    tpm_capacity: int | None
    max_parallel_requests: int | None
    strategy: str
    saturation_threshold: float | None
    burst_multiplier: float | None
    source_tier_version_ids: tuple[str, ...]
    source_pool_ids: tuple[str, ...]
    metadata: FrozenMetadata | None = None
    rate_limit_descriptors: tuple[CompiledTierRateLimitDescriptor, ...] = ()


@dataclass(frozen=True, slots=True)
class TierPolicySnapshot:
    etag: str
    generated_at: datetime
    next_transition_at: datetime | None
    org_allowed_callable_keys: Mapping[str, frozenset[str]]
    org_model_policy: Mapping[tuple[str, str], CompiledTierModelPolicy]
    pricing_policies: Mapping[tuple[str, str, str], CompiledTierPricingPolicy]
    rate_limit_descriptors: Mapping[tuple[str, str], tuple[CompiledTierRateLimitDescriptor, ...]]
    capacity_pool_policy: Mapping[tuple[str, str], CompiledTierCapacityPoolPolicy]
    org_tier_keys: Mapping[str, tuple[str, ...]]
    org_has_explicit_tier_policy: frozenset[str]
    assignment_count: int = 0
    model_policy_count: int = 0
    capacity_pool_count: int = 0

    @property
    def org_count(self) -> int:
        return len(self.org_has_explicit_tier_policy)


def empty_tier_policy_snapshot(*, generated_at: datetime | None = None) -> TierPolicySnapshot:
    return TierPolicySnapshot(
        etag="empty",
        generated_at=generated_at or datetime.now(tz=UTC),
        next_transition_at=None,
        org_allowed_callable_keys=MappingProxyType({}),
        org_model_policy=MappingProxyType({}),
        pricing_policies=MappingProxyType({}),
        rate_limit_descriptors=MappingProxyType({}),
        capacity_pool_policy=MappingProxyType({}),
        org_tier_keys=MappingProxyType({}),
        org_has_explicit_tier_policy=frozenset(),
    )
