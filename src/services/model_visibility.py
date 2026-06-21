from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from fastapi import HTTPException, status

from src.metrics import (
    increment_callable_target_policy_fallback,
    increment_callable_target_policy_shadow_mismatch,
)
from src.models.errors import PermissionDeniedError
from src.models.responses import UserAPIKeyAuth
from src.services.runtime_scopes import resolve_runtime_scope_context
from src.services.tier_model_access import (
    TierPolicyMode,
    log_tier_policy_denial_if_applicable,
    log_tier_policy_shadow_mismatch,
    normalize_tier_policy_mode,
    resolve_tier_model_access,
)

if TYPE_CHECKING:
    from src.services.callable_target_grants import CallableTargetGrantService
    from src.services.tier_policy_service import TierPolicyService

logger = logging.getLogger(__name__)

CallableTargetPolicyMode = Literal["shadow", "enforce"]
_ALLOWED_POLICY_MODES = {"shadow", "enforce"}
_MISSING = object()


@dataclass(frozen=True, slots=True)
class ModelAllowlistResolution:
    effective_allowlist: set[str] | None
    pre_tier_allowlist: set[str] | None
    hybrid_allowlist: set[str] | None
    policy_allowlist: set[str] | None
    policy_authoritative: bool
    policy_fallback_reason: str | None
    policy_mode: CallableTargetPolicyMode
    shadow_mismatch: bool = False
    tier_mode: TierPolicyMode = "disabled"
    tier_allowlist: set[str] | None = None
    tier_effective_allowlist: set[str] | None = None
    tier_applied: bool = False
    tier_authoritative: bool = True
    tier_unavailable_reason: str | None = None
    tier_shadow_mismatch: bool = False


def resolve_effective_model_allowlist(
    auth: UserAPIKeyAuth,
    *,
    callable_target_grant_service: CallableTargetGrantService | None = None,
    tier_policy_service: TierPolicyService | None = None,
    policy_mode: CallableTargetPolicyMode | str = "enforce",
    tier_policy_mode: TierPolicyMode | str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
    emit_shadow_log: bool = False,
) -> set[str] | None:
    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=callable_target_grant_service,
        tier_policy_service=tier_policy_service,
        policy_mode=policy_mode,
        tier_policy_mode=tier_policy_mode,
        tier_policy_missing_service_mode=tier_policy_missing_service_mode,
        emit_shadow_log=emit_shadow_log,
    )
    return resolution.effective_allowlist


