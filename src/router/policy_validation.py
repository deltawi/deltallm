from __future__ import annotations

from copy import deepcopy
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


def validate_route_policy(
    payload: dict[str, Any],
    *,
    available_member_ids: set[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("policy payload must be an object")

    unknown = sorted([key for key in payload.keys() if key not in ALLOWED_POLICY_KEYS])
    if unknown:
        raise ValueError(f"unknown policy fields: {', '.join(unknown)}")

    normalized = dict(payload)
    warnings: list[str] = []

    if "mode" in normalized:
        mode = str(normalized.get("mode") or "").strip().lower()
        if mode not in ALLOWED_POLICY_MODES:
            allowed = ", ".join(sorted(ALLOWED_POLICY_MODES))
            raise ValueError(f"mode must be one of: {allowed}")
        if mode == "conditional":
            raise ValueError("mode 'conditional' is not supported by the runtime; use a concrete strategy instead")
        if mode == "adaptive":
            raise ValueError("mode 'adaptive' is not supported by the runtime; use a concrete strategy instead")
        normalized["mode"] = mode

    if "strategy" in normalized:
        strategy = str(normalized.get("strategy") or "").strip()
        if strategy and strategy not in RoutingStrategy._value2member_map_:
            allowed = ", ".join(item.value for item in RoutingStrategy)
            raise ValueError(f"strategy must be one of: {allowed}")
        normalized["strategy"] = strategy or None

    members = normalized.get("members")
    if members is not None:
        if not isinstance(members, list):
            raise ValueError("members must be a list")

        validated_members: list[dict[str, Any]] = []
        for idx, raw_member in enumerate(members):
            if not isinstance(raw_member, dict):
                raise ValueError(f"members[{idx}] must be an object")
            deployment_id = str(raw_member.get("deployment_id") or "").strip()
            if not deployment_id:
                raise ValueError(f"members[{idx}].deployment_id is required")

            member = {
                "deployment_id": deployment_id,
                "enabled": bool(raw_member.get("enabled", True)),
            }
            weight = raw_member.get("weight")
            if weight is not None:
                member["weight"] = _normalize_int(weight, f"members[{idx}].weight", minimum=1)
            priority = raw_member.get("priority")
            if priority is not None:
                member["priority"] = _normalize_int(priority, f"members[{idx}].priority", minimum=0)
            validated_members.append(member)
        normalized["members"] = validated_members

    if "timeouts" in normalized:
        timeouts = normalized.get("timeouts")
        if not isinstance(timeouts, dict):
            raise ValueError("timeouts must be an object")
        timeout_unknown = sorted([key for key in timeouts.keys() if key not in ALLOWED_TIMEOUT_KEYS])
        if timeout_unknown:
            raise ValueError(f"unknown timeouts fields: {', '.join(timeout_unknown)}")
        validated_timeouts: dict[str, Any] = {}
        if "global_ms" in timeouts:
            validated_timeouts["global_ms"] = _normalize_int(timeouts["global_ms"], "timeouts.global_ms", minimum=1)
        if "global_seconds" in timeouts:
            validated_timeouts["global_seconds"] = _normalize_float(
                timeouts["global_seconds"],
                "timeouts.global_seconds",
                minimum=0.001,
            )
        normalized["timeouts"] = validated_timeouts

    if "retry" in normalized:
        retry = normalized.get("retry")
        if not isinstance(retry, dict):
            raise ValueError("retry must be an object")
        retry_unknown = sorted([key for key in retry.keys() if key not in ALLOWED_RETRY_KEYS])
        if retry_unknown:
            raise ValueError(f"unknown retry fields: {', '.join(retry_unknown)}")
        validated_retry: dict[str, Any] = {}
        if "max_attempts" in retry:
            validated_retry["max_attempts"] = _normalize_int(retry["max_attempts"], "retry.max_attempts", minimum=0)
        if "retryable_error_classes" in retry:
            values = _normalize_string_list(retry["retryable_error_classes"], "retry.retryable_error_classes")
            invalid = sorted(set(values) - ALLOWED_RETRYABLE_ERROR_CLASSES)
            if invalid:
                allowed = ", ".join(sorted(ALLOWED_RETRYABLE_ERROR_CLASSES))
                raise ValueError(f"retry.retryable_error_classes values must be one of: {allowed}")
            validated_retry["retryable_error_classes"] = values
        normalized["retry"] = validated_retry

    valid_member_ids = {item.strip() for item in (available_member_ids or set()) if item and item.strip()}
    if valid_member_ids:
        referenced_ids = {
            str(member.get("deployment_id") or "").strip()
            for member in normalized.get("members", [])
            if isinstance(member, dict)
        }
        unknown_refs = sorted([member_id for member_id in referenced_ids if member_id and member_id not in valid_member_ids])
        if unknown_refs:
            raise ValueError(f"policy references unknown members: {', '.join(unknown_refs)}")

    if "members" in normalized:
        active_members = [member for member in normalized["members"] if bool(member.get("enabled", True))]
    else:
        active_members = [{"deployment_id": member_id, "enabled": True} for member_id in sorted(valid_member_ids)]
    if not active_members:
        raise ValueError("policy results in empty active member pool")

    mode = normalized.get("mode")
    strategy = normalized.get("strategy")
    if mode == "fallback":
        if strategy in (None, ""):
            normalized["strategy"] = RoutingStrategy.PRIORITY_BASED.value
        elif strategy != RoutingStrategy.PRIORITY_BASED.value:
            warnings.append("Fallback mode is advisory when strategy is set explicitly; strategy takes precedence.")
        if "members" in normalized:
            prioritized_members: list[dict[str, Any]] = []
            for index, member in enumerate(normalized["members"]):
                updated_member = deepcopy(member)
                if updated_member.get("priority") is None:
                    updated_member["priority"] = index
                prioritized_members.append(updated_member)
            normalized["members"] = prioritized_members
    elif mode == "weighted":
        if strategy in (None, ""):
            normalized["strategy"] = RoutingStrategy.WEIGHTED.value
        elif strategy != RoutingStrategy.WEIGHTED.value:
            warnings.append("Weighted mode is advisory when strategy is set explicitly; strategy takes precedence.")

    if "mode" in normalized and normalized["mode"] == "weighted":
        has_weight = any(isinstance(member, dict) and member.get("weight") is not None for member in active_members)
        if not has_weight:
            warnings.append("Weighted mode without explicit member weights will use deployment defaults.")

    return normalized, warnings
