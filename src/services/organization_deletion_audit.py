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


async def record_deletion_expedite_audit(
    tx: Any,
    *,
    job: OrganizationDeletionJobRecord,
) -> None:
    if job.expedite_previous_not_before_at is None or job.expedited_at is None:
        raise RuntimeError("organization deletion expedite provenance is incomplete")
    await AuditRepository(tx).create_event(
        AuditEventRecord(
            event_id="",
            action=AuditAction.ADMIN_ORGANIZATION_DELETION_EXPEDITE.value,
            organization_id=job.organization_id,
            actor_type=("platform_account" if job.expedited_by_account_id else "master_key"),
            actor_id=job.expedited_by_account_id,
            resource_type="organization_deletion_job",
            resource_id=job.deletion_job_id,
            status="success",
            metadata={
                "previous_not_before_at": job.expedite_previous_not_before_at.isoformat(),
                "not_before_at": job.expedited_at.isoformat(),
                "recovery_window_waived": True,
            },
        )
    )


__all__ = ["record_deletion_expedite_audit", "record_lifecycle_mutation_audit"]
