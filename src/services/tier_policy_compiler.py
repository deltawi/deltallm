from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from src.db.tiers import (
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    TierPolicyAssignmentRecord,
    TierPolicyLoadResult,
)
from src.services.tier_policy_builders import (
    compile_capacity_pools,
    compile_model_policy,
    compile_pricing_policies,
    compile_rate_limit_descriptors,
)
from src.services.tier_policy_models import (
    CompiledTierCapacityPoolMember,
    CompiledTierModelPolicy,
    CompiledTierPricingPolicy,
    CompiledTierRateLimitDescriptor,
    TierPolicySnapshot,
)

_ASSIGNMENT_RANK = {"primary": 1, "addon": 2, "override": 3}


def compile_tier_policy_snapshot(
    inputs: TierPolicyLoadResult,
    *,
    generated_at: datetime | None = None,
    reference_time: datetime | None = None,
) -> TierPolicySnapshot:
    now = _utc(reference_time or generated_at or datetime.now(tz=UTC))
    generated_at = _utc(generated_at or now)
    next_transition_at = _next_transition_at(
        inputs.assignments,
        now,
        provided=inputs.next_transition_at,
    )

    assignments = tuple(
        assignment
        for assignment in inputs.assignments
        if _assignment_is_active(assignment, now)
    )
    active_version_ids = {
        assignment.effective_tier_version_id
        for assignment in assignments
        if assignment.effective_tier_version_id
    }
    model_policies = tuple(
        policy
        for policy in inputs.model_policies
        if policy.tier_version_id in active_version_ids and policy.enabled and policy.callable_key
    )
    capacity_pools = tuple(
        pool
        for pool in inputs.capacity_pools
        if pool.tier_version_id in active_version_ids and pool.pool_key and pool.callable_key
    )

    policies_by_version: dict[str, list[TierModelPolicyRecord]] = defaultdict(list)
    for policy in model_policies:
        policies_by_version[policy.tier_version_id].append(policy)

    org_tier_keys: dict[str, set[str]] = defaultdict(set)
    explicit_orgs: set[str] = set()
    allow_candidates: dict[tuple[str, str], CompiledTierModelPolicy] = {}
    deny_candidates: dict[tuple[str, str], CompiledTierModelPolicy] = {}

    for assignment in assignments:
        organization_id = assignment.organization_id
        if not organization_id:
            continue
        explicit_orgs.add(organization_id)
        if assignment.tier_key:
            org_tier_keys[organization_id].add(assignment.tier_key)

        for policy in policies_by_version.get(assignment.effective_tier_version_id, ()):
            compiled = compile_model_policy(assignment, policy)
            key = (compiled.organization_id, compiled.callable_key)
            if compiled.access_mode == "deny":
                _set_if_better(deny_candidates, key, compiled)
            else:
                _set_if_better(allow_candidates, key, compiled)

    org_model_policy: dict[tuple[str, str], CompiledTierModelPolicy] = {}
    org_allowed_callable_keys: dict[str, set[str]] = {
        organization_id: set() for organization_id in explicit_orgs
    }
    pricing_policies: dict[tuple[str, str, str], CompiledTierPricingPolicy] = {}
    rate_limit_descriptors: dict[tuple[str, str], tuple[CompiledTierRateLimitDescriptor, ...]] = {}

    for key in sorted(set(allow_candidates) | set(deny_candidates)):
        deny_policy = deny_candidates.get(key)
        if deny_policy is not None:
            org_model_policy[key] = deny_policy
            continue

        allow_policy = allow_candidates[key]
        org_model_policy[key] = allow_policy
        org_allowed_callable_keys.setdefault(allow_policy.organization_id, set()).add(
            allow_policy.callable_key
        )
        pricing_policies.update(compile_pricing_policies(allow_policy))
        descriptors = compile_rate_limit_descriptors(allow_policy)
        if descriptors:
            rate_limit_descriptors[key] = descriptors

    capacity_pool_policy = compile_capacity_pools(capacity_pools)
    capacity_pool_members = _compile_capacity_pool_members(
        org_model_policy,
        capacity_pool_policy,
    )

    return TierPolicySnapshot(
        etag=_snapshot_etag(assignments, model_policies, capacity_pools),
        generated_at=generated_at,
        next_transition_at=next_transition_at,
        org_allowed_callable_keys=MappingProxyType(
            {
                org_id: frozenset(callable_keys)
                for org_id, callable_keys in org_allowed_callable_keys.items()
            }
        ),
        org_model_policy=MappingProxyType(org_model_policy),
        pricing_policies=MappingProxyType(pricing_policies),
        rate_limit_descriptors=MappingProxyType(rate_limit_descriptors),
        capacity_pool_policy=MappingProxyType(capacity_pool_policy),
        capacity_pool_members=MappingProxyType(capacity_pool_members),
        org_tier_keys=MappingProxyType(
            {
                org_id: tuple(sorted(tier_keys))
                for org_id, tier_keys in org_tier_keys.items()
            }
        ),
        org_has_explicit_tier_policy=frozenset(explicit_orgs),
        assignment_count=len(assignments),
        model_policy_count=len(model_policies),
        capacity_pool_count=len(capacity_pools),
    )


