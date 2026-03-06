from __future__ import annotations

from typing import Any

from src.router.router import RoutingStrategy

ALLOWED_POLICY_MODES = {"fallback", "weighted", "conditional", "adaptive"}
ALLOWED_POLICY_KEYS = {"mode", "strategy", "members", "timeouts", "retry", "conditions", "constraints", "health"}
ALLOWED_TIMEOUT_KEYS = {"global_ms", "global_seconds", "member_ms", "member_seconds"}
ALLOWED_RETRY_KEYS = {"max_attempts", "retryable_error_classes"}
ALLOWED_RETRYABLE_ERROR_CLASSES = {
    "timeout",
    "rate_limit",
    "context_window_exceeded",
    "content_policy_violation",
    "generic",
}
ALLOWED_CONSTRAINT_KEYS = {
    "provider_allowlist",
    "provider_denylist",
    "max_input_tokens",
    "max_output_tokens",
    "max_total_tokens",
    "max_cost_usd",
}
ALLOWED_HEALTH_KEYS = {"cooldown_seconds", "circuit_breaker_failures", "circuit_breaker_window_seconds"}


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
        for field_name in ("global_ms", "member_ms"):
            if field_name in timeouts:
                validated_timeouts[field_name] = _normalize_int(timeouts[field_name], f"timeouts.{field_name}", minimum=1)
        for field_name in ("global_seconds", "member_seconds"):
            if field_name in timeouts:
                validated_timeouts[field_name] = _normalize_float(timeouts[field_name], f"timeouts.{field_name}", minimum=0.001)
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

    if "conditions" in normalized:
        conditions = normalized.get("conditions")
        if not isinstance(conditions, list):
            raise ValueError("conditions must be a list")
        for idx, condition in enumerate(conditions):
            if not isinstance(condition, dict):
                raise ValueError(f"conditions[{idx}] must be an object")

    if "constraints" in normalized:
        constraints = normalized.get("constraints")
        if not isinstance(constraints, dict):
            raise ValueError("constraints must be an object")
        constraint_unknown = sorted([key for key in constraints.keys() if key not in ALLOWED_CONSTRAINT_KEYS])
        if constraint_unknown:
            raise ValueError(f"unknown constraints fields: {', '.join(constraint_unknown)}")
        validated_constraints: dict[str, Any] = {}
        for field_name in ("provider_allowlist", "provider_denylist"):
            if field_name in constraints:
                validated_constraints[field_name] = _normalize_string_list(constraints[field_name], f"constraints.{field_name}")
        for field_name in ("max_input_tokens", "max_output_tokens", "max_total_tokens"):
            if field_name in constraints:
                validated_constraints[field_name] = _normalize_int(constraints[field_name], f"constraints.{field_name}", minimum=1)
        if "max_cost_usd" in constraints:
            validated_constraints["max_cost_usd"] = _normalize_float(
                constraints["max_cost_usd"],
                "constraints.max_cost_usd",
                minimum=0.0,
            )
        normalized["constraints"] = validated_constraints

    if "health" in normalized:
        health = normalized.get("health")
        if not isinstance(health, dict):
            raise ValueError("health must be an object")
        health_unknown = sorted([key for key in health.keys() if key not in ALLOWED_HEALTH_KEYS])
        if health_unknown:
            raise ValueError(f"unknown health fields: {', '.join(health_unknown)}")
        validated_health: dict[str, Any] = {}
        if "cooldown_seconds" in health:
            validated_health["cooldown_seconds"] = _normalize_int(health["cooldown_seconds"], "health.cooldown_seconds", minimum=1)
        if "circuit_breaker_failures" in health:
            validated_health["circuit_breaker_failures"] = _normalize_int(
                health["circuit_breaker_failures"],
                "health.circuit_breaker_failures",
                minimum=1,
            )
        if "circuit_breaker_window_seconds" in health:
            validated_health["circuit_breaker_window_seconds"] = _normalize_int(
                health["circuit_breaker_window_seconds"],
                "health.circuit_breaker_window_seconds",
                minimum=1,
            )
        normalized["health"] = validated_health

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

    if "mode" in normalized and normalized["mode"] == "weighted":
        has_weight = any(isinstance(member, dict) and member.get("weight") is not None for member in active_members)
        if not has_weight:
            warnings.append("Weighted mode without explicit member weights will use deployment defaults.")

    return normalized, warnings
