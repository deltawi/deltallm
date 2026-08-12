from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from src.metrics import increment_tier_policy_shadow_mismatch
from src.models.responses import UserAPIKeyAuth
from src.services.runtime_scopes import resolve_runtime_scope_context
from src.services.tier_policy_service import resolve_tier_policy_unavailable_decision

if TYPE_CHECKING:
    from src.services.tier_policy_service import TierPolicyService

logger = logging.getLogger(__name__)

TierPolicyMode = Literal["disabled", "shadow", "enforce"]
_ALLOWED_TIER_POLICY_MODES = {"disabled", "shadow", "enforce"}
_LOG_MODEL_LIMIT = 25


@dataclass(frozen=True, slots=True)
class TierModelAccessResolution:
    effective_allowlist: set[str] | None
    tier_mode: TierPolicyMode
    tier_allowlist: set[str] | None = None
    tier_effective_allowlist: set[str] | None = None
    tier_applied: bool = False
    tier_authoritative: bool = True
    tier_unavailable_reason: str | None = None
    tier_shadow_mismatch: bool = False


class TierModelAccessResolutionView(Protocol):
    tier_mode: TierPolicyMode
    tier_allowlist: set[str] | None
    tier_effective_allowlist: set[str] | None
    tier_applied: bool
    tier_authoritative: bool
    tier_unavailable_reason: str | None


def normalize_tier_policy_mode(value: object) -> TierPolicyMode:
    normalized = str(value or "").strip().lower()
    if normalized not in _ALLOWED_TIER_POLICY_MODES:
        return "disabled"
    return normalized  # type: ignore[return-value]


def resolve_tier_model_access(
    auth: UserAPIKeyAuth,
    *,
    pre_tier_allowlist: set[str] | None,
    direct_restrict_allowlist: set[str] | frozenset[str] | None = None,
    tier_policy_service: TierPolicyService | None,
    tier_policy_mode: TierPolicyMode | str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
) -> TierModelAccessResolution:
    tier_mode = normalize_tier_policy_mode(
        getattr(tier_policy_service, "mode", tier_policy_mode)
        if tier_policy_service is not None
        else tier_policy_mode
    )
    if tier_mode == "disabled":
        return TierModelAccessResolution(
            effective_allowlist=pre_tier_allowlist,
            tier_mode=tier_mode,
        )

    scope_context = resolve_runtime_scope_context(auth)
    if scope_context.is_master_key or scope_context.organization_id is None:
        return TierModelAccessResolution(
            effective_allowlist=pre_tier_allowlist,
            tier_mode=tier_mode,
        )

    if tier_policy_service is None or not callable(
        getattr(tier_policy_service, "resolve_org_allowed_callable_keys", None)
    ):
        decision = _resolve_tier_unavailable_decision(
            tier_policy_service,
            organization_id=scope_context.organization_id,
            tier_mode=tier_mode,
            missing_service_mode=tier_policy_missing_service_mode,
        )
        return _resolve_unavailable_tier_access(
            pre_tier_allowlist=pre_tier_allowlist,
            tier_mode=tier_mode,
            decision=decision,
        )

    if bool(getattr(tier_policy_service, "snapshot_stale", False)):
        decision = _resolve_tier_unavailable_decision(
            tier_policy_service,
            organization_id=scope_context.organization_id,
            tier_mode=tier_mode,
            missing_service_mode=tier_policy_missing_service_mode,
        )
        if not bool(getattr(decision, "allowed", False)):
            return _resolve_unavailable_tier_access(
                pre_tier_allowlist=pre_tier_allowlist,
                tier_mode=tier_mode,
                decision=decision,
            )
        return TierModelAccessResolution(
            effective_allowlist=pre_tier_allowlist,
            tier_mode=tier_mode,
            tier_authoritative=False,
            tier_unavailable_reason=str(getattr(decision, "reason", "tier_policy_unavailable")),
        )

    org_allowlist = tier_policy_service.resolve_org_allowed_callable_keys(
        scope_context.organization_id
    )
    if org_allowlist is None:
        return TierModelAccessResolution(
            effective_allowlist=pre_tier_allowlist,
            tier_mode=tier_mode,
        )

    tier_allowlist = _normalize_allowlist(org_allowlist)
    tier_effective_allowlist = _apply_direct_restrict_allowlist(
        tier_allowlist,
        direct_restrict_allowlist,
    )
    tier_shadow_mismatch = tier_mode == "shadow" and _allowlists_differ(
        pre_tier_allowlist,
        tier_effective_allowlist,
    )
    return TierModelAccessResolution(
        effective_allowlist=tier_effective_allowlist if tier_mode == "enforce" else pre_tier_allowlist,
        tier_mode=tier_mode,
        tier_allowlist=tier_allowlist,
        tier_effective_allowlist=tier_effective_allowlist,
        tier_applied=True,
        tier_shadow_mismatch=tier_shadow_mismatch,
    )


