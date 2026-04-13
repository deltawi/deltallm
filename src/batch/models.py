from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


class BatchJobStatus:
    VALIDATING = "validating"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


BATCH_JOB_STATUSES = (
    BatchJobStatus.VALIDATING,
    BatchJobStatus.QUEUED,
    BatchJobStatus.IN_PROGRESS,
    BatchJobStatus.FINALIZING,
    BatchJobStatus.COMPLETED,
    BatchJobStatus.FAILED,
    BatchJobStatus.CANCELLED,
    BatchJobStatus.EXPIRED,
)
_BATCH_JOB_STATUS_SET = frozenset(BATCH_JOB_STATUSES)


def normalize_batch_job_status(status: str) -> str:
    normalized = str(status or "").strip()
    if normalized not in _BATCH_JOB_STATUS_SET:
        raise ValueError("batch job status must be one of: " + ", ".join(BATCH_JOB_STATUSES))
    return normalized


class BatchItemStatus:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchCompletionOutboxStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYING = "retrying"
    SENT = "sent"
    FAILED = "failed"


OPERATOR_FAILED_PREFIX = "__operator_failed__:"


def encode_operator_failed_reason(reason: str) -> str:
    normalized = str(reason or "").strip() or "Marked failed by operator"
    return f"{OPERATOR_FAILED_PREFIX}{normalized}"


def is_operator_failed_reason(value: str | None) -> bool:
    return str(value or "").startswith(OPERATOR_FAILED_PREFIX)


def decode_operator_failed_reason(value: str | None) -> str | None:
    if not is_operator_failed_reason(value):
        return value
    decoded = str(value)[len(OPERATOR_FAILED_PREFIX) :].strip()
    return decoded or None


@dataclass
class BatchFileRecord:
    file_id: str
    purpose: str
    filename: str
    bytes: int
    status: str
    storage_backend: str
    storage_key: str
    checksum: str | None
    created_by_api_key: str | None
    created_by_user_id: str | None
    created_by_team_id: str | None
    created_at: datetime
    expires_at: datetime | None
    created_by_organization_id: str | None = None


@dataclass
class BatchJobRecord:
    batch_id: str
    endpoint: str
    status: str
    execution_mode: str
    input_file_id: str
    output_file_id: str | None
    error_file_id: str | None
    model: str | None
    metadata: dict[str, Any] | None
    provider_batch_id: str | None
    provider_status: str | None
    provider_error: str | None
    provider_last_sync_at: datetime | None
    total_items: int
    in_progress_items: int
    completed_items: int
    failed_items: int
    cancelled_items: int
    locked_by: str | None
    lease_expires_at: datetime | None
    cancel_requested_at: datetime | None
    status_last_updated_at: datetime | None
    created_by_api_key: str | None
    created_by_user_id: str | None
    created_by_team_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None
    created_by_organization_id: str | None = None

    def __post_init__(self) -> None:
        self.status = normalize_batch_job_status(self.status)


@dataclass
class BatchItemRecord:
    item_id: str
    batch_id: str
    line_number: int
    custom_id: str
    status: str
    request_body: dict[str, Any]
    response_body: dict[str, Any] | None
    error_body: dict[str, Any] | None
    usage: dict[str, Any] | None
    provider_cost: float
    billed_cost: float
    attempts: int
    last_error: str | None
    locked_by: str | None
    lease_expires_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass
class BatchCompletionOutboxRecord:
    completion_id: str
    batch_id: str
    item_id: str
    payload_json: dict[str, Any]
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None
    locked_by: str | None = None
    lease_expires_at: datetime | None = None


@dataclass
class BatchCompletionOutboxCreate:
    batch_id: str
    item_id: str
    payload_json: dict[str, Any]
    status: str = BatchCompletionOutboxStatus.QUEUED
    attempt_count: int = 0
    max_attempts: int = 5
    next_attempt_at: datetime | None = None
    last_error: str | None = None


@dataclass
class BatchItemCreate:
    line_number: int
    custom_id: str
    request_body: dict[str, Any]
