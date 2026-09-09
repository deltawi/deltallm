from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from src.route_group_config import validate_context_routing_workload_mode
from src.router.router import RoutingStrategy
from src.router.selection.policy import (
    CONTEXT_POLICY_SEMANTICS_VERSION,
    LLMTierSelectorPolicy,
    RoutePolicyMember,
    SELECTOR_POLICY_SEMANTICS_VERSION,
    validate_selector_assignments,
)

ALLOWED_POLICY_MODES = {"fallback", "weighted", "conditional", "adaptive"}
POLICY_MODE_STRATEGY_ALIASES = {
    "fallback": RoutingStrategy.PRIORITY_BASED.value,
    "weighted": RoutingStrategy.WEIGHTED.value,
}
LEGACY_POLICY_KEYS = {"mode", "strategy", "members", "timeouts", "retry"}
CONTEXT_POLICY_KEYS = {*LEGACY_POLICY_KEYS, "context"}
ALLOWED_POLICY_KEYS = {*CONTEXT_POLICY_KEYS, "selector"}
ALLOWED_TIMEOUT_KEYS = {"global_ms", "global_seconds"}
ALLOWED_RETRY_KEYS = {"max_attempts", "retryable_error_classes"}
ALLOWED_CONTEXT_KEYS = {
    "mode",
    "unknown_capacity",
    "default_output_tokens",
    "safety_margin_tokens",
}
ALLOWED_RETRYABLE_ERROR_CLASSES = {
    "timeout",
    "rate_limit",
    "context_window_exceeded",
    "content_policy_violation",
    "generic",
}
LEGACY_POLICY_MEMBER_KEYS = {"deployment_id", "enabled", "weight", "priority"}
POLICY_MEMBER_KEYS = {*LEGACY_POLICY_MEMBER_KEYS, "lane"}
LEGACY_POLICY_SEMANTICS_VERSION = 1
CURRENT_POLICY_SEMANTICS_VERSION = SELECTOR_POLICY_SEMANTICS_VERSION


@dataclass(frozen=True, slots=True)
class PolicyMemberInventoryItem:
    deployment_id: str
    enabled: bool = True
    workload_mode: str | None = None
    model_info: Mapping[str, object] | None = None
    provider_model: str | None = None
    provider_name: str | None = None


class _SelectorWriteIntent(StrEnum):
    OMITTED = "omitted"
    REPLACED = "replaced"
    REMOVED = "removed"


class PolicyValidationKind(StrEnum):
    CLIENT_DOCUMENT = "client_document"
    STORED_DOCUMENT = "stored_document"
    SELECTOR_WRITE_FIELDS = "selector_write_fields"
    LEGACY_WRITE_FIELDS = "legacy_write_fields"


def _normalize_int(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if normalized < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return normalized


def _normalize_exact_int(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, int):
        normalized = value
    elif (
        isinstance(value, str)
        and value.strip()
        and value.strip().isascii()
        and value.strip().isdigit()
    ):
        normalized = int(value.strip())
    else:
        raise ValueError(f"{field_name} must be an integer")
    if normalized < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return normalized


def _normalize_context_choice(
    context: Mapping[str, Any],
    field_name: str,
    *,
    default: str,
    allowed: set[str],
) -> str:
    if field_name not in context:
        return default
    value = context[field_name]
    if not isinstance(value, str):
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"context.{field_name} must be one of: {choices}")
    normalized = value.strip().lower()
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"context.{field_name} must be one of: {choices}")
    return normalized


