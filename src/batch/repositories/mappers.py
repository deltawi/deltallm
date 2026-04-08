from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from src.batch.models import (
    BatchFileRecord,
    BatchItemRecord,
    BatchItemStatus,
    BatchJobRecord,
    BatchJobStatus,
)


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


def parse_json_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def file_from_row(row: dict[str, Any]) -> BatchFileRecord:
    return BatchFileRecord(
        file_id=str(row.get("file_id") or ""),
        purpose=str(row.get("purpose") or ""),
        filename=str(row.get("filename") or ""),
        bytes=int(row.get("bytes") or 0),
        status=str(row.get("status") or "processed"),
        storage_backend=str(row.get("storage_backend") or "local"),
        storage_key=str(row.get("storage_key") or ""),
        checksum=row.get("checksum"),
        created_by_api_key=row.get("created_by_api_key"),
        created_by_user_id=row.get("created_by_user_id"),
        created_by_team_id=row.get("created_by_team_id"),
        created_at=parse_datetime(row.get("created_at")) or datetime.now(tz=UTC),
        expires_at=parse_datetime(row.get("expires_at")),
        created_by_organization_id=row.get("created_by_organization_id"),
    )


def job_from_row(row: dict[str, Any]) -> BatchJobRecord:
    return BatchJobRecord(
        batch_id=str(row.get("batch_id") or ""),
        endpoint=str(row.get("endpoint") or ""),
        status=str(row.get("status") or BatchJobStatus.VALIDATING),
        execution_mode=str(row.get("execution_mode") or "managed_internal"),
        input_file_id=str(row.get("input_file_id") or ""),
        output_file_id=row.get("output_file_id"),
        error_file_id=row.get("error_file_id"),
        model=row.get("model"),
        metadata=parse_json_dict(row.get("metadata")),
        provider_batch_id=row.get("provider_batch_id"),
        provider_status=row.get("provider_status"),
        provider_error=row.get("provider_error"),
        provider_last_sync_at=parse_datetime(row.get("provider_last_sync_at")),
        total_items=int(row.get("total_items") or 0),
        in_progress_items=int(row.get("in_progress_items") or 0),
        completed_items=int(row.get("completed_items") or 0),
        failed_items=int(row.get("failed_items") or 0),
        cancelled_items=int(row.get("cancelled_items") or 0),
        locked_by=row.get("locked_by"),
        lease_expires_at=parse_datetime(row.get("lease_expires_at")),
        cancel_requested_at=parse_datetime(row.get("cancel_requested_at")),
        status_last_updated_at=parse_datetime(row.get("status_last_updated_at")),
        created_by_api_key=row.get("created_by_api_key"),
        created_by_user_id=row.get("created_by_user_id"),
        created_by_team_id=row.get("created_by_team_id"),
        created_at=parse_datetime(row.get("created_at")) or datetime.now(tz=UTC),
        started_at=parse_datetime(row.get("started_at")),
        completed_at=parse_datetime(row.get("completed_at")),
        expires_at=parse_datetime(row.get("expires_at")),
        created_by_organization_id=row.get("created_by_organization_id"),
    )


def item_from_row(row: dict[str, Any]) -> BatchItemRecord:
    return BatchItemRecord(
        item_id=str(row.get("item_id") or ""),
        batch_id=str(row.get("batch_id") or ""),
        line_number=int(row.get("line_number") or 0),
        custom_id=str(row.get("custom_id") or ""),
        status=str(row.get("status") or BatchItemStatus.PENDING),
        request_body=parse_json_dict(row.get("request_body")) or {},
        response_body=parse_json_dict(row.get("response_body")),
        error_body=parse_json_dict(row.get("error_body")),
        usage=parse_json_dict(row.get("usage")),
        provider_cost=float(row.get("provider_cost") or 0.0),
        billed_cost=float(row.get("billed_cost") or 0.0),
        attempts=int(row.get("attempts") or 0),
        last_error=row.get("last_error"),
        locked_by=row.get("locked_by"),
        lease_expires_at=parse_datetime(row.get("lease_expires_at")),
        created_at=parse_datetime(row.get("created_at")) or datetime.now(tz=UTC),
        started_at=parse_datetime(row.get("started_at")),
        completed_at=parse_datetime(row.get("completed_at")),
    )
