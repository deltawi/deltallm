from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from src.services.notification_recipients import NotificationRecipients

ChannelOutcome = Literal["queued", "no_recipients", "undeliverable", "error"]


@dataclass(frozen=True)
class NotificationMessage:
    """A channel-neutral notification ready to be fanned out.

    `alert_type` is the routing key: it doubles as the email template key and is
    what channel allowlists match against (e.g. "budget_threshold",
    "api_key_lifecycle"). `metric_kind` is the granular label used for metrics
    and audit (e.g. "budget_threshold" or "api_key_created"), preserving the
    existing per-event metric cardinality.
    """

    alert_type: str
    metric_kind: str
    payload: dict[str, Any]
    created_by_account_id: str | None = None


@dataclass(frozen=True)
class ChannelResult:
    outcome: ChannelOutcome
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@runtime_checkable
class NotificationChannel(Protocol):
    name: str

    def supports(self, alert_type: str) -> bool: ...

    async def send(
        self,
        *,
        message: NotificationMessage,
        recipients: NotificationRecipients,
    ) -> ChannelResult: ...
