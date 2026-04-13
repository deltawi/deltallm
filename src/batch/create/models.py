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
