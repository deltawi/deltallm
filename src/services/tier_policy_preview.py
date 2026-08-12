from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from src.billing.audio_usage import billable_transcription_duration_seconds
from src.billing.cost import (
    BillingResult,
    compute_billing_result,
)
from src.billing.tier_pricing import (
    PricingSource,
    resolve_deployment_tier_pricing,
    resolve_token_billing_result,
)
from src.providers.resolution import resolve_provider
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
_SUPPORTED_BILLING_MODES = frozenset(
    {
        "chat",
        "embedding",
        "rerank",
        "image_generation",
        "audio_speech",
        "audio_transcription",
    }
)
_TOKEN_BILLING_MODES = frozenset({"chat", "embedding", "rerank"})
_APPLICABLE_PRICING_FIELDS: dict[str, frozenset[str]] = {
    "chat": frozenset(
        {
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_token_cache_hit",
            "output_cost_per_token_cache_hit",
            "batch_input_cost_per_token",
            "batch_output_cost_per_token",
            "batch_price_multiplier",
            "cost_per_request",
        }
    ),
    "embedding": frozenset(
        {
            "input_cost_per_token",
            "batch_input_cost_per_token",
            "batch_price_multiplier",
            "cost_per_request",
        }
    ),
    "rerank": frozenset(
        {
            "input_cost_per_token",
            "batch_input_cost_per_token",
            "batch_price_multiplier",
            "cost_per_request",
        }
    ),
    "image_generation": frozenset(
        {"input_cost_per_image", "output_cost_per_image", "cost_per_request"}
    ),
    "audio_speech": frozenset(
        {
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_audio_token",
            "output_cost_per_audio_token",
            "input_cost_per_character",
            "output_cost_per_character",
            "input_cost_per_second",
            "output_cost_per_second",
            "cost_per_request",
        }
    ),
    "audio_transcription": frozenset(
        {
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_audio_token",
            "input_cost_per_second",
            "output_cost_per_second",
            "cost_per_request",
        }
    ),
}


class TierPolicyPreviewError(RuntimeError):
    detail: str

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class TierPolicyPreviewUnavailableError(TierPolicyPreviewError):
    pass


@dataclass(frozen=True, slots=True)
class _CandidateQuote:
    billing: BillingResult
    pricing_sources: tuple[PricingSource, ...] = ()


def build_tier_policy_preview(
    *,
    organization_id: str,
    tier_policy_service: Any,
    assignments: list[Any] | tuple[Any, ...] = (),
    organization_limits: Mapping[str, Any] | None = None,
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
    organization_rate_limits = _organization_rate_limit_descriptors(
        organization_id=normalized_org_id,
        organization_limits=organization_limits,
        mode="all",
    )

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
        "organization_hard_caps": _serialize_organization_limits(organization_limits),
        "organization_rate_limits": [
            _serialize_rate_limit_descriptor(descriptor)
            for descriptor in organization_rate_limits
        ],
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
    billing_mode: str | None = None,
    input_images: int = 0,
    output_images: int = 1,
    input_characters: int = 0,
    output_characters: int = 0,
    input_audio_tokens: int = 0,
    output_audio_tokens: int = 0,
    duration_seconds: float = 0,
    configured_deployments: list[Any] | tuple[Any, ...] = (),
    organization_limits: Mapping[str, Any] | None = None,
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
    normalized_billing_mode = _normalize_optional_billing_mode(billing_mode)
    deployments = tuple(configured_deployments)
    usage_billing_mode = normalized_billing_mode or _sole_deployment_billing_mode(
        deployments
    )
    if usage_billing_mode in {"embedding", "rerank"} and completion_tokens > 0:
        raise TierPolicyPreviewError(
            f"completion_tokens is not supported for {usage_billing_mode} billing"
        )
    input_images = _non_negative_int(input_images, field_name="input_images")
    output_images = _non_negative_int(output_images, field_name="output_images")
    input_characters = _non_negative_int(input_characters, field_name="input_characters")
    output_characters = _non_negative_int(output_characters, field_name="output_characters")
    input_audio_tokens = _non_negative_int(
        input_audio_tokens,
        field_name="input_audio_tokens",
    )
    output_audio_tokens = _non_negative_int(
        output_audio_tokens,
        field_name="output_audio_tokens",
    )
    duration_seconds = _non_negative_float(duration_seconds, field_name="duration_seconds")
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "input_images": input_images,
        "output_images": output_images,
        "images": output_images,
        "input_characters": input_characters,
        "output_characters": output_characters,
        "input_audio_tokens": input_audio_tokens,
        "output_audio_tokens": output_audio_tokens,
        "duration_seconds": duration_seconds,
    }
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
    organization_rate_limits = _organization_rate_limit_descriptors(
        organization_id=normalized_org_id,
        organization_limits=organization_limits,
        mode=normalized_mode,
        callable_key=normalized_callable_key,
    )
    checks = _simulate_static_limit_checks(
        tuple(rate_limits) + tuple(pool_rate_limits) + tuple(organization_rate_limits),
        request_count=request_count,
        tokens_per_request=tokens_per_request,
    )
    decision = _simulation_decision(
        access_allowed=allowed,
        access_reason=reason,
        checks=checks,
    )
    calculated_price = _calculate_price_quote(
        organization_id=normalized_org_id,
        callable_key=normalized_callable_key,
        mode=normalized_mode,
        request_count=request_count,
        requested_billing_mode=normalized_billing_mode,
        usage=usage,
        tier_policy_service=service,
        configured_deployments=deployments,
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
            "billing_mode": calculated_price["billing_mode"],
            "usage": calculated_price["usage_snapshot"],
        },
        "access": {
            "allowed": allowed,
            "reason": reason,
            "explicit_policy": explicit_policy,
            "tier_keys": list(snapshot.org_tier_keys.get(normalized_org_id, ())),
        },
        "decision": decision,
        "model_policy": (
            _serialize_model_policy(model_policy) if model_policy is not None else None
        ),
        "pricing": _serialize_pricing_policy(pricing) if pricing is not None else None,
        "calculated_price": calculated_price,
        "rate_limits": [_serialize_rate_limit_descriptor(item) for item in rate_limits],
        "organization_hard_caps": _serialize_organization_limits(organization_limits),
        "organization_rate_limits": [
            _serialize_rate_limit_descriptor(item)
            for item in organization_rate_limits
        ],
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


