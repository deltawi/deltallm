from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.db.tiers import TierCapacityPoolRecord, TierModelPolicyRecord, TierRecord
from src.services.tier_admin_errors import TierAdminValidationError
from src.services.tiers import (
    float_gte_one_or_none,
    normalize_access_mode,
    normalize_callable_key,
    normalize_capacity_strategy,
    normalize_metadata,
    normalize_pool_key,
    normalize_pricing,
    normalize_tier_key,
    positive_int_or_none,
    ratio_gt_zero_lte_one_or_none,
)


def normalize_tier_create(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tier_key": _normalize_field(normalize_tier_key, payload.get("tier_key")),
        "name": _required_string(payload.get("name"), "name"),
        "description": _optional_string(payload.get("description"), "description"),
        "enabled": _bool_field(payload.get("enabled", True), "enabled"),
        "metadata": _normalize_metadata_field(payload.get("metadata")),
    }


def normalize_tier_update(payload: Mapping[str, Any], *, existing: TierRecord) -> dict[str, Any]:
    return {
        "tier_key": _normalize_field(normalize_tier_key, payload["tier_key"])
        if "tier_key" in payload
        else existing.tier_key,
        "name": _required_string(payload["name"], "name") if "name" in payload else existing.name,
        "description": _optional_string(payload["description"], "description")
        if "description" in payload
        else existing.description,
        "enabled": _bool_field(payload["enabled"], "enabled")
        if "enabled" in payload
        else existing.enabled,
        "metadata": _normalize_metadata_field(payload["metadata"])
        if "metadata" in payload
        else existing.metadata,
    }


def normalize_tier_version_create(
    payload: Mapping[str, Any],
    *,
    default_version_number: int | None,
) -> dict[str, Any]:
    version_number = payload.get("version_number")
    if version_number is None:
        if default_version_number is None:
            raise TierAdminValidationError("version_number must be a positive integer")
        version_number = default_version_number
    else:
        version_number = _positive_int(version_number, "version_number")
    return {
        "version_number": version_number,
        "metadata": _normalize_metadata_field(payload.get("metadata")),
    }


def normalize_model_policy_records(
    tier_version_id: str,
    policies: Sequence[Mapping[str, Any]],
) -> list[TierModelPolicyRecord]:
    seen: set[str] = set()
    records: list[TierModelPolicyRecord] = []
    for index, payload in enumerate(policies):
        prefix = f"policies[{index}]"
        callable_key = _normalize_field(
            normalize_callable_key,
            payload.get("callable_key"),
            field_name=f"{prefix}.callable_key",
        )
        if callable_key in seen:
            raise TierAdminValidationError("model policies must have unique callable_key values")
        seen.add(callable_key)
        capacity_pool_key = payload.get("capacity_pool_key")
        records.append(
            TierModelPolicyRecord(
                tier_model_policy_id="",
                tier_version_id=tier_version_id,
                callable_key=callable_key,
                enabled=_bool_field(payload.get("enabled", True), f"{prefix}.enabled"),
                access_mode=_normalize_field(
                    normalize_access_mode,
                    payload.get("access_mode"),
                    field_name=f"{prefix}.access_mode",
                ),
                rpm_limit=_positive_int_or_none(payload.get("rpm_limit"), f"{prefix}.rpm_limit"),
                tpm_limit=_positive_int_or_none(payload.get("tpm_limit"), f"{prefix}.tpm_limit"),
                rph_limit=_positive_int_or_none(payload.get("rph_limit"), f"{prefix}.rph_limit"),
                rpd_limit=_positive_int_or_none(payload.get("rpd_limit"), f"{prefix}.rpd_limit"),
                tpd_limit=_positive_int_or_none(payload.get("tpd_limit"), f"{prefix}.tpd_limit"),
                max_parallel_requests=_positive_int_or_none(
                    payload.get("max_parallel_requests"),
                    f"{prefix}.max_parallel_requests",
                ),
                batch_rpm_limit=_positive_int_or_none(
                    payload.get("batch_rpm_limit"),
                    f"{prefix}.batch_rpm_limit",
                ),
                batch_tpm_limit=_positive_int_or_none(
                    payload.get("batch_tpm_limit"),
                    f"{prefix}.batch_tpm_limit",
                ),
                pricing=_normalize_pricing_field(payload.get("pricing")),
                capacity_pool_key=_normalize_field(
                    normalize_pool_key,
                    capacity_pool_key,
                    field_name=f"{prefix}.capacity_pool_key",
                )
                if capacity_pool_key is not None
                else None,
                priority=_int_field(payload.get("priority", 0), f"{prefix}.priority"),
                metadata=_normalize_metadata_field(payload.get("metadata")),
            )
        )
    return records