def _compile_capacity_pool_members(
    org_model_policy: Mapping[tuple[str, str], CompiledTierModelPolicy],
    capacity_pool_policy: Mapping[tuple[str, str], Any],
) -> dict[tuple[str, str], tuple[CompiledTierCapacityPoolMember, ...]]:
    members: dict[tuple[str, str], dict[str, CompiledTierCapacityPoolMember]] = defaultdict(dict)
    for (_organization_id, callable_key), policy in org_model_policy.items():
        if policy.access_mode == "deny" or not policy.capacity_pool_key:
            continue
        pool_ref = (policy.capacity_pool_key, callable_key)
        if pool_ref not in capacity_pool_policy:
            continue
        source = policy.source
        existing = members[pool_ref].get(policy.organization_id)
        candidate = CompiledTierCapacityPoolMember(
            pool_key=policy.capacity_pool_key,
            callable_key=callable_key,
            organization_id=policy.organization_id,
            tier_key=source.tier_key,
            assignment_weight=max(1, int(source.assignment_weight or 1)),
        )
        if existing is None or candidate.assignment_weight > existing.assignment_weight:
            members[pool_ref][policy.organization_id] = candidate

    return {
        pool_ref: tuple(sorted(pool_members.values(), key=lambda item: item.organization_id))
        for pool_ref, pool_members in members.items()
    }


def _set_if_better(
    candidates: dict[tuple[str, str], CompiledTierModelPolicy],
    key: tuple[str, str],
    policy: CompiledTierModelPolicy,
) -> None:
    existing = candidates.get(key)
    if existing is None or _policy_rank(policy) > _policy_rank(existing):
        candidates[key] = policy


def _policy_rank(policy: CompiledTierModelPolicy) -> tuple[int, int, int, int, str, str]:
    source = policy.source
    return (
        _ASSIGNMENT_RANK.get(source.assignment_type, 0),
        int(source.assignment_weight or 0),
        int(source.model_policy_priority or 0),
        int(source.tier_version_number or 0),
        source.assignment_id,
        source.model_policy_id or "",
    )


def _assignment_is_active(assignment: TierPolicyAssignmentRecord, now: datetime) -> bool:
    if not assignment.enabled or not assignment.organization_id or not assignment.effective_tier_version_id:
        return False
    if str(assignment.tier_version_status or "").strip().lower() != "active":
        return False
    starts_at = _utc_or_none(assignment.starts_at)
    ends_at = _utc_or_none(assignment.ends_at)
    if starts_at is not None and starts_at > now:
        return False
    return not (ends_at is not None and ends_at <= now)


def _next_transition_at(
    assignments: Sequence[TierPolicyAssignmentRecord],
    now: datetime,
    *,
    provided: datetime | None,
) -> datetime | None:
    candidates: list[datetime] = []
    provided_at = _utc_or_none(provided)
    if provided_at is not None and provided_at >= now:
        candidates.append(provided_at)

    for assignment in assignments:
        if not assignment.enabled or not assignment.organization_id or not assignment.effective_tier_version_id:
            continue
        if str(assignment.tier_version_status or "").strip().lower() != "active":
            continue

        starts_at = _utc_or_none(assignment.starts_at)
        ends_at = _utc_or_none(assignment.ends_at)
        if starts_at is not None and starts_at > now:
            candidates.append(starts_at)
            continue
        if ends_at is not None and ends_at > now:
            candidates.append(ends_at)

    return min(candidates) if candidates else None


def _snapshot_etag(
    assignments: Sequence[TierPolicyAssignmentRecord],
    policies: Sequence[TierModelPolicyRecord],
    pools: Sequence[TierCapacityPoolRecord],
) -> str:
    payload = {
        "assignments": [_record_dict(item) for item in assignments],
        "model_policies": [_record_dict(item) for item in policies],
        "capacity_pools": [_record_dict(item) for item in pools],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _record_dict(record: Any) -> dict[str, Any]:
    if is_dataclass(record):
        return {
            field.name: _etag_value(getattr(record, field.name))
            for field in fields(record)
        }
    payload: dict[str, Any] = {}
    for key, value in getattr(record, "__dict__", {}).items():
        payload[key] = _etag_value(value)
    return payload


def _etag_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _etag_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_etag_value(item) for item in value]
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_or_none(value: datetime | None) -> datetime | None:
    return _utc(value) if value is not None else None
