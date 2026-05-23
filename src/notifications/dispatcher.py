from __future__ import annotations

import logging
from typing import Any, Sequence

from src.metrics import increment_notification_enqueue
from src.notifications.preferences import GlobalConfigPreferenceResolver, NotificationPreferenceResolver
from src.notifications.types import ChannelOutcome, ChannelResult, NotificationChannel, NotificationMessage
from src.services.audit_service import AuditEventInput, AuditService
from src.services.notification_recipients import NotificationRecipients

logger = logging.getLogger(__name__)

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

    A single Redis dedupe slot (when `dedupe_key` is supplied) is claimed
    before fan-out so all channels share one silence window. The slot is
    released only when no channel achieved delivery; a partial success
    (e.g. email queued but Slack failed) keeps the slot claimed.
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
        dedupe_key: str | None = None,
    ) -> None:
        if dedupe_key is not None and not await self._claim_slot(dedupe_key):
            increment_notification_enqueue(kind=message.metric_kind, channel="all", status="throttled")
            return

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
            self._emit(
                channel=channel.name,
                message=message,
                result=result,
                audit_action=audit_action,
                resource_type=resource_type,
                resource_id=resource_id,
                organization_id=organization_id,
            )

        if dedupe_key is not None and not any_delivered:
            await self._release_slot(dedupe_key)

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

    async def _claim_slot(self, key: str) -> bool:
        if self.redis is None:
            return True
        if hasattr(self.redis, "set"):
            claimed = await self.redis.set(key, "1", ex=self.dedupe_ttl_seconds, nx=True)
            return bool(claimed)
        if await self.redis.exists(key):
            return False
        await self.redis.setex(key, self.dedupe_ttl_seconds, "1")
        return True

    async def _release_slot(self, key: str) -> None:
        if self.redis is None or not hasattr(self.redis, "delete"):
            return
        await self.redis.delete(key)
