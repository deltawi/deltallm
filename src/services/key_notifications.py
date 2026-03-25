from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.audit.actions import AuditAction
from src.metrics import increment_notification_enqueue
from src.services.audit_service import AuditEventInput, AuditService
from src.services.email_outbox_service import EmailOutboxService, enqueue_succeeded
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
        outbox_service: EmailOutboxService | None,
        recipient_resolver: NotificationRecipientResolver | None,
        audit_service: AuditService | None = None,
        config_getter=None,  # noqa: ANN001
    ) -> None:
        self.outbox_service = outbox_service
        self.recipient_resolver = recipient_resolver
        self.audit_service = audit_service
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
        if self.outbox_service is None or self.recipient_resolver is None:
            return
        if actor_account_id and record.owner_account_id and actor_account_id == record.owner_account_id:
            increment_notification_enqueue(kind=event_kind, status="suppressed")
            await self._record_audit(
                record=record,
                status="skipped",
                metadata={"notification_kind": event_kind, "reason": "actor_is_owner"},
            )
            return

        try:
            recipients = await self.recipient_resolver.resolve_key_lifecycle_recipients(
                owner_account_id=record.owner_account_id,
                team_id=record.team_id,
                organization_id=record.organization_id,
            )
            if not recipients.emails:
                increment_notification_enqueue(kind=event_kind, status="no_recipients")
                await self._record_audit(
                    record=record,
                    status="skipped",
                    metadata={"notification_kind": event_kind, "reason": "no_recipients"},
                )
                return

            actor_email = await self.recipient_resolver.get_account_email(actor_account_id)
            queued = await self.outbox_service.enqueue_template_email(
                template_key="api_key_lifecycle",
                to_addresses=recipients.emails,
                payload_json={
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
                kind="notification",
                created_by_account_id=actor_account_id,
            )
            if not enqueue_succeeded(queued):
                increment_notification_enqueue(kind=event_kind, status="undeliverable")
                logger.info(
                    "key lifecycle notification skipped; email not queueable",
                    extra={
                        "notification_kind": event_kind,
                        "email_id": queued.email_id,
                        "outbox_status": getattr(queued, "status", None),
                        "resource_id": record.token_hash,
                    },
                )
                await self._record_audit(
                    record=record,
                    status="skipped",
                    metadata={
                        "notification_kind": event_kind,
                        "email_id": queued.email_id,
                        "reason": "undeliverable",
                        "outbox_status": getattr(queued, "status", None),
                    },
                )
                return
            increment_notification_enqueue(kind=event_kind, status="queued")
            logger.info(
                "key lifecycle notification queued",
                extra={
                    "notification_kind": event_kind,
                    "email_id": queued.email_id,
                    "recipient_count": len(recipients.emails),
                    "recipient_policy": recipients.policy,
                    "resource_id": record.token_hash,
                },
            )
            await self._record_audit(
                record=record,
                status="success",
                metadata={
                    "notification_kind": event_kind,
                    "email_id": queued.email_id,
                    "recipient_count": len(recipients.emails),
                    "recipient_policy": recipients.policy,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive path
            increment_notification_enqueue(kind=event_kind, status="error")
            logger.warning(
                "key lifecycle notification failed",
                extra={
                    "notification_kind": event_kind,
                    "resource_id": record.token_hash,
                    "error": str(exc),
                },
            )
            await self._record_audit(
                record=record,
                status="error",
                metadata={"notification_kind": event_kind},
                error=str(exc),
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

    async def _record_audit(
        self,
        *,
        record: KeyNotificationRecord,
        status: str,
        metadata: dict[str, Any],
        error: str | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record_event(
            AuditEventInput(
                action=AuditAction.SYSTEM_KEY_NOTIFICATION_ENQUEUE.value,
                actor_type="system",
                organization_id=record.organization_id,
                resource_type="api_key",
                resource_id=record.token_hash,
                status=status,
                error_type="NotificationEnqueueError" if error else None,
                metadata={**metadata, "error": error},
            ),
            critical=False,
        )


def _event_label(event_kind: str) -> str:
    mapping = {
        "api_key_created": "created",
        "api_key_regenerated": "regenerated",
        "api_key_revoked": "revoked",
        "api_key_deleted": "deleted",
    }
    return mapping.get(event_kind, event_kind.replace("_", " "))
