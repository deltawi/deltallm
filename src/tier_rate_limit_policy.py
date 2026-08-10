from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.models.errors import ServiceUnavailableError
from src.services.limit_counter import ParallelLimitCheck, RateLimitCheck
from src.services.tier_capacity_fair_share import (
    TierFairShareCheck,
    is_advanced_capacity_pool_strategy,
)
from src.services.tier_policy_service import resolve_tier_policy_unavailable_decision

RateLimitMode = Literal["sync", "batch"]

_TIER_POLICY_UNAVAILABLE_MESSAGE = "Tier policy unavailable for rate limiting"
_BATCH_SCOPE_FALLBACKS = {
    "tier_org_model_batch_rpm": "tier_org_model_rpm",
    "tier_org_model_batch_tpm": "tier_org_model_tpm",
}
_BATCH_FALLBACK_SYNC_SCOPES = frozenset(_BATCH_SCOPE_FALLBACKS.values())


@dataclass(frozen=True, slots=True)
class TierLimitControls:
    rate_checks: tuple[RateLimitCheck, ...] = ()
    parallel_checks: tuple[ParallelLimitCheck, ...] = ()
    fair_share_checks: tuple[TierFairShareCheck, ...] = ()


def build_tier_rate_limit_checks(
    *,
    auth: Any,
    tokens: int,
    model: str | None,
    tier_policy_service: Any | None,
    tier_policy_mode: str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
    tier_capacity_fair_share_enabled: bool = False,
    mode: RateLimitMode | str = "sync",
) -> list[RateLimitCheck]:
    return list(
        build_tier_limit_controls(
            auth=auth,
            tokens=tokens,
            model=model,
            tier_policy_service=tier_policy_service,
            tier_policy_mode=tier_policy_mode,
            tier_policy_missing_service_mode=tier_policy_missing_service_mode,
            tier_capacity_fair_share_enabled=tier_capacity_fair_share_enabled,
            mode=mode,
        ).rate_checks
    )


def build_tier_parallel_limit_checks(
    *,
    auth: Any,
    model: str | None,
    tier_policy_service: Any | None,
    tier_policy_mode: str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
    tier_capacity_fair_share_enabled: bool = False,
    mode: RateLimitMode | str = "sync",
) -> list[ParallelLimitCheck]:
    return list(
        build_tier_limit_controls(
            auth=auth,
            tokens=0,
            model=model,
            tier_policy_service=tier_policy_service,
            tier_policy_mode=tier_policy_mode,
            tier_policy_missing_service_mode=tier_policy_missing_service_mode,
            tier_capacity_fair_share_enabled=tier_capacity_fair_share_enabled,
            mode=mode,
        ).parallel_checks
    )


def build_tier_limit_controls(
    *,
    auth: Any,
    tokens: int,
    model: str | None,
    tier_policy_service: Any | None,
    tier_policy_mode: str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
    tier_capacity_fair_share_enabled: bool = False,
    mode: RateLimitMode | str = "sync",
) -> TierLimitControls:
    organization_id = _normalize_id(getattr(auth, "organization_id", None))
    callable_key = _normalize_id(model)
    if organization_id is None or callable_key is None:
        return TierLimitControls()

    policy_mode = _effective_tier_policy_mode(tier_policy_service, tier_policy_mode)
    if policy_mode != "enforce":
        return TierLimitControls()

    if not _tier_policy_service_has_rate_limit_lookups(tier_policy_service):
        _ensure_tier_policy_available(
            tier_policy_service,
            organization_id=organization_id,
            tier_policy_mode=policy_mode,
            tier_policy_missing_service_mode=tier_policy_missing_service_mode,
        )
        return TierLimitControls()

    if bool(getattr(tier_policy_service, "snapshot_stale", False)):
        _ensure_tier_policy_available(
            tier_policy_service,
            organization_id=organization_id,
            tier_policy_mode=policy_mode,
            tier_policy_missing_service_mode=tier_policy_missing_service_mode,
        )
        return TierLimitControls()

    request_mode = _normalize_rate_limit_mode(mode)
    try:
        return _build_tier_controls_from_service(
            tier_policy_service=tier_policy_service,
            organization_id=organization_id,
            callable_key=callable_key,
            tokens=tokens,
            tier_capacity_fair_share_enabled=tier_capacity_fair_share_enabled,
            request_mode=request_mode,
        )
    except Exception as exc:
        _ensure_tier_policy_available(
            tier_policy_service,
            organization_id=organization_id,
            tier_policy_mode=policy_mode,
            tier_policy_missing_service_mode=tier_policy_missing_service_mode,
            cause=exc,
        )
        return TierLimitControls()


