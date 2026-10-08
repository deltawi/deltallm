from __future__ import annotations

from dataclasses import asdict, dataclass
from uuid import uuid4

from src.audit.actions import AuditAction
from src.db.platform_accounts import PlatformAccountDatabase
from src.audit.delivery import AuditDeliveryClass
from src.auth.external_errors import ExternalAuthUnavailable
from src.db.audit_ingestion import AuditIngestionRepository, AuditOutboxEnvelope
from src.services.audit_service import AuditEventInput, AuditService


@dataclass(frozen=True, slots=True)
class ExternalAuditEvent:
    action: AuditAction
    integration_id: str
    correlation_id: str
    status: str
    organization_id: str | None = None
    binding_id: str | None = None
    account_id: str | None = None
    purpose: str | None = None
    reason: str | None = None
    approval_reference: str | None = None
    approved_by: str | None = None
    resource_type: str = "external_auth_binding"
    resource_id: str | None = None
    actor_type: str = "external_integration"


class ExternalAuthAudit:
    """Write redacted metadata through the existing transaction-bound audit service."""

    def __init__(self, service: AuditService) -> None:
        if not service.ingestion_config.enabled:
            raise ValueError("External auth requires durable audit outbox mode")
        self.service = service

    async def write(self, db: PlatformAccountDatabase, event: ExternalAuditEvent) -> None:
        await self.write_many(db, [event])

    async def write_many(
        self, db: PlatformAccountDatabase, events: list[ExternalAuditEvent]
    ) -> None:
        if not events or len(events) > 2:
            raise ValueError("External audit bundle must contain one or two events")
        config = self.service.ingestion_config
        if not config.enabled:
            raise ExternalAuthUnavailable()
        envelopes = []
        for event in events:
            payload = {
                "event": asdict(
                    AuditEventInput(
                        action=event.action.value,
                        actor_type=event.actor_type,
                        actor_id=event.integration_id,
                        organization_id=event.organization_id,
                        resource_type=event.resource_type,
                        resource_id=event.resource_id or event.binding_id,
                        correlation_id=event.correlation_id,
                        status=event.status,
                        metadata={
                            "account_id": event.account_id,
                            "purpose": event.purpose,
                            "reason": event.reason,
                            "approval_reference": event.approval_reference,
                            "approved_by": event.approved_by,
                        },
                    )
                ),
                "payloads": [],
                "critical": True,
            }
            envelopes.append(
                AuditOutboxEnvelope(
                    event_id=str(uuid4()),
                    record_type="audit_event",
                    organization_id=event.organization_id,
                    delivery_class=AuditDeliveryClass.REQUIRED,
                    payload=payload,
                    redacted_payload=payload,
                    max_attempts=config.max_attempts,
                )
            )
        result = await AuditIngestionRepository(db).enqueue_bundle(
            envelopes=envelopes,
            max_pending_events=config.max_pending_events,
            required_reserve=config.required_reserve,
        )
        if any(status != "accepted" for status in result.statuses.values()) or len(
            result.statuses
        ) != len(envelopes):
            raise ExternalAuthUnavailable()
