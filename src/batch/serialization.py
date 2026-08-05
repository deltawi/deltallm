from __future__ import annotations

from datetime import datetime
from typing import Any

from src.batch.models import BatchJobRecord, BatchJobStatus, OPENAI_BATCH_COMPLETION_WINDOW


def _timestamp_or_none(value: datetime | None) -> int | None:
    return int(value.timestamp()) if value else None


def _public_batch_status(job: BatchJobRecord) -> str:
    status_value = str(job.status or "")
    if status_value == BatchJobStatus.QUEUED.value:
        return "validating"
    if status_value == BatchJobStatus.IN_PROGRESS.value and job.cancel_requested_at is not None:
        return "cancelling"
    return status_value


def _terminal_status_timestamp(
    job: BatchJobRecord,
    terminal_status: BatchJobStatus,
) -> int | None:
    if str(job.status or "") != terminal_status.value:
        return None
    return _timestamp_or_none(job.status_last_updated_at)


def serialize_public_batch(job: BatchJobRecord) -> dict[str, Any]:
    response: dict[str, Any] = {
        "id": job.batch_id,
        "object": "batch",
        "endpoint": job.endpoint,
        "completion_window": OPENAI_BATCH_COMPLETION_WINDOW,
        "status": _public_batch_status(job),
        "input_file_id": job.input_file_id,
        "output_file_id": job.output_file_id,
        "error_file_id": job.error_file_id,
        "created_at": int(job.created_at.timestamp()),
        "expires_at": _timestamp_or_none(job.expires_at),
        "in_progress_at": int(job.started_at.timestamp()) if job.started_at else None,
        "completed_at": int(job.completed_at.timestamp()) if job.completed_at else None,
        "failed_at": _terminal_status_timestamp(job, BatchJobStatus.FAILED),
        "expired_at": _terminal_status_timestamp(job, BatchJobStatus.EXPIRED),
        "errors": None,
        "request_counts": {
            "total": job.total_items,
            "completed": job.completed_items,
            "failed": job.failed_items,
            "cancelled": job.cancelled_items,
            "in_progress": job.in_progress_items,
        },
        "metadata": job.metadata or {},
    }
    if job.webhook_config_ciphertext is not None:
        response["webhook"] = {"configured": True}
    return response
