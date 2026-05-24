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
            return None  # redis-py returns None when an nx claim fails
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
    def __init__(
        self,
        emails: tuple[str, ...],
        *,
        policy: str = "team_admins_and_org_admins",
        raise_on_resolve: bool = False,
    ) -> None:
        self.emails = emails
        self.policy = policy
        self.raise_on_resolve = raise_on_resolve
        self.calls = 0

    async def resolve_budget_recipients(self, *, entity_type: str, entity_id: str):  # noqa: ANN201
        self.calls += 1
        if self.raise_on_resolve:
            raise RuntimeError("db unavailable")
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
    resolver: _FakeRecipientResolver | None = None,
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
        recipient_resolver=resolver or _FakeRecipientResolver(recipients),
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


@pytest.mark.asyncio
async def test_budget_alert_swallows_recipient_resolution_errors() -> None:
    redis = _FakeRedis()
    audit = _FakeAuditService()
    outbox = _FakeOutboxService()
    service = _build_service(
        enabled=True,
        redis=redis,
        outbox=outbox,
        audit=audit,
        resolver=_FakeRecipientResolver((), raise_on_resolve=True),
    )

    # Must not raise into the caller (the inference request that triggered the
    # budget check).
    await service.send_budget_alert(
        entity_type="team",
        entity_id="team-1",
        current_spend=12.0,
        soft_budget=10.0,
        hard_budget=20.0,
    )

    assert outbox.calls == []
    assert "alert:budget:team:team-1" in redis.deleted
    assert len(audit.events) == 1
    assert audit.events[0].status == "error"
    assert audit.events[0].metadata["reason"] == "exception"


@pytest.mark.asyncio
async def test_budget_alert_skips_recipient_query_when_throttled() -> None:
    redis = _FakeRedis()
    resolver = _FakeRecipientResolver(("owner@example.com",))
    service = _build_service(enabled=True, redis=redis, resolver=resolver, audit=_FakeAuditService())

    for _ in range(2):
        await service.send_budget_alert(
            entity_type="team",
            entity_id="team-1",
            current_spend=12.0,
            soft_budget=10.0,
            hard_budget=20.0,
        )

    # The second alert is throttled before any recipient DB query.
    assert resolver.calls == 1


class _DeliveringChannel:
    name = "slack"

    def __init__(self) -> None:
        self.calls = 0

    def supports(self, alert_type: str) -> bool:
        return True

    async def send(self, *, message, recipients):  # noqa: ANN001, ANN201
        from src.notifications.types import ChannelResult

        self.calls += 1
        return ChannelResult(outcome="queued")


@pytest.mark.asyncio
async def test_budget_alert_keeps_slot_when_only_slack_delivers() -> None:
    redis = _FakeRedis()
    slack = _DeliveringChannel()
    service = _build_service(
        enabled=True,
        redis=redis,
        recipients=(),  # no email recipients
        extra_channels=[slack],
        audit=_FakeAuditService(),
    )

    await service.send_budget_alert(
        entity_type="team",
        entity_id="team-1",
        current_spend=12.0,
        soft_budget=10.0,
        hard_budget=20.0,
    )

    # The slot was claimed and Slack actually delivered, so the shared silence
    # window is retained (not released).
    assert slack.calls == 1
    assert "alert:budget:team:team-1" in redis.values
    assert "alert:budget:team:team-1" not in redis.deleted


class _ClaimRaisingRedis(_FakeRedis):
    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool | None = None):  # noqa: ANN201
        self.set_calls.append((key, value, ex, nx))
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_budget_alert_skips_when_redis_claim_fails() -> None:
    redis = _ClaimRaisingRedis()
    resolver = _FakeRecipientResolver(("owner@example.com",))
    outbox = _FakeOutboxService()
    service = _build_service(
        enabled=True,
        redis=redis,
        outbox=outbox,
        resolver=resolver,
        audit=_FakeAuditService(),
    )

    # Redis being down must not raise into the inference request that triggered
    # the budget check; the alert is simply skipped (fail-closed).
    await service.send_budget_alert(
        entity_type="team",
        entity_id="team-1",
        current_spend=12.0,
        soft_budget=10.0,
        hard_budget=20.0,
    )

    # The claim was actually attempted (so the skip happened at the claim step,
    # not an earlier short-circuit) and nothing downstream ran.
    assert len(redis.set_calls) == 1
    assert resolver.calls == 0
    assert outbox.calls == []
