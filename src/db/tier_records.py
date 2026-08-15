from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


def json_param(value: dict[str, Any] | None) -> str | None:
    return json.dumps(value) if value is not None else None


def int_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None


def float_or_none(value: Any) -> float | None:
    return float(value) if value is not None else None


@dataclass
class TierRecord:
    tier_id: str
    tier_key: str
    name: str
    description: str | None = None
    enabled: bool = True
    metadata: dict[str, Any] | None = None
    active_version_id: str | None = None
    version_count: int = 0
    assignment_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TierCreationRequestRecord:
    tier_creation_request_id: str
    principal_scope: str
    idempotency_key: str
    request_hash: str
    tier_id: str
    created_at: datetime | None = None


@dataclass
class TierVersionRecord:
    tier_version_id: str
    tier_id: str
    version_number: int
    status: str = "draft"
    configuration_revision: int = 0
    published_at: datetime | None = None
    published_by_account_id: str | None = None
    created_by_account_id: str | None = None
    created_by_kind: str = "unknown"
    source_tier_version_id: str | None = None
    metadata: dict[str, Any] | None = None
    model_policy_count: int = 0
    capacity_pool_count: int = 0
    assignment_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class TierModelPolicyRecord:
    tier_model_policy_id: str
    tier_version_id: str
    callable_key: str
    enabled: bool = True
    access_mode: str = "allow"
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    rph_limit: int | None = None
    rpd_limit: int | None = None
    tpd_limit: int | None = None
    max_parallel_requests: int | None = None
    batch_rpm_limit: int | None = None
    batch_tpm_limit: int | None = None
    pricing: dict[str, Any] | None = None
    capacity_pool_key: str | None = None
    priority: int = 0
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class TierCapacityPoolRecord:
    tier_capacity_pool_id: str
    tier_version_id: str
    pool_key: str
    callable_key: str
    rpm_capacity: int | None = None
    tpm_capacity: int | None = None
    max_parallel_requests: int | None = None
    strategy: str = "hard_cap"
    saturation_threshold: float | None = None
    burst_multiplier: float | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class OrganizationTierAssignmentRecord:
    assignment_id: str
    organization_id: str
    tier_id: str
    tier_version_id: str | None = None
    assignment_type: str = "primary"
    enabled: bool = True
    weight: int = 1
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    tier_key: str | None = None
    tier_name: str | None = None
    tier_version_number: int | None = None
    tier_version_status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TierPolicyAssignmentRecord:
    assignment_id: str
    organization_id: str
    tier_id: str
    tier_version_id: str | None
    effective_tier_version_id: str
    assignment_type: str = "primary"
    enabled: bool = True
    weight: int = 1
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    tier_key: str | None = None
    tier_name: str | None = None
    tier_version_number: int | None = None
    tier_version_status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TierPolicyLoadResult:
    assignments: tuple[TierPolicyAssignmentRecord, ...]
    model_policies: tuple[TierModelPolicyRecord, ...]
    capacity_pools: tuple[TierCapacityPoolRecord, ...]
    next_transition_at: datetime | None = None


def tier_version_select_sql() -> str:
    return """
        SELECT
            v.tier_version_id,
            v.tier_id,
            v.version_number,
            v.status,
            v.configuration_revision,
            v.published_at,
            v.published_by_account_id,
            v.created_by_account_id,
            v.created_by_kind,
            v.source_tier_version_id,
            v.metadata,
            v.created_at,
            v.updated_at,
            (
                SELECT COUNT(*)::int
                FROM deltallm_tiermodelpolicy p
                WHERE p.tier_version_id = v.tier_version_id
            ) AS model_policy_count,
            (
                SELECT COUNT(*)::int
                FROM deltallm_tiercapacitypool p
                WHERE p.tier_version_id = v.tier_version_id
            ) AS capacity_pool_count,
            (
                SELECT COUNT(*)::int
                FROM deltallm_organizationtierassignment a
                WHERE a.tier_version_id = v.tier_version_id
            ) AS assignment_count
        FROM deltallm_tierversion v
    """


