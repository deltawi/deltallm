from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from src.db.email_feedback import EmailFeedbackRepository, EmailWebhookEventRecord


class FakePrisma:
    def __init__(self) -> None:
        self.suppressions: dict[str, dict[str, object]] = {}
        self.events: dict[str, dict[str, object]] = {}
        self.outbox_rows: dict[tuple[str, str], str] = {
            ("resend", "re_123"): "email-1",
            ("sendgrid", "re_123"): "email-2",
        }

    async def query_raw(self, query: str, *args):
        if "INSERT INTO deltallm_emailwebhookevent" in query:
            webhook_event_id = str(args[0])
            if webhook_event_id in self.events:
                return []
            row = {
                "webhook_event_id": webhook_event_id,
                "provider": str(args[1]),
                "event_type": str(args[2]),
                "recipient_address": args[3],
                "provider_message_id": args[4],
                "email_id": args[5],
                "payload_json": json.loads(str(args[6])) if args[6] is not None else None,
                "occurred_at": args[7],
                "created_at": datetime.now(tz=UTC).isoformat(),
                "updated_at": datetime.now(tz=UTC).isoformat(),
            }
            self.events[webhook_event_id] = row
            return [{"webhook_event_id": webhook_event_id}]

        if "SELECT email_id" in query and "FROM deltallm_emailoutbox" in query:
            email_id = self.outbox_rows.get((str(args[0]), str(args[1])))
            return [{"email_id": email_id}] if email_id else []

        if "INSERT INTO deltallm_emailsuppression" in query:
            email_address = str(args[0])
            existing = self.suppressions.get(email_address)
            row = {
                "email_address": email_address,
                "provider": str(args[1]),
                "reason": str(args[2]),
                "source": str(args[3]),
                "provider_message_id": args[4],
                "webhook_event_id": args[5],
                "metadata": json.loads(str(args[6])) if args[6] is not None else None,
                "first_seen_at": (existing or {}).get("first_seen_at") or datetime.now(tz=UTC).isoformat(),
                "last_seen_at": datetime.now(tz=UTC).isoformat(),
                "created_at": (existing or {}).get("created_at") or datetime.now(tz=UTC).isoformat(),
                "updated_at": datetime.now(tz=UTC).isoformat(),
            }
            self.suppressions[email_address] = row
            return [dict(row)]

        if "FROM deltallm_emailsuppression" in query and "WHERE email_address IN" in query:
            return [
                {"email_address": item}
                for item in args
                if str(item) in self.suppressions
            ]

        if "FROM deltallm_emailsuppression" in query and "ORDER BY last_seen_at DESC" in query:
            return list(self.suppressions.values())[: int(args[-1])]

        return []

    async def execute_raw(self, query: str, *args):
        if "DELETE FROM deltallm_emailsuppression" in query:
            return 1 if self.suppressions.pop(str(args[0]), None) else 0
        return 0


@pytest.mark.asyncio
async def test_email_feedback_repository_roundtrip() -> None:
    repo = EmailFeedbackRepository(FakePrisma())
    created = await repo.create_webhook_event(
        EmailWebhookEventRecord(
            webhook_event_id="evt-1",
            provider="resend",
            event_type="email.bounced",
            recipient_address="user@example.com",
            provider_message_id="re_123",
            email_id="email-1",
            payload_json={"type": "email.bounced"},
            occurred_at=datetime.now(tz=UTC),
        )
    )
    duplicate = await repo.create_webhook_event(
        EmailWebhookEventRecord(
            webhook_event_id="evt-1",
            provider="resend",
            event_type="email.bounced",
        )
    )

    stored = await repo.upsert_suppression(
        email_address="User@example.com",
        provider="resend",
        reason="bounce",
        source="webhook",
        provider_message_id="re_123",
        webhook_event_id="evt-1",
        metadata={"type": "email.bounced"},
    )

    assert created is True
    assert duplicate is False
    assert stored.email_address == "user@example.com"
    assert await repo.resolve_email_id_by_provider_message_id(provider="resend", provider_message_id="re_123") == "email-1"
    assert await repo.resolve_email_id_by_provider_message_id(provider="sendgrid", provider_message_id="re_123") == "email-2"
    assert await repo.get_suppressed_addresses(["USER@example.com", "other@example.com"]) == {"user@example.com"}
    assert len(await repo.list_suppressions(limit=10)) == 1
    assert await repo.remove_suppression("user@example.com") is True
