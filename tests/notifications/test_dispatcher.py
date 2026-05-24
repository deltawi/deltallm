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
            return None  # redis-py returns None when an nx claim fails
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


async def _dispatch(dispatcher: NotificationDispatcher) -> bool:
    return await dispatcher.dispatch(
        message=_message(),
        recipients=_recipients(),
        audit_action="system.budget_notification.enqueue",
        resource_type="team",
        resource_id="team-1",
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
    email = _FakeChannel("email", outcome="queued")
    slack = _FakeChannel("slack", supports_types={"budget_threshold"}, raises=True)
    audit = _FakeAuditService()
    dispatcher = NotificationDispatcher(channels=[email, slack], audit_service=audit)

    delivered = await _dispatch(dispatcher)

    assert len(email.calls) == 1
    assert len(slack.calls) == 1
    # email delivered despite the slack failure, so dispatch reports delivery
    assert delivered is True
    statuses = {event.metadata["channel"]: event.status for event in audit.events}
    assert statuses == {"email": "success", "slack": "error"}


@pytest.mark.asyncio
async def test_dispatch_reports_delivery_when_only_slack_succeeds() -> None:
    email = _FakeChannel("email", outcome="no_recipients")
    slack = _FakeChannel("slack", supports_types={"budget_threshold"}, outcome="queued")
    dispatcher = NotificationDispatcher(channels=[email, slack])

    delivered = await _dispatch(dispatcher)

    assert len(slack.calls) == 1
    # slack delivered, so the producer keeps the shared dedupe slot claimed
    assert delivered is True


@pytest.mark.asyncio
async def test_dispatch_reports_no_delivery_when_no_channel_delivers() -> None:
    email = _FakeChannel("email", outcome="no_recipients")
    dispatcher = NotificationDispatcher(channels=[email])

    delivered = await _dispatch(dispatcher)

    assert delivered is False


@pytest.mark.asyncio
async def test_emit_failure_does_not_abort_fan_out_or_lose_delivery() -> None:
    class _ExplodingAudit:
        def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN003
            raise RuntimeError("audit down")

    email = _FakeChannel("email", outcome="queued")
    slack = _FakeChannel("slack", supports_types={"budget_threshold"}, outcome="queued")
    dispatcher = NotificationDispatcher(channels=[email, slack], audit_service=_ExplodingAudit())

    delivered = await _dispatch(dispatcher)

    assert len(email.calls) == 1
    assert len(slack.calls) == 1
    assert delivered is True


@pytest.mark.asyncio
async def test_try_claim_throttles_repeat_within_window() -> None:
    redis = _FakeRedis()
    dispatcher = NotificationDispatcher(channels=[], redis_client=redis)

    assert await dispatcher.try_claim("alert:budget:team:team-1") is True
    assert await dispatcher.try_claim("alert:budget:team:team-1") is False
    await dispatcher.release("alert:budget:team:team-1")
    assert "alert:budget:team:team-1" in redis.deleted
    assert await dispatcher.try_claim("alert:budget:team:team-1") is True


class _RaisingRedis:
    async def set(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise ConnectionError("redis down")

    async def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_try_claim_fails_closed_on_redis_error() -> None:
    dispatcher = NotificationDispatcher(channels=[], redis_client=_RaisingRedis())

    # Must not raise into the caller; a Redis error means "skip the alert".
    assert await dispatcher.try_claim("alert:budget:team:team-1") is False


@pytest.mark.asyncio
async def test_release_swallows_redis_error() -> None:
    dispatcher = NotificationDispatcher(channels=[], redis_client=_RaisingRedis())

    # Must not raise into the caller.
    await dispatcher.release("alert:budget:team:team-1")


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