def log_tier_policy_shadow_mismatch(
    auth: UserAPIKeyAuth,
    *,
    current_allowlist: set[str] | None,
    tier_effective_allowlist: set[str] | None,
    reason: str | None,
    tier_policy_service: TierPolicyService | None,
) -> None:
    scope_context = resolve_runtime_scope_context(auth)
    current = current_allowlist or set()
    tier_effective = tier_effective_allowlist or set()
    removed_models = (
        _bounded_sorted_values(current - tier_effective)
        if current_allowlist is not None and tier_effective_allowlist is not None
        else []
    )
    added_models = (
        _bounded_sorted_values(tier_effective - current)
        if current_allowlist is not None and tier_effective_allowlist is not None
        else []
    )
    difference_type = _tier_difference_type(
        current_allowlist=current_allowlist,
        tier_effective_allowlist=tier_effective_allowlist,
        removed_models=removed_models,
        added_models=added_models,
    )
    increment_tier_policy_shadow_mismatch(
        auth_source=scope_context.auth_source,
        difference_type=difference_type,
        reason=reason,
    )
    logger.info(
        "tier_policy_shadow_mismatch",
        extra={
            "actor_id": scope_context.actor_id,
            "auth_source": scope_context.auth_source,
            "organization_id": scope_context.organization_id,
            "team_id": scope_context.team_id,
            "api_key_scope_id": scope_context.api_key_scope_id,
            "current_count": None if current_allowlist is None else len(current),
            "tier_effective_count": (
                None if tier_effective_allowlist is None else len(tier_effective)
            ),
            "current_unrestricted": current_allowlist is None,
            "tier_effective_unrestricted": tier_effective_allowlist is None,
            "removed_models": removed_models,
            "added_models": added_models,
            "difference_type": difference_type,
            "reason": reason,
            **_tier_snapshot_log_fields(tier_policy_service),
        },
    )


def log_tier_policy_denial_if_applicable(
    auth: UserAPIKeyAuth,
    model: str,
    *,
    pre_tier_allowlist: set[str] | None,
    tier_resolution: TierModelAccessResolutionView,
    tier_policy_service: TierPolicyService | None,
) -> bool:
    reason = _tier_policy_denial_reason(
        auth,
        model,
        pre_tier_allowlist=pre_tier_allowlist,
        tier_resolution=tier_resolution,
        tier_policy_service=tier_policy_service,
    )
    if reason is None:
        return False

    scope_context = resolve_runtime_scope_context(auth)
    logger.info(
        "tier_policy_model_access_denied",
        extra={
            "actor_id": scope_context.actor_id,
            "auth_source": scope_context.auth_source,
            "organization_id": scope_context.organization_id,
            "team_id": scope_context.team_id,
            "api_key_scope_id": scope_context.api_key_scope_id,
            "model": model,
            "tier_mode": tier_resolution.tier_mode,
            "reason": reason,
            "pre_tier_count": None if pre_tier_allowlist is None else len(pre_tier_allowlist),
            "tier_allowlist_count": (
                None
                if tier_resolution.tier_allowlist is None
                else len(tier_resolution.tier_allowlist)
            ),
            "tier_effective_count": (
                None
                if tier_resolution.tier_effective_allowlist is None
                else len(tier_resolution.tier_effective_allowlist)
            ),
            "tier_unavailable_reason": tier_resolution.tier_unavailable_reason,
            **_tier_snapshot_log_fields(tier_policy_service),
        },
    )
    return True


def _resolve_tier_unavailable_decision(
    tier_policy_service: TierPolicyService | None,
    *,
    organization_id: str | None,
    tier_mode: TierPolicyMode,
    missing_service_mode: str,
) -> Any:
    resolver = getattr(tier_policy_service, "resolve_unavailable_decision", None)
    if callable(resolver):
        return resolver(organization_id)

    service_for_decision = (
        tier_policy_service
        if callable(getattr(tier_policy_service, "has_explicit_tier_policy", None))
        else None
    )
    return resolve_tier_policy_unavailable_decision(
        service_for_decision,
        organization_id,
        mode=tier_mode,
        missing_service_mode=str(
            getattr(tier_policy_service, "missing_service_mode", missing_service_mode)
            if tier_policy_service is not None
            else missing_service_mode
        ),
    )


