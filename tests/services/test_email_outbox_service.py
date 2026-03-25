from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.db.email import EmailOutboxRecord
from src.email.models import EmailDeliveryError, EmailDeliveryResult, PreparedEmail
from src.services.email_outbox_service import EmailOutboxService, EmailOutboxWorker


def _email_config(**overrides):
    general_settings = SimpleNamespace(
        email_enabled=True,
        email_provider="smtp",
        email_from_address="noreply@example.com",
        email_from_name="DeltaLLM",
        email_reply_to=None,
        email_max_attempts=3,
        email_retry_initial_seconds=60,
        email_retry_max_seconds=600,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username=None,
        smtp_password=None,
        smtp_use_tls=False,
        smtp_use_starttls=True,
    )
    for key, value in overrides.items():
        setattr(general_settings, key, value)
    return SimpleNamespace(general_settings=general_settings)


class FakeRepository:
    def __init__(self) -> None:
        self.records: dict[str, EmailOutboxRecord] = {}
        self.claimed_ids: list[str] = []

    async def enqueue(self, record: EmailOutboxRecord) -> EmailOutboxRecord:
        email_id = record.email_id or "email-1"
        stored = replace(record, email_id=email_id, created_at=datetime.now(tz=UTC), updated_at=datetime.now(tz=UTC))
        self.records[email_id] = stored
        return stored

    async def claim_due(self, *, limit: int = 10) -> list[EmailOutboxRecord]:
        ready = [
            record
            for record in self.records.values()
            if record.status in {"queued", "retrying"}
        ][:limit]
        claimed: list[EmailOutboxRecord] = []
        for record in ready:
            updated = replace(record, status="sending", attempt_count=record.attempt_count + 1)
            self.records[record.email_id] = updated
            self.claimed_ids.append(record.email_id)
            claimed.append(updated)
        return claimed

    async def mark_sent(self, email_id: str, *, provider_message_id: str | None = None) -> None:
        record = self.records[email_id]
        self.records[email_id] = replace(
            record,
            status="sent",
            last_provider_message_id=provider_message_id,
            last_error=None,
            sent_at=datetime.now(tz=UTC),
        )

    async def mark_retry(self, email_id: str, *, error: str, next_attempt_at: datetime) -> None:
        record = self.records[email_id]
        self.records[email_id] = replace(
            record,
            status="retrying",
            last_error=error,
            next_attempt_at=next_attempt_at,
        )

    async def mark_failed(self, email_id: str, *, error: str) -> None:
        record = self.records[email_id]
        self.records[email_id] = replace(record, status="failed", last_error=error)

    async def update_recipients_and_payload(
        self,
        email_id: str,
        *,
        to_addresses: list[str],
        cc_addresses: list[str],
        bcc_addresses: list[str],
        payload_json: dict[str, object] | None,
    ) -> None:
        record = self.records[email_id]
        self.records[email_id] = replace(
            record,
            to_addresses=list(to_addresses),
            cc_addresses=list(cc_addresses),
            bcc_addresses=list(bcc_addresses),
            payload_json=payload_json,
        )

    async def cancel(self, email_id: str, *, reason: str | None = None) -> None:
        record = self.records[email_id]
        self.records[email_id] = replace(record, status="cancelled", last_error=reason)

    async def count_pending(self) -> int:
        return sum(1 for record in self.records.values() if record.status in {"queued", "retrying"})


class FakeAuditService:
    def __init__(self) -> None:
        self.events: list[object] = []

    def record_event(self, event, *, payloads=None, critical=False) -> None:  # noqa: ANN001, ANN002, ANN003
        del payloads
        self.events.append((event, critical))


class FakeDeliveryService:
    def __init__(self) -> None:
        self.prepared_messages: list[PreparedEmail] = []
        self.next_result: EmailDeliveryResult | Exception = EmailDeliveryResult(provider="smtp", provider_message_id="msg-1")

    def prepare_template_email(self, **kwargs):  # noqa: ANN003, ANN201
        message = PreparedEmail(
            kind=kwargs.get("kind", "transactional"),
            provider="smtp",
            to_addresses=tuple(kwargs["to_addresses"]),
            from_address="DeltaLLM <noreply@example.com>",
            subject="DeltaLLM email delivery test",
            text_body="body",
            template_key=kwargs["template_key"],
            payload_json=kwargs.get("payload_json"),
        )
        self.prepared_messages.append(message)
        return message

    async def send_prepared_email(self, message: PreparedEmail) -> EmailDeliveryResult:
        self.prepared_messages.append(message)
        if isinstance(self.next_result, Exception):
            raise self.next_result
        return self.next_result


