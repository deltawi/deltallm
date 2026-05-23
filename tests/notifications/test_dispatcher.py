from __future__ import annotations

import pytest

from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.preferences import NotificationPreferenceResolver
from src.notifications.types import ChannelResult, NotificationMessage
from src.services.notification_recipients import NotificationRecipients


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool | None = None):  # noqa: ANN201
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.deleted.append(key)


class _FakeAuditService:
    def __init__(self) -> None:
        self.events: list[object] = []

    def record_event(self, event, *, payloads=None, critical=False) -> None:  # noqa: ANN001, ANN003
        del payloads, critical
        self.events.append(event)


class _FakeChannel:
    def __init__(self, name: str, *, outcome: str = "queued", supports_types=None, raises: bool = False) -> None:
        self.name = name
        self._outcome = outcome
        self._supports = supports_types
        self.raises = raises
        self.calls: list[NotificationMessage] = []

    def supports(self, alert_type: str) -> bool:
        return True if self._supports is None else alert_type in self._supports

    async def send(self, *, message: NotificationMessage, recipients: NotificationRecipients) -> ChannelResult:
        self.calls.append(message)
        if self.raises:
            raise RuntimeError("boom")
        return ChannelResult(outcome=self._outcome)  # type: ignore[arg-type]


def _message() -> NotificationMessage:
    return NotificationMessage(alert_type="budget_threshold", metric_kind="budget_threshold", payload={})


def _recipients(emails: tuple[str, ...] = ("a@example.com",)) -> NotificationRecipients:
    return NotificationRecipients(emails=emails, policy="test", organization_id="org-1")


async def _dispatch(dispatcher: NotificationDispatcher, *, dedupe_key=None) -> None:
    await dispatcher.dispatch(
        message=_message(),
        recipients=_recipients(),
        audit_action="system.budget_notification.enqueue",
        resource_type="team",
        resource_id="team-1",
        dedupe_key=dedupe_key,
    )


@pytest.mark.asyncio
async def test_dispatch_fans_out_to_supporting_channels() -> None:
    email = _FakeChannel("email")
    slack = _FakeChannel("slack", supports_types={"budget_threshold"})
    dispatcher = NotificationDispatcher(channels=[email, slack])

    await _dispatch(dispatcher)

    assert len(email.calls) == 1
    assert len(slack.calls) == 1


@pytest.mark.asyncio
async def test_dispatch_skips_channel_when_alert_type_not_allowed() -> None:
    email = _FakeChannel("email")
    slack = _FakeChannel("slack", supports_types={"api_key_lifecycle"})
    dispatcher = NotificationDispatcher(channels=[email, slack])

    await _dispatch(dispatcher)

    assert len(email.calls) == 1
    assert slack.calls == []


@pytest.mark.asyncio
async def test_dispatch_isolates_channel_failures() -> None:
    redis = _FakeRedis()
    email = _FakeChannel("email", outcome="queued")
    slack = _FakeChannel("slack", supports_types={"budget_threshold"}, raises=True)
    audit = _FakeAuditService()
    dispatcher = NotificationDispatcher(channels=[email, slack], redis_client=redis, audit_service=audit)

    await _dispatch(dispatcher, dedupe_key="alert:budget:team:team-1")

    assert len(email.calls) == 1
    assert len(slack.calls) == 1
    # email delivered, so the dedupe slot is retained despite the slack failure
    assert redis.deleted == []
    statuses = {event.metadata["channel"]: event.status for event in audit.events}
    assert statuses == {"email": "success", "slack": "error"}


@pytest.mark.asyncio
async def test_dispatch_slack_fires_when_email_has_no_recipients() -> None:
    redis = _FakeRedis()
    email = _FakeChannel("email", outcome="no_recipients")
    slack = _FakeChannel("slack", supports_types={"budget_threshold"}, outcome="queued")
    dispatcher = NotificationDispatcher(channels=[email, slack], redis_client=redis)

    await _dispatch(dispatcher, dedupe_key="alert:budget:team:team-1")

    assert len(slack.calls) == 1
    # slack delivered, so the slot stays claimed
    assert redis.deleted == []


@pytest.mark.asyncio
async def test_dispatch_releases_slot_when_no_channel_delivers() -> None:
    redis = _FakeRedis()
    email = _FakeChannel("email", outcome="no_recipients")
    dispatcher = NotificationDispatcher(channels=[email], redis_client=redis)

    await _dispatch(dispatcher, dedupe_key="alert:budget:team:team-1")

    assert "alert:budget:team:team-1" in redis.deleted


@pytest.mark.asyncio
async def test_dispatch_throttles_repeat_within_window() -> None:
    redis = _FakeRedis()
    email = _FakeChannel("email")
    dispatcher = NotificationDispatcher(channels=[email], redis_client=redis)

    await _dispatch(dispatcher, dedupe_key="alert:budget:team:team-1")
    await _dispatch(dispatcher, dedupe_key="alert:budget:team:team-1")

    assert len(email.calls) == 1


@pytest.mark.asyncio
async def test_preference_resolver_can_filter_channels() -> None:
    class _EmailOnlyResolver:
        def channels_for(self, *, alert_type, recipients, channels):  # noqa: ANN001, ANN003
            del alert_type, recipients
            return [c for c in channels if c.name == "email"]

    email = _FakeChannel("email")
    slack = _FakeChannel("slack", supports_types={"budget_threshold"})
    resolver: NotificationPreferenceResolver = _EmailOnlyResolver()
    dispatcher = NotificationDispatcher(channels=[email, slack], preference_resolver=resolver)

    await _dispatch(dispatcher)

    assert len(email.calls) == 1
    assert slack.calls == []
