from __future__ import annotations

from typing import Any

from src.batch.models import BatchWebhookOutboxRecord
from src.batch.webhooks.observability import bounded_webhook_reason, webhook_status_class


def serialize_batch_webhook_delivery(record: BatchWebhookOutboxRecord) -> dict[str, Any]:
    """Return the operator-safe delivery state; customer delivery material is omitted."""
    return {
        "event_id": record.event_id,
        "event_type": record.event_type.value,
        "status": record.status.value,
        "attempt_count": record.attempt_count,
        "max_attempts": record.max_attempts,
        "next_attempt_at": record.next_attempt_at,
        "last_status_class": webhook_status_class(record.last_status_code),
        "last_error": (
            bounded_webhook_reason(record.last_error) if record.last_error is not None else None
        ),
        "lease_expires_at": record.lease_expires_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "delivered_at": record.delivered_at,
    }
