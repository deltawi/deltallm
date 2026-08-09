from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import pytest

from src.batch.models import (
    BatchWebhookDeliveryStatus,
    BatchWebhookEventType,
    BatchWebhookOutboxRecord,
)
from src.batch.webhooks.events import (
    batch_webhook_event_payload_sha256,
    canonical_batch_webhook_event_bytes,
)
from src.batch.webhooks.signing import (
    BatchWebhookPayloadIntegrityError,
    batch_webhook_raw_body,
    build_batch_webhook_headers,
)


def _record(*, payload_sha256: str | None = None) -> BatchWebhookOutboxRecord:
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    payload = {
        "type": "batch.completed",
        "object": "event",
        "id": "evt-1",
        "data": {"batch": {"metadata": {"customer": "value"}, "id": "batch-1"}},
        "created_at": 1_785_847_200,
    }
    return BatchWebhookOutboxRecord(
        event_id="evt-1",
        batch_id="batch-1",
        event_type=BatchWebhookEventType.COMPLETED,
        target_config_ciphertext="ciphertext",
        payload_json=payload,
        payload_sha256=payload_sha256 or batch_webhook_event_payload_sha256(payload),
        status=BatchWebhookDeliveryStatus.PROCESSING,
        attempt_count=2,
        max_attempts=8,
        next_attempt_at=now,
        last_status_code=None,
        last_error=None,
        locked_by="worker-1",
        lease_expires_at=now,
        created_at=now,
        updated_at=now,
        delivered_at=None,
    )


def test_webhook_signature_covers_exact_canonical_transmitted_bytes() -> None:
    record = _record()
    secret = "customer-secret-that-is-at-least-32-bytes"
    timestamp = 1_785_847_300
    raw_body = batch_webhook_raw_body(record)

    headers = build_batch_webhook_headers(
        record,
        signing_secret=secret,
        timestamp=timestamp,
        raw_body=raw_body,
    )

    expected = hmac.new(
        secret.encode(),
        str(timestamp).encode() + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    assert raw_body == canonical_batch_webhook_event_bytes(record.payload_json)
    assert headers == {
        "Content-Type": "application/json",
        "User-Agent": "DeltaLLM-Webhooks/1.0",
        "Idempotency-Key": "evt-1",
        "X-DeltaLLM-Event-Id": "evt-1",
        "X-DeltaLLM-Event-Type": "batch.completed",
        "X-DeltaLLM-Webhook-Attempt": "2",
        "X-DeltaLLM-Timestamp": str(timestamp),
        "X-DeltaLLM-Signature": f"v1={expected}",
    }


def test_webhook_payload_integrity_mismatch_fails_before_signing() -> None:
    with pytest.raises(BatchWebhookPayloadIntegrityError):
        batch_webhook_raw_body(_record(payload_sha256="0" * 64))

    with pytest.raises(BatchWebhookPayloadIntegrityError):
        build_batch_webhook_headers(
            _record(),
            signing_secret="s" * 32,
            timestamp=1,
            raw_body=b'{"different":true}',
        )