class FakeFeedbackRepository:
    def __init__(self, suppressed: set[str] | None = None) -> None:
        self.suppressed = {item.lower() for item in (suppressed or set())}

    async def get_suppressed_addresses(self, addresses: list[str]) -> set[str]:
        return {address.strip().lower() for address in addresses if address.strip().lower() in self.suppressed}


@pytest.mark.asyncio
async def test_enqueue_template_email_persists_prepared_message() -> None:
    repository = FakeRepository()
    delivery_service = FakeDeliveryService()
    service = EmailOutboxService(
        repository=repository,
        delivery_service=delivery_service,
        config_getter=lambda: _email_config(),
    )

    record = await service.enqueue_template_email(
        template_key="test_email",
        to_addresses=("user@example.com",),
        payload_json={"instance_name": "DeltaLLM"},
        created_by_account_id="acct-1",
    )

    assert record.email_id == "email-1"
    assert record.status == "queued"
    assert record.created_by_account_id == "acct-1"
    assert repository.records["email-1"].subject == "DeltaLLM email delivery test"


@pytest.mark.asyncio
async def test_enqueue_template_email_filters_suppressed_recipients() -> None:
    repository = FakeRepository()
    delivery_service = FakeDeliveryService()
    service = EmailOutboxService(
        repository=repository,
        delivery_service=delivery_service,
        config_getter=lambda: _email_config(),
        feedback_repository=FakeFeedbackRepository({"blocked@example.com"}),
    )

    record = await service.enqueue_template_email(
        template_key="test_email",
        to_addresses=("blocked@example.com", "ok@example.com"),
        payload_json={"instance_name": "DeltaLLM"},
    )

    assert record.status == "queued"
    assert record.to_addresses == ["ok@example.com"]
    assert record.payload_json == {
        "instance_name": "DeltaLLM",
        "suppressed_recipients": ["blocked@example.com"],
    }


@pytest.mark.asyncio
async def test_enqueue_template_email_cancels_when_all_recipients_suppressed() -> None:
    repository = FakeRepository()
    delivery_service = FakeDeliveryService()
    service = EmailOutboxService(
        repository=repository,
        delivery_service=delivery_service,
        config_getter=lambda: _email_config(),
        feedback_repository=FakeFeedbackRepository({"blocked@example.com"}),
    )

    record = await service.enqueue_template_email(
        template_key="test_email",
        to_addresses=("blocked@example.com",),
    )

    assert record.status == "cancelled"
    assert record.to_addresses == []
    assert record.last_error == "all recipients are suppressed"


@pytest.mark.asyncio
async def test_worker_marks_sent_on_success() -> None:
    repository = FakeRepository()
    record = EmailOutboxRecord(
        email_id="email-1",
        kind="test",
        provider="smtp",
        to_addresses=["user@example.com"],
        from_address="DeltaLLM <noreply@example.com>",
        subject="subject",
        text_body="body",
    )
    await repository.enqueue(record)
    delivery_service = FakeDeliveryService()
    audit_service = FakeAuditService()
    worker = EmailOutboxWorker(
        repository=repository,
        delivery_service=delivery_service,
        config_getter=lambda: _email_config(),
        audit_service=audit_service,
    )

    processed = await worker.process_once()

    assert processed == 1
    assert repository.records["email-1"].status == "sent"
    assert repository.records["email-1"].last_provider_message_id == "msg-1"
    assert len(audit_service.events) == 1
    event, critical = audit_service.events[0]
    assert event.action == "EMAIL_DELIVERY_RESULT"
    assert event.status == "sent"
    assert critical is True


@pytest.mark.asyncio
async def test_worker_marks_retry_before_terminal_failure() -> None:
    repository = FakeRepository()
    await repository.enqueue(
        EmailOutboxRecord(
            email_id="email-1",
            kind="test",
            provider="smtp",
            to_addresses=["user@example.com"],
            from_address="DeltaLLM <noreply@example.com>",
            subject="subject",
            text_body="body",
            max_attempts=3,
        )
    )
    delivery_service = FakeDeliveryService()
    delivery_service.next_result = EmailDeliveryError("temporary failure", retriable=True, provider="smtp")
    audit_service = FakeAuditService()
    worker = EmailOutboxWorker(
        repository=repository,
        delivery_service=delivery_service,
        config_getter=lambda: _email_config(email_retry_initial_seconds=30),
        audit_service=audit_service,
    )

    processed = await worker.process_once()

    assert processed == 1
    stored = repository.records["email-1"]
    assert stored.status == "retrying"
    assert stored.attempt_count == 1
    assert stored.last_error == "temporary failure"
    assert stored.next_attempt_at is not None

    repository.records["email-1"] = replace(stored, status="retrying", attempt_count=3)
    processed = await worker.process_once()

    assert processed == 1
    assert repository.records["email-1"].status == "failed"
    assert len(audit_service.events) == 1
    event, _ = audit_service.events[0]
    assert event.status == "failed"


