from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.billing.alerts import AlertService
from src.billing.budget_notifications import BudgetNotificationWorker
from tests.test_budget_notification_worker import dependencies, record
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
        dispatcher=dispatcher,
        recipient_resolver=resolver or _FakeRecipientResolver(recipients),
        config_getter=lambda: _config(enabled=enabled),
    )


async def _process(service):
    repo, _ = dependencies()
    await BudgetNotificationWorker(repo, service).process(record())
    return repo


@pytest.mark.asyncio
async def test_budget_alert_notifications_are_opt_in() -> None:
    outbox = _FakeOutboxService()
    service = _build_service(enabled=False, redis=_FakeRedis(), outbox=outbox)
    repo = await _process(service)
    assert outbox.calls == []
    repo.finish.assert_awaited_once_with(record(), delivered=True, outcome="disabled")


@pytest.mark.asyncio
async def test_budget_alert_dispatches_email_and_records_audit_without_redis_claim() -> None:
    redis, outbox, audit = _FakeRedis(), _FakeOutboxService(), _FakeAuditService()
    service = _build_service(enabled=True, redis=redis, outbox=outbox, audit=audit)
    repo = await _process(service)
    assert len(outbox.calls) == 1
    assert outbox.calls[0]["template_key"] == "budget_threshold"
    assert outbox.calls[0]["to_addresses"] == ("owner@example.com",)
    assert audit.events[0].status == "success"
    assert redis.set_calls == []
    repo.finish.assert_awaited_once_with(record(), delivered=True, outcome="delivered")


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"fail": True}, {"status": "cancelled"}])
async def test_budget_alert_preserves_unknown_outcome_when_email_is_not_confirmed(options) -> None:
    outbox = _FakeOutboxService(**options)
    repo = await _process(_build_service(enabled=True, outbox=outbox, audit=_FakeAuditService()))
    repo.finish.assert_awaited_once_with(record(), delivered=False, outcome="delivery_unknown")
    repo.retry_preparation.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_alert_no_recipients_has_observable_terminal_outcome() -> None:
    audit = _FakeAuditService()
    repo = await _process(_build_service(enabled=True, recipients=(), audit=audit))
    repo.finish.assert_awaited_once_with(record(), delivered=False, outcome="delivery_unknown")
    assert audit.events[0].status == "skipped"
    assert audit.events[0].metadata["reason"] == "no_recipients"


@pytest.mark.asyncio
async def test_budget_alert_recipient_failure_retries_only_preparation() -> None:
    outbox = _FakeOutboxService()
    repo = await _process(
        _build_service(
            enabled=True,
            outbox=outbox,
            resolver=_FakeRecipientResolver((), raise_on_resolve=True),
        )
    )
    assert outbox.calls == []
    repo.retry_preparation.assert_awaited_once()
    repo.begin_dispatch.assert_not_awaited()


class _DeliveringChannel:
    name = "slack"

    def __init__(self) -> None:
        self.calls = 0

    def supports(self, alert_type: str) -> bool:
        return True

    async def send(self, *, message, recipients):
        from src.notifications.types import ChannelResult

        self.calls += 1
        return ChannelResult(outcome="queued")


@pytest.mark.asyncio
async def test_budget_alert_completes_when_only_slack_delivers() -> None:
    slack = _DeliveringChannel()
    repo = await _process(
        _build_service(
            enabled=True,
            recipients=(),
            extra_channels=[slack],
            audit=_FakeAuditService(),
        )
    )
    assert slack.calls == 1
    repo.finish.assert_awaited_once_with(record(), delivered=True, outcome="delivered")


class _ClaimRaisingRedis(_FakeRedis):
    async def set(self, *args, **kwargs):
        raise AssertionError("durable budget delivery must not require Redis")


@pytest.mark.asyncio
async def test_budget_alert_delivery_survives_redis_outage() -> None:
    outbox = _FakeOutboxService()
    repo = await _process(_build_service(enabled=True, redis=_ClaimRaisingRedis(), outbox=outbox))
    assert len(outbox.calls) == 1
    repo.finish.assert_awaited_once_with(record(), delivered=True, outcome="delivered")
