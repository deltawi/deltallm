from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


class BatchCreateSessionStatus:
    STAGED = "staged"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_PERMANENT = "failed_permanent"
    EXPIRED = "expired"


BATCH_CREATE_SESSION_STATUSES = (
    BatchCreateSessionStatus.STAGED,
    BatchCreateSessionStatus.COMPLETED,
    BatchCreateSessionStatus.FAILED_RETRYABLE,
    BatchCreateSessionStatus.FAILED_PERMANENT,
    BatchCreateSessionStatus.EXPIRED,
)
_BATCH_CREATE_SESSION_STATUS_SET = frozenset(BATCH_CREATE_SESSION_STATUSES)


def normalize_batch_create_session_status(status: str) -> str:
    normalized = str(status or "").strip()
    if normalized not in _BATCH_CREATE_SESSION_STATUS_SET:
        raise ValueError(
            "batch create session status must be one of: "
            + ", ".join(BATCH_CREATE_SESSION_STATUSES)
        )
    return normalized


def normalize_idempotency_pair(
    idempotency_scope_key: str | None,
    idempotency_key: str | None,
) -> tuple[str | None, str | None]:
    normalized_scope_key = str(idempotency_scope_key or "").strip() or None
    normalized_key = str(idempotency_key or "").strip() or None
    if (normalized_scope_key is None) != (normalized_key is None):
        raise ValueError("idempotency_scope_key and idempotency_key must both be set or both be omitted")
    return normalized_scope_key, normalized_key


@dataclass
class BatchCreateStagedRequest:
    line_number: int
    custom_id: str
    request_body: dict[str, Any]

    def __post_init__(self) -> None:
        if int(self.line_number) <= 0:
            raise ValueError("line_number must be positive")
        self.line_number = int(self.line_number)
        self.custom_id = str(self.custom_id or "").strip()
        if not self.custom_id:
            raise ValueError("custom_id is required")
        if not isinstance(self.request_body, dict):
            raise ValueError("request_body must be an object")
        self.request_body = dict(self.request_body)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "custom_id": self.custom_id,
            "request_body": self.request_body,
        }

    @classmethod
    def from_jsonable(cls, payload: dict[str, Any]) -> "BatchCreateStagedRequest":
        if not isinstance(payload, dict):
            raise ValueError("staged batch-create line must be an object")
        return cls(
            line_number=int(payload.get("line_number") or 0),
            custom_id=str(payload.get("custom_id") or ""),
            request_body=payload.get("request_body"),
        )


@dataclass
class BatchCreateSessionCreate:
    target_batch_id: str
    endpoint: str
    input_file_id: str
    staged_storage_backend: str
    staged_storage_key: str
    staged_bytes: int
    expected_item_count: int
    status: str = BatchCreateSessionStatus.STAGED
    staged_checksum: str | None = None
    inferred_model: str | None = None
    metadata: dict[str, Any] | None = None
    requested_service_tier: str | None = None
    effective_service_tier: str | None = None
    service_tier_source: str | None = None
    scheduling_scope_key: str | None = None
    priority_quota_scope_key: str | None = None
    idempotency_scope_key: str | None = None
    idempotency_key: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    promotion_attempt_count: int = 0
    created_by_api_key: str | None = None
    created_by_user_id: str | None = None
    created_by_team_id: str | None = None
    created_by_organization_id: str | None = None
    completed_at: datetime | None = None
    last_attempt_at: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        self.status = normalize_batch_create_session_status(self.status)
        self.idempotency_scope_key, self.idempotency_key = normalize_idempotency_pair(
            self.idempotency_scope_key,
            self.idempotency_key,
        )


@dataclass
class BatchCreateSessionRecord:
    session_id: str
    target_batch_id: str
    status: str
    endpoint: str
    input_file_id: str
    staged_storage_backend: str
    staged_storage_key: str
    staged_checksum: str | None
    staged_bytes: int
    expected_item_count: int
    inferred_model: str | None
    metadata: dict[str, Any] | None
    requested_service_tier: str | None
    effective_service_tier: str | None
    service_tier_source: str | None
    scheduling_scope_key: str | None
    priority_quota_scope_key: str | None
    idempotency_scope_key: str | None
    idempotency_key: str | None
    last_error_code: str | None
    last_error_message: str | None
    promotion_attempt_count: int
    created_by_api_key: str | None
    created_by_user_id: str | None
    created_by_team_id: str | None
    created_by_organization_id: str | None
    created_at: datetime
    completed_at: datetime | None
    last_attempt_at: datetime | None
    expires_at: datetime | None

    def __post_init__(self) -> None:
        self.status = normalize_batch_create_session_status(self.status)