def resolve_model_allowlist_resolution(
    auth: UserAPIKeyAuth,
    *,
    callable_target_grant_service: CallableTargetGrantService | None = None,
    tier_policy_service: TierPolicyService | None = None,
    policy_mode: CallableTargetPolicyMode | str = "enforce",
    tier_policy_mode: TierPolicyMode | str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
    emit_shadow_log: bool = False,
) -> ModelAllowlistResolution:
    normalized_policy_mode = normalize_callable_target_policy_mode(policy_mode)
    if resolve_runtime_scope_context(auth).is_master_key:
        return ModelAllowlistResolution(
            effective_allowlist=None,
            pre_tier_allowlist=None,
            hybrid_allowlist=None,
            policy_allowlist=None,
            policy_authoritative=True,
            policy_fallback_reason=None,
            policy_mode=normalized_policy_mode,
        )

    hybrid_allowlist = (
        _resolve_legacy_model_allowlist(auth)
        if normalized_policy_mode == "shadow"
        else None
    )

    if callable_target_grant_service is None:
        policy_allowlist: set[str] | None = set()
        policy_authoritative = True
        policy_fallback_reason = "grant_service_unavailable"
    else:
        policy_resolution = callable_target_grant_service.resolve_policy_allowlist(auth)
        policy_authoritative = policy_resolution.authoritative
        policy_fallback_reason = policy_resolution.fallback_reason
        policy_allowlist = (
            set(policy_resolution.allowlist)
            if policy_resolution.allowlist is not None
            else None
        )

    direct_restrict_allowlist = _resolve_direct_restrict_allowlist(
        auth,
        callable_target_grant_service=callable_target_grant_service,
        policy_mode=normalized_policy_mode,
        policy_fallback_reason=policy_fallback_reason,
    )
    pre_tier_allowlist = policy_allowlist
    if normalized_policy_mode == "shadow":
        pre_tier_allowlist = hybrid_allowlist

    shadow_mismatch = (
        normalized_policy_mode == "shadow"
        and policy_authoritative
        and _allowlists_differ(hybrid_allowlist, policy_allowlist)
    )
    if shadow_mismatch and emit_shadow_log:
        _log_policy_shadow_mismatch(
            auth,
            hybrid_allowlist=hybrid_allowlist,
            policy_allowlist=policy_allowlist,
            fallback_reason=policy_fallback_reason,
        )
    if normalized_policy_mode == "enforce" and policy_fallback_reason is not None:
        increment_callable_target_policy_fallback(
            policy_mode=normalized_policy_mode,
            auth_source=resolve_runtime_scope_context(auth).auth_source,
            reason=policy_fallback_reason,
        )

    tier_resolution = resolve_tier_model_access(
        auth,
        pre_tier_allowlist=pre_tier_allowlist,
        direct_restrict_allowlist=direct_restrict_allowlist,
        tier_policy_service=tier_policy_service,
        tier_policy_mode=tier_policy_mode,
        tier_policy_missing_service_mode=tier_policy_missing_service_mode,
    )
    if tier_resolution.tier_shadow_mismatch and emit_shadow_log:
        log_tier_policy_shadow_mismatch(
            auth,
            current_allowlist=pre_tier_allowlist,
            tier_effective_allowlist=tier_resolution.tier_effective_allowlist,
            reason=tier_resolution.tier_unavailable_reason,
            tier_policy_service=tier_policy_service,
        )

    return ModelAllowlistResolution(
        effective_allowlist=tier_resolution.effective_allowlist,
        pre_tier_allowlist=pre_tier_allowlist,
        hybrid_allowlist=hybrid_allowlist,
        policy_allowlist=policy_allowlist,
        policy_authoritative=policy_authoritative,
        policy_fallback_reason=policy_fallback_reason,
        policy_mode=normalized_policy_mode,
        shadow_mismatch=shadow_mismatch,
        tier_mode=tier_resolution.tier_mode,
        tier_allowlist=tier_resolution.tier_allowlist,
        tier_effective_allowlist=tier_resolution.tier_effective_allowlist,
        tier_applied=tier_resolution.tier_applied,
        tier_authoritative=tier_resolution.tier_authoritative,
        tier_unavailable_reason=tier_resolution.tier_unavailable_reason,
        tier_shadow_mismatch=tier_resolution.tier_shadow_mismatch,
    )


def filter_visible_models(
    model_ids: Iterable[str],
    auth: UserAPIKeyAuth,
    *,
    callable_target_grant_service: CallableTargetGrantService | None = None,
    tier_policy_service: TierPolicyService | None = None,
    policy_mode: CallableTargetPolicyMode | str = "enforce",
    tier_policy_mode: TierPolicyMode | str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
    emit_shadow_log: bool = False,
) -> list[str]:
    allowed_models = resolve_effective_model_allowlist(
        auth,
        callable_target_grant_service=callable_target_grant_service,
        tier_policy_service=tier_policy_service,
        policy_mode=policy_mode,
        tier_policy_mode=tier_policy_mode,
        tier_policy_missing_service_mode=tier_policy_missing_service_mode,
        emit_shadow_log=emit_shadow_log,
    )
    if allowed_models is None:
        return list(model_ids)
    return [model_id for model_id in model_ids if model_id in allowed_models]


