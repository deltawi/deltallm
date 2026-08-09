from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.batch.models import BatchJobRecord, BatchJobStatus, BatchWebhookEventType
from src.batch.serialization import serialize_public_batch


_EVENT_TYPE_BY_TERMINAL_STATUS = {
    BatchJobStatus.COMPLETED: BatchWebhookEventType.COMPLETED,
    BatchJobStatus.FAILED: BatchWebhookEventType.FAILED,
    BatchJobStatus.CANCELLED: BatchWebhookEventType.CANCELLED,
    BatchJobStatus.EXPIRED: BatchWebhookEventType.EXPIRED,
}


@dataclass(frozen=True, slots=True)
class BatchWebhookEvent:
    event_id: str
    event_type: BatchWebhookEventType
    payload_json: dict[str, Any]
    payload_sha256: str


def canonical_batch_webhook_event_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def batch_webhook_event_payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_batch_webhook_event_bytes(payload)).hexdigest()


def batch_webhook_event_type_for_status(
    status: str | BatchJobStatus,
) -> BatchWebhookEventType:
    normalized = status if isinstance(status, BatchJobStatus) else BatchJobStatus(str(status or ""))
    try:
        return _EVENT_TYPE_BY_TERMINAL_STATUS[normalized]
    except KeyError as exc:
        raise ValueError(f"batch status '{normalized.value}' is not terminal") from exc


def build_batch_webhook_event(
    job: BatchJobRecord,
    *,
    event_id: str | None = None,
    created_at: datetime | None = None,
) -> BatchWebhookEvent:
    event_type = batch_webhook_event_type_for_status(job.status)
    stable_event_id = str(event_id or f"evt_{uuid4().hex}")
    event_created_at = created_at or datetime.now(tz=UTC)
    payload = {
        "id": stable_event_id,
        "object": "event",
        "type": event_type.value,
        "created_at": int(event_created_at.timestamp()),
        "data": {"batch": serialize_public_batch(job)},
    }
    return BatchWebhookEvent(
        event_id=stable_event_id,
        event_type=event_type,
        payload_json=payload,
        payload_sha256=batch_webhook_event_payload_sha256(payload),
    )
