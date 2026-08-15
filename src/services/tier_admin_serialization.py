from __future__ import annotations

from datetime import datetime
from typing import Any

from src.db.tiers import (
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    TierRecord,
    TierVersionRecord,
)


def serialize_tier(record: TierRecord) -> dict[str, Any]:
    return {
        "tier_id": record.tier_id,
        "tier_key": record.tier_key,
        "name": record.name,
        "description": record.description,
        "enabled": record.enabled,
        "metadata": _json_value(record.metadata),
        "active_version_id": record.active_version_id,
        "version_count": record.version_count,
        "assignment_count": record.assignment_count,
        "created_at": _json_value(record.created_at),
        "updated_at": _json_value(record.updated_at),
    }


def serialize_tier_version(record: TierVersionRecord) -> dict[str, Any]:
    return {
        "tier_version_id": record.tier_version_id,
        "tier_id": record.tier_id,
        "version_number": record.version_number,
        "status": record.status,
        "configuration_revision": record.configuration_revision,
        "published_at": _json_value(record.published_at),
        "published_by_account_id": record.published_by_account_id,
        "created_by_account_id": record.created_by_account_id,
        "created_by_kind": record.created_by_kind,
        "source_tier_version_id": record.source_tier_version_id,
        "metadata": _json_value(record.metadata),
        "model_policy_count": record.model_policy_count,
        "capacity_pool_count": record.capacity_pool_count,
        "assignment_count": record.assignment_count,
        "created_at": _json_value(record.created_at),
        "updated_at": _json_value(record.updated_at),
    }


def serialize_model_policy(record: TierModelPolicyRecord) -> dict[str, Any]:
    return {
        "tier_model_policy_id": record.tier_model_policy_id,
        "tier_version_id": record.tier_version_id,
        "callable_key": record.callable_key,
        "enabled": record.enabled,
        "access_mode": record.access_mode,
        "rpm_limit": record.rpm_limit,
        "tpm_limit": record.tpm_limit,
        "rph_limit": record.rph_limit,
        "rpd_limit": record.rpd_limit,
        "tpd_limit": record.tpd_limit,
        "max_parallel_requests": record.max_parallel_requests,
        "batch_rpm_limit": record.batch_rpm_limit,
        "batch_tpm_limit": record.batch_tpm_limit,
        "pricing": _json_value(record.pricing),
        "capacity_pool_key": record.capacity_pool_key,
        "priority": record.priority,
        "metadata": _json_value(record.metadata),
        "created_at": _json_value(record.created_at),
        "updated_at": _json_value(record.updated_at),
    }


def serialize_capacity_pool(record: TierCapacityPoolRecord) -> dict[str, Any]:
    return {
        "tier_capacity_pool_id": record.tier_capacity_pool_id,
        "tier_version_id": record.tier_version_id,
        "pool_key": record.pool_key,
        "callable_key": record.callable_key,
        "rpm_capacity": record.rpm_capacity,
        "tpm_capacity": record.tpm_capacity,
        "max_parallel_requests": record.max_parallel_requests,
        "strategy": record.strategy,
        "saturation_threshold": record.saturation_threshold,
        "burst_multiplier": record.burst_multiplier,
        "metadata": _json_value(record.metadata),
        "created_at": _json_value(record.created_at),
        "updated_at": _json_value(record.updated_at),
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


__all__ = [
    "serialize_capacity_pool",
    "serialize_model_policy",
    "serialize_tier",
    "serialize_tier_version",
]