def _resolve_unavailable_tier_access(
    *,
    pre_tier_allowlist: set[str] | None,
    tier_mode: TierPolicyMode,
    decision: Any,
) -> TierModelAccessResolution:
    reason = str(getattr(decision, "reason", "tier_policy_unavailable"))
    if bool(getattr(decision, "allowed", False)):
        return TierModelAccessResolution(
            effective_allowlist=pre_tier_allowlist,
            tier_mode=tier_mode,
            tier_authoritative=False,
            tier_unavailable_reason=reason,
        )

    tier_allowlist: set[str] = set()
    tier_effective_allowlist = set()
    tier_shadow_mismatch = tier_mode == "shadow" and _allowlists_differ(
        pre_tier_allowlist,
        tier_effective_allowlist,
    )
    return TierModelAccessResolution(
        effective_allowlist=tier_effective_allowlist if tier_mode == "enforce" else pre_tier_allowlist,
        tier_mode=tier_mode,
        tier_allowlist=tier_allowlist,
        tier_effective_allowlist=tier_effective_allowlist,
        tier_applied=True,
        tier_authoritative=False,
        tier_unavailable_reason=reason,
        tier_shadow_mismatch=tier_shadow_mismatch,
    )


def _apply_direct_restrict_allowlist(
    base_allowlist: set[str],
    direct_restrict_allowlist: set[str] | frozenset[str] | None,
) -> set[str]:
    if direct_restrict_allowlist is None:
        return set(base_allowlist)
    return set(base_allowlist).intersection(direct_restrict_allowlist)


def _normalize_allowlist(values: Iterable[str] | None) -> set[str]:
    normalized: set[str] = set()
    if values is None:
        return normalized
    for value in values:
        item = str(value).strip()
        if item:
            normalized.add(item)
    return normalized


def _allowlists_differ(left: set[str] | None, right: set[str] | None) -> bool:
    if left is None and right is None:
        return False
    if left is None or right is None:
        return True
    return left != right


def _tier_policy_denial_reason(
    auth: UserAPIKeyAuth,
    model: str,
    *,
    pre_tier_allowlist: set[str] | None,
    tier_resolution: TierModelAccessResolutionView,
    tier_policy_service: TierPolicyService | None,
) -> str | None:
    if tier_resolution.tier_mode != "enforce" or not tier_resolution.tier_applied:
        return None
    policy = _get_tier_model_policy(auth, model, tier_policy_service)
    if str(getattr(policy, "access_mode", "")).strip().lower() == "deny":
        return "tier_policy_deny"
    if (
        tier_resolution.tier_effective_allowlist is None
        or model in tier_resolution.tier_effective_allowlist
    ):
        return None
    if (
        tier_resolution.tier_unavailable_reason is not None
        and not tier_resolution.tier_authoritative
    ):
        return tier_resolution.tier_unavailable_reason

    if tier_resolution.tier_allowlist is not None:
        if model not in tier_resolution.tier_allowlist:
            return "tier_policy_allowlist_excluded"
        return None
    if pre_tier_allowlist is not None and model not in pre_tier_allowlist:
        return None
    return "tier_policy_allowlist_excluded"


def _get_tier_model_policy(
    auth: UserAPIKeyAuth,
    model: str,
    tier_policy_service: TierPolicyService | None,
) -> Any | None:
    getter = getattr(tier_policy_service, "get_model_policy", None)
    if not callable(getter):
        return None
    return getter(resolve_runtime_scope_context(auth).organization_id, model)


def _tier_snapshot_log_fields(tier_policy_service: TierPolicyService | None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "tier_snapshot_stale": bool(getattr(tier_policy_service, "snapshot_stale", False)),
    }
    info_getter = getattr(tier_policy_service, "snapshot_info", None)
    if not callable(info_getter):
        return fields
    try:
        info = info_getter()
    except Exception:
        logger.debug("tier policy snapshot info unavailable", exc_info=True)
        return fields
    fields.update(
        {
            "tier_snapshot_etag": getattr(info, "etag", None),
            "tier_snapshot_org_count": getattr(info, "org_count", None),
        }
    )
    return fields


def _difference_type(*, removed_models: list[str], added_models: list[str]) -> str:
    if removed_models and added_models:
        return "both"
    if removed_models:
        return "removed_only"
    if added_models:
        return "added_only"
    return "none"


def _tier_difference_type(
    *,
    current_allowlist: set[str] | None,
    tier_effective_allowlist: set[str] | None,
    removed_models: list[str],
    added_models: list[str],
) -> str:
    if current_allowlist is None or tier_effective_allowlist is None:
        return "unrestricted_changed"
    return _difference_type(removed_models=removed_models, added_models=added_models)


def _bounded_sorted_values(values: set[str]) -> list[str]:
    return sorted(values)[:_LOG_MODEL_LIMIT]
