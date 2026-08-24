from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from src.router.router import RoutingStrategy

ALLOWED_POLICY_MODES = {"fallback", "weighted", "conditional", "adaptive"}
ALLOWED_POLICY_KEYS = {"mode", "strategy", "members", "timeouts", "retry"}
ALLOWED_TIMEOUT_KEYS = {"global_ms", "global_seconds"}
ALLOWED_RETRY_KEYS = {"max_attempts", "retryable_error_classes"}
ALLOWED_RETRYABLE_ERROR_CLASSES = {
    "timeout",
    "rate_limit",
    "context_window_exceeded",
    "content_policy_violation",
    "generic",
}
POLICY_MEMBER_KEYS = {"deployment_id", "enabled", "weight", "priority"}
LEGACY_POLICY_SEMANTICS_VERSION = 1
CURRENT_POLICY_SEMANTICS_VERSION = 2


@dataclass(frozen=True, slots=True)
class PolicyMemberInventoryItem:
    deployment_id: str
    enabled: bool = True
    workload_mode: str | None = None


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
        for field_name in ("weight", "priority"):
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
) -> dict[str, Any]:
    """Replace client-owned policy fields while retaining opaque stored fields."""

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

    replacement_members = replacement.get("members")
    if isinstance(replacement_members, list):
        current_members = current.get("members")
        current_member_list = current_members if isinstance(current_members, list) else []
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
            preserved_members.append(member)
        merged["members"] = preserved_members

    return merged


def _validate_policy_mode(normalized: dict[str, Any]) -> None:
    if "mode" not in normalized:
        return
    mode = str(normalized.get("mode") or "").strip().lower()
    if mode not in ALLOWED_POLICY_MODES:
        allowed = ", ".join(sorted(ALLOWED_POLICY_MODES))
        raise ValueError(f"mode must be one of: {allowed}")
    if mode in {"conditional", "adaptive"}:
        raise ValueError(
            f"mode '{mode}' is not supported by the runtime; use a concrete strategy instead"
        )
    normalized["mode"] = mode


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


def _validate_policy_members(normalized: dict[str, Any], warnings: list[str]) -> None:
    members = normalized.get("members")
    if members is None:
        return
    if not isinstance(members, list):
        raise ValueError("members must be a list")

    validated_members: list[dict[str, Any]] = []
    seen_member_ids: set[str] = set()
    for idx, raw_member in enumerate(members):
        if not isinstance(raw_member, dict):
            raise ValueError(f"members[{idx}] must be an object")
        deployment_id = str(raw_member.get("deployment_id") or "").strip()
        if not deployment_id:
            raise ValueError(f"members[{idx}].deployment_id is required")
        if deployment_id in seen_member_ids:
            raise ValueError(f"members[{idx}].deployment_id is duplicated")
        seen_member_ids.add(deployment_id)
        unknown = sorted(key for key in raw_member if key not in POLICY_MEMBER_KEYS)
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
        validated_members.append(member)
    normalized["members"] = validated_members


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
) -> None:
    mode = normalized.get("mode")
    expected_strategies = {
        "fallback": RoutingStrategy.PRIORITY_BASED.value,
        "weighted": RoutingStrategy.WEIGHTED.value,
    }
    expected_strategy = expected_strategies.get(mode)
    if expected_strategy:
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


def validate_route_policy(
    payload: dict[str, Any],
    *,
    available_members: Mapping[str, PolicyMemberInventoryItem] | None = None,
    semantics_version: int = CURRENT_POLICY_SEMANTICS_VERSION,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("policy payload must be an object")
    unknown = sorted(key for key in payload if key not in ALLOWED_POLICY_KEYS)
    warnings: list[str] = []
    if unknown:
        warnings.append(_ignored_fields_warning("policy", unknown))

    normalized = {key: value for key, value in payload.items() if key in ALLOWED_POLICY_KEYS}
    _validate_policy_mode(normalized)
    _validate_policy_strategy(normalized)
    _validate_policy_members(normalized, warnings)
    _validate_policy_timeouts(normalized, warnings)
    _validate_policy_retry(normalized, warnings)
    active_members = _validate_member_pool(
        normalized,
        available_members,
        semantics_version=semantics_version,
    )
    _apply_policy_mode(normalized, active_members, warnings)
    return normalized, warnings
