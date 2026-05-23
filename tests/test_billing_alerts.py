from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.billing.alerts import AlertConfig, AlertService
from src.notifications.channels.email import EmailChannel
from src.notifications.dispatcher import NotificationDispatcher


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []
        self.set_calls: list[tuple[str, str, int | None, bool | None]] = []

    async def exists(self, key: str) -> bool:
        return key in self.values

    async def setex(self, key: str, ttl: int, value: str) -> None:
        del ttl
        self.values[key] = value

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool | None = None):  # noqa: ANN201
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.deleted.append(key)


class _FakeOutboxService:
    def __init__(self, *, fail: bool = False, status: str = "queued") -> None:
        self.fail = fail
        self.status = status
        self.calls: list[dict[str, object]] = []

    async def enqueue_template_email(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("enqueue failed")
        return SimpleNamespace(email_id="email-1", status=self.status)


class _FakeRecipientResolver:
    def __init__(self, emails: tuple[str, ...], *, policy: str = "team_admins_and_org_admins") -> None:
        self.emails = emails
        self.policy = policy

    async def resolve_budget_recipients(self, *, entity_type: str, entity_id: str):  # noqa: ANN201
        return SimpleNamespace(
            emails=self.emails,
            policy=self.policy,
            team_id=entity_id if entity_type == "team" else None,
            organization_id="org-1",
            owner_account_id=None,
        )


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
            budget_notifications_enabled=enabled,
        )
    )


def _build_service(
    *,
    enabled: bool,
    redis: _FakeRedis | None = None,
    outbox: _FakeOutboxService | None = None,
    recipients: tuple[str, ...] = ("owner@example.com",),
    audit: _FakeAuditService | None = None,
    extra_channels: list | None = None,
) -> AlertService:
    outbox = outbox if outbox is not None else _FakeOutboxService()
    channels = [EmailChannel(outbox_service=outbox)]
    if extra_channels:
        channels.extend(extra_channels)
    dispatcher = NotificationDispatcher(
        channels=channels,
        redis_client=redis,
        audit_service=audit,
        dedupe_ttl_seconds=60,
    )
    return AlertService(
        config=AlertConfig(budget_alert_ttl=60),
        dispatcher=dispatcher,
        recipient_resolver=_FakeRecipientResolver(recipients),
        config_getter=lambda: _config(enabled=enabled),
    )


@pytest.mark.asyncio
async def test_budget_alert_notifications_are_opt_in() -> None:
    outbox = _FakeOutboxService()
    service = _build_service(enabled=False, redis=_FakeRedis(), outbox=outbox)

    await service.send_budget_alert(
        entity_type="team",
        entity_id="team-1",
        current_spend=12.0,
        soft_budget=10.0,
        hard_budget=20.0,
    )

    assert outbox.calls == []


@pytest.mark.asyncio
async def test_budget_alert_enqueues_once_per_ttl_window() -> None:
    redis = _FakeRedis()
    outbox = _FakeOutboxService()
    audit = _FakeAuditService()
    service = _build_service(enabled=True, redis=redis, outbox=outbox, audit=audit)

    await service.send_budget_alert(
        entity_type="team",
        entity_id="team-1",
        current_spend=12.0,
        soft_budget=10.0,
        hard_budget=20.0,
    )
    await service.send_budget_alert(
        entity_type="team",
        entity_id="team-1",
        current_spend=13.0,
        soft_budget=10.0,
        hard_budget=20.0,
    )

    assert len(outbox.calls) == 1
    assert outbox.calls[0]["template_key"] == "budget_threshold"
    assert outbox.calls[0]["to_addresses"] == ("owner@example.com",)
    assert audit.events[0].status == "success"
    assert redis.set_calls[0] == ("alert:budget:team:team-1", "1", 60, True)


@pytest.mark.asyncio
async def test_budget_alert_releases_slot_when_enqueue_fails() -> None:
    redis = _FakeRedis()
    service = _build_service(
        enabled=True,
        redis=redis,
        outbox=_FakeOutboxService(fail=True),
        audit=_FakeAuditService(),
    )

    await service.send_budget_alert(
        entity_type="team",
        entity_id="team-1",
        current_spend=12.0,
        soft_budget=10.0,
        hard_budget=20.0,
    )

    assert "alert:budget:team:team-1" in redis.deleted


@pytest.mark.asyncio
async def test_budget_alert_releases_slot_when_outbox_cancels_email() -> None:
    redis = _FakeRedis()
    audit = _FakeAuditService()
    service = _build_service(
        enabled=True,
        redis=redis,
        outbox=_FakeOutboxService(status="cancelled"),
        audit=audit,
    )

    await service.send_budget_alert(
        entity_type="team",
        entity_id="team-1",
        current_spend=12.0,
        soft_budget=10.0,
        hard_budget=20.0,
    )

    assert "alert:budget:team:team-1" in redis.deleted
    assert audit.events[0].status == "skipped"
    assert audit.events[0].metadata["reason"] == "undeliverable"


@pytest.mark.asyncio
async def test_budget_alert_releases_slot_when_no_recipients() -> None:
    redis = _FakeRedis()
    service = _build_service(
        enabled=True,
        redis=redis,
        recipients=(),
        audit=_FakeAuditService(),
    )

    await service.send_budget_alert(
        entity_type="org",
        entity_id="org-1",
        current_spend=22.0,
        soft_budget=20.0,
        hard_budget=40.0,
    )

    assert "alert:budget:org:org-1" in redis.deleted