@pytest.mark.asyncio
async def test_worker_filters_suppressed_recipients_before_send() -> None:
    repository = FakeRepository()
    await repository.enqueue(
        EmailOutboxRecord(
            email_id="email-1",
            kind="notification",
            provider="smtp",
            to_addresses=["blocked@example.com", "ok@example.com"],
            from_address="DeltaLLM <noreply@example.com>",
            subject="subject",
            text_body="body",
            payload_json={"instance_name": "DeltaLLM"},
        )
    )
    delivery_service = FakeDeliveryService()
    worker = EmailOutboxWorker(
        repository=repository,
        delivery_service=delivery_service,
        config_getter=lambda: _email_config(),
        feedback_repository=FakeFeedbackRepository({"blocked@example.com"}),
    )

    processed = await worker.process_once()

    assert processed == 1
    sent_message = delivery_service.prepared_messages[-1]
    assert sent_message.to_addresses == ("ok@example.com",)
    stored = repository.records["email-1"]
    assert stored.status == "sent"
    assert stored.to_addresses == ["ok@example.com"]
    assert stored.payload_json == {
        "instance_name": "DeltaLLM",
        "suppressed_recipients": ["blocked@example.com"],
    }


@pytest.mark.asyncio
async def test_worker_cancels_when_all_recipients_become_suppressed() -> None:
    repository = FakeRepository()
    await repository.enqueue(
        EmailOutboxRecord(
            email_id="email-1",
            kind="test",
            provider="smtp",
            to_addresses=["blocked@example.com"],
            from_address="DeltaLLM <noreply@example.com>",
            subject="subject",
            text_body="body",
        )
    )
    delivery_service = FakeDeliveryService()
    audit_service = FakeAuditService()
    worker = EmailOutboxWorker(
        repository=repository,
        delivery_service=delivery_service,
        config_getter=lambda: _email_config(),
        audit_service=audit_service,
        feedback_repository=FakeFeedbackRepository({"blocked@example.com"}),
    )

    processed = await worker.process_once()

    assert processed == 1
    assert delivery_service.prepared_messages == []
    stored = repository.records["email-1"]
    assert stored.status == "cancelled"
    assert stored.last_error == "all recipients are suppressed"
    assert stored.to_addresses == []
    assert stored.payload_json == {"suppressed_recipients": ["blocked@example.com"]}
    assert len(audit_service.events) == 1
    event, critical = audit_service.events[0]
    assert event.status == "cancelled"
    assert critical is True


@pytest.mark.asyncio
async def test_worker_processes_batch_with_bounded_concurrency() -> None:
    repository = FakeRepository()
    for index in range(3):
        await repository.enqueue(
            EmailOutboxRecord(
                email_id=f"email-{index}",
                kind="notification",
                provider="smtp",
                to_addresses=[f"user{index}@example.com"],
                from_address="DeltaLLM <noreply@example.com>",
                subject="subject",
                text_body="body",
            )
        )

    class ConcurrencyAwareDeliveryService(FakeDeliveryService):
        def __init__(self) -> None:
            super().__init__()
            self.inflight = 0
            self.max_inflight = 0

        async def send_prepared_email(self, message: PreparedEmail) -> EmailDeliveryResult:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            await asyncio.sleep(0.01)
            self.inflight -= 1
            return await super().send_prepared_email(message)

    delivery_service = ConcurrencyAwareDeliveryService()
    worker = EmailOutboxWorker(
        repository=repository,
        delivery_service=delivery_service,
        config_getter=lambda: _email_config(),
        config=type("Cfg", (), {"poll_interval_seconds": 5.0, "max_batch_size": 10, "max_concurrency": 2})(),
    )

    processed = await worker.process_once()

    assert processed == 3
    assert delivery_service.max_inflight == 2
