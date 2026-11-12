"""Content records attach to their target audit event, wait for it, or fall back."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.db.audit.audit_ingestion import AuditOutboxRecord
from src.db.audit.repository import AuditEventRecord, AuditPayloadRecord
from src.services.audit.audit_service import AuditService
from src.services.audit.content_attachment import (
    AUDIT_CONTENT_RECORD_TYPE,
    AuditContentTarget,
    PendingAuditContent,
    apply_audit_content,
    audit_content_fields,
    audit_content_record_id,
    audit_content_target,
)

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


class _FakeRepository:
    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.events: list[AuditEventRecord] = []
        self.payloads: list[AuditPayloadRecord] = []
        self.stored: list[str] = []
        self.calls: list[str] = []

    async def existing_event_ids(self, event_ids: list[str]) -> set[str]:
        self.calls.append("existing")
        return {event_id for event_id in event_ids if event_id in self.existing}

    async def create_events_batch(self, records: list[AuditEventRecord]) -> int:
        self.calls.append("events")
        self.events.extend(records)
        return len(records)

    async def create_payloads_batch(self, records: list[AuditPayloadRecord]) -> int:
        self.calls.append("payloads")
        self.payloads.extend(records)
        return len(records)

    async def mark_content_stored(self, event_ids: list[str]) -> int:
        self.calls.append("stored")
        self.stored.extend(event_ids)
        return len(event_ids)


def _pending(*, age_seconds: float, content: bool = True) -> PendingAuditContent:
    target = str(uuid4())
    outbox_id = audit_content_record_id(target)

    def payload(event_id: str) -> AuditPayloadRecord:
        return AuditPayloadRecord(
            payload_id=str(uuid4()),
            event_id=event_id,
            kind="response",
            content_json={"text": "reply"} if content else None,
            redacted=not content,
        )

    return PendingAuditContent(
        outbox_event_id=outbox_id,
        target=AuditContentTarget(
            event_id=target, enqueued_at=NOW - timedelta(seconds=age_seconds)
        ),
        attached_payloads=[payload(target)],
        fallback_event=AuditEventRecord(event_id=outbox_id, action="CHAT_COMPLETION_REQUEST"),
        fallback_payloads=[payload(outbox_id)],
    )


async def test_content_attaches_to_an_existing_event_and_marks_it_stored():
    item = _pending(age_seconds=1)
    repository = _FakeRepository({item.target.event_id})

    result = await apply_audit_content(repository, [item], now=NOW)

    assert result.deferred_event_ids == []
    assert (result.attached, result.fallback) == (1, 0)
    assert [payload.event_id for payload in repository.payloads] == [item.target.event_id]
    assert repository.stored == [item.target.event_id]
    assert repository.events == []


async def test_redacted_content_does_not_mark_the_event_stored():
    item = _pending(age_seconds=1, content=False)
    repository = _FakeRepository({item.target.event_id})

    await apply_audit_content(repository, [item], now=NOW)

    assert repository.stored == []


async def test_content_waits_for_a_missing_event_inside_the_wait():
    item = _pending(age_seconds=299)
    repository = _FakeRepository(set())

    result = await apply_audit_content(repository, [item], now=NOW)

    assert result.deferred_event_ids == [item.outbox_event_id]
    assert repository.payloads == [] and repository.events == []


async def test_content_becomes_its_own_event_after_the_wait():
    item = _pending(age_seconds=300)
    repository = _FakeRepository(set())

    result = await apply_audit_content(repository, [item], now=NOW)

    assert result.deferred_event_ids == []
    assert result.fallback == 1
    assert [event.event_id for event in repository.events] == [item.outbox_event_id]
    assert [payload.event_id for payload in repository.payloads] == [item.outbox_event_id]
    # The fallback event goes in before the payload rows that reference it.
    assert repository.calls.index("events") < repository.calls.index("payloads")


async def test_one_read_and_fixed_writes_for_a_mixed_batch():
    attached, waiting, late = (
        _pending(age_seconds=1),
        _pending(age_seconds=5),
        _pending(age_seconds=900),
    )
    repository = _FakeRepository({attached.target.event_id})

    result = await apply_audit_content(repository, [attached, waiting, late], now=NOW)

    assert repository.calls == ["existing", "events", "payloads", "stored"]
    assert result.deferred_event_ids == [waiting.outbox_event_id]
    assert (result.attached, result.fallback) == (1, 1)


async def test_no_pending_content_does_no_database_work():
    repository = _FakeRepository(set())

    result = await apply_audit_content(repository, [], now=NOW)

    assert result.deferred_event_ids == []
    assert repository.calls == []


def test_target_fields_round_trip_and_the_record_id_is_stable():
    target = str(uuid4())
    fields = audit_content_fields(target_event_id=target, enqueued_at=NOW)

    parsed = audit_content_target(fields)

    assert parsed == AuditContentTarget(event_id=target, enqueued_at=NOW)
    assert audit_content_record_id(target) == audit_content_record_id(target.upper())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"target_event_id": "not-a-uuid", "enqueued_at": NOW.isoformat()},
        {"target_event_id": str(uuid4()), "enqueued_at": "2026-11-12T10:00:00"},
        {"target_event_id": str(uuid4()), "enqueued_at": 5},
    ],
)
def test_invalid_targets_are_rejected(payload):
    with pytest.raises(ValueError):
        audit_content_target(payload)


def test_naive_enqueue_time_is_rejected_when_written():
    with pytest.raises(ValueError):
        audit_content_fields(target_event_id=str(uuid4()), enqueued_at=datetime(2026, 11, 12))


def _content_record(payload: dict) -> AuditOutboxRecord:
    return AuditOutboxRecord(
        event_id=str(uuid4()),
        record_type=AUDIT_CONTENT_RECORD_TYPE,
        organization_id="org-1",
        delivery_class="best_effort",
        payload=payload,
        redacted_payload=payload,
        policy_version=1,
        attempt_count=1,
    )


def test_worker_validation_accepts_a_content_record():
    payload = {
        "event": {"action": "CHAT_COMPLETION_REQUEST"},
        "payloads": [{"kind": "request", "content_json": {"messages": []}}],
        **audit_content_fields(target_event_id=str(uuid4()), enqueued_at=NOW),
    }

    AuditService._validate_durable_record(_content_record(payload))


def test_worker_validation_rejects_a_content_record_without_target():
    payload = {"event": {"action": "CHAT_COMPLETION_REQUEST"}, "payloads": []}

    with pytest.raises(ValueError):
        AuditService._validate_durable_record(_content_record(payload))