def ensure_model_allowed(
    auth: UserAPIKeyAuth,
    model: str,
    *,
    callable_target_grant_service: CallableTargetGrantService | None = None,
    tier_policy_service: TierPolicyService | None = None,
    policy_mode: CallableTargetPolicyMode | str = "enforce",
    tier_policy_mode: TierPolicyMode | str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
    emit_shadow_log: bool = False,
) -> None:
    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=callable_target_grant_service,
        tier_policy_service=tier_policy_service,
        policy_mode=policy_mode,
        tier_policy_mode=tier_policy_mode,
        tier_policy_missing_service_mode=tier_policy_missing_service_mode,
        emit_shadow_log=emit_shadow_log,
    )
    if log_tier_policy_denial_if_applicable(
        auth,
        model,
        pre_tier_allowlist=resolution.pre_tier_allowlist,
        tier_resolution=resolution,
        tier_policy_service=tier_policy_service,
    ):
        raise PermissionDeniedError(message=model_not_allowed_message(model))
    if resolution.effective_allowlist is not None and model not in resolution.effective_allowlist:
        raise PermissionDeniedError(message=model_not_allowed_message(model))


def ensure_batch_model_allowed(
    auth: UserAPIKeyAuth,
    model: str,
    *,
    callable_target_grant_service: CallableTargetGrantService | None = None,
    tier_policy_service: TierPolicyService | None = None,
    policy_mode: CallableTargetPolicyMode | str = "enforce",
    tier_policy_mode: TierPolicyMode | str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
    emit_shadow_log: bool = False,
) -> None:
    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=callable_target_grant_service,
        tier_policy_service=tier_policy_service,
        policy_mode=policy_mode,
        tier_policy_mode=tier_policy_mode,
        tier_policy_missing_service_mode=tier_policy_missing_service_mode,
        emit_shadow_log=emit_shadow_log,
    )
    if log_tier_policy_denial_if_applicable(
        auth,
        model,
        pre_tier_allowlist=resolution.pre_tier_allowlist,
        tier_resolution=resolution,
        tier_policy_service=tier_policy_service,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=model_not_allowed_message(model),
        )
    if resolution.effective_allowlist is not None and model not in resolution.effective_allowlist:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=model_not_allowed_message(model),
        )


def model_not_allowed_message(model: str) -> str:
    return f"Model '{model}' is not allowed for this key"


def get_callable_target_policy_mode_from_app(app: Any) -> CallableTargetPolicyMode:
    return normalize_callable_target_policy_mode(
        _app_setting(
            app,
            "callable_target_scope_policy_mode",
            default="enforce",
        )
    )


def get_tier_policy_mode_from_app(app: Any) -> TierPolicyMode:
    service = getattr(app.state, "tier_policy_service", None)
    if service is not None and hasattr(service, "mode"):
        return normalize_tier_policy_mode(getattr(service, "mode"))
    return normalize_tier_policy_mode(
        _app_setting(app, "tier_policy_mode", default="disabled")
    )


def get_tier_policy_missing_service_mode_from_app(app: Any) -> str:
    service = getattr(app.state, "tier_policy_service", None)
    if service is not None and hasattr(service, "missing_service_mode"):
        return normalize_tier_policy_missing_service_mode(getattr(service, "missing_service_mode"))
    return normalize_tier_policy_missing_service_mode(
        _app_setting(
            app,
            "tier_policy_missing_service_mode",
            default="fail_open",
        )
    )


def normalize_callable_target_policy_mode(value: object) -> CallableTargetPolicyMode:
    normalized = str(value or "").strip().lower()
    if normalized == "legacy":
        return "enforce"
    if normalized not in _ALLOWED_POLICY_MODES:
        return "enforce"
    return normalized  # type: ignore[return-value]


def normalize_tier_policy_missing_service_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"fail_open", "fail_closed"}:
        return "fail_open"
    return normalized


