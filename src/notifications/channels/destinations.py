"""Named alert destinations: Slack, Microsoft Teams, Discord, and signed webhooks."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.email.rendering import render_email_template
from src.notifications.destination_http import AlertDestinationHTTP
from src.notifications.types import ChannelResult, NotificationMessage
from src.outbound.webhook_signing import webhook_signature
from src.services.email.notification_recipients import NotificationRecipients

_DISCORD_CONTENT_LIMIT = 2000
_USER_AGENT = "DeltaLLM-Alerts/1.0"


class AlertDestinationChannel:
    """One configured destination. ``name`` is the operator's bounded destination name."""

    def __init__(self, *, settings: Any, http: AlertDestinationHTTP) -> None:
        self.settings = settings
        self.http = http
        self.name = str(settings.name)
        self.type = str(settings.type)
        self._alert_types = frozenset(settings.alert_types)

    def supports(self, alert_type: str) -> bool:
        return "*" in self._alert_types or alert_type in self._alert_types

    async def send(
        self,
        *,
        message: NotificationMessage,
        recipients: NotificationRecipients,
    ) -> ChannelResult:
        del recipients
        body, headers = self.render(message)
        result = await self.http.post(url=self.settings.url, body=body, headers=headers)
        detail = {"destination_type": self.type, "status_code": result.status_code}
        if not result.ok:
            return ChannelResult(outcome="undeliverable", detail=detail, error=result.error)
        return ChannelResult(outcome="queued", detail=detail)

    def render(self, message: NotificationMessage) -> tuple[bytes, dict[str, str]]:
        headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
        if self.type == "webhook":
            return self._signed_event(message, headers)
        rendered = render_email_template(message.alert_type, message.payload)
        subject, text = rendered.subject, rendered.text_body.strip()
        if self.type == "slack":
            payload: dict[str, Any] = {"text": f"*{subject}*\n{text}"}
        elif self.type == "discord":
            content = f"**{subject}**\n{text}"
            payload = {
                "content": content[:_DISCORD_CONTENT_LIMIT],
                # Alert text can contain names; never let it ping anyone.
                "allowed_mentions": {"parse": []},
            }
        else:
            payload = _teams_card(subject, text)
        return _json_bytes(payload), headers

    def _signed_event(
        self, message: NotificationMessage, headers: dict[str, str]
    ) -> tuple[bytes, dict[str, str]]:
        event_id = f"alert_{uuid4().hex}"
        timestamp = int(time.time())
        body = _json_bytes(
            {
                "id": event_id,
                "type": message.alert_type,
                "kind": message.metric_kind,
                "created_at": datetime.now(tz=UTC).isoformat(),
                "data": message.payload,
            }
        )
        return body, {
            **headers,
            "Idempotency-Key": event_id,
            "X-DeltaLLM-Event-Id": event_id,
            "X-DeltaLLM-Event-Type": message.alert_type,
            "X-DeltaLLM-Timestamp": str(timestamp),
            "X-DeltaLLM-Signature": webhook_signature(
                signing_secret=self.settings.signing_secret, timestamp=timestamp, body=body
            ),
        }


def _teams_card(subject: str, text: str) -> dict[str, Any]:
    # Microsoft Teams Workflows ("When a Teams webhook request is received")
    # accepts a message with one Adaptive Card attachment.
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": subject,
                            "weight": "Bolder",
                            "size": "Medium",
                            "wrap": True,
                        },
                        {"type": "TextBlock", "text": text, "wrap": True},
                    ],
                },
            }
        ],
    }


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), default=str, sort_keys=True).encode("utf-8")
