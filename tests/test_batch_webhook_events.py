from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from src.batch.models import BatchJobRecord, BatchJobStatus, BatchWebhookEventType
from src.batch.serialization import serialize_public_batch
from src.batch.webhooks.events import (
    batch_webhook_event_payload_sha256,
    build_batch_webhook_event,
    canonical_batch_webhook_event_bytes,
)


def _terminal_job(status: BatchJobStatus = BatchJobStatus.COMPLETED) -> BatchJobRecord:
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    return BatchJobRecord(
        batch_id="batch-1",
        endpoint="/v1/embeddings",
        status=status,
        execution_mode="managed_internal",
        input_file_id="file-input",
        output_file_id="file-output",
        error_file_id=None,
        model="model-1",
        metadata={"customer_job_id": "job-4821"},
        provider_batch_id=None,
        provider_status=None,
        provider_error=None,
        provider_last_sync_at=None,
        total_items=3,
        in_progress_items=0,
        completed_items=3,
        failed_items=0,
        cancelled_items=0,
        locked_by=None,
        lease_expires_at=None,
        cancel_requested_at=None,
        status_last_updated_at=now,
        created_by_api_key="key-1",
        created_by_user_id=None,
        created_by_team_id=None,
        created_at=now,
        started_at=now,
        completed_at=now,
        expires_at=None,
        webhook_config_ciphertext="v1.key.ciphertext",
        webhook_config_fingerprint="a" * 64,
    )


def test_public_batch_serializer_exposes_only_webhook_indicator() -> None:
    configured = serialize_public_batch(_terminal_job())
    legacy = serialize_public_batch(
        replace(
            _terminal_job(),
            webhook_config_ciphertext=None,
            webhook_config_fingerprint=None,
        )
    )

    assert configured["webhook"] == {"configured": True}
    assert "webhook" not in legacy
    rendered = str(configured)
    assert "ciphertext" not in rendered
    assert "fingerprint" not in rendered


@pytest.mark.parametrize(
    ("status", "event_type"),
    [
        (BatchJobStatus.COMPLETED, BatchWebhookEventType.COMPLETED),
        (BatchJobStatus.FAILED, BatchWebhookEventType.FAILED),
        (BatchJobStatus.CANCELLED, BatchWebhookEventType.CANCELLED),
        (BatchJobStatus.EXPIRED, BatchWebhookEventType.EXPIRED),
    ],
)
def test_terminal_event_snapshots_public_batch_with_stable_hash(
    status: BatchJobStatus,
    event_type: BatchWebhookEventType,
) -> None:
    created_at = datetime(2026, 8, 5, 12, 30, tzinfo=UTC)
    event = build_batch_webhook_event(
        replace(_terminal_job(), status=status),
        event_id="evt-stable",
        created_at=created_at,
    )

    assert event.event_id == "evt-stable"
    assert event.event_type is event_type
    assert event.payload_json["created_at"] == int(created_at.timestamp())
    assert event.payload_json["data"]["batch"]["metadata"] == {"customer_job_id": "job-4821"}
    assert event.payload_sha256 == batch_webhook_event_payload_sha256(event.payload_json)
    assert canonical_batch_webhook_event_bytes(
        event.payload_json
    ) == canonical_batch_webhook_event_bytes(dict(reversed(list(event.payload_json.items()))))


def test_event_builder_rejects_nonterminal_batch() -> None:
    with pytest.raises(ValueError, match="not terminal"):
        build_batch_webhook_event(replace(_terminal_job(), status=BatchJobStatus.FINALIZING))