def _build_tier_controls_from_service(
    *,
    tier_policy_service: Any,
    organization_id: str,
    callable_key: str,
    tokens: int,
    tier_capacity_fair_share_enabled: bool,
    request_mode: RateLimitMode,
) -> TierLimitControls:
    checks: list[RateLimitCheck] = []
    descriptors = select_tier_rate_limit_descriptors(
        tier_policy_service.get_rate_limit_descriptors(organization_id, callable_key),
        request_mode=request_mode,
    )
    for descriptor in descriptors:
        check = _rate_limit_check_from_descriptor(
            descriptor,
            tokens=tokens,
        )
        if check is not None:
            checks.append(check)

    model_policy = tier_policy_service.get_model_policy(organization_id, callable_key)
    if str(getattr(model_policy, "access_mode", "allow") or "allow").strip().lower() == "deny":
        return TierLimitControls(rate_checks=tuple(checks))

    parallel_checks = [
        check
        for check in (
            _model_parallel_limit_check(model_policy, organization_id, callable_key),
        )
        if check is not None
    ]

    capacity_pool_key = _normalize_id(getattr(model_policy, "capacity_pool_key", None))
    if capacity_pool_key is None:
        return TierLimitControls(rate_checks=tuple(checks), parallel_checks=tuple(parallel_checks))

    pool_policy = tier_policy_service.get_capacity_pool_policy(capacity_pool_key, callable_key)
    fair_share_check = _capacity_pool_fair_share_check(
        pool_policy,
        model_policy=model_policy,
        organization_id=organization_id,
        callable_key=callable_key,
        tokens=tokens,
        enabled=tier_capacity_fair_share_enabled,
    )
    if fair_share_check is None:
        checks.extend(
            _capacity_pool_rate_limit_checks(
                pool_policy,
                tokens=tokens,
                request_mode=request_mode,
            )
        )
    pool_parallel_check = _pool_parallel_limit_check(
        pool_policy,
        fallback_pool_key=capacity_pool_key,
        fallback_callable_key=callable_key,
    )
    if pool_parallel_check is not None:
        parallel_checks.append(pool_parallel_check)
    return TierLimitControls(
        rate_checks=tuple(checks),
        parallel_checks=tuple(parallel_checks),
        fair_share_checks=(fair_share_check,) if fair_share_check is not None else (),
    )


def _capacity_pool_fair_share_check(
    pool_policy: Any | None,
    *,
    model_policy: Any | None,
    organization_id: str,
    callable_key: str,
    tokens: int,
    enabled: bool,
) -> TierFairShareCheck | None:
    if not enabled or pool_policy is None:
        return None
    if not is_advanced_capacity_pool_strategy(getattr(pool_policy, "strategy", None)):
        return None
    pool_key = _normalize_id(getattr(pool_policy, "pool_key", None))
    pool_callable_key = _normalize_id(getattr(pool_policy, "callable_key", None)) or callable_key
    if pool_key is None:
        return None
    source = getattr(model_policy, "source", None)
    return TierFairShareCheck(
        pool_key=pool_key,
        callable_key=pool_callable_key,
        organization_id=organization_id,
        tier_key=_normalize_id(getattr(source, "tier_key", None)),
        assignment_weight=max(1, int(getattr(source, "assignment_weight", 1) or 1)),
        rpm_capacity=_positive_int_or_none(getattr(pool_policy, "rpm_capacity", None)),
        tpm_capacity=_positive_int_or_none(getattr(pool_policy, "tpm_capacity", None)),
        request_amount=1,
        token_amount=max(0, int(tokens)),
        strategy=str(getattr(pool_policy, "strategy", "weighted_fair") or "weighted_fair").strip().lower(),
        saturation_threshold=getattr(pool_policy, "saturation_threshold", None),
        burst_multiplier=getattr(pool_policy, "burst_multiplier", None),
    )


