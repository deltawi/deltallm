from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from src.db.email_feedback import EmailSuppressionRecord
from src.services.email_feedback_service import EmailFeedbackError, EmailFeedbackService


def _config(**overrides):
    general_settings = SimpleNamespace(
        resend_webhook_signing_secret="whsec_testsecret",
        resend_webhook_tolerance_seconds=300,
    )
    for key, value in overrides.items():
        setattr(general_settings, key, value)
    return SimpleNamespace(general_settings=general_settings)


def _signed_headers(*, secret: str, body: str, webhook_id: str = "evt-1", timestamp: int | None = None) -> dict[str, str]:
    ts = timestamp if timestamp is not None else int(datetime.now(tz=UTC).timestamp())
    signing_key = secret[len("whsec_") :] if secret.startswith("whsec_") else secret
    signed_content = f"{webhook_id}.{ts}.{body}".encode("utf-8")
    signature = base64.b64encode(
        hmac.new(signing_key.encode("utf-8"), signed_content, hashlib.sha256).digest()
    ).decode("utf-8")
    return {
        "svix-id": webhook_id,
        "svix-timestamp": str(ts),
        "svix-signature": f"v1,{signature}",
    }


class FakeRepository:
    def __init__(self) -> None:
        self.event_ids: set[str] = set()
        self.suppressions: dict[str, EmailSuppressionRecord] = {}
        self.message_to_email_id: dict[str, str] = {"re_123": "email-1"}
        self.resolve_calls: list[tuple[str, str | None]] = []

    async def resolve_email_id_by_provider_message_id(self, *, provider: str, provider_message_id: str | None) -> str | None:
        self.resolve_calls.append((provider, provider_message_id))
        if provider_message_id is None:
            return None
        return self.message_to_email_id.get(provider_message_id)

    async def create_webhook_event(self, record):  # noqa: ANN001, ANN201
        if record.webhook_event_id in self.event_ids:
            return False
        self.event_ids.add(record.webhook_event_id)
        return True

    async def upsert_suppression(self, **kwargs):  # noqa: ANN003, ANN201
        record = EmailSuppressionRecord(
            email_address=kwargs["email_address"],
            provider=kwargs["provider"],
            reason=kwargs["reason"],
            source=kwargs["source"],
            provider_message_id=kwargs.get("provider_message_id"),
            webhook_event_id=kwargs.get("webhook_event_id"),
            metadata=kwargs.get("metadata"),
        )
        self.suppressions[record.email_address] = record
        return record


@pytest.mark.asyncio
async def test_handle_resend_bounce_webhook_suppresses_recipients() -> None:
    repository = FakeRepository()
    service = EmailFeedbackService(repository=repository, config_getter=lambda: _config())
    payload = {
        "type": "email.bounced",
        "created_at": "2026-03-25T10:00:00Z",
        "data": {
            "email_id": "re_123",
            "to": ["User@example.com", "other@example.com"],
        },
    }
    body = json.dumps(payload, separators=(",", ":"))

    outcome = await service.handle_resend_webhook(
        headers=_signed_headers(secret="whsec_testsecret", body=body),
        raw_body=body.encode("utf-8"),
    )

    assert outcome.provider == "resend"
    assert outcome.event_type == "email.bounced"
    assert outcome.duplicate is False
    assert outcome.suppressed_count == 2
    assert outcome.recipient_addresses == ("other@example.com", "user@example.com")
    assert outcome.email_id == "email-1"
    assert repository.resolve_calls == [("resend", "re_123")]
    assert sorted(repository.suppressions) == ["other@example.com", "user@example.com"]
    assert repository.suppressions["user@example.com"].reason == "bounce"


@pytest.mark.asyncio
async def test_handle_resend_webhook_detects_duplicate_events() -> None:
    repository = FakeRepository()
    service = EmailFeedbackService(repository=repository, config_getter=lambda: _config())
    payload = {"type": "email.complained", "data": {"email_id": "re_123", "to": ["user@example.com"]}}
    body = json.dumps(payload, separators=(",", ":"))
    headers = _signed_headers(secret="whsec_testsecret", body=body, webhook_id="evt-dup")

    first = await service.handle_resend_webhook(headers=headers, raw_body=body.encode("utf-8"))
    second = await service.handle_resend_webhook(headers=headers, raw_body=body.encode("utf-8"))

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.suppressed_count == 0


