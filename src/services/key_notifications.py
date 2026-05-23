from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.audit.actions import AuditAction
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.types import NotificationMessage
from src.services.notification_recipients import NotificationRecipientResolver

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeyNotificationRecord:
    token_hash: str
    key_name: str
    team_id: str | None
    team_alias: str | None
    organization_id: str | None
    owner_account_id: str | None
    owner_service_account_id: str | None
    owner_service_account_name: str | None = None


class KeyNotificationService:
    def __init__(
        self,
        *,
        dispatcher: NotificationDispatcher,
        recipient_resolver: NotificationRecipientResolver | None,
        config_getter=None,  # noqa: ANN001
    ) -> None:
        self.dispatcher = dispatcher
        self.recipient_resolver = recipient_resolver
        self._config_getter = config_getter

    async def notify_lifecycle(
        self,
        *,
        event_kind: str,
        actor_account_id: str | None,
        record: KeyNotificationRecord,
    ) -> None:
        if not self._notifications_enabled():
            return
        if self.recipient_resolver is None:
            return

        if actor_account_id and record.owner_account_id and actor_account_id == record.owner_account_id:
            await self.dispatcher.record_skip(
                message=self._message(event_kind=event_kind, payload={}),
                audit_action=AuditAction.SYSTEM_KEY_NOTIFICATION_ENQUEUE.value,
                resource_type="api_key",
                resource_id=record.token_hash,
                organization_id=record.organization_id,
                reason="actor_is_owner",
            )
            return

        recipients = await self.recipient_resolver.resolve_key_lifecycle_recipients(
            owner_account_id=record.owner_account_id,
            team_id=record.team_id,
            organization_id=record.organization_id,
        )
        actor_email = await self.recipient_resolver.get_account_email(actor_account_id)
        message = self._message(
            event_kind=event_kind,
            payload={
                "instance_name": self._instance_name(),
                "event_kind": event_kind,
                "event_label": _event_label(event_kind),
                "key_name": record.key_name,
                "team_name": record.team_alias or record.team_id or "unknown team",
                "organization_id": record.organization_id,
                "actor_email": actor_email or "an administrator",
                "recipient_policy": recipients.policy,
                "owner_label": record.owner_service_account_name or "account owner",
            },
            created_by_account_id=actor_account_id,
        )
        await self.dispatcher.dispatch(
            message=message,
            recipients=recipients,
            audit_action=AuditAction.SYSTEM_KEY_NOTIFICATION_ENQUEUE.value,
            resource_type="api_key",
            resource_id=record.token_hash,
            organization_id=record.organization_id,
        )

    def _message(
        self,
        *,
        event_kind: str,
        payload: dict[str, Any],
        created_by_account_id: str | None = None,
    ) -> NotificationMessage:
        return NotificationMessage(
            alert_type="api_key_lifecycle",
            metric_kind=event_kind,
            payload=payload,
            created_by_account_id=created_by_account_id,
        )

    def _notifications_enabled(self) -> bool:
        cfg = self._current_config()
        general = getattr(cfg, "general_settings", None)
        if general is None:
            return False
        return bool(
            getattr(general, "governance_notifications_enabled", False)
            and getattr(general, "key_lifecycle_notifications_enabled", False)
        )

    def _current_config(self) -> Any:
        if self._config_getter is None:
            return None
        return self._config_getter()

    def _instance_name(self) -> str:
        cfg = self._current_config()
        general = getattr(cfg, "general_settings", None)
        return str(getattr(general, "instance_name", "DeltaLLM") or "DeltaLLM")


def _event_label(event_kind: str) -> str:
    mapping = {
        "api_key_created": "created",
        "api_key_regenerated": "regenerated",
        "api_key_revoked": "revoked",
        "api_key_deleted": "deleted",
    }
    return mapping.get(event_kind, event_kind.replace("_", " "))
