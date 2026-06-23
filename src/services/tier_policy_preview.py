from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from src.services.tier_assignment_admin_serialization import serialize_tier_assignment
from src.services.tier_policy_models import (
    CompiledTierCapacityPoolPolicy,
    CompiledTierModelPolicy,
    CompiledTierPricingPolicy,
    CompiledTierRateLimitDescriptor,
    TierPolicySnapshot,
)
from src.tier_rate_limit_policy import select_tier_rate_limit_descriptors

_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")


class TierPolicyPreviewError(RuntimeError):
    detail: str

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class TierPolicyPreviewUnavailableError(TierPolicyPreviewError):
    pass


def build_tier_policy_preview(
    *,
    organization_id: str,
    tier_policy_service: Any,
    assignments: list[Any] | tuple[Any, ...] = (),
) -> dict[str, Any]:
    service = _require_tier_policy_service(tier_policy_service)
    snapshot = _get_snapshot(service)
    info = _snapshot_info(service, snapshot)
    normalized_org_id = _normalize_id(organization_id)
    explicit_policy = normalized_org_id in snapshot.org_has_explicit_tier_policy

    effective_model_policies = [
        policy
        for (org_id, _), policy in sorted(snapshot.org_model_policy.items())
        if org_id == normalized_org_id
    ]
    model_policies = [
        _serialize_model_policy(policy)
        for policy in effective_model_policies
    ]
    pricing_policies = [
        _serialize_pricing_policy(policy)
        for (org_id, _, _), policy in sorted(snapshot.pricing_policies.items())
        if org_id == normalized_org_id
    ]
    rate_limits = [
        _serialize_rate_limit_descriptor(descriptor)
        for (org_id, _), descriptors in sorted(snapshot.rate_limit_descriptors.items())
        if org_id == normalized_org_id
        for descriptor in descriptors
    ]
    capacity_pools = [
        _serialize_capacity_pool(policy)
        for policy in _capacity_pools_for_effective_policies(
            snapshot,
            effective_model_policies,
        )
    ]

    return {
        "organization_id": normalized_org_id,
        "snapshot": info,
        "explicit_policy": explicit_policy,
        "tier_keys": list(snapshot.org_tier_keys.get(normalized_org_id, ())),
        "assignments": [_serialize_assignment(assignment) for assignment in assignments],
        "allowed_callable_keys": sorted(
            snapshot.org_allowed_callable_keys.get(normalized_org_id, frozenset())
        ),
        "model_policies": model_policies,
        "pricing_policies": pricing_policies,
        "rate_limits": rate_limits,
        "capacity_pools": capacity_pools,
    }


