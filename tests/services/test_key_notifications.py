from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.key_notifications import KeyNotificationRecord, KeyNotificationService


class _FakeOutboxService:
    def __init__(self, *, status: str = "queued") -> None:
        self.status = status
        self.calls: list[dict[str, object]] = []

    async def enqueue_template_email(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        return SimpleNamespace(email_id="email-1", status=self.status)


class _FakeRecipientResolver:
    def __init__(self, emails: tuple[str, ...], *, owner_email: str | None = None) -> None:
        self.emails = emails
        self.owner_email = owner_email

    async def resolve_key_lifecycle_recipients(self, **kwargs):  # noqa: ANN003, ANN201
        return SimpleNamespace(
            emails=self.emails,
            policy="key_owner" if len(self.emails) == 1 else "key_fallback",
            owner_account_id=kwargs.get("owner_account_id"),
            team_id=kwargs.get("team_id"),
            organization_id=kwargs.get("organization_id"),
        )

    async def get_account_email(self, account_id: str | None):  # noqa: ANN201
        if account_id == "acct-actor":
            return "actor@example.com"
        return self.owner_email


class _FakeAuditService:
    def __init__(self) -> None:
        self.events: list[object] = []

    def record_event(self, event, *, payloads=None, critical=False) -> None:  # noqa: ANN001, ANN003
        del payloads, critical
        self.events.append(event)


def _config(*, enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        general_settings=SimpleNamespace(
            instance_name="DeltaLLM",
            governance_notifications_enabled=enabled,
            key_lifecycle_notifications_enabled=enabled,
        )
    )


def _record(*, owner_account_id: str | None = "acct-owner") -> KeyNotificationRecord:
    return KeyNotificationRecord(
        token_hash="key-1",
        key_name="Primary Key",
        team_id="team-1",
        team_alias="Team One",
        organization_id="org-1",
        owner_account_id=owner_account_id,
        owner_service_account_id=None,
    )


@pytest.mark.asyncio
async def test_key_lifecycle_notifications_are_opt_in() -> None:
    outbox = _FakeOutboxService()
    service = KeyNotificationService(
        outbox_service=outbox,
        recipient_resolver=_FakeRecipientResolver(("owner@example.com",)),
        config_getter=lambda: _config(enabled=False),
    )

    await service.notify_lifecycle(
        event_kind="api_key_created",
        actor_account_id="acct-admin",
        record=_record(),
    )

    assert outbox.calls == []


@pytest.mark.asyncio
async def test_key_lifecycle_notifications_are_suppressed_for_owner_actor() -> None:
    outbox = _FakeOutboxService()
    audit = _FakeAuditService()
    service = KeyNotificationService(
        outbox_service=outbox,
        recipient_resolver=_FakeRecipientResolver(("owner@example.com",)),
        audit_service=audit,
        config_getter=lambda: _config(enabled=True),
    )

    await service.notify_lifecycle(
        event_kind="api_key_deleted",
        actor_account_id="acct-owner",
        record=_record(),
    )

    assert outbox.calls == []
    assert audit.events[0].status == "skipped"


@pytest.mark.asyncio
async def test_key_lifecycle_notifications_enqueue_without_secret_values() -> None:
    outbox = _FakeOutboxService()
    service = KeyNotificationService(
        outbox_service=outbox,
        recipient_resolver=_FakeRecipientResolver(("owner@example.com",)),
        config_getter=lambda: _config(enabled=True),
    )

    await service.notify_lifecycle(
        event_kind="api_key_regenerated",
        actor_account_id="acct-actor",
        record=_record(),
    )

    assert outbox.calls[0]["template_key"] == "api_key_lifecycle"
    payload = outbox.calls[0]["payload_json"]
    assert payload["event_kind"] == "api_key_regenerated"
    assert payload["key_name"] == "Primary Key"
    assert "raw_key" not in payload


@pytest.mark.asyncio
async def test_key_lifecycle_notifications_skip_cancelled_outbox_records() -> None:
    outbox = _FakeOutboxService(status="cancelled")
    audit = _FakeAuditService()
    service = KeyNotificationService(
        outbox_service=outbox,
        recipient_resolver=_FakeRecipientResolver(("owner@example.com",)),
        audit_service=audit,
        config_getter=lambda: _config(enabled=True),
    )

    await service.notify_lifecycle(
        event_kind="api_key_created",
        actor_account_id="acct-actor",
        record=_record(),
    )

    assert len(outbox.calls) == 1
    assert audit.events[0].status == "skipped"
    assert audit.events[0].metadata["reason"] == "undeliverable"