def _capacity_pool_rate_limit_checks(
    pool_policy: Any | None,
    *,
    tokens: int,
    request_mode: RateLimitMode,
) -> list[RateLimitCheck]:
    descriptors = select_tier_rate_limit_descriptors(
        getattr(pool_policy, "rate_limit_descriptors", ()) if pool_policy is not None else (),
        request_mode=request_mode,
    )
    checks: list[RateLimitCheck] = []
    for descriptor in descriptors:
        check = _rate_limit_check_from_descriptor(
            descriptor,
            tokens=tokens,
        )
        if check is not None:
            checks.append(check)
    return checks


def select_tier_rate_limit_descriptors(
    descriptors: Any,
    *,
    request_mode: RateLimitMode,
) -> tuple[Any, ...]:
    descriptor_tuple = tuple(descriptors or ())
    if request_mode == "sync":
        return tuple(
            descriptor
            for descriptor in descriptor_tuple
            if _descriptor_mode(descriptor) in {"sync", "all"}
        )

    batch_override_targets = _batch_override_targets(descriptor_tuple)
    selected = []
    for descriptor in descriptor_tuple:
        descriptor_mode = _descriptor_mode(descriptor)
        scope = _descriptor_scope(descriptor)
        if descriptor_mode in {"batch", "all"}:
            selected.append(descriptor)
        elif (
            descriptor_mode == "sync"
            and scope in _BATCH_FALLBACK_SYNC_SCOPES
            and scope not in batch_override_targets
        ):
            selected.append(descriptor)
    return tuple(selected)


def _batch_override_targets(descriptors: tuple[Any, ...]) -> set[str]:
    targets: set[str] = set()
    for descriptor in descriptors:
        if _descriptor_mode(descriptor) != "batch":
            continue
        scope = _descriptor_scope(descriptor)
        fallback_scope = _BATCH_SCOPE_FALLBACKS.get(scope)
        if fallback_scope is not None:
            targets.add(fallback_scope)
    return targets


def _rate_limit_check_from_descriptor(
    descriptor: Any,
    *,
    tokens: int,
) -> RateLimitCheck | None:
    amount_kind = str(getattr(descriptor, "amount_kind", "") or "").strip().lower()
    if amount_kind == "requests":
        amount = 1
    elif amount_kind == "tokens":
        amount = int(tokens)
    else:
        return None

    scope = _normalize_id(getattr(descriptor, "scope", None))
    entity_id = _normalize_id(getattr(descriptor, "entity_id", None))
    limit = _positive_int_or_none(getattr(descriptor, "limit", None))
    window_seconds = _positive_int_or_none(getattr(descriptor, "window_seconds", None))
    if scope is None or entity_id is None or limit is None or window_seconds is None or amount <= 0:
        return None

    return RateLimitCheck(
        scope=scope,
        entity_id=entity_id,
        limit=limit,
        amount=amount,
        window_seconds=window_seconds,
    )


