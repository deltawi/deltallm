from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.email_feedback import EmailSuppressionRecord
from src.services.email_feedback_service import EmailFeedbackError, EmailFeedbackOutcome


class _FakeEmailOutboxService:
    def __init__(self, *, status: str = "queued") -> None:
        self.calls: list[dict[str, object]] = []
        self.status = status

    async def enqueue_template_email(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        return type(
            "QueuedEmail",
            (),
            {
                "email_id": "email-123",
                "status": self.status,
                "provider": kwargs.get("provider_override") or "smtp",
            },
        )()


class _FakeEmailRepository:
    async def summarize_status_counts(self):  # noqa: ANN201
        return [
            type("Count", (), {"status": "queued", "count": 2})(),
            type("Count", (), {"status": "sent", "count": 5})(),
        ]

    async def list_recent(self, *, limit: int = 20):  # noqa: ANN201
        del limit
        now = datetime.now(tz=UTC)
        return [
            type(
                "Record",
                (),
                {
                    "email_id": "email-123",
                    "kind": "test",
                    "provider": "smtp",
                    "template_key": "test_email",
                    "status": "sent",
                    "attempt_count": 1,
                    "max_attempts": 3,
                    "to_addresses": ["user@example.com"],
                    "cc_addresses": [],
                    "bcc_addresses": [],
                    "last_error": None,
                    "created_at": now,
                    "updated_at": now,
                    "sent_at": now,
                    "text_body": "secret",
                    "html_body": "<p>secret</p>",
                },
            )()
        ]


class _FakeEmailFeedbackService:
    def __init__(self, outcome: EmailFeedbackOutcome | Exception) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    async def handle_resend_webhook(self, *, headers, raw_body):  # noqa: ANN001, ANN201
        self.calls.append({"headers": dict(headers), "raw_body": raw_body})
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeEmailFeedbackRepository:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.records = [
            EmailSuppressionRecord(
                email_address="user@example.com",
                provider="resend",
                reason="bounce",
                source="webhook",
                provider_message_id="re_123",
                webhook_event_id="evt-1",
                metadata={"type": "email.bounced"},
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
        ]
        self.removed: list[str] = []

    async def list_suppressions(self, *, limit: int = 100, search: str | None = None):  # noqa: ANN201
        del limit
        if not search:
            return self.records
        return [record for record in self.records if search.lower() in record.email_address]

    async def remove_suppression(self, email_address: str) -> bool:
        self.removed.append(email_address)
        return True


@pytest.mark.asyncio
async def test_send_test_email_rejects_invalid_recipient(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.email_outbox_service = _FakeEmailOutboxService()

    response = await client.post(
        "/ui/api/email/test",
        headers={"Authorization": "Bearer mk-test"},
        json={"to_address": "not-an-email"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "valid to_address is required"


@pytest.mark.asyncio
async def test_send_test_email_uses_master_key_and_returns_provider_result(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    setattr(test_app.state.app_config.general_settings, "master_key", "mk-test")
    service = _FakeEmailOutboxService()
    setattr(test_app.state.app_config.general_settings, "instance_name", "DeltaLLM")
    setattr(test_app.state.app_config.general_settings, "email_provider", "smtp")
    test_app.state.email_outbox_service = service

    response = await client.post(
        "/ui/api/email/test",
        headers={"Authorization": "Bearer mk-test"},
        json={"to_address": "user@example.com", "provider": "sendgrid"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "queued": True,
        "email_id": "email-123",
        "status": "queued",
        "to_address": "user@example.com",
        "provider": "sendgrid",
    }
    assert len(service.calls) == 1
    assert service.calls[0]["template_key"] == "test_email"
    assert service.calls[0]["to_addresses"] == ("user@example.com",)
    assert service.calls[0]["provider_override"] == "sendgrid"


@pytest.mark.asyncio
async def test_send_test_email_rejects_undeliverable_outbox_records(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    setattr(test_app.state.app_config.general_settings, "master_key", "mk-test")
    test_app.state.email_outbox_service = _FakeEmailOutboxService(status="cancelled")

    response = await client.post(
        "/ui/api/email/test",
        headers={"Authorization": "Bearer mk-test"},
        json={"to_address": "user@example.com"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "test email cannot be delivered to the requested recipient"


@pytest.mark.asyncio
async def test_get_email_outbox_summary_returns_safe_fields(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    setattr(test_app.state.app_config.general_settings, "master_key", "mk-test")
    test_app.state.email_outbox_repository = _FakeEmailRepository()

    response = await client.get(
        "/ui/api/email/outbox/summary",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status_counts": {"queued": 2, "sent": 5},
        "pending_count": 2,
        "recent": [
            {
                "email_id": "email-123",
                "kind": "test",
                "provider": "smtp",
                "template_key": "test_email",
                "status": "sent",
                "attempt_count": 1,
                "max_attempts": 3,
                "recipient_count": 1,
                "last_error": None,
                "created_at": response.json()["recent"][0]["created_at"],
                "updated_at": response.json()["recent"][0]["updated_at"],
                "sent_at": response.json()["recent"][0]["sent_at"],
            }
        ],
    }


@pytest.mark.asyncio
async def test_resend_email_webhook_returns_feedback_outcome(client, test_app) -> None:
    service = _FakeEmailFeedbackService(
        EmailFeedbackOutcome(
            provider="resend",
            event_type="email.bounced",
            duplicate=False,
            suppressed_count=1,
            recipient_addresses=("user@example.com",),
            email_id="email-1",
        )
    )
    test_app.state.email_feedback_service = service

    response = await client.post(
        "/webhooks/email/resend",
        headers={"svix-id": "evt-1", "svix-timestamp": "1", "svix-signature": "v1,abc"},
        content=b'{"type":"email.bounced"}',
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "provider": "resend",
        "event_type": "email.bounced",
        "duplicate": False,
        "suppressed_count": 1,
        "recipient_addresses": ["user@example.com"],
        "email_id": "email-1",
    }
    assert len(service.calls) == 1


@pytest.mark.asyncio
async def test_resend_email_webhook_surfaces_validation_errors(client, test_app) -> None:
    test_app.state.email_feedback_service = _FakeEmailFeedbackService(
        EmailFeedbackError("webhook signature verification failed")
    )

    response = await client.post("/webhooks/email/resend", content=b"{}")

    assert response.status_code == 400
    assert response.json()["detail"] == "webhook signature verification failed"


@pytest.mark.asyncio
async def test_list_and_delete_email_suppressions(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    setattr(test_app.state.app_config.general_settings, "master_key", "mk-test")
    repository = _FakeEmailFeedbackRepository()
    test_app.state.email_feedback_repository = repository

    listed = await client.get("/ui/api/email/suppressions", headers={"Authorization": "Bearer mk-test"})
    deleted = await client.delete(
        "/ui/api/email/suppressions/user%40example.com",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["data"][0]["email_address"] == "user@example.com"
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert repository.removed == ["user@example.com"]
