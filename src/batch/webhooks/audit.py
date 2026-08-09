from __future__ import annotations

from src.audit.actions import AuditAction
from src.batch.models import BatchWebhookOutboxRecord
from src.batch.repository import BatchRepository
from src.batch.webhooks.observability import bounded_webhook_reason, webhook_status_class
from src.db.repositories import AuditRepository
from src.services.audit_service import AuditEventInput, AuditService

async def build_batch_webhook_terminal_audit_event(
    *,
    repository: BatchRepository,
    record: BatchWebhookOutboxRecord,
    worker_id: str,
    outcome: str,
    reason: str,
    status_code: int | None,
) -> AuditEventInput:
    if outcome not in {"delivered", "failed"}:
        raise ValueError("terminal webhook audit outcome must be delivered or failed")

    resolver = getattr(repository, "resolve_batch_organization_id", None)
    if callable(resolver):
        organization_id = await resolver(
            batch_id=record.batch_id,
            created_by_team_id=record.created_by_team_id,
            created_by_organization_id=record.created_by_organization_id,
        )
    else:
        organization_id = record.created_by_organization_id
    if organization_id is None and not callable(resolver):
        job = await repository.get_job(record.batch_id)
        organization_id = job.created_by_organization_id if job is not None else None

    return AuditEventInput(
        action=(
            AuditAction.BATCH_WEBHOOK_DELIVERED.value
            if outcome == "delivered"
            else AuditAction.BATCH_WEBHOOK_FAILED.value
        ),
        organization_id=organization_id,
        actor_type="system",
        actor_id=worker_id,
        resource_type="batch_webhook",
        resource_id=record.event_id,
        status=outcome,
        metadata={
            "batch_id": record.batch_id,
            "event_type": record.event_type.value,
            "attempt_count": record.attempt_count,
            "max_attempts": record.max_attempts,
            "status_class": webhook_status_class(status_code),
            "reason": bounded_webhook_reason(reason),
        },
    )


async def persist_batch_webhook_terminal_audit(
    *,
    audit_service: AuditService,
    audit_repository: AuditRepository,
    repository: BatchRepository,
    record: BatchWebhookOutboxRecord,
    worker_id: str,
    outcome: str,
    reason: str,
    status_code: int | None,
) -> None:
    event = await build_batch_webhook_terminal_audit_event(
        repository=repository,
        record=record,
        worker_id=worker_id,
        outcome=outcome,
        reason=reason,
        status_code=status_code,
    )
    await audit_service.record_event_sync(event, repository=audit_repository)