def normalize_capacity_pool_records(
    tier_version_id: str,
    pools: Sequence[Mapping[str, Any]],
) -> list[TierCapacityPoolRecord]:
    seen: set[tuple[str, str]] = set()
    records: list[TierCapacityPoolRecord] = []
    for index, payload in enumerate(pools):
        prefix = f"pools[{index}]"
        pool_key = _normalize_field(
            normalize_pool_key,
            payload.get("pool_key"),
            field_name=f"{prefix}.pool_key",
        )
        callable_key = _normalize_field(
            normalize_callable_key,
            payload.get("callable_key"),
            field_name=f"{prefix}.callable_key",
        )
        ref = (pool_key, callable_key)
        if ref in seen:
            raise TierAdminValidationError(
                "capacity pools must have unique pool_key and callable_key pairs"
            )
        seen.add(ref)
        records.append(
            TierCapacityPoolRecord(
                tier_capacity_pool_id="",
                tier_version_id=tier_version_id,
                pool_key=pool_key,
                callable_key=callable_key,
                rpm_capacity=_positive_int_or_none(
                    payload.get("rpm_capacity"),
                    f"{prefix}.rpm_capacity",
                ),
                tpm_capacity=_positive_int_or_none(
                    payload.get("tpm_capacity"),
                    f"{prefix}.tpm_capacity",
                ),
                max_parallel_requests=_positive_int_or_none(
                    payload.get("max_parallel_requests"),
                    f"{prefix}.max_parallel_requests",
                ),
                strategy=_normalize_field(
                    normalize_capacity_strategy,
                    payload.get("strategy"),
                    field_name=f"{prefix}.strategy",
                ),
                saturation_threshold=_ratio_or_none(
                    payload.get("saturation_threshold"),
                    f"{prefix}.saturation_threshold",
                ),
                burst_multiplier=_float_gte_one_or_none(
                    payload.get("burst_multiplier"),
                    f"{prefix}.burst_multiplier",
                ),
                metadata=_normalize_metadata_field(payload.get("metadata")),
            )
        )
    return records


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TierAdminValidationError(f"{field_name} is required")
    normalized = value.strip()
    if not normalized:
        raise TierAdminValidationError(f"{field_name} is required")
    return normalized


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TierAdminValidationError(f"{field_name} must be a string")
    return value.strip() or None


def _bool_field(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TierAdminValidationError(f"{field_name} must be a boolean")
    return value


def _int_field(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise TierAdminValidationError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TierAdminValidationError(f"{field_name} must be an integer") from exc


def _positive_int(value: Any, field_name: str) -> int:
    normalized = _positive_int_or_none(value, field_name)
    if normalized is None:
        raise TierAdminValidationError(f"{field_name} must be a positive integer")
    return normalized


def _positive_int_or_none(value: Any, field_name: str) -> int | None:
    try:
        return positive_int_or_none(value, field_name)
    except ValueError as exc:
        raise TierAdminValidationError(str(exc)) from exc


def _ratio_or_none(value: Any, field_name: str) -> float | None:
    try:
        return ratio_gt_zero_lte_one_or_none(value, field_name)
    except ValueError as exc:
        raise TierAdminValidationError(str(exc)) from exc


def _float_gte_one_or_none(value: Any, field_name: str) -> float | None:
    try:
        return float_gte_one_or_none(value, field_name)
    except ValueError as exc:
        raise TierAdminValidationError(str(exc)) from exc


def _normalize_pricing_field(value: Any) -> dict[str, float] | None:
    try:
        return normalize_pricing(value)
    except ValueError as exc:
        raise TierAdminValidationError(str(exc)) from exc


def _normalize_metadata_field(value: Any) -> dict[str, Any] | None:
    try:
        return normalize_metadata(value)
    except ValueError as exc:
        raise TierAdminValidationError(str(exc)) from exc


def _normalize_field(
    normalizer: Any,
    value: Any,
    *,
    field_name: str | None = None,
) -> Any:
    try:
        return normalizer(value)
    except ValueError as exc:
        if field_name is None:
            raise TierAdminValidationError(str(exc)) from exc
        detail = str(exc)
        _, _, suffix = detail.partition(" ")
        raise TierAdminValidationError(f"{field_name} {suffix or detail}") from exc


__all__ = [
    "normalize_capacity_pool_records",
    "normalize_model_policy_records",
    "normalize_tier_create",
    "normalize_tier_update",
    "normalize_tier_version_create",
]
