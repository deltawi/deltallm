from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any, Iterable

TIER_STATUSES = {"draft", "active", "archived"}
ASSIGNMENT_TYPES = {"primary", "addon", "override"}
ACCESS_MODES = {"allow", "deny"}
CAPACITY_STRATEGIES = {"hard_cap", "weighted_fair", "reserved_burst"}

PRICING_KEYS = {
    "input_cost_per_token",
    "output_cost_per_token",
    "input_cost_per_token_cache_hit",
    "output_cost_per_token_cache_hit",
    "batch_input_cost_per_token",
    "batch_output_cost_per_token",
    "batch_price_multiplier",
    "input_cost_per_character",
    "output_cost_per_character",
    "input_cost_per_second",
    "output_cost_per_second",
    "input_cost_per_image",
    "output_cost_per_image",
    "input_cost_per_audio_token",
    "output_cost_per_audio_token",
    "cost_per_request",
}

_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def normalize_tier_key(value: object) -> str:
    return _normalize_key(value, field_name="tier_key")


def normalize_pool_key(value: object) -> str:
    return _normalize_key(value, field_name="pool_key")


def normalize_callable_key(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("callable_key is required")
    return normalized


def normalize_status(value: object | None, *, default: str = "draft") -> str:
    return _normalize_enum(value, field_name="status", allowed=TIER_STATUSES, default=default)


def normalize_assignment_type(value: object | None, *, default: str = "primary") -> str:
    return _normalize_enum(
        value,
        field_name="assignment_type",
        allowed=ASSIGNMENT_TYPES,
        default=default,
    )


def normalize_access_mode(value: object | None, *, default: str = "allow") -> str:
    return _normalize_enum(value, field_name="access_mode", allowed=ACCESS_MODES, default=default)


def normalize_capacity_strategy(value: object | None, *, default: str = "hard_cap") -> str:
    return _normalize_enum(
        value,
        field_name="strategy",
        allowed=CAPACITY_STRATEGIES,
        default=default,
    )


def positive_int_or_none(value: object | None, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if not re.fullmatch(r"\d+", raw):
            raise ValueError(f"{field_name} must be a positive integer")
        normalized = int(raw)
    else:
        raise ValueError(f"{field_name} must be a positive integer")
    if normalized <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return normalized


def positive_weight(value: object | None, *, default: int = 1) -> int:
    normalized = positive_int_or_none(default if value is None else value, "weight")
    if normalized is None:
        raise ValueError("weight must be a positive integer")
    return normalized


def non_negative_float(value: object, field_name: str) -> float:
    normalized = _finite_float(value, field_name, requirement="a non-negative number")
    if normalized < 0:
        raise ValueError(f"{field_name} must be a non-negative number")
    return normalized


def positive_float_or_none(value: object | None, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    normalized = _finite_float(value, field_name, requirement="a positive number")
    if normalized <= 0:
        raise ValueError(f"{field_name} must be a positive number")
    return normalized


def ratio_gt_zero_lte_one_or_none(value: object | None, field_name: str) -> float | None:
    normalized = positive_float_or_none(value, field_name)
    if normalized is None:
        return None
    if normalized > 1:
        raise ValueError(f"{field_name} must be greater than 0 and less than or equal to 1")
    return normalized


def float_gte_one_or_none(value: object | None, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    normalized = _finite_float(value, field_name, requirement="greater than or equal to 1")
    if normalized < 1:
        raise ValueError(f"{field_name} must be greater than or equal to 1")
    return normalized


def normalize_pricing(pricing: object | None) -> dict[str, float] | None:
    if pricing is None:
        return None
    if not isinstance(pricing, dict):
        raise ValueError("pricing must be an object")

    normalized: dict[str, float] = {}
    for raw_key, raw_value in pricing.items():
        key = str(raw_key).strip()
        if not key:
            continue
        if key not in PRICING_KEYS:
            raise ValueError(f"pricing field is unsupported: {key}")
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            continue
        normalized[key] = non_negative_float(raw_value, f"pricing.{key}")
    return normalized or None


def normalize_metadata(metadata: object | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    return dict(metadata)


def validate_effective_window(
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    starts_at = _datetime_utc_or_none(starts_at)
    ends_at = _datetime_utc_or_none(ends_at)
    if starts_at is not None and ends_at is not None and starts_at >= ends_at:
        raise ValueError("starts_at must be before ends_at")
    return starts_at, ends_at


def ensure_single_active_primary_assignment(
    assignments: Iterable[Any],
    *,
    reference_time: datetime | None = None,
) -> None:
    now = _datetime_utc_or_none(reference_time) or datetime.now(UTC)
    active_primary_count = 0
    for assignment in assignments:
        if not bool(getattr(assignment, "enabled", False)):
            continue
        if str(getattr(assignment, "assignment_type", "") or "").strip().lower() != "primary":
            continue
        starts_at = _datetime_utc_or_none(getattr(assignment, "starts_at", None))
        ends_at = _datetime_utc_or_none(getattr(assignment, "ends_at", None))
        if starts_at is not None and starts_at > now:
            continue
        if ends_at is not None and ends_at <= now:
            continue
        active_primary_count += 1
        if active_primary_count > 1:
            raise ValueError("organization can only have one active primary tier assignment")


def _normalize_key(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if not _KEY_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must use lowercase letters, numbers, underscores, or hyphens"
        )
    return normalized


def _normalize_enum(
    value: object | None,
    *,
    field_name: str,
    allowed: set[str],
    default: str,
) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of: {allowed_values}")
    return normalized


def _finite_float(value: object, field_name: str, *, requirement: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be {requirement}")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be {requirement}") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be {requirement}")
    return normalized


def _datetime_utc_or_none(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