def _model_parallel_limit_check(
    model_policy: Any | None,
    organization_id: str,
    callable_key: str,
) -> ParallelLimitCheck | None:
    limits = getattr(model_policy, "limits", None)
    limit = _positive_int_or_none(getattr(limits, "max_parallel_requests", None))
    if limit is None:
        return None
    return ParallelLimitCheck(
        scope="tier_org_model_parallel",
        entity_id=f"{organization_id}:{callable_key}",
        limit=limit,
    )


def _pool_parallel_limit_check(
    pool_policy: Any | None,
    *,
    fallback_pool_key: str,
    fallback_callable_key: str,
) -> ParallelLimitCheck | None:
    if pool_policy is None:
        return None
    limit = _positive_int_or_none(getattr(pool_policy, "max_parallel_requests", None))
    if limit is None:
        return None
    pool_key = _normalize_id(getattr(pool_policy, "pool_key", None)) or fallback_pool_key
    callable_key = _normalize_id(getattr(pool_policy, "callable_key", None)) or fallback_callable_key
    return ParallelLimitCheck(
        scope="tier_pool_model_parallel",
        entity_id=f"{pool_key}:{callable_key}",
        limit=limit,
    )


def _tier_policy_service_has_rate_limit_lookups(tier_policy_service: Any | None) -> bool:
    return (
        tier_policy_service is not None
        and callable(getattr(tier_policy_service, "get_rate_limit_descriptors", None))
        and callable(getattr(tier_policy_service, "get_model_policy", None))
        and callable(getattr(tier_policy_service, "get_capacity_pool_policy", None))
    )


def _ensure_tier_policy_available(
    tier_policy_service: Any | None,
    *,
    organization_id: str | None,
    tier_policy_mode: str,
    tier_policy_missing_service_mode: str,
    cause: Exception | None = None,
) -> None:
    decision = _resolve_tier_unavailable_decision(
        tier_policy_service,
        organization_id=organization_id,
        tier_policy_mode=tier_policy_mode,
        tier_policy_missing_service_mode=tier_policy_missing_service_mode,
    )
    if bool(getattr(decision, "allowed", False)):
        return
    raise ServiceUnavailableError(
        message=_TIER_POLICY_UNAVAILABLE_MESSAGE,
        code=str(getattr(decision, "reason", "tier_policy_unavailable")),
    ) from cause


def _resolve_tier_unavailable_decision(
    tier_policy_service: Any | None,
    *,
    organization_id: str | None,
    tier_policy_mode: str,
    tier_policy_missing_service_mode: str,
) -> Any:
    resolver = getattr(tier_policy_service, "resolve_unavailable_decision", None)
    if callable(resolver):
        try:
            return resolver(organization_id)
        except Exception:
            pass

    service_for_decision = (
        tier_policy_service
        if callable(getattr(tier_policy_service, "has_explicit_tier_policy", None))
        else None
    )
    return resolve_tier_policy_unavailable_decision(
        service_for_decision,
        organization_id,
        mode=tier_policy_mode,
        missing_service_mode=str(
            getattr(tier_policy_service, "missing_service_mode", tier_policy_missing_service_mode)
            if tier_policy_service is not None
            else tier_policy_missing_service_mode
        ),
    )


def _effective_tier_policy_mode(tier_policy_service: Any | None, tier_policy_mode: str) -> str:
    return _normalize_tier_policy_mode(
        getattr(tier_policy_service, "mode", tier_policy_mode)
        if tier_policy_service is not None
        else tier_policy_mode
    )


def _normalize_tier_policy_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"disabled", "shadow", "enforce"}:
        return "disabled"
    return normalized


def _normalize_rate_limit_mode(value: object) -> RateLimitMode:
    return "batch" if str(value or "").strip().lower() == "batch" else "sync"


def _descriptor_mode(descriptor: Any) -> str:
    return str(getattr(descriptor, "mode", "sync") or "sync").strip().lower()


def _descriptor_scope(descriptor: Any) -> str:
    return str(getattr(descriptor, "scope", "") or "").strip()


def _normalize_id(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _positive_int_or_none(value: object) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None
