from __future__ import annotations

import logging

from src.notifications.types import ChannelResult, NotificationMessage
from src.services.email_outbox_service import EmailOutboxService, enqueue_succeeded
from src.services.notification_recipients import NotificationRecipients

logger = logging.getLogger(__name__)


class EmailChannel:
    """Delivers a notification by enqueueing a templated email.

    Wraps `EmailOutboxService.enqueue_template_email`, preserving the exact
    template key, payload, and outbox status semantics the producers used
    before the dispatcher existed.
    """

    name = "email"

    def __init__(self, *, outbox_service: EmailOutboxService) -> None:
        self.outbox_service = outbox_service

    def supports(self, alert_type: str) -> bool:
        del alert_type
        return True

    async def send(
        self,
        *,
        message: NotificationMessage,
        recipients: NotificationRecipients,
    ) -> ChannelResult:
        if not recipients.emails:
            return ChannelResult(outcome="no_recipients")

        queued = await self.outbox_service.enqueue_template_email(
            template_key=message.alert_type,
            to_addresses=recipients.emails,
            payload_json=message.payload,
            kind="notification",
            created_by_account_id=message.created_by_account_id,
        )
        if not enqueue_succeeded(queued):
            return ChannelResult(
                outcome="undeliverable",
                detail={
                    "email_id": queued.email_id,
                    "outbox_status": getattr(queued, "status", None),
                },
            )
        return ChannelResult(
            outcome="queued",
            detail={
                "email_id": queued.email_id,
                "recipient_count": len(recipients.emails),
                "recipient_policy": recipients.policy,
            },
        )