def simulate_tier_policy_request(
    *,
    organization_id: str,
    callable_key: str,
    tier_policy_service: Any,
    mode: str = "sync",
    request_count: int = 1,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> dict[str, Any]:
    service = _require_tier_policy_service(tier_policy_service)
    snapshot = _get_snapshot(service)
    normalized_org_id = _normalize_id(organization_id)
    normalized_callable_key = _normalize_id(callable_key)
    normalized_mode = _normalize_mode(mode)
    request_count = _non_negative_int(request_count, field_name="request_count", minimum=1)
    prompt_tokens = _non_negative_int(prompt_tokens, field_name="prompt_tokens")
    completion_tokens = _non_negative_int(
        completion_tokens,
        field_name="completion_tokens",
    )
    tokens_per_request = prompt_tokens + completion_tokens
    aggregate_tokens = request_count * tokens_per_request

    explicit_policy = normalized_org_id in snapshot.org_has_explicit_tier_policy
    model_policy = snapshot.org_model_policy.get(
        (normalized_org_id, normalized_callable_key)
    )
    allowed_keys = snapshot.org_allowed_callable_keys.get(normalized_org_id, frozenset())
    allowed = not explicit_policy or normalized_callable_key in allowed_keys
    reason = _access_reason(
        explicit_policy=explicit_policy,
        model_policy=model_policy,
        callable_key=normalized_callable_key,
        allowed=allowed,
    )
    pricing = _pricing_for_mode(
        snapshot,
        organization_id=normalized_org_id,
        callable_key=normalized_callable_key,
        mode=normalized_mode,
    )
    rate_limits = _descriptors_for_mode(
        snapshot.rate_limit_descriptors.get(
            (normalized_org_id, normalized_callable_key),
            (),
        ),
        mode=normalized_mode,
    )
    pool_policy = _capacity_pool_for_allowed_policy(
        snapshot,
        model_policy=model_policy,
        callable_key=normalized_callable_key,
    )
    pool_rate_limits = _descriptors_for_mode(
        pool_policy.rate_limit_descriptors if pool_policy is not None else (),
        mode=normalized_mode,
    )
    checks = _simulate_static_limit_checks(
        tuple(rate_limits) + tuple(pool_rate_limits),
        request_count=request_count,
        tokens_per_request=tokens_per_request,
    )

    return {
        "organization_id": normalized_org_id,
        "callable_key": normalized_callable_key,
        "mode": normalized_mode,
        "request": {
            "request_count": request_count,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "tokens_per_request": tokens_per_request,
            "aggregate_tokens": aggregate_tokens,
        },
        "access": {
            "allowed": allowed,
            "reason": reason,
            "explicit_policy": explicit_policy,
            "tier_keys": list(snapshot.org_tier_keys.get(normalized_org_id, ())),
        },
        "model_policy": (
            _serialize_model_policy(model_policy) if model_policy is not None else None
        ),
        "pricing": _serialize_pricing_policy(pricing) if pricing is not None else None,
        "rate_limits": [_serialize_rate_limit_descriptor(item) for item in rate_limits],
        "capacity_pool": (
            _serialize_capacity_pool(pool_policy) if pool_policy is not None else None
        ),
        "capacity_pool_rate_limits": [
            _serialize_rate_limit_descriptor(item) for item in pool_rate_limits
        ],
        "static_limit_checks": checks,
        "snapshot": _snapshot_info(service, snapshot),
    }


def _require_tier_policy_service(service: Any) -> Any:
    if service is None:
        raise TierPolicyPreviewUnavailableError("Tier policy service unavailable")
    if not callable(getattr(service, "get_snapshot", None)):
        raise TierPolicyPreviewUnavailableError("Tier policy snapshot unavailable")
    return service


def _get_snapshot(service: Any) -> TierPolicySnapshot:
    snapshot = service.get_snapshot()
    if not isinstance(snapshot, TierPolicySnapshot):
        raise TierPolicyPreviewUnavailableError("Tier policy snapshot unavailable")
    return snapshot


def _snapshot_info(service: Any, snapshot: TierPolicySnapshot) -> dict[str, Any]:
    info_getter = getattr(service, "snapshot_info", None)
    info = info_getter() if callable(info_getter) else None
    if info is None:
        return {
            "etag": snapshot.etag,
            "generated_at": _json_value(snapshot.generated_at),
            "org_count": snapshot.org_count,
            "assignment_count": snapshot.assignment_count,
            "model_policy_count": snapshot.model_policy_count,
            "capacity_pool_count": snapshot.capacity_pool_count,
            "next_transition_at": _json_value(snapshot.next_transition_at),
            "mode": str(getattr(service, "mode", "disabled")),
            "snapshot_stale": bool(getattr(service, "snapshot_stale", False)),
            "last_reload_failed": bool(getattr(service, "last_reload_failed", False)),
            "last_reload_error_at": _json_value(
                getattr(service, "last_reload_error_at", None)
            ),
        }
    return _json_value(info)


def _serialize_model_policy(policy: CompiledTierModelPolicy) -> dict[str, Any]:
    return {
        "organization_id": policy.organization_id,
        "callable_key": policy.callable_key,
        "access_mode": policy.access_mode,
        "source": _json_value(policy.source),
        "limits": _json_value(policy.limits),
        "pricing": _json_value(policy.pricing),
        "capacity_pool_key": policy.capacity_pool_key,
        "metadata": _json_value(policy.metadata),
    }


def _serialize_pricing_policy(policy: CompiledTierPricingPolicy) -> dict[str, Any]:
    return {
        "organization_id": policy.organization_id,
        "callable_key": policy.callable_key,
        "mode": policy.mode,
        "pricing": _json_value(policy.pricing),
        "source": _json_value(policy.source),
    }


def _serialize_rate_limit_descriptor(
    descriptor: CompiledTierRateLimitDescriptor,
) -> dict[str, Any]:
    return {
        "scope": descriptor.scope,
        "entity_id": descriptor.entity_id,
        "limit": descriptor.limit,
        "amount_kind": descriptor.amount_kind,
        "window_seconds": descriptor.window_seconds,
        "mode": descriptor.mode,
    }


def _serialize_capacity_pool(
    policy: CompiledTierCapacityPoolPolicy,
) -> dict[str, Any]:
    return {
        "pool_key": policy.pool_key,
        "callable_key": policy.callable_key,
        "rpm_capacity": policy.rpm_capacity,
        "tpm_capacity": policy.tpm_capacity,
        "max_parallel_requests": policy.max_parallel_requests,
        "strategy": policy.strategy,
        "saturation_threshold": policy.saturation_threshold,
        "burst_multiplier": policy.burst_multiplier,
        "source_tier_version_ids": list(policy.source_tier_version_ids),
        "source_pool_ids": list(policy.source_pool_ids),
        "metadata": _json_value(policy.metadata),
        "rate_limit_descriptors": [
            _serialize_rate_limit_descriptor(descriptor)
            for descriptor in policy.rate_limit_descriptors
        ],
    }


def _serialize_assignment(assignment: Any) -> dict[str, Any]:
    if isinstance(assignment, Mapping):
        return _json_value(assignment)
    return serialize_tier_assignment(assignment)


def _pricing_for_mode(
    snapshot: TierPolicySnapshot,
    *,
    organization_id: str,
    callable_key: str,
    mode: str,
) -> CompiledTierPricingPolicy | None:
    if (organization_id, callable_key, mode) in snapshot.pricing_policies:
        return snapshot.pricing_policies[(organization_id, callable_key, mode)]
    if mode != "sync":
        return snapshot.pricing_policies.get((organization_id, callable_key, "sync"))
    return None


def _descriptors_for_mode(
    descriptors: tuple[CompiledTierRateLimitDescriptor, ...],
    *,
    mode: str,
) -> tuple[CompiledTierRateLimitDescriptor, ...]:
    return select_tier_rate_limit_descriptors(
        descriptors,
        request_mode="batch" if mode == "batch" else "sync",
    )


def _capacity_pool_for_allowed_policy(
    snapshot: TierPolicySnapshot,
    *,
    model_policy: CompiledTierModelPolicy | None,
    callable_key: str,
) -> CompiledTierCapacityPoolPolicy | None:
    if model_policy is None or not model_policy.capacity_pool_key:
        return None
    if str(model_policy.access_mode or "").strip().lower() == "deny":
        return None
    return snapshot.capacity_pool_policy.get((model_policy.capacity_pool_key, callable_key))


def _capacity_pools_for_effective_policies(
    snapshot: TierPolicySnapshot,
    policies: list[CompiledTierModelPolicy],
) -> list[CompiledTierCapacityPoolPolicy]:
    seen: set[tuple[str, str]] = set()
    pools: list[CompiledTierCapacityPoolPolicy] = []
    for policy in policies:
        pool = _capacity_pool_for_allowed_policy(
            snapshot,
            model_policy=policy,
            callable_key=policy.callable_key,
        )
        if pool is None:
            continue
        ref = (pool.pool_key, pool.callable_key)
        if ref in seen:
            continue
        seen.add(ref)
        pools.append(pool)
    return sorted(pools, key=lambda item: (item.pool_key, item.callable_key))


def _simulate_static_limit_checks(
    descriptors: tuple[CompiledTierRateLimitDescriptor, ...],
    *,
    request_count: int,
    tokens_per_request: int,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    total_tokens = request_count * tokens_per_request
    for descriptor in descriptors:
        amount = request_count if descriptor.amount_kind == "requests" else total_tokens
        checks.append(
            {
                **_serialize_rate_limit_descriptor(descriptor),
                "amount": amount,
                "would_exceed_limit": amount > descriptor.limit,
                "remaining_after_amount": descriptor.limit - amount,
            }
        )
    return checks


def _access_reason(
    *,
    explicit_policy: bool,
    model_policy: CompiledTierModelPolicy | None,
    callable_key: str,
    allowed: bool,
) -> str:
    if not explicit_policy:
        return "no_explicit_tier_policy"
    if model_policy is None:
        return "callable_not_in_tier_policy"
    if model_policy.access_mode == "deny":
        return "tier_policy_denied"
    if allowed:
        return "tier_policy_allowed"
    return f"{callable_key}_not_allowed"


def _normalize_id(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise TierPolicyPreviewError("identifier is required")
    return normalized


def _normalize_mode(value: str | None) -> str:
    normalized = str(value or "sync").strip().lower()
    if not normalized:
        return "sync"
    if normalized not in {"sync", "batch"}:
        raise TierPolicyPreviewError("mode must be sync or batch")
    return normalized


def _non_negative_int(
    value: Any,
    *,
    field_name: str,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool):
        raise TierPolicyPreviewError(f"{field_name} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not _INTEGER_PATTERN.fullmatch(stripped):
            raise TierPolicyPreviewError(f"{field_name} must be an integer")
        parsed = int(stripped)
    else:
        raise TierPolicyPreviewError(f"{field_name} must be an integer")
    if parsed < minimum:
        raise TierPolicyPreviewError(f"{field_name} must be >= {minimum}")
    return parsed


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


__all__ = [
    "TierPolicyPreviewError",
    "TierPolicyPreviewUnavailableError",
    "build_tier_policy_preview",
    "simulate_tier_policy_request",
]
