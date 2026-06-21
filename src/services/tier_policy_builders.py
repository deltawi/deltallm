from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping, Sequence

from src.db.tiers import (
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    TierPolicyAssignmentRecord,
)
from src.services.tier_policy_models import (
    CompiledTierCapacityPoolPolicy,
    CompiledTierModelPolicy,
    CompiledTierPricingPolicy,
    CompiledTierRateLimitDescriptor,
    TierPolicyLimits,
    TierPolicySource,
)

_BATCH_PRICING_PREFIX = "batch_"
_CAPACITY_STRATEGY_RANK = {"hard_cap": 1, "weighted_fair": 2, "reserved_burst": 3}


def compile_model_policy(
    assignment: TierPolicyAssignmentRecord,
    policy: TierModelPolicyRecord,
) -> CompiledTierModelPolicy:
    return CompiledTierModelPolicy(
        organization_id=assignment.organization_id,
        callable_key=policy.callable_key,
        access_mode=str(policy.access_mode or "allow").strip().lower(),
        source=TierPolicySource(
            assignment_id=assignment.assignment_id,
            organization_id=assignment.organization_id,
            tier_id=assignment.tier_id,
            tier_key=assignment.tier_key,
            tier_version_id=assignment.effective_tier_version_id,
            tier_version_number=assignment.tier_version_number,
            assignment_type=str(assignment.assignment_type or "primary").strip().lower(),
            assignment_weight=int(assignment.weight or 1),
            model_policy_id=policy.tier_model_policy_id,
            model_policy_priority=int(policy.priority or 0),
        ),
        limits=TierPolicyLimits(
            rpm_limit=policy.rpm_limit,
            tpm_limit=policy.tpm_limit,
            rph_limit=policy.rph_limit,
            rpd_limit=policy.rpd_limit,
            tpd_limit=policy.tpd_limit,
            max_parallel_requests=policy.max_parallel_requests,
            batch_rpm_limit=policy.batch_rpm_limit,
            batch_tpm_limit=policy.batch_tpm_limit,
        ),
        pricing=_freeze_pricing(policy.pricing),
        capacity_pool_key=policy.capacity_pool_key,
        metadata=_freeze_metadata(policy.metadata),
    )


def compile_pricing_policies(
    policy: CompiledTierModelPolicy,
) -> dict[tuple[str, str, str], CompiledTierPricingPolicy]:
    if not policy.pricing:
        return {}

    compiled: dict[tuple[str, str, str], CompiledTierPricingPolicy] = {}
    sync_pricing = {
        key: value
        for key, value in policy.pricing.items()
        if not key.startswith(_BATCH_PRICING_PREFIX)
    }
    if sync_pricing:
        compiled[(policy.organization_id, policy.callable_key, "sync")] = (
            CompiledTierPricingPolicy(
                organization_id=policy.organization_id,
                callable_key=policy.callable_key,
                mode="sync",
                pricing=MappingProxyType(sync_pricing),
                source=policy.source,
            )
        )
    if any(key.startswith(_BATCH_PRICING_PREFIX) for key in policy.pricing):
        compiled[(policy.organization_id, policy.callable_key, "batch")] = (
            CompiledTierPricingPolicy(
                organization_id=policy.organization_id,
                callable_key=policy.callable_key,
                mode="batch",
                pricing=policy.pricing,
                source=policy.source,
            )
        )
    return compiled


def compile_rate_limit_descriptors(
    policy: CompiledTierModelPolicy,
) -> tuple[CompiledTierRateLimitDescriptor, ...]:
    entity_id = f"{policy.organization_id}:{policy.callable_key}"
    limits = policy.limits
    descriptors = [
        _descriptor("tier_org_model_rpm", entity_id, limits.rpm_limit, "requests", 60),
        _descriptor("tier_org_model_tpm", entity_id, limits.tpm_limit, "tokens", 60),
        _descriptor("tier_org_model_rph", entity_id, limits.rph_limit, "requests", 3600),
        _descriptor("tier_org_model_rpd", entity_id, limits.rpd_limit, "requests", 86400),
        _descriptor("tier_org_model_tpd", entity_id, limits.tpd_limit, "tokens", 86400),
        _descriptor(
            "tier_org_model_parallel",
            entity_id,
            limits.max_parallel_requests,
            "concurrency",
            0,
        ),
        _descriptor(
            "tier_org_model_batch_rpm",
            entity_id,
            limits.batch_rpm_limit,
            "requests",
            60,
            mode="batch",
        ),
        _descriptor(
            "tier_org_model_batch_tpm",
            entity_id,
            limits.batch_tpm_limit,
            "tokens",
            60,
            mode="batch",
        ),
    ]
    return tuple(descriptor for descriptor in descriptors if descriptor is not None)


