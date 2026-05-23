from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.audit.actions import AuditAction
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.types import NotificationMessage
from src.services.notification_recipients import NotificationRecipientResolver

logger = logging.getLogger(__name__)


@dataclass
class AlertConfig:
    budget_alert_ttl: int = 3600


class AlertService:
    """Alerting abstraction for budget and reporting notifications."""

    def __init__(
        self,
        *,
        config: AlertConfig | None = None,
        dispatcher: NotificationDispatcher,
        recipient_resolver: NotificationRecipientResolver | None = None,
        config_getter=None,  # noqa: ANN001
    ) -> None:
        self.config = config or AlertConfig()
        self.dispatcher = dispatcher
        self.recipient_resolver = recipient_resolver
        self._config_getter = config_getter

    async def send_budget_alert(
        self,
        *,
        entity_type: str,
        entity_id: str,
        current_spend: float,
        soft_budget: float | None,
        hard_budget: float | None,
    ) -> None:
        if not self._budget_notifications_enabled():
            return
        if self.recipient_resolver is None:
            return

        percentage = (current_spend / hard_budget * 100.0) if hard_budget and hard_budget > 0 else 0.0
        base_payload = {
            "type": "budget_alert",
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "current_spend": float(current_spend),
            "soft_budget": float(soft_budget) if soft_budget is not None else None,
            "hard_budget": float(hard_budget) if hard_budget is not None else None,
            "percentage": percentage,
        }

        recipients = await self.recipient_resolver.resolve_budget_recipients(
            entity_type=entity_type, entity_id=entity_id
        )
        message = NotificationMessage(
            alert_type="budget_threshold",
            metric_kind="budget_threshold",
            payload={
                **base_payload,
                "instance_name": self._instance_name(),
                "recipient_policy": recipients.policy,
                "team_id": recipients.team_id,
                "organization_id": recipients.organization_id,
                "owner_account_id": recipients.owner_account_id,
            },
        )
        await self.dispatcher.dispatch(
            message=message,
            recipients=recipients,
            audit_action=AuditAction.SYSTEM_BUDGET_NOTIFICATION_ENQUEUE.value,
            resource_type=entity_type,
            resource_id=entity_id,
            dedupe_key=self._alert_key("budget", entity_type=entity_type, entity_id=entity_id),
        )

    def _budget_notifications_enabled(self) -> bool:
        cfg = self._current_config()
        general = getattr(cfg, "general_settings", None)
        if general is None:
            return False
        return bool(
            getattr(general, "governance_notifications_enabled", False)
            and getattr(general, "budget_notifications_enabled", False)
        )

    def _instance_name(self) -> str:
        cfg = self._current_config()
        general = getattr(cfg, "general_settings", None)
        return str(getattr(general, "instance_name", "DeltaLLM") or "DeltaLLM")

    def _current_config(self) -> Any:
        if self._config_getter is None:
            return None
        return self._config_getter()

    def _alert_key(self, alert_type: str, *, entity_type: str, entity_id: str) -> str:
        return f"alert:{alert_type}:{entity_type}:{entity_id}"
