from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from src.audit.actions import AuditAction
from src.batch.models import (
    BatchWebhookDeliveryStatus,
    BatchWebhookEventType,
    BatchWebhookOutboxRecord,
)
from src.batch.webhooks.terminalization import BatchWebhookTerminalRecorder


NOW = datetime(2026, 8, 8, tzinfo=UTC)


def _record() -> BatchWebhookOutboxRecord:
    return BatchWebhookOutboxRecord(
        event_id="event-1",
        batch_id="batch-1",
        event_type=BatchWebhookEventType.COMPLETED,
        target_config_ciphertext="ciphertext",
        payload_json={"id": "event-1"},
        payload_sha256="digest",
        status=BatchWebhookDeliveryStatus.PROCESSING,
        attempt_count=2,
        max_attempts=3,
        next_attempt_at=NOW,
        last_status_code=None,
        last_error=None,
        locked_by="worker-1",
        lease_expires_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        delivered_at=None,
        created_by_team_id=None,
        created_by_organization_id="org-1",
    )


class _Repository:
    def __init__(self) -> None:
        self.status = BatchWebhookDeliveryStatus.PROCESSING
        self.update_result = True
        self.prisma = _Prisma(self)

    def with_prisma(self, prisma) -> _Repository:  # noqa: ANN001
        assert prisma is self.prisma
        return self

    async def mark_webhook_outbox_delivered(self, **kwargs) -> bool:  # noqa: ANN003
        del kwargs
        if self.update_result:
            self.status = BatchWebhookDeliveryStatus.DELIVERED
        return self.update_result

    async def mark_webhook_outbox_failed(self, **kwargs) -> bool:  # noqa: ANN003
        del kwargs
        if self.update_result:
            self.status = BatchWebhookDeliveryStatus.FAILED
        return self.update_result

    async def resolve_batch_organization_id(self, **kwargs):  # noqa: ANN003, ANN201
        return kwargs["created_by_organization_id"]


class _Prisma:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.commits = 0
        self.rollbacks = 0

    @asynccontextmanager
    async def tx(self):  # noqa: ANN201
        previous_status = self.repository.status
        try:
            yield self
        except BaseException:
            self.repository.status = previous_status
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


class _AuditService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[object] = []

    async def record_event_sync(self, event, *, payloads=None, repository=None) -> None:  # noqa: ANN001
        assert payloads is None
        assert repository is not None
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


@pytest.mark.asyncio
async def test_terminal_delivery_and_audit_commit_together() -> None:
    repository = _Repository()
    audit = _AuditService()
    recorder = BatchWebhookTerminalRecorder(
        repository=repository,  # type: ignore[arg-type]
        audit_service=audit,  # type: ignore[arg-type]
        worker_id="worker-1",
    )

    assert await recorder.mark_delivered(_record(), status_code=204) is True

    assert repository.status is BatchWebhookDeliveryStatus.DELIVERED
    assert repository.prisma.commits == 1
    assert repository.prisma.rollbacks == 0
    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.action == AuditAction.BATCH_WEBHOOK_DELIVERED  # type: ignore[attr-defined]
    assert event.organization_id == "org-1"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_terminal_delivery() -> None:
    repository = _Repository()
    recorder = BatchWebhookTerminalRecorder(
        repository=repository,  # type: ignore[arg-type]
        audit_service=_AuditService(fail=True),  # type: ignore[arg-type]
        worker_id="worker-1",
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await recorder.mark_failed(
            _record(),
            reason="http_permanent_status",
            status_code=400,
        )

    assert repository.status is BatchWebhookDeliveryStatus.PROCESSING
    assert repository.prisma.commits == 0
    assert repository.prisma.rollbacks == 1


@pytest.mark.asyncio
async def test_fence_loss_does_not_write_an_audit_event() -> None:
    repository = _Repository()
    repository.update_result = False
    audit = _AuditService()
    recorder = BatchWebhookTerminalRecorder(
        repository=repository,  # type: ignore[arg-type]
        audit_service=audit,  # type: ignore[arg-type]
        worker_id="worker-1",
    )

    assert await recorder.mark_delivered(_record(), status_code=200) is False

    assert repository.status is BatchWebhookDeliveryStatus.PROCESSING
    assert audit.events == []
    assert repository.prisma.commits == 1