def assignment_select_sql() -> str:
    return """
        SELECT
            a.assignment_id,
            a.organization_id,
            a.tier_id,
            a.tier_version_id,
            a.assignment_type,
            a.enabled,
            a.weight,
            a.starts_at,
            a.ends_at,
            a.metadata,
            a.created_at,
            a.updated_at,
            t.tier_key,
            t.name AS tier_name,
            v.version_number AS tier_version_number,
            v.status AS tier_version_status
        FROM deltallm_organizationtierassignment a
        JOIN deltallm_tier t ON t.tier_id = a.tier_id
        LEFT JOIN deltallm_tierversion v ON v.tier_version_id = a.tier_version_id
    """


def to_tier_record(row: dict[str, Any]) -> TierRecord:
    return TierRecord(
        tier_id=str(row.get("tier_id") or ""),
        tier_key=str(row.get("tier_key") or ""),
        name=str(row.get("name") or ""),
        description=str(row.get("description")) if row.get("description") is not None else None,
        enabled=bool(row.get("enabled", True)),
        metadata=parse_json_object(row.get("metadata"))
        if row.get("metadata") is not None
        else None,
        active_version_id=str(row.get("active_version_id"))
        if row.get("active_version_id") is not None
        else None,
        version_count=int(row.get("version_count") or 0),
        assignment_count=int(row.get("assignment_count") or 0),
        created_at=parse_datetime(row.get("created_at")),
        updated_at=parse_datetime(row.get("updated_at")),
    )


def to_tier_creation_request_record(row: dict[str, Any]) -> TierCreationRequestRecord:
    return TierCreationRequestRecord(
        tier_creation_request_id=str(row.get("tier_creation_request_id") or ""),
        principal_scope=str(row.get("principal_scope") or ""),
        idempotency_key=str(row.get("idempotency_key") or ""),
        request_hash=str(row.get("request_hash") or ""),
        tier_id=str(row.get("tier_id") or ""),
        created_at=parse_datetime(row.get("created_at")),
    )


def to_version_record(row: dict[str, Any]) -> TierVersionRecord:
    return TierVersionRecord(
        tier_version_id=str(row.get("tier_version_id") or ""),
        tier_id=str(row.get("tier_id") or ""),
        version_number=int(row.get("version_number") or 0),
        status=str(row.get("status") or "draft"),
        configuration_revision=int(row.get("configuration_revision") or 0),
        published_at=parse_datetime(row.get("published_at")),
        published_by_account_id=str(row.get("published_by_account_id"))
        if row.get("published_by_account_id") is not None
        else None,
        created_by_account_id=str(row.get("created_by_account_id"))
        if row.get("created_by_account_id") is not None
        else None,
        created_by_kind=str(row.get("created_by_kind") or "unknown"),
        source_tier_version_id=str(row.get("source_tier_version_id"))
        if row.get("source_tier_version_id") is not None
        else None,
        metadata=parse_json_object(row.get("metadata"))
        if row.get("metadata") is not None
        else None,
        model_policy_count=int(row.get("model_policy_count") or 0),
        capacity_pool_count=int(row.get("capacity_pool_count") or 0),
        assignment_count=int(row.get("assignment_count") or 0),
        created_at=parse_datetime(row.get("created_at")),
        updated_at=parse_datetime(row.get("updated_at")),
    )


def to_model_policy_record(row: dict[str, Any]) -> TierModelPolicyRecord:
    return TierModelPolicyRecord(
        tier_model_policy_id=str(row.get("tier_model_policy_id") or ""),
        tier_version_id=str(row.get("tier_version_id") or ""),
        callable_key=str(row.get("callable_key") or ""),
        enabled=bool(row.get("enabled", True)),
        access_mode=str(row.get("access_mode") or "allow"),
        rpm_limit=int_or_none(row.get("rpm_limit")),
        tpm_limit=int_or_none(row.get("tpm_limit")),
        rph_limit=int_or_none(row.get("rph_limit")),
        rpd_limit=int_or_none(row.get("rpd_limit")),
        tpd_limit=int_or_none(row.get("tpd_limit")),
        max_parallel_requests=int_or_none(row.get("max_parallel_requests")),
        batch_rpm_limit=int_or_none(row.get("batch_rpm_limit")),
        batch_tpm_limit=int_or_none(row.get("batch_tpm_limit")),
        pricing=parse_json_object(row.get("pricing")) if row.get("pricing") is not None else None,
        capacity_pool_key=str(row.get("capacity_pool_key"))
        if row.get("capacity_pool_key") is not None
        else None,
        priority=int(row.get("priority") or 0),
        metadata=parse_json_object(row.get("metadata"))
        if row.get("metadata") is not None
        else None,
        created_at=parse_datetime(row.get("created_at")),
        updated_at=parse_datetime(row.get("updated_at")),
    )