_ORGANIZATION_LIMIT_FIELDS = (
    "rpm_limit",
    "tpm_limit",
    "rph_limit",
    "rpd_limit",
    "tpd_limit",
    "model_rpm_limit",
    "model_tpm_limit",
)


def _serialize_organization_limits(
    limits: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(limits, Mapping):
        return {}
    return {
        field: _json_value(limits[field])
        for field in _ORGANIZATION_LIMIT_FIELDS
        if limits.get(field) is not None
    }


def _organization_rate_limit_descriptors(
    *,
    organization_id: str,
    organization_limits: Mapping[str, Any] | None,
    mode: str,
    callable_key: str | None = None,
) -> tuple[CompiledTierRateLimitDescriptor, ...]:
    if not isinstance(organization_limits, Mapping):
        return ()
    descriptors: list[CompiledTierRateLimitDescriptor] = []

    def _append(
        *,
        scope: str,
        raw_limit: Any,
        amount_kind: str,
        window_seconds: int,
        entity_id: str = organization_id,
    ) -> None:
        limit = _positive_int_or_none(raw_limit)
        if limit is None:
            return
        descriptors.append(
            CompiledTierRateLimitDescriptor(
                scope=scope,
                entity_id=entity_id,
                limit=limit,
                amount_kind=amount_kind,
                window_seconds=window_seconds,
                mode=mode,
            )
        )

    _append(
        scope="org_rpm",
        raw_limit=organization_limits.get("rpm_limit"),
        amount_kind="requests",
        window_seconds=60,
    )
    _append(
        scope="org_tpm",
        raw_limit=organization_limits.get("tpm_limit"),
        amount_kind="tokens",
        window_seconds=60,
    )
    _append(
        scope="org_rph",
        raw_limit=organization_limits.get("rph_limit"),
        amount_kind="requests",
        window_seconds=3_600,
    )
    _append(
        scope="org_rpd",
        raw_limit=organization_limits.get("rpd_limit"),
        amount_kind="requests",
        window_seconds=86_400,
    )
    _append(
        scope="org_tpd",
        raw_limit=organization_limits.get("tpd_limit"),
        amount_kind="tokens",
        window_seconds=86_400,
    )
    if callable_key:
        model_entity_id = f"{organization_id}:{callable_key}"
        _append(
            scope="org_model_rpm",
            raw_limit=_model_limit(organization_limits.get("model_rpm_limit"), callable_key),
            amount_kind="requests",
            window_seconds=60,
            entity_id=model_entity_id,
        )
        _append(
            scope="org_model_tpm",
            raw_limit=_model_limit(organization_limits.get("model_tpm_limit"), callable_key),
            amount_kind="tokens",
            window_seconds=60,
            entity_id=model_entity_id,
        )
    return tuple(descriptors)


def _model_limit(raw_limits: Any, callable_key: str) -> int | None:
    if not isinstance(raw_limits, Mapping):
        return None
    if callable_key in raw_limits:
        return _positive_int_or_none(raw_limits.get(callable_key))
    best_prefix_length = -1
    best_limit: int | None = None
    for raw_pattern, raw_limit in raw_limits.items():
        pattern = str(raw_pattern)
        if not pattern.endswith("*"):
            continue
        prefix = pattern[:-1]
        if callable_key.startswith(prefix) and len(prefix) > best_prefix_length:
            best_prefix_length = len(prefix)
            best_limit = _positive_int_or_none(raw_limit)
    return best_limit


def _positive_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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


def _simulation_decision(
    *,
    access_allowed: bool,
    access_reason: str,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    exceeded = [check for check in checks if bool(check["would_exceed_limit"])]
    ranked = sorted(
        exceeded,
        key=lambda check: (
            float(check["remaining_after_amount"]) / max(1.0, float(check["limit"])),
            float(check["remaining_after_amount"]),
            str(check["scope"]),
            str(check["entity_id"]),
        ),
    )
    limiting_scopes = list(dict.fromkeys(str(check["scope"]) for check in ranked))
    if not access_allowed:
        allowed = False
        reason = access_reason
        primary_limiting_scope = "tier_model_access"
        limiting_scopes = ["tier_model_access", *limiting_scopes]
    elif ranked:
        allowed = False
        reason = "static_limit_exceeded"
        primary_limiting_scope = str(ranked[0]["scope"])
    else:
        allowed = True
        reason = "allowed_in_empty_window"
        primary_limiting_scope = None
    return {
        "allowed": allowed,
        "reason": reason,
        "primary_limiting_scope": primary_limiting_scope,
        "limiting_scopes": limiting_scopes,
        "basis": "empty_window_static",
        "live_capacity_evaluated": False,
    }


def _calculate_price_quote(
    *,
    organization_id: str,
    callable_key: str,
    mode: str,
    request_count: int,
    requested_billing_mode: str | None,
    usage: Mapping[str, Any],
    tier_policy_service: Any,
    configured_deployments: list[Any] | tuple[Any, ...],
) -> dict[str, Any]:
    deployments = tuple(configured_deployments)
    usage_snapshot = _compact_simulation_usage(usage, requested_billing_mode)
    if not deployments:
        return _unavailable_price_quote(
            reason="no_configured_routes",
            billing_mode=requested_billing_mode,
            usage_snapshot=usage_snapshot,
            configured_candidate_count=0,
            request_count=request_count,
        )

    deployment_modes = {_deployment_billing_mode(deployment) for deployment in deployments}
    unsupported_modes = sorted(deployment_modes - _SUPPORTED_BILLING_MODES)
    if unsupported_modes:
        return _unavailable_price_quote(
            reason="unsupported_billing_mode",
            billing_mode=requested_billing_mode or unsupported_modes[0],
            usage_snapshot=usage_snapshot,
            configured_candidate_count=len(deployments),
            request_count=request_count,
        )
    if len(deployment_modes) != 1:
        return _unavailable_price_quote(
            reason="mixed_billing_modes",
            billing_mode=requested_billing_mode,
            usage_snapshot=usage_snapshot,
            configured_candidate_count=len(deployments),
            request_count=request_count,
        )
    resolved_billing_mode = next(iter(deployment_modes))
    if requested_billing_mode is not None and requested_billing_mode != resolved_billing_mode:
        return _unavailable_price_quote(
            reason="billing_mode_mismatch",
            billing_mode=requested_billing_mode,
            usage_snapshot=usage_snapshot,
            configured_candidate_count=len(deployments),
            request_count=request_count,
        )
    usage_snapshot = _compact_simulation_usage(usage, resolved_billing_mode)

    auth = SimpleNamespace(organization_id=organization_id)
    per_request_quotes: list[float] = []
    sources: set[str] = set()
    unpriced_reasons: set[str] = set()
    for deployment in deployments:
        resolution = resolve_deployment_tier_pricing(
            auth=auth,
            model=callable_key,
            deployment=deployment,
            tier_policy_service=tier_policy_service,
            mode=mode,
        )
        candidate_quote = _candidate_billing_result(
            callable_key=callable_key,
            pricing_mode=mode,
            billing_mode=resolved_billing_mode,
            usage=usage,
            deployment=deployment,
            resolution=resolution,
        )
        billing = candidate_quote.billing
        if billing.unpriced_reason is not None:
            unpriced_reasons.add(billing.unpriced_reason)
            continue
        per_request_quotes.append(round(billing.cost, 10))
        sources.update(candidate_quote.pricing_sources)

    if not per_request_quotes:
        reason = (
            "missing_usage_for_billing_mode"
            if "missing_usage_for_billing_mode" in unpriced_reasons
            else "no_configured_pricing"
        )
        return _unavailable_price_quote(
            reason=reason,
            billing_mode=resolved_billing_mode,
            usage_snapshot=usage_snapshot,
            configured_candidate_count=len(deployments),
            request_count=request_count,
            unpriced_reasons=sorted(unpriced_reasons),
            pricing_evaluated=True,
        )

    per_request_minimum = min(per_request_quotes)
    per_request_maximum = max(per_request_quotes)
    minimum = round(per_request_minimum * request_count, 10)
    maximum = round(per_request_maximum * request_count, 10)
    exact = per_request_minimum == per_request_maximum
    unpriced_candidate_count = len(deployments) - len(per_request_quotes)
    return {
        "status": "partial" if unpriced_candidate_count else "available",
        "reason": "some_routes_unpriced" if unpriced_candidate_count else None,
        "currency": "USD",
        "kind": "exact" if exact else "range",
        "amount": minimum if exact and not unpriced_candidate_count else None,
        "minimum_amount": minimum,
        "maximum_amount": maximum,
        "request_count": request_count,
        "amount_scope": "aggregate",
        "per_request_amount": (
            per_request_minimum if exact and not unpriced_candidate_count else None
        ),
        "per_request_minimum_amount": per_request_minimum,
        "per_request_maximum_amount": per_request_maximum,
        "billing_mode": resolved_billing_mode,
        "usage_snapshot": usage_snapshot,
        "configured_candidate_count": len(deployments),
        "priced_candidate_count": len(per_request_quotes),
        "unpriced_candidate_count": unpriced_candidate_count,
        "unevaluated_candidate_count": 0,
        "unpriced_reasons": sorted(unpriced_reasons),
        "pricing_sources": sorted(sources),
        "basis": "configured_routes",
    }


def _candidate_billing_result(
    *,
    callable_key: str,
    pricing_mode: str,
    billing_mode: str,
    usage: Mapping[str, Any],
    deployment: Any,
    resolution: Any,
) -> _CandidateQuote:
    candidate_usage = _candidate_usage_for_billing(
        billing_mode=billing_mode,
        usage=usage,
        deployment=deployment,
    )
    if billing_mode in {"embedding", "rerank"}:
        candidate_usage["completion_tokens"] = 0
    configured_pricing_fields = frozenset(resolution.customer_pricing_fields)
    if billing_mode in _TOKEN_BILLING_MODES:
        token_billing = resolve_token_billing_result(
            resolution,
            model=callable_key,
            usage=candidate_usage,
            mode="batch" if pricing_mode == "batch" else "sync",
        )
        return _CandidateQuote(
            billing=token_billing.billing,
            pricing_sources=token_billing.pricing_sources_used,
        )
    if not _has_applicable_pricing(
        billing_mode=billing_mode,
        configured_pricing_fields=configured_pricing_fields,
    ):
        return _CandidateQuote(
            billing=BillingResult(cost=0.0, unpriced_reason="no_configured_pricing")
        )
    billing = compute_billing_result(
        mode=billing_mode,
        usage=candidate_usage,
        model_info=resolution.customer_model_info,
    )
    if billing.unpriced_reason is not None:
        if billing.unpriced_reason == "no_configured_pricing":
            return _CandidateQuote(billing=billing)
        return _CandidateQuote(
            billing=BillingResult(
                cost=0.0,
                unpriced_reason="missing_usage_for_billing_mode",
            )
        )
    if not billing.pricing_fields_used:
        return _CandidateQuote(
            billing=BillingResult(
                cost=0.0,
                unpriced_reason="missing_usage_for_billing_mode",
            )
        )
    return _CandidateQuote(
        billing=billing,
        pricing_sources=_pricing_sources_for_fields(
            resolution,
            billing.pricing_fields_used,
        ),
    )


def _pricing_sources_for_fields(
    resolution: Any,
    pricing_fields: tuple[str, ...],
) -> tuple[PricingSource, ...]:
    tier_fields = frozenset(resolution.tier_pricing_fields)
    provider_fields = frozenset(resolution.provider_pricing_fields)
    sources: set[PricingSource] = set()
    for field_name in pricing_fields:
        if resolution.tier_pricing_applied and field_name in tier_fields:
            sources.add("tier")
        elif field_name in provider_fields:
            sources.add("deployment")
    return tuple(sorted(sources))


def _candidate_usage_for_billing(
    *,
    billing_mode: str,
    usage: Mapping[str, Any],
    deployment: Any,
) -> dict[str, Any]:
    candidate_usage = dict(usage)
    if billing_mode != "audio_transcription":
        return candidate_usage
    duration_seconds = float(candidate_usage.get("duration_seconds", 0) or 0)
    provider = resolve_provider(getattr(deployment, "deltallm_params", None) or {})
    billable_duration = billable_transcription_duration_seconds(
        duration_seconds,
        provider,
    )
    if billable_duration != duration_seconds:
        candidate_usage["billable_duration_seconds"] = billable_duration
    return candidate_usage


def _has_applicable_pricing(
    *,
    billing_mode: str,
    configured_pricing_fields: frozenset[str],
) -> bool:
    fields = _APPLICABLE_PRICING_FIELDS[billing_mode]
    return bool(fields & configured_pricing_fields)


def _deployment_billing_mode(deployment: Any) -> str:
    model_info = getattr(deployment, "model_info", None)
    raw_mode = model_info.get("mode") if isinstance(model_info, Mapping) else None
    return str(raw_mode or "chat").strip().lower() or "chat"


def _sole_deployment_billing_mode(deployments: tuple[Any, ...]) -> str | None:
    modes = {_deployment_billing_mode(deployment) for deployment in deployments}
    if len(modes) != 1:
        return None
    mode = next(iter(modes))
    return mode if mode in _SUPPORTED_BILLING_MODES else None


def _unavailable_price_quote(
    *,
    reason: str,
    billing_mode: str | None,
    usage_snapshot: dict[str, Any],
    configured_candidate_count: int,
    request_count: int,
    unpriced_reasons: list[str] | None = None,
    pricing_evaluated: bool = False,
) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "currency": "USD",
        "kind": None,
        "amount": None,
        "minimum_amount": None,
        "maximum_amount": None,
        "request_count": request_count,
        "amount_scope": "aggregate",
        "per_request_amount": None,
        "per_request_minimum_amount": None,
        "per_request_maximum_amount": None,
        "billing_mode": billing_mode,
        "usage_snapshot": usage_snapshot,
        "configured_candidate_count": configured_candidate_count,
        "priced_candidate_count": 0,
        "unpriced_candidate_count": (
            configured_candidate_count if pricing_evaluated else 0
        ),
        "unevaluated_candidate_count": (
            0 if pricing_evaluated else configured_candidate_count
        ),
        "unpriced_reasons": list(unpriced_reasons or ()),
        "pricing_sources": [],
        "basis": "configured_routes",
    }


def _compact_simulation_usage(
    usage: Mapping[str, Any],
    billing_mode: str | None,
) -> dict[str, Any]:
    fields_by_mode = {
        "chat": ("prompt_tokens", "completion_tokens"),
        "embedding": ("prompt_tokens",),
        "rerank": ("prompt_tokens",),
        "image_generation": ("input_images", "output_images"),
        "audio_speech": (
            "prompt_tokens",
            "completion_tokens",
            "input_audio_tokens",
            "output_audio_tokens",
            "input_characters",
            "output_characters",
            "duration_seconds",
        ),
        "audio_transcription": (
            "prompt_tokens",
            "completion_tokens",
            "input_audio_tokens",
            "duration_seconds",
        ),
    }
    fields = fields_by_mode.get(billing_mode or "chat", fields_by_mode["chat"])
    return {field: usage.get(field, 0) for field in fields}


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


def _normalize_optional_billing_mode(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized not in _SUPPORTED_BILLING_MODES:
        raise TierPolicyPreviewError(
            "billing_mode must be chat, embedding, rerank, image_generation, "
            "audio_speech, or audio_transcription"
        )
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


def _non_negative_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise TierPolicyPreviewError(f"{field_name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise TierPolicyPreviewError(f"{field_name} must be a number") from None
    if not math.isfinite(parsed) or parsed < 0:
        raise TierPolicyPreviewError(f"{field_name} must be a non-negative finite number")
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