@pytest.mark.asyncio
async def test_handle_resend_webhook_rejects_invalid_signature() -> None:
    repository = FakeRepository()
    service = EmailFeedbackService(repository=repository, config_getter=lambda: _config())
    payload = {"type": "email.bounced", "data": {"email_id": "re_123", "to": ["user@example.com"]}}
    body = json.dumps(payload)

    with pytest.raises(EmailFeedbackError, match="signature verification failed"):
        await service.handle_resend_webhook(
            headers={
                "svix-id": "evt-1",
                "svix-timestamp": str(int(datetime.now(tz=UTC).timestamp())),
                "svix-signature": "v1,invalid",
            },
            raw_body=body.encode("utf-8"),
        )


@pytest.mark.asyncio
async def test_handle_resend_webhook_rejects_invalid_utf8_body() -> None:
    repository = FakeRepository()
    service = EmailFeedbackService(repository=repository, config_getter=lambda: _config())

    with pytest.raises(EmailFeedbackError, match="invalid resend webhook payload"):
        await service.handle_resend_webhook(
            headers=_signed_headers(secret="whsec_testsecret", body="{}"),
            raw_body=b"\xff\xfe",
        )


@pytest.mark.asyncio
async def test_handle_resend_webhook_rejects_invalid_created_at() -> None:
    repository = FakeRepository()
    service = EmailFeedbackService(repository=repository, config_getter=lambda: _config())
    payload = {
        "type": "email.bounced",
        "created_at": "not-a-timestamp",
        "data": {"email_id": "re_123", "to": ["user@example.com"]},
    }
    body = json.dumps(payload, separators=(",", ":"))

    with pytest.raises(EmailFeedbackError, match="invalid resend webhook payload"):
        await service.handle_resend_webhook(
            headers=_signed_headers(secret="whsec_testsecret", body=body),
            raw_body=body.encode("utf-8"),
        )


@pytest.mark.asyncio
async def test_handle_resend_webhook_rejects_invalid_actionable_data_shape() -> None:
    repository = FakeRepository()
    service = EmailFeedbackService(repository=repository, config_getter=lambda: _config())
    payload = {
        "type": "email.bounced",
        "data": "wrong",
    }
    body = json.dumps(payload, separators=(",", ":"))

    with pytest.raises(EmailFeedbackError, match="invalid resend webhook payload"):
        await service.handle_resend_webhook(
            headers=_signed_headers(secret="whsec_testsecret", body=body),
            raw_body=body.encode("utf-8"),
        )


@pytest.mark.asyncio
async def test_handle_resend_webhook_rejects_invalid_recipient_list_shape() -> None:
    repository = FakeRepository()
    service = EmailFeedbackService(repository=repository, config_getter=lambda: _config())
    payload = {
        "type": "email.complained",
        "data": {"email_id": "re_123", "to": "user@example.com"},
    }
    body = json.dumps(payload, separators=(",", ":"))

    with pytest.raises(EmailFeedbackError, match="invalid resend webhook payload"):
        await service.handle_resend_webhook(
            headers=_signed_headers(secret="whsec_testsecret", body=body),
            raw_body=body.encode("utf-8"),
        )


@pytest.mark.asyncio
async def test_handle_resend_webhook_ignores_non_actionable_events() -> None:
    repository = FakeRepository()
    service = EmailFeedbackService(repository=repository, config_getter=lambda: _config())
    payload = {
        "type": "email.sent",
        "data": "ignored",
    }
    body = json.dumps(payload, separators=(",", ":"))

    outcome = await service.handle_resend_webhook(
        headers=_signed_headers(secret="whsec_testsecret", body=body),
        raw_body=body.encode("utf-8"),
    )

    assert outcome.event_type == "email.sent"
    assert outcome.suppressed_count == 0
    assert repository.suppressions == {}
