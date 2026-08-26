from __future__ import annotations

from typing import Any

from src.audit.actions import AuditAction
from src.db.organization_deletion_records import OrganizationDeletionJobRecord
from src.db.repositories import AuditEventRecord, AuditRepository


async def record_lifecycle_mutation_audit(
    tx: Any,
    *,
    action: AuditAction,
    job: OrganizationDeletionJobRecord,
    actor_id: str | None,
    before_state: str,
    after_state: str,
) -> None:
    await AuditRepository(tx).create_event(
        AuditEventRecord(
            event_id="",
            action=action.value,
            organization_id=job.organization_id,
            actor_type="platform_account" if actor_id else "master_key",
            actor_id=actor_id,
            resource_type="organization_deletion_job",
            resource_id=job.deletion_job_id,
            status="success",
            metadata={
                "before_lifecycle_state": before_state,
                "after_lifecycle_state": after_state,
            },
        )
    )


__all__ = ["record_lifecycle_mutation_audit"]
