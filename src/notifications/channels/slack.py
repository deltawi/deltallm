from __future__ import annotations

import logging

from pydantic import SecretStr

from src.email.rendering import render_email_template
from src.notifications.types import ChannelResult, NotificationMessage
from src.notifications.webhook import post_webhook
from src.services.notification_recipients import NotificationRecipients

logger = logging.getLogger(__name__)


class SlackChannel:
    """Posts a notification to a Slack incoming webhook.

    Reuses the channel-neutral `text_body` from the shared email renderer
    rather than maintaining a parallel Slack template: only one alert type
    reaches Slack in v1, and the plaintext body already reads cleanly in
    Slack. Slack delivery does not depend on email recipients.
    """

    name = "slack"

    def __init__(self, *, webhook_url: SecretStr, allowed_alert_types: set[str]) -> None:
        self.webhook_url = webhook_url
        self.allowed_alert_types = allowed_alert_types

    def supports(self, alert_type: str) -> bool:
        return alert_type in self.allowed_alert_types

    async def send(
        self,
        *,
        message: NotificationMessage,
        recipients: NotificationRecipients,
    ) -> ChannelResult:
        del recipients
        rendered = render_email_template(message.alert_type, message.payload)
        text = f"*{rendered.subject}*\n{rendered.text_body}"
        result = await post_webhook(url=self.webhook_url, json_body={"text": text})
        if not result.ok:
            return ChannelResult(
                outcome="undeliverable",
                detail={"status_code": result.status_code},
                error=result.error,
            )
        return ChannelResult(outcome="queued", detail={"status_code": result.status_code})
