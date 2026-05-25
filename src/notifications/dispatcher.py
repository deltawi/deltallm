from __future__ import annotations

import logging
from typing import Any, Literal, Sequence

from src.metrics import increment_notification_enqueue
from src.notifications.preferences import GlobalConfigPreferenceResolver, NotificationPreferenceResolver
from src.notifications.types import ChannelOutcome, ChannelResult, NotificationChannel, NotificationMessage
from src.services.audit_service import AuditEventInput, AuditService
from src.services.notification_recipients import NotificationRecipients

logger = logging.getLogger(__name__)

ClaimOutcome = Literal["claimed", "held", "unavailable"]

_METRIC_STATUS: dict[ChannelOutcome, str] = {
    "queued": "queued",
    "no_recipients": "no_recipients",
    "undeliverable": "undeliverable",
    "error": "error",
}
_AUDIT_STATUS: dict[ChannelOutcome, str] = {
    "queued": "success",
    "no_recipients": "skipped",
    "undeliverable": "skipped",
    "error": "error",
}
_SKIP_REASON: dict[ChannelOutcome, str] = {
    "no_recipients": "no_recipients",
    "undeliverable": "undeliverable",
}


class NotificationDispatcher:
    """Fans a notification out to every applicable channel.

    Dedupe is the producer's responsibility via `try_claim`/`release`: a budget
    alert claims a single shared Redis slot before resolving recipients or
    dispatching, so a throttled alert does no extra work. Any one channel
    achieving delivery holds that one silence window for all channels (so a
    Slack-only success with no email recipients keeps the slot claimed); the
    producer releases the slot only when `dispatch` reports nothing delivered.
    """

    def __init__(
        self,
        *,
        channels: Sequence[NotificationChannel],
        preference_resolver: NotificationPreferenceResolver | None = None,
        redis_client: Any | None = None,
        audit_service: AuditService | None = None,
        dedupe_ttl_seconds: int = 3600,
    ) -> None:
        self.channels = list(channels)
        self.preference_resolver = preference_resolver or GlobalConfigPreferenceResolver()
        self.redis = redis_client
        self.audit_service = audit_service
        self.dedupe_ttl_seconds = dedupe_ttl_seconds

    async def dispatch(
        self,
        *,
        message: NotificationMessage,
        recipients: NotificationRecipients,
        audit_action: str,
        resource_type: str,
        resource_id: str,
        organization_id: str | None = None,
    ) -> bool:
        """Fan a message out to every applicable channel.

        Returns True if at least one channel achieved delivery. Emitting metrics
        and audit rows for a channel must never abort the fan-out, so those are
        isolated per channel.
        """
        targets = self.preference_resolver.channels_for(
            alert_type=message.alert_type,
            recipients=recipients,
            channels=self.channels,
        )

        any_delivered = False
        for channel in targets:
            result = await self._send_one(channel=channel, message=message, recipients=recipients)
            if result.outcome == "queued":
                any_delivered = True
            try:
                self._emit(
                    channel=channel.name,
                    message=message,
                    result=result,
                    audit_action=audit_action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    organization_id=organization_id,
                )
            except Exception:  # pragma: no cover - metrics/audit must never break fan-out
                logger.exception("notification emit failed", extra={"channel": channel.name})
        return any_delivered

    async def record_skip(
        self,
        *,
        message: NotificationMessage,
        audit_action: str,
        resource_type: str,
        resource_id: str,
        organization_id: str | None,
        reason: str,
        metric_status: str = "suppressed",
    ) -> None:
        """Record a producer-level decision not to dispatch (no channel involved)."""
        increment_notification_enqueue(kind=message.metric_kind, channel="none", status=metric_status)
        self._record_audit(
            audit_action=audit_action,
            resource_type=resource_type,
            resource_id=resource_id,
            organization_id=organization_id,
            status="skipped",
            metadata={"notification_kind": message.metric_kind, "reason": reason},
        )

    async def record_error(
        self,
        *,
        metric_kind: str,
        audit_action: str,
        resource_type: str,
        resource_id: str,
        organization_id: str | None,
        error: str,
    ) -> None:
        """Record a producer-level failure (e.g. recipient resolution raised).

        Keeps notification failures off the caller's path: the producer catches
        the exception, calls this, and swallows it so the originating request is
        unaffected.
        """
        increment_notification_enqueue(kind=metric_kind, channel="none", status="error")
        self._record_audit(
            audit_action=audit_action,
            resource_type=resource_type,
            resource_id=resource_id,
            organization_id=organization_id,
            status="error",
            metadata={"notification_kind": metric_kind, "reason": "exception"},
            error=error,
        )

    async def _send_one(
        self,
        *,
        channel: NotificationChannel,
        message: NotificationMessage,
        recipients: NotificationRecipients,
    ) -> ChannelResult:
        try:
            return await channel.send(message=message, recipients=recipients)
        except Exception as exc:  # pragma: no cover - defensive, channel-isolated
            logger.warning(
                "notification channel raised",
                extra={"channel": channel.name, "notification_kind": message.metric_kind, "error": str(exc)},
            )
            return ChannelResult(outcome="error", error=str(exc))

    def _emit(
        self,
        *,
        channel: str,
        message: NotificationMessage,
        result: ChannelResult,
        audit_action: str,
        resource_type: str,
        resource_id: str,
        organization_id: str | None,
    ) -> None:
        increment_notification_enqueue(
            kind=message.metric_kind,
            channel=channel,
            status=_METRIC_STATUS[result.outcome],
        )
        metadata: dict[str, Any] = {
            "notification_kind": message.metric_kind,
            "channel": channel,
            **result.detail,
        }
        reason = _SKIP_REASON.get(result.outcome)
        if reason is not None:
            metadata.setdefault("reason", reason)
        self._record_audit(
            audit_action=audit_action,
            resource_type=resource_type,
            resource_id=resource_id,
            organization_id=organization_id,
            status=_AUDIT_STATUS[result.outcome],
            metadata=metadata,
            error=result.error,
        )

    def _record_audit(
        self,
        *,
        audit_action: str,
        resource_type: str,
        resource_id: str,
        organization_id: str | None,
        status: str,
        metadata: dict[str, Any],
        error: str | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record_event(
            AuditEventInput(
                action=audit_action,
                actor_type="system",
                organization_id=organization_id,
                resource_type=resource_type,
                resource_id=resource_id,
                status=status,
                error_type="NotificationEnqueueError" if error else None,
                metadata={**metadata, "error": error},
            ),
            critical=False,
        )

    async def try_claim(self, key: str) -> ClaimOutcome:
        """Claim a dedupe slot.

        Returns "claimed" if the slot was taken, "held" if a live slot already
        exists (genuine throttle), or "unavailable" if Redis errored. Fails
        closed: an error never raises, so a notification can't break the request
        that triggered it, and the caller can report the outage distinctly from
        a normal throttle.
        """
        if self.redis is None:
            return "claimed"
        try:
            if hasattr(self.redis, "set"):
                claimed = await self.redis.set(key, "1", ex=self.dedupe_ttl_seconds, nx=True)
                return "claimed" if claimed else "held"
            if await self.redis.exists(key):
                return "held"
            await self.redis.setex(key, self.dedupe_ttl_seconds, "1")
            return "claimed"
        except Exception:
            logger.warning("notification dedupe claim failed; skipping alert", extra={"key": key})
            return "unavailable"

    async def release(self, key: str) -> None:
        if self.redis is None or not hasattr(self.redis, "delete"):
            return
        try:
            await self.redis.delete(key)
        except Exception:
            logger.warning("notification dedupe release failed", extra={"key": key})