def _normalize_float(value: Any, field_name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if normalized < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return normalized


def _normalize_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    normalized: list[str] = []
    for idx, item in enumerate(value):
        entry = str(item or "").strip()
        if not entry:
            raise ValueError(f"{field_name}[{idx}] must be a non-empty string")
        normalized.append(entry)
    return normalized


def merge_policy_members(
    base_members: list[dict[str, Any]],
    policy_members: Any,
    *,
    semantics_version: int = CURRENT_POLICY_SEMANTICS_VERSION,
) -> list[dict[str, Any]]:
    """Resolve policy membership under the version persisted with the policy."""

    if not isinstance(policy_members, list):
        return [dict(member) for member in base_members]

    base_by_id = {
        str(member.get("deployment_id") or ""): dict(member)
        for member in base_members
        if isinstance(member, dict) and str(member.get("deployment_id") or "")
    }
    resolved: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for item in policy_members:
        if not isinstance(item, dict):
            continue
        deployment_id = str(item.get("deployment_id") or "").strip()
        base = base_by_id.get(deployment_id)
        if base is None:
            continue
        selected_ids.add(deployment_id)
        merged = dict(base)
        fields = ["weight", "priority"]
        if semantics_version >= SELECTOR_POLICY_SEMANTICS_VERSION:
            fields.append("lane")
        for field_name in fields:
            if field_name in item:
                merged[field_name] = item[field_name]
        # Group membership is the eligibility boundary. A policy may narrow it,
        # but must never reactivate a member disabled by an operator.
        merged["enabled"] = bool(base.get("enabled", True)) and bool(item.get("enabled", True))
        resolved.append(merged)
    if semantics_version <= LEGACY_POLICY_SEMANTICS_VERSION:
        resolved.extend(
            dict(member)
            for deployment_id, member in base_by_id.items()
            if deployment_id not in selected_ids
        )
    return resolved


def merge_policy_document_for_write(
    existing: dict[str, Any] | None,
    replacement: dict[str, Any],
    *,
    existing_semantics_version: int = CURRENT_POLICY_SEMANTICS_VERSION,
) -> dict[str, Any]:
    """Replace client-owned fields without promoting older opaque policy data."""

    current = existing if isinstance(existing, dict) else {}
    merged = {
        key: deepcopy(value) for key, value in current.items() if key not in ALLOWED_POLICY_KEYS
    }
    merged.update(deepcopy(replacement))

    for field_name, client_keys in (
        ("timeouts", ALLOWED_TIMEOUT_KEYS),
        ("retry", ALLOWED_RETRY_KEYS),
    ):
        current_value = current.get(field_name)
        replacement_value = replacement.get(field_name)
        current_mapping = current_value if isinstance(current_value, dict) else {}
        opaque = {
            key: deepcopy(value) for key, value in current_mapping.items() if key not in client_keys
        }
        if isinstance(replacement_value, dict):
            opaque.update(deepcopy(replacement_value))
        if opaque:
            merged[field_name] = opaque

    existing_context = (
        current.get("context")
        if existing_semantics_version >= CONTEXT_POLICY_SEMANTICS_VERSION
        else None
    )
    context = merge_context_policy_block(existing_context, replacement)
    if context is None:
        merged.pop("context", None)
    else:
        merged["context"] = context

    existing_selector = (
        current.get("selector")
        if existing_semantics_version >= SELECTOR_POLICY_SEMANTICS_VERSION
        else None
    )
    selector_write_intent = _selector_write_intent(replacement)
    selector = merge_selector_policy_block(existing_selector, replacement)
    if selector is None:
        merged.pop("selector", None)
    else:
        merged["selector"] = selector

    _merge_policy_members_for_write(
        merged=merged,
        current=current,
        replacement=replacement,
        selector_write_intent=selector_write_intent,
        existing_selector_owned=isinstance(existing_selector, dict),
    )

    return merged


def _selector_write_intent(replacement: Mapping[str, Any]) -> _SelectorWriteIntent:
    if "selector" not in replacement:
        return _SelectorWriteIntent.OMITTED
    if replacement.get("selector") is None:
        return _SelectorWriteIntent.REMOVED
    return _SelectorWriteIntent.REPLACED


def _merge_policy_members_for_write(
    *,
    merged: dict[str, Any],
    current: Mapping[str, Any],
    replacement: Mapping[str, Any],
    selector_write_intent: _SelectorWriteIntent,
    existing_selector_owned: bool,
) -> None:
    current_members = current.get("members")
    current_member_list = current_members if isinstance(current_members, list) else []
    replacement_members = replacement.get("members")
    if not isinstance(replacement_members, list):
        if (
            "members" not in replacement
            and existing_selector_owned
            and selector_write_intent is not _SelectorWriteIntent.REPLACED
        ):
            preserved_members = deepcopy(current_member_list)
            if selector_write_intent is _SelectorWriteIntent.REMOVED:
                for member in preserved_members:
                    if isinstance(member, dict):
                        member.pop("lane", None)
            merged["members"] = preserved_members
        return

    current_by_id = {
        str(member.get("deployment_id") or ""): member
        for member in current_member_list
        if isinstance(member, dict) and str(member.get("deployment_id") or "")
    }
    preserved_members: list[dict[str, Any]] = []
    for replacement_member in replacement_members:
        if not isinstance(replacement_member, dict):
            continue
        deployment_id = str(replacement_member.get("deployment_id") or "")
        current_member = current_by_id.get(deployment_id, {})
        member = {
            key: deepcopy(value)
            for key, value in current_member.items()
            if key not in POLICY_MEMBER_KEYS
        }
        member.update(deepcopy(replacement_member))
        if (
            existing_selector_owned
            and selector_write_intent is _SelectorWriteIntent.OMITTED
            and "lane" not in replacement_member
            and "lane" in current_member
        ):
            member["lane"] = deepcopy(current_member["lane"])
        if selector_write_intent is _SelectorWriteIntent.REMOVED:
            member.pop("lane", None)
        preserved_members.append(member)
    merged["members"] = preserved_members


def merge_context_policy_block(
    existing: object,
    replacement: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Preserve omitted context, while treating explicit null as a deletion tombstone."""

    current = existing if isinstance(existing, dict) else None
    if "context" not in replacement:
        return deepcopy(current) if current is not None else None
    replacement_context = replacement.get("context")
    if replacement_context is None:
        return None
    if not isinstance(replacement_context, dict):
        raise ValueError("context must be an object or null")
    merged = {
        key: deepcopy(value)
        for key, value in (current or {}).items()
        if key not in ALLOWED_CONTEXT_KEYS
    }
    merged.update(deepcopy(replacement_context))
    return merged


def merge_selector_policy_block(
    existing: object,
    replacement: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Preserve a v3 selector when omitted and remove it only through explicit null."""

    current = existing if isinstance(existing, dict) else None
    if "selector" not in replacement:
        return deepcopy(current) if current is not None else None
    replacement_selector = replacement.get("selector")
    if replacement_selector is None:
        return None
    if not isinstance(replacement_selector, dict):
        raise ValueError("selector must be an object or null")
    return deepcopy(replacement_selector)


def _validate_policy_mode(normalized: dict[str, Any]) -> str | None:
    if "mode" not in normalized:
        return None
    mode = str(normalized.get("mode") or "").strip().lower()
    if mode not in ALLOWED_POLICY_MODES:
        allowed = ", ".join(sorted(ALLOWED_POLICY_MODES))
        raise ValueError(f"mode must be one of: {allowed}")
    if mode in {"conditional", "adaptive"}:
        raise ValueError(
            f"mode '{mode}' is not supported by the runtime; use a concrete strategy instead"
        )
    normalized["mode"] = mode
    return mode


def _validate_policy_strategy(normalized: dict[str, Any]) -> None:
    if "strategy" not in normalized:
        return
    strategy = str(normalized.get("strategy") or "").strip()
    if strategy and strategy not in RoutingStrategy._value2member_map_:
        allowed = ", ".join(item.value for item in RoutingStrategy)
        raise ValueError(f"strategy must be one of: {allowed}")
    normalized["strategy"] = strategy or None


def _ignored_fields_warning(path: str, fields: list[str]) -> str:
    return f"Ignored opaque {path} fields: {', '.join(fields)}"


def _validate_policy_members(
    normalized: dict[str, Any],
    warnings: list[str],
    *,
    semantics_version: int,
    selector_enabled: bool,
    stored_document: bool,
) -> None:
    members = normalized.get("members")
    if "members" not in normalized:
        return
    if not isinstance(members, list):
        raise ValueError("members must be a list")

    validated_members: list[dict[str, Any]] = []
    seen_member_ids: set[str] = set()
    for idx, raw_member in enumerate(members):
        if not isinstance(raw_member, dict):
            raise ValueError(f"members[{idx}] must be an object")
        if selector_enabled:
            selector_member = raw_member
            if stored_document:
                unknown = sorted(key for key in raw_member if key not in POLICY_MEMBER_KEYS)
                if unknown:
                    warnings.append(_ignored_fields_warning(f"members[{idx}]", unknown))
                selector_member = {
                    key: value for key, value in raw_member.items() if key in POLICY_MEMBER_KEYS
                }
            try:
                strict_member = RoutePolicyMember.model_validate(selector_member)
            except ValidationError as exc:
                messages = "; ".join(error["msg"] for error in exc.errors(include_input=False))
                raise ValueError(f"members[{idx}] is invalid: {messages}") from exc
            member = strict_member.model_dump(mode="python", exclude_none=True)
            if "lane" in raw_member:
                member["lane"] = strict_member.lane
            deployment_id = strict_member.deployment_id
            if deployment_id in seen_member_ids:
                raise ValueError(f"members[{idx}].deployment_id is duplicated")
            seen_member_ids.add(deployment_id)
            validated_members.append(member)
            continue

        deployment_id = str(raw_member.get("deployment_id") or "").strip()
        if not deployment_id:
            raise ValueError(f"members[{idx}].deployment_id is required")
        if deployment_id in seen_member_ids:
            raise ValueError(f"members[{idx}].deployment_id is duplicated")
        seen_member_ids.add(deployment_id)
        allowed_member_keys = (
            POLICY_MEMBER_KEYS
            if semantics_version >= SELECTOR_POLICY_SEMANTICS_VERSION
            else LEGACY_POLICY_MEMBER_KEYS
        )
        unknown = sorted(key for key in raw_member if key not in allowed_member_keys)
        if unknown:
            warnings.append(_ignored_fields_warning(f"members[{idx}]", unknown))
        member: dict[str, Any] = {
            "deployment_id": deployment_id,
            "enabled": bool(raw_member.get("enabled", True)),
        }
        if raw_member.get("weight") is not None:
            member["weight"] = _normalize_int(
                raw_member["weight"], f"members[{idx}].weight", minimum=1
            )
        if raw_member.get("priority") is not None:
            member["priority"] = _normalize_int(
                raw_member["priority"], f"members[{idx}].priority", minimum=0
            )
        if (
            semantics_version >= SELECTOR_POLICY_SEMANTICS_VERSION
            and raw_member.get("lane") is not None
        ):
            member["lane"] = str(raw_member["lane"]).strip()
        validated_members.append(member)
    normalized["members"] = validated_members


def _validate_selector(
    normalized: dict[str, Any],
    *,
    available_members: Mapping[str, PolicyMemberInventoryItem] | None,
    group_mode: str | None,
    semantics_version: int,
) -> None:
    if semantics_version < SELECTOR_POLICY_SEMANTICS_VERSION:
        return

    raw_selector = normalized.get("selector")
    if raw_selector is None:
        members = normalized.get("members")
        if isinstance(members, list) and any(
            isinstance(member, dict) and member.get("lane") is not None for member in members
        ):
            raise ValueError("member lanes require a selector")
        return
    if "members" not in normalized:
        raise ValueError("selector policies must provide an explicit members list")

    try:
        selector = LLMTierSelectorPolicy.model_validate(raw_selector)
        members = [RoutePolicyMember.model_validate(member) for member in normalized["members"]]
        validate_selector_assignments(
            selector,
            members,
            group_mode=group_mode,
            available_members=available_members,
        )
    except ValidationError as exc:
        messages = "; ".join(error["msg"] for error in exc.errors(include_input=False))
        raise ValueError(f"selector is invalid: {messages}") from exc

    normalized["selector"] = selector.model_dump(mode="json")


def _validate_policy_timeouts(normalized: dict[str, Any], warnings: list[str]) -> None:
    if "timeouts" not in normalized:
        return
    timeouts = normalized.get("timeouts")
    if not isinstance(timeouts, dict):
        raise ValueError("timeouts must be an object")
    unknown = sorted(key for key in timeouts if key not in ALLOWED_TIMEOUT_KEYS)
    if unknown:
        warnings.append(_ignored_fields_warning("timeouts", unknown))

    validated: dict[str, Any] = {}
    if "global_ms" in timeouts:
        validated["global_ms"] = _normalize_int(
            timeouts["global_ms"], "timeouts.global_ms", minimum=1
        )
    if "global_seconds" in timeouts:
        validated["global_seconds"] = _normalize_float(
            timeouts["global_seconds"], "timeouts.global_seconds", minimum=0.001
        )
    normalized["timeouts"] = validated


def _validate_policy_retry(normalized: dict[str, Any], warnings: list[str]) -> None:
    if "retry" not in normalized:
        return
    retry = normalized.get("retry")
    if not isinstance(retry, dict):
        raise ValueError("retry must be an object")
    unknown = sorted(key for key in retry if key not in ALLOWED_RETRY_KEYS)
    if unknown:
        warnings.append(_ignored_fields_warning("retry", unknown))

    validated: dict[str, Any] = {}
    if "max_attempts" in retry:
        validated["max_attempts"] = _normalize_int(
            retry["max_attempts"], "retry.max_attempts", minimum=0
        )
    if "retryable_error_classes" in retry:
        values = _normalize_string_list(
            retry["retryable_error_classes"], "retry.retryable_error_classes"
        )
        invalid = sorted(set(values) - ALLOWED_RETRYABLE_ERROR_CLASSES)
        if invalid:
            allowed = ", ".join(sorted(ALLOWED_RETRYABLE_ERROR_CLASSES))
            raise ValueError(f"retry.retryable_error_classes values must be one of: {allowed}")
        validated["retryable_error_classes"] = values
    normalized["retry"] = validated


def _validate_context_policy(
    normalized: dict[str, Any],
    warnings: list[str],
    *,
    workload_mode: object | None,
) -> None:
    if "context" not in normalized:
        return
    context = normalized.get("context")
    if context is None:
        return
    if not isinstance(context, dict):
        raise ValueError("context must be an object or null")
    unknown = sorted(key for key in context if key not in ALLOWED_CONTEXT_KEYS)
    if unknown:
        warnings.append(_ignored_fields_warning("context", unknown))

    mode = _normalize_context_choice(
        context,
        "mode",
        default="eligible-only",
        allowed={"eligible-only", "smallest-sufficient"},
    )
    unknown_capacity = _normalize_context_choice(
        context,
        "unknown_capacity",
        default="allow",
        allowed={"allow", "exclude"},
    )

    validate_context_routing_workload_mode(workload_mode)

    normalized["context"] = {
        "mode": mode,
        "unknown_capacity": unknown_capacity,
        "default_output_tokens": _normalize_exact_int(
            context.get("default_output_tokens", 1024),
            "context.default_output_tokens",
        ),
        "safety_margin_tokens": _normalize_exact_int(
            context.get("safety_margin_tokens", 256),
            "context.safety_margin_tokens",
        ),
    }


def _validate_member_pool(
    normalized: dict[str, Any],
    available_members: Mapping[str, PolicyMemberInventoryItem] | None,
    *,
    semantics_version: int,
) -> list[dict[str, Any]]:
    valid_ids = set(available_members or {})
    members = normalized.get("members", [])
    if available_members is not None:
        referenced_ids = {
            str(member.get("deployment_id") or "").strip()
            for member in members
            if isinstance(member, dict)
        }
        unknown = sorted(member_id for member_id in referenced_ids if member_id not in valid_ids)
        if unknown:
            raise ValueError(f"policy references unknown members: {', '.join(unknown)}")

    if available_members is None:
        active = [member for member in members if bool(member.get("enabled", True))]
    else:
        base_members = [
            {
                "deployment_id": member_id,
                "enabled": available_members[member_id].enabled,
            }
            for member_id in sorted(valid_ids)
        ]
        effective = merge_policy_members(
            base_members,
            members if "members" in normalized else None,
            semantics_version=semantics_version,
        )
        active = [member for member in effective if bool(member.get("enabled", True))]
    if not active:
        raise ValueError("policy results in empty active member pool")
    return active


def _apply_policy_mode(
    normalized: dict[str, Any],
    active_members: list[dict[str, Any]],
    warnings: list[str],
    *,
    mode: str | None,
) -> None:
    expected_strategy = POLICY_MODE_STRATEGY_ALIASES.get(mode or "")
    if expected_strategy:
        warnings.append(f"Policy mode '{mode}' is deprecated; use strategy '{expected_strategy}'.")
        strategy = normalized.get("strategy")
        if strategy in (None, ""):
            normalized["strategy"] = expected_strategy
        elif strategy != expected_strategy:
            warnings.append(
                f"{mode.title()} mode is advisory when strategy is set explicitly; "
                "strategy takes precedence."
            )

    if mode == "fallback" and "members" in normalized:
        for index, member in enumerate(normalized["members"]):
            if member.get("priority") is None:
                member["priority"] = index
    if mode == "weighted" and not any(
        member.get("weight") is not None for member in active_members
    ):
        warnings.append(
            "Weighted mode without explicit member weights will use deployment defaults."
        )


def _validate_route_policy_document(
    payload: dict[str, Any],
    *,
    available_members: Mapping[str, PolicyMemberInventoryItem] | None = None,
    semantics_version: int = CURRENT_POLICY_SEMANTICS_VERSION,
    workload_mode: object | None = None,
    validation_kind: PolicyValidationKind,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("policy payload must be an object")
    selector_enabled = (
        semantics_version >= SELECTOR_POLICY_SEMANTICS_VERSION
        and payload.get("selector") is not None
    ) or validation_kind is PolicyValidationKind.SELECTOR_WRITE_FIELDS
    stored_document = validation_kind is PolicyValidationKind.STORED_DOCUMENT
    if semantics_version >= SELECTOR_POLICY_SEMANTICS_VERSION:
        allowed_policy_keys = ALLOWED_POLICY_KEYS
    elif semantics_version >= CONTEXT_POLICY_SEMANTICS_VERSION:
        allowed_policy_keys = CONTEXT_POLICY_KEYS
    else:
        allowed_policy_keys = LEGACY_POLICY_KEYS
    unknown = sorted(key for key in payload if key not in allowed_policy_keys)
    warnings: list[str] = []
    if unknown:
        if selector_enabled and not stored_document:
            raise ValueError(f"selector policies contain unknown fields: {', '.join(unknown)}")
        warnings.append(_ignored_fields_warning("policy", unknown))

    normalized = {key: value for key, value in payload.items() if key in allowed_policy_keys}
    mode = _validate_policy_mode(normalized)
    _validate_policy_strategy(normalized)
    _validate_policy_members(
        normalized,
        warnings,
        semantics_version=semantics_version,
        selector_enabled=selector_enabled,
        stored_document=stored_document,
    )
    _validate_policy_timeouts(normalized, warnings)
    _validate_policy_retry(normalized, warnings)
    _validate_context_policy(normalized, warnings, workload_mode=workload_mode)
    if validation_kind in {
        PolicyValidationKind.SELECTOR_WRITE_FIELDS,
        PolicyValidationKind.LEGACY_WRITE_FIELDS,
    }:
        if normalized.get("selector") is not None:
            try:
                normalized["selector"] = LLMTierSelectorPolicy.model_validate(
                    normalized["selector"]
                ).model_dump(mode="json")
            except ValidationError as exc:
                raise ValueError("selector has invalid fields or types") from exc
        _apply_policy_mode(normalized, normalized.get("members", []), warnings, mode=mode)
        normalized.pop("mode", None)
        return normalized, warnings
    active_members = _validate_member_pool(
        normalized,
        available_members,
        semantics_version=semantics_version,
    )
    _apply_policy_mode(normalized, active_members, warnings, mode=mode)
    normalized.pop("mode", None)
    _validate_selector(
        normalized,
        available_members=available_members,
        group_mode=str(workload_mode) if workload_mode is not None else None,
        semantics_version=semantics_version,
    )
    return normalized, warnings


def validate_route_policy(
    payload: dict[str, Any],
    *,
    available_members: Mapping[str, PolicyMemberInventoryItem] | None = None,
    semantics_version: int = CURRENT_POLICY_SEMANTICS_VERSION,
    workload_mode: object | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Validate an untrusted client-authored policy document."""

    return _validate_route_policy_document(
        payload,
        available_members=available_members,
        semantics_version=semantics_version,
        workload_mode=workload_mode,
        validation_kind=PolicyValidationKind.CLIENT_DOCUMENT,
    )


def validate_stored_route_policy(
    payload: dict[str, Any],
    *,
    available_members: Mapping[str, PolicyMemberInventoryItem] | None = None,
    semantics_version: int = CURRENT_POLICY_SEMANTICS_VERSION,
    workload_mode: object | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Validate routing-owned fields while tolerating trusted opaque stored fields."""

    return _validate_route_policy_document(
        payload,
        available_members=available_members,
        semantics_version=semantics_version,
        workload_mode=workload_mode,
        validation_kind=PolicyValidationKind.STORED_DOCUMENT,
    )


def normalize_route_policy_write_fields(
    payload: dict[str, Any], *, selector_enabled: bool, workload_mode: str
) -> tuple[dict[str, Any], list[str]]:
    """Validate authored fields before merging; assignments need the locked durable base."""
    return _validate_route_policy_document(
        payload,
        workload_mode=workload_mode,
        validation_kind=(
            PolicyValidationKind.SELECTOR_WRITE_FIELDS
            if selector_enabled
            else PolicyValidationKind.LEGACY_WRITE_FIELDS
        ),
    )
