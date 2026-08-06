from __future__ import annotations

import hashlib
import hmac

from pydantic import SecretStr

from src.batch.models import BatchWebhookOutboxRecord
from src.batch.webhooks.events import canonical_batch_webhook_event_bytes


class BatchWebhookPayloadIntegrityError(ValueError):
    """The persisted webhook snapshot no longer matches its immutable digest."""


def batch_webhook_raw_body(record: BatchWebhookOutboxRecord) -> bytes:
    raw_body = canonical_batch_webhook_event_bytes(record.payload_json)
    actual_digest = hashlib.sha256(raw_body).hexdigest()
    if not hmac.compare_digest(actual_digest, record.payload_sha256):
        raise BatchWebhookPayloadIntegrityError("batch webhook payload integrity check failed")
    return raw_body


def build_batch_webhook_headers(
    record: BatchWebhookOutboxRecord,
    *,
    signing_secret: SecretStr | str,
    timestamp: int,
    raw_body: bytes | None = None,
) -> dict[str, str]:
    verified_body = batch_webhook_raw_body(record)
    body = verified_body if raw_body is None else raw_body
    if not hmac.compare_digest(body, verified_body):
        raise BatchWebhookPayloadIntegrityError("batch webhook payload integrity check failed")
    secret = (
        signing_secret.get_secret_value()
        if isinstance(signing_secret, SecretStr)
        else str(signing_secret)
    )
    signed_payload = str(int(timestamp)).encode("ascii") + b"." + body
    signature = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "User-Agent": "DeltaLLM-Webhooks/1.0",
        "Idempotency-Key": record.event_id,
        "X-DeltaLLM-Event-Id": record.event_id,
        "X-DeltaLLM-Event-Type": record.event_type.value,
        "X-DeltaLLM-Webhook-Attempt": str(record.attempt_count),
        "X-DeltaLLM-Timestamp": str(int(timestamp)),
        "X-DeltaLLM-Signature": f"v1={signature}",
    }
