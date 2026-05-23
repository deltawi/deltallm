from __future__ import annotations

from typing import Protocol, Sequence

from src.notifications.types import NotificationChannel
from src.services.notification_recipients import NotificationRecipients


class NotificationPreferenceResolver(Protocol):
    """Decides which channels receive a given alert.

    The signature already accepts `recipients`, so a future org-scoped resolver
    can read per-organization preferences keyed on
    `recipients.organization_id` without changing producers or channels.
    """

    def channels_for(
        self,
        *,
        alert_type: str,
        recipients: NotificationRecipients,
        channels: Sequence[NotificationChannel],
    ) -> list[NotificationChannel]: ...


class GlobalConfigPreferenceResolver:
    """v1 resolver: a channel applies when it declares support for the alert type.

    Channel-level enablement (global flags, webhook configured) is already
    decided at construction time in bootstrap, so by the time a channel is in
    the list it is enabled; here we only filter on the per-channel kind
    allowlist via `channel.supports(...)`.
    """

    def channels_for(
        self,
        *,
        alert_type: str,
        recipients: NotificationRecipients,
        channels: Sequence[NotificationChannel],
    ) -> list[NotificationChannel]:
        del recipients
        return [channel for channel in channels if channel.supports(alert_type)]