def compile_capacity_pools(
    pools: Sequence[TierCapacityPoolRecord],
) -> dict[tuple[str, str], CompiledTierCapacityPoolPolicy]:
    compiled: dict[tuple[str, str], CompiledTierCapacityPoolPolicy] = {}
    for pool in sorted(
        pools,
        key=lambda item: (
            item.pool_key,
            item.callable_key,
            item.tier_version_id,
            item.tier_capacity_pool_id,
        ),
    ):
        key = (pool.pool_key, pool.callable_key)
        next_policy = CompiledTierCapacityPoolPolicy(
            pool_key=pool.pool_key,
            callable_key=pool.callable_key,
            rpm_capacity=pool.rpm_capacity,
            tpm_capacity=pool.tpm_capacity,
            max_parallel_requests=pool.max_parallel_requests,
            strategy=str(pool.strategy or "hard_cap").strip().lower(),
            saturation_threshold=pool.saturation_threshold,
            burst_multiplier=pool.burst_multiplier,
            source_tier_version_ids=(pool.tier_version_id,),
            source_pool_ids=(pool.tier_capacity_pool_id,),
            metadata=_freeze_metadata(pool.metadata),
        )
        existing = compiled.get(key)
        compiled[key] = (
            next_policy if existing is None else _merge_capacity_pool(existing, next_policy)
        )
    return compiled


def _descriptor(
    scope: str,
    entity_id: str,
    limit: int | None,
    amount_kind: str,
    window_seconds: int,
    *,
    mode: str = "sync",
) -> CompiledTierRateLimitDescriptor | None:
    if limit is None or limit <= 0:
        return None
    return CompiledTierRateLimitDescriptor(
        scope=scope,
        entity_id=entity_id,
        limit=int(limit),
        amount_kind=amount_kind,
        window_seconds=window_seconds,
        mode=mode,
    )


def _merge_capacity_pool(
    left: CompiledTierCapacityPoolPolicy,
    right: CompiledTierCapacityPoolPolicy,
) -> CompiledTierCapacityPoolPolicy:
    strategy = min(
        (left.strategy, right.strategy),
        key=lambda item: _CAPACITY_STRATEGY_RANK.get(item, 99),
    )
    return CompiledTierCapacityPoolPolicy(
        pool_key=left.pool_key,
        callable_key=left.callable_key,
        rpm_capacity=_min_optional_int(left.rpm_capacity, right.rpm_capacity),
        tpm_capacity=_min_optional_int(left.tpm_capacity, right.tpm_capacity),
        max_parallel_requests=_min_optional_int(
            left.max_parallel_requests,
            right.max_parallel_requests,
        ),
        strategy=strategy,
        saturation_threshold=_min_optional_float(
            left.saturation_threshold,
            right.saturation_threshold,
        ),
        burst_multiplier=_min_optional_float(left.burst_multiplier, right.burst_multiplier),
        source_tier_version_ids=tuple(
            sorted(set(left.source_tier_version_ids) | set(right.source_tier_version_ids))
        ),
        source_pool_ids=tuple(sorted(set(left.source_pool_ids) | set(right.source_pool_ids))),
        metadata=left.metadata or right.metadata,
    )


def _freeze_pricing(pricing: Mapping[str, Any] | None) -> Mapping[str, float]:
    if not pricing:
        return MappingProxyType({})
    return MappingProxyType({str(key): float(value) for key, value in pricing.items()})


def _freeze_metadata(metadata: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not metadata:
        return None
    return MappingProxyType({str(key): _freeze_json_value(value) for key, value in metadata.items()})


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json_value(nested) for key, nested in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _min_optional_int(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _min_optional_float(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)