def to_capacity_pool_record(row: dict[str, Any]) -> TierCapacityPoolRecord:
    return TierCapacityPoolRecord(
        tier_capacity_pool_id=str(row.get("tier_capacity_pool_id") or ""),
        tier_version_id=str(row.get("tier_version_id") or ""),
        pool_key=str(row.get("pool_key") or ""),
        callable_key=str(row.get("callable_key") or ""),
        rpm_capacity=int_or_none(row.get("rpm_capacity")),
        tpm_capacity=int_or_none(row.get("tpm_capacity")),
        max_parallel_requests=int_or_none(row.get("max_parallel_requests")),
        strategy=str(row.get("strategy") or "hard_cap"),
        saturation_threshold=float_or_none(row.get("saturation_threshold")),
        burst_multiplier=float_or_none(row.get("burst_multiplier")),
        metadata=parse_json_object(row.get("metadata"))
        if row.get("metadata") is not None
        else None,
        created_at=parse_datetime(row.get("created_at")),
        updated_at=parse_datetime(row.get("updated_at")),
    )


def to_assignment_record(row: dict[str, Any]) -> OrganizationTierAssignmentRecord:
    return OrganizationTierAssignmentRecord(
        assignment_id=str(row.get("assignment_id") or ""),
        organization_id=str(row.get("organization_id") or ""),
        tier_id=str(row.get("tier_id") or ""),
        tier_version_id=str(row.get("tier_version_id"))
        if row.get("tier_version_id") is not None
        else None,
        assignment_type=str(row.get("assignment_type") or "primary"),
        enabled=bool(row.get("enabled", True)),
        weight=int(row.get("weight") or 1),
        starts_at=parse_datetime(row.get("starts_at")),
        ends_at=parse_datetime(row.get("ends_at")),
        metadata=parse_json_object(row.get("metadata"))
        if row.get("metadata") is not None
        else None,
        tier_key=str(row.get("tier_key")) if row.get("tier_key") is not None else None,
        tier_name=str(row.get("tier_name")) if row.get("tier_name") is not None else None,
        tier_version_number=int_or_none(row.get("tier_version_number")),
        tier_version_status=str(row.get("tier_version_status"))
        if row.get("tier_version_status") is not None
        else None,
        created_at=parse_datetime(row.get("created_at")),
        updated_at=parse_datetime(row.get("updated_at")),
    )


def to_tier_policy_assignment_record(row: dict[str, Any]) -> TierPolicyAssignmentRecord:
    return TierPolicyAssignmentRecord(
        assignment_id=str(row.get("assignment_id") or ""),
        organization_id=str(row.get("organization_id") or ""),
        tier_id=str(row.get("tier_id") or ""),
        tier_version_id=str(row.get("tier_version_id"))
        if row.get("tier_version_id") is not None
        else None,
        effective_tier_version_id=str(row.get("effective_tier_version_id") or ""),
        assignment_type=str(row.get("assignment_type") or "primary"),
        enabled=bool(row.get("enabled", True)),
        weight=int(row.get("weight") or 1),
        starts_at=parse_datetime(row.get("starts_at")),
        ends_at=parse_datetime(row.get("ends_at")),
        metadata=parse_json_object(row.get("metadata"))
        if row.get("metadata") is not None
        else None,
        tier_key=str(row.get("tier_key")) if row.get("tier_key") is not None else None,
        tier_name=str(row.get("tier_name")) if row.get("tier_name") is not None else None,
        tier_version_number=int_or_none(row.get("tier_version_number")),
        tier_version_status=str(row.get("tier_version_status"))
        if row.get("tier_version_status") is not None
        else None,
        created_at=parse_datetime(row.get("created_at")),
        updated_at=parse_datetime(row.get("updated_at")),
    )
