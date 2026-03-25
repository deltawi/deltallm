from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.audit.actions import AuditAction
from src.metrics import increment_notification_enqueue
from src.services.audit_service import AuditEventInput, AuditService
from src.services.email_outbox_service import EmailOutboxService, enqueue_succeeded
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
        redis_client: Any | None = None,
        outbox_service: EmailOutboxService | None = None,
        recipient_resolver: NotificationRecipientResolver | None = None,
        audit_service: AuditService | None = None,
        config_getter=None,  # noqa: ANN001
    ) -> None:
        self.config = config or AlertConfig()
        self.redis = redis_client
        self.outbox_service = outbox_service
        self.recipient_resolver = recipient_resolver
        self.audit_service = audit_service
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

        alert_key = self._alert_key("budget", entity_type=entity_type, entity_id=entity_id)
        if not await self._claim_alert_slot(alert_key):
            increment_notification_enqueue(kind="budget_threshold", status="throttled")
            return

        percentage = (current_spend / hard_budget * 100.0) if hard_budget and hard_budget > 0 else 0.0
        payload = {
            "type": "budget_alert",
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "current_spend": float(current_spend),
            "soft_budget": float(soft_budget) if soft_budget is not None else None,
            "hard_budget": float(hard_budget) if hard_budget is not None else None,
            "percentage": percentage,
        }
        try:
            if self.outbox_service is None or self.recipient_resolver is None:
                raise RuntimeError("notification services unavailable")

            recipients = await self.recipient_resolver.resolve_budget_recipients(entity_type=entity_type, entity_id=entity_id)
            if not recipients.emails:
                increment_notification_enqueue(kind="budget_threshold", status="no_recipients")
                await self._release_alert_slot(alert_key)
                logger.info("budget notification skipped; no recipients", extra=payload)
                await self._record_budget_notification_audit(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    status="skipped",
                    metadata={"notification_kind": "budget_threshold", "reason": "no_recipients"},
                )
                return

            queued = await self.outbox_service.enqueue_template_email(
                template_key="budget_threshold",
                to_addresses=recipients.emails,
                payload_json={
                    **payload,
                    "instance_name": self._instance_name(),
                    "recipient_policy": recipients.policy,
                    "team_id": recipients.team_id,
                    "organization_id": recipients.organization_id,
                    "owner_account_id": recipients.owner_account_id,
                },
                kind="notification",
            )
            if not enqueue_succeeded(queued):
                increment_notification_enqueue(kind="budget_threshold", status="undeliverable")
                await self._release_alert_slot(alert_key)
                logger.info(
                    "budget notification skipped; email not queueable",
                    extra={**payload, "email_id": queued.email_id, "outbox_status": getattr(queued, "status", None)},
                )
                await self._record_budget_notification_audit(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    status="skipped",
                    metadata={
                        "notification_kind": "budget_threshold",
                        "email_id": queued.email_id,
                        "reason": "undeliverable",
                        "outbox_status": getattr(queued, "status", None),
                    },
                )
                return
            increment_notification_enqueue(kind="budget_threshold", status="queued")
            logger.info(
                "budget notification queued",
                extra={
                    **payload,
                    "email_id": queued.email_id,
                    "recipient_count": len(recipients.emails),
                    "recipient_policy": recipients.policy,
                },
            )
            await self._record_budget_notification_audit(
                entity_type=entity_type,
                entity_id=entity_id,
                status="success",
                metadata={
                    "notification_kind": "budget_threshold",
                    "email_id": queued.email_id,
                    "recipient_count": len(recipients.emails),
                    "recipient_policy": recipients.policy,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive path
            increment_notification_enqueue(kind="budget_threshold", status="error")
            await self._release_alert_slot(alert_key)
            logger.warning("budget notification failed", extra={**payload, "error": str(exc)})
            await self._record_budget_notification_audit(
                entity_type=entity_type,
                entity_id=entity_id,
                status="error",
                metadata={"notification_kind": "budget_threshold"},
                error=str(exc),
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

    async def _claim_alert_slot(self, key: str) -> bool:
        if self.redis is None:
            return True

        if hasattr(self.redis, "set"):
            claimed = await self.redis.set(key, "1", ex=self.config.budget_alert_ttl, nx=True)
            return bool(claimed)
        if await self.redis.exists(key):
            return False
        await self.redis.setex(key, self.config.budget_alert_ttl, "1")
        return True

    async def _release_alert_slot(self, key: str) -> None:
        if self.redis is None or not hasattr(self.redis, "delete"):
            return
        await self.redis.delete(key)

    async def _record_budget_notification_audit(
        self,
        *,
        entity_type: str,
        entity_id: str,
        status: str,
        metadata: dict[str, Any],
        error: str | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record_event(
            AuditEventInput(
                action=AuditAction.SYSTEM_BUDGET_NOTIFICATION_ENQUEUE.value,
                actor_type="system",
                resource_type=entity_type,
                resource_id=entity_id,
                status=status,
                error_type="NotificationEnqueueError" if error else None,
                metadata={**metadata, "error": error},
            ),
            critical=False,
        )
