from __future__ import annotations

from datetime import datetime
from typing import Any

from src.db.tiers import OrganizationTierAssignmentRecord


def serialize_tier_assignment(record: OrganizationTierAssignmentRecord) -> dict[str, Any]:
    return {
        "assignment_id": record.assignment_id,
        "organization_id": record.organization_id,
        "tier_id": record.tier_id,
        "tier_key": record.tier_key,
        "tier_name": record.tier_name,
        "tier_version_id": record.tier_version_id,
        "tier_version_number": record.tier_version_number,
        "tier_version_status": record.tier_version_status,
        "assignment_type": record.assignment_type,
        "enabled": record.enabled,
        "weight": record.weight,
        "starts_at": _datetime_iso(record.starts_at),
        "ends_at": _datetime_iso(record.ends_at),
        "metadata": record.metadata,
        "created_at": _datetime_iso(record.created_at),
        "updated_at": _datetime_iso(record.updated_at),
    }


def _datetime_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
