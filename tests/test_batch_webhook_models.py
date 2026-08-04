from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.batch.models import (
    BatchWebhookDeliveryStatus,
    BatchWebhookEventType,
    BatchWebhookOutboxCreate,
    BatchWebhookOutboxRecord,
)
from src.batch.repositories.mappers import webhook_outbox_from_row
from src.batch.webhooks.models import (
    BatchWebhookRequest,
    batch_webhook_config_fingerprint,
    canonical_batch_webhook_config_bytes,
    parse_batch_webhook_request,
    redact_batch_webhook_config,
)


SIGNING_SECRET = "signing-secret-with-at-least-32-bytes"


def test_webhook_request_normalizes_url_and_protects_secret() -> None:
    config = parse_batch_webhook_request(
        {
            "url": " HTTPS://ExAmPlE.com:443/callback?source=batch ",
            "signing_secret": SIGNING_SECRET,
        }
    )

    assert config.url == "https://example.com/callback?source=batch"
    assert config.signing_secret.get_secret_value() == SIGNING_SECRET
    assert SIGNING_SECRET not in repr(config)


def test_webhook_request_defaults_path_and_rejects_unknown_fields() -> None:
    config = parse_batch_webhook_request(
        {"url": "https://example.com", "signing_secret": SIGNING_SECRET}
    )
    assert config.url == "https://example.com/"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        parse_batch_webhook_request(
            {
                "url": "https://example.com",
                "signing_secret": SIGNING_SECRET,
                "headers": {"Authorization": "not-supported"},
            }
        )


@pytest.mark.parametrize(
    "url,error",
    [
        ("ftp://example.com/hook", "scheme"),
        ("https://user:pass@example.com/hook", "user information"),
        ("https://example.com/hook#delivery", "fragment"),
        ("https://example.com/hook#", "fragment"),
        ("https://example.com:70000/hook", "invalid"),
        ("https://exa mple.com/hook", "whitespace"),
    ],
)
def test_webhook_request_rejects_invalid_urls(url: str, error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        BatchWebhookRequest.model_validate({"url": url, "signing_secret": SIGNING_SECRET})


def test_webhook_request_requires_https_by_default() -> None:
    payload = {"url": "http://localhost:8080/hook", "signing_secret": SIGNING_SECRET}

    with pytest.raises(ValueError, match="must use https"):
        parse_batch_webhook_request(payload)

    assert (
        parse_batch_webhook_request(payload, allow_http=True, allowed_ports=[8080]).url
        == "http://localhost:8080/hook"
    )

    with pytest.raises(ValueError, match="port 8080 is not allowed"):
        parse_batch_webhook_request(payload, allow_http=True)


def test_webhook_request_enforces_signing_secret_byte_length() -> None:
    with pytest.raises(ValidationError, match="at least 32 UTF-8 bytes"):
        parse_batch_webhook_request(
            {"url": "https://example.com/hook", "signing_secret": "too-short"}
        )

    multibyte_secret = "é" * 16
    assert (
        parse_batch_webhook_request(
            {"url": "https://example.com/hook", "signing_secret": multibyte_secret}
        ).signing_secret.get_secret_value()
        == multibyte_secret
    )


def test_webhook_fingerprint_is_canonical_and_sensitive_to_secret() -> None:
    first = parse_batch_webhook_request(
        {"url": "HTTPS://EXAMPLE.COM:443/hook", "signing_secret": SIGNING_SECRET}
    )
    equivalent = parse_batch_webhook_request(
        {"url": "https://example.com/hook", "signing_secret": SIGNING_SECRET}
    )
    changed = parse_batch_webhook_request(
        {"url": "https://example.com/hook", "signing_secret": SIGNING_SECRET + "-changed"}
    )

    assert canonical_batch_webhook_config_bytes(first) == canonical_batch_webhook_config_bytes(
        equivalent
    )
    assert batch_webhook_config_fingerprint(first) == batch_webhook_config_fingerprint(equivalent)
    assert batch_webhook_config_fingerprint(first) != batch_webhook_config_fingerprint(changed)
    assert redact_batch_webhook_config(first) == {"configured": True}
    assert redact_batch_webhook_config(None) == {"configured": False}


def test_webhook_outbox_models_and_mapper_normalize_contract_values() -> None:
    now = datetime.now(tz=UTC)
    create = BatchWebhookOutboxCreate(
        batch_id="batch-1",
        event_type="batch.completed",  # type: ignore[arg-type]
        target_config_ciphertext="v1.key.ciphertext",
        payload_json={"id": "batch-1"},
        payload_sha256="a" * 64,
    )
    assert create.event_type is BatchWebhookEventType.COMPLETED
    assert create.status is BatchWebhookDeliveryStatus.QUEUED

    record = webhook_outbox_from_row(
        {
            "event_id": "event-1",
            "batch_id": "batch-1",
            "event_type": "batch.failed",
            "target_config_ciphertext": "v1.key.ciphertext",
            "payload_json": '{"id":"batch-1"}',
            "payload_sha256": "b" * 64,
            "status": "retrying",
            "attempt_count": 2,
            "max_attempts": 8,
            "next_attempt_at": now,
            "last_status_code": 503,
            "last_error": "server error",
            "locked_by": None,
            "lease_expires_at": None,
            "created_at": now,
            "updated_at": now,
            "delivered_at": None,
        }
    )

    assert isinstance(record, BatchWebhookOutboxRecord)
    assert record.event_type is BatchWebhookEventType.FAILED
    assert record.status is BatchWebhookDeliveryStatus.RETRYING
    assert record.payload_json == {"id": "batch-1"}

    with pytest.raises(ValueError, match="event type"):
        BatchWebhookOutboxCreate(
            batch_id="batch-1",
            event_type="batch.unknown",  # type: ignore[arg-type]
            target_config_ciphertext="ciphertext",
            payload_json={},
            payload_sha256="c" * 64,
        )