def _resolve_direct_restrict_allowlist(
    auth: UserAPIKeyAuth,
    *,
    callable_target_grant_service: CallableTargetGrantService | None,
    policy_mode: CallableTargetPolicyMode,
    policy_fallback_reason: str | None,
) -> frozenset[str] | None:
    if policy_mode == "shadow":
        return None
    if policy_mode == "enforce" and policy_fallback_reason is not None:
        return frozenset()
    if callable_target_grant_service is None:
        return None

    resolver = getattr(callable_target_grant_service, "resolve_direct_restrict_allowlist", None)
    if not callable(resolver):
        return None

    direct_restrict_allowlist = resolver(auth)
    if direct_restrict_allowlist is None:
        return None
    return frozenset(_normalize_allowlist(direct_restrict_allowlist))


def _app_setting(app: Any, field_name: str, *, default: Any) -> Any:
    app_config = getattr(app.state, "app_config", None)
    general_settings = getattr(app_config, "general_settings", None)
    value = _explicit_general_setting(general_settings, field_name)
    if value is not _MISSING:
        return value
    settings = getattr(app.state, "settings", None)
    return getattr(settings, field_name, default)


def _explicit_general_setting(general_settings: Any, field_name: str) -> Any:
    if general_settings is None:
        return _MISSING
    fields_set = getattr(general_settings, "model_fields_set", None)
    if fields_set is not None and field_name not in fields_set:
        return _MISSING
    return getattr(general_settings, field_name, _MISSING)


def _normalize_allowlist(values: Iterable[str] | None) -> set[str]:
    normalized: set[str] = set()
    if values is None:
        return normalized
    for value in values:
        item = str(value).strip()
        if item:
            normalized.add(item)
    return normalized


def _resolve_legacy_model_allowlist(auth: UserAPIKeyAuth) -> set[str] | None:
    allowlists = [
        _normalize_allowlist(auth.models),
        _normalize_allowlist(auth.team_models),
    ]
    scoped_allowlists = [allowlist for allowlist in allowlists if allowlist]
    if not scoped_allowlists:
        return None

    effective = set(scoped_allowlists[0])
    for allowlist in scoped_allowlists[1:]:
        effective.intersection_update(allowlist)
    return effective


def _allowlists_differ(left: set[str] | None, right: set[str] | None) -> bool:
    if left is None and right is None:
        return False
    if left is None or right is None:
        return True
    return left != right


def _log_policy_shadow_mismatch(
    auth: UserAPIKeyAuth,
    *,
    hybrid_allowlist: set[str] | None,
    policy_allowlist: set[str] | None,
    fallback_reason: str | None,
) -> None:
    scope_context = resolve_runtime_scope_context(auth)
    hybrid = hybrid_allowlist or set()
    policy = policy_allowlist or set()
    removed_models = sorted(hybrid - policy)
    added_models = sorted(policy - hybrid)
    difference_type = _difference_type(removed_models=removed_models, added_models=added_models)
    increment_callable_target_policy_shadow_mismatch(
        auth_source=scope_context.auth_source,
        difference_type=difference_type,
        fallback_reason=fallback_reason,
    )
    logger.info(
        "callable_target_policy_shadow_mismatch",
        extra={
            "actor_id": scope_context.actor_id,
            "auth_source": scope_context.auth_source,
            "organization_id": scope_context.organization_id,
            "team_id": scope_context.team_id,
            "api_key_scope_id": scope_context.api_key_scope_id,
            "hybrid_count": len(hybrid),
            "policy_count": len(policy),
            "removed_models": removed_models,
            "added_models": added_models,
            "difference_type": difference_type,
            "fallback_reason": fallback_reason,
        },
    )


def _difference_type(*, removed_models: list[str], added_models: list[str]) -> str:
    if removed_models and added_models:
        return "both"
    if removed_models:
        return "removed_only"
    if added_models:
        return "added_only"
    return "none"
