from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from src.db.tiers import OrganizationTierAssignmentRecord
from src.services.tiers import (
    normalize_assignment_type,
    normalize_metadata,
    positive_weight,
    validate_effective_window,
)


_NULLABLE_PATCH_FIELDS = {"tier_version_id", "starts_at", "ends_at", "metadata"}


def normalize_assignment_create(payload: Mapping[str, Any]) -> dict[str, Any]:
    tier_id = _nonempty_string(payload.get("tier_id"), field_name="tier_id")
    starts_at, ends_at = _effective_window_from_payload(payload)
    return {
        "tier_id": tier_id,
        "tier_version_id": _optional_nonempty_string(
            payload.get("tier_version_id"),
            field_name="tier_version_id",
        ),
        "assignment_type": _assignment_type_value(
            payload.get("assignment_type"),
            default="primary",
        ),
        "enabled": _bool_value(payload.get("enabled", True), field_name="enabled"),
        "weight": positive_weight(payload.get("weight", 1)),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "metadata": normalize_metadata(payload.get("metadata")),
    }


def normalize_assignment_patch(
    payload: Mapping[str, Any],
    *,
    existing: OrganizationTierAssignmentRecord,
) -> dict[str, Any]:
    if not payload:
        raise ValueError("At least one assignment field is required")

    starts_at = _patch_value(payload, existing.starts_at, "starts_at")
    ends_at = _patch_value(payload, existing.ends_at, "ends_at")
    starts_at, ends_at = validate_effective_window(starts_at, ends_at)

    tier_id = _nonempty_string(
        _non_nullable_patch_value(payload, existing.tier_id, "tier_id"),
        field_name="tier_id",
    )
    tier_version_id = _patch_value(payload, existing.tier_version_id, "tier_version_id")
    if tier_version_id is not None:
        tier_version_id = _nonempty_string(tier_version_id, field_name="tier_version_id")

    metadata = (
        normalize_metadata(payload["metadata"]) if "metadata" in payload else existing.metadata
    )

    return {
        "tier_id": tier_id,
        "tier_version_id": tier_version_id,
        "assignment_type": _assignment_type_value(
            _non_nullable_patch_value(payload, existing.assignment_type, "assignment_type"),
            default=existing.assignment_type,
        ),
        "enabled": _bool_value(
            _non_nullable_patch_value(payload, existing.enabled, "enabled"),
            field_name="enabled",
        ),
        "weight": positive_weight(_non_nullable_patch_value(payload, existing.weight, "weight")),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "metadata": metadata,
    }


def _patch_value(
    payload: Mapping[str, Any],
    existing: Any,
    field_name: str,
) -> Any:
    if field_name not in payload:
        return existing
    if payload[field_name] is None and field_name in _NULLABLE_PATCH_FIELDS:
        return None
    return payload[field_name]


def _non_nullable_patch_value(
    payload: Mapping[str, Any],
    existing: Any,
    field_name: str,
) -> Any:
    if field_name not in payload:
        return existing
    if payload[field_name] is None:
        raise ValueError(f"{field_name} cannot be null")
    return payload[field_name]


def _effective_window_from_payload(
    payload: Mapping[str, Any],
) -> tuple[datetime | None, datetime | None]:
    starts_at = payload.get("starts_at")
    ends_at = payload.get("ends_at")
    if starts_at is not None and not isinstance(starts_at, datetime):
        raise ValueError("starts_at must be a datetime")
    if ends_at is not None and not isinstance(ends_at, datetime):
        raise ValueError("ends_at must be a datetime")
    return validate_effective_window(starts_at, ends_at)


def _nonempty_string(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _optional_nonempty_string(value: object | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field_name=field_name)


def _assignment_type_value(value: object | None, *, default: str) -> str:
    if isinstance(value, str) and not value.strip():
        raise ValueError("assignment_type is required")
    return normalize_assignment_type(value, default=default)


def _bool_value(value: object, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")
