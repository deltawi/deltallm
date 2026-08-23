from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from src.db.organization_deletion_queries import ORGANIZATION_DELETION_PLAN_SQL
from src.db.organization_deletion_records import (
    ORGANIZATION_DELETION_JOB_COLUMNS,
    OrganizationDeletionJobRecord,
    OrganizationDeletionPlanRecord,
)
from src.models.organization_lifecycle import TeamOrganizationLifecycle


class OrganizationDeletionRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    def with_db(self, prisma_client: Any) -> OrganizationDeletionRepository:
        return OrganizationDeletionRepository(prisma_client)

    def supports_transactions(self) -> bool:
        return self.prisma is not None and callable(getattr(self.prisma, "tx", None))

    async def get_plan(self, organization_id: str) -> OrganizationDeletionPlanRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(ORGANIZATION_DELETION_PLAN_SQL, organization_id)
        return OrganizationDeletionPlanRecord.from_row(dict(rows[0])) if rows else None

    async def get_organization_for_update(self, organization_id: str) -> dict[str, Any] | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT organization_id, organization_name, lifecycle_state, lifecycle_version,
                   deletion_requested_at, deletion_not_before_at, deletion_job_id
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            FOR UPDATE
            """,
            organization_id,
        )
        return dict(rows[0]) if rows else None

    async def get_job(
        self,
        *,
        organization_id: str,
        deletion_job_id: str,
        for_update: bool = False,
    ) -> OrganizationDeletionJobRecord | None:
        if self.prisma is None:
            return None
        lock_sql = " FOR UPDATE" if for_update else ""
        rows = await self.prisma.query_raw(
            f"""
            SELECT {ORGANIZATION_DELETION_JOB_COLUMNS}
            FROM deltallm_organizationdeletionjob
            WHERE organization_id = $1 AND deletion_job_id = $2
            LIMIT 1{lock_sql}
            """,
            organization_id,
            deletion_job_id,
        )
        return OrganizationDeletionJobRecord.from_row(dict(rows[0])) if rows else None

    async def get_job_by_idempotency_key(
        self,
        *,
        organization_id: str,
        idempotency_key: str,
    ) -> OrganizationDeletionJobRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            SELECT {ORGANIZATION_DELETION_JOB_COLUMNS}
            FROM deltallm_organizationdeletionjob
            WHERE organization_id = $1 AND idempotency_key = $2
            LIMIT 1
            """,
            organization_id,
            idempotency_key,
        )
        return OrganizationDeletionJobRecord.from_row(dict(rows[0])) if rows else None

    async def create_job(
        self,
        *,
        organization_id: str,
        requested_by_account_id: str | None,
        idempotency_key: str,
        request_hash: str,
        plan_token: str,
        plan_snapshot: dict[str, object],
        options: dict[str, object],
        not_before_at: datetime,
        max_attempts: int,
    ) -> OrganizationDeletionJobRecord:
        if self.prisma is None:
            raise RuntimeError("organization deletion repository is unavailable")
        deletion_job_id = str(uuid4())
        rows = await self.prisma.query_raw(
            f"""
            INSERT INTO deltallm_organizationdeletionjob (
                deletion_job_id, organization_id, status, phase,
                requested_by_account_id, idempotency_key, request_hash,
                plan_token, plan_snapshot, options, progress, not_before_at,
                attempt_count, max_attempts, next_attempt_at, claim_epoch,
                created_at, updated_at
            )
            VALUES (
                $1, $2, 'pending', 'cancel_pending', $3, $4, $5,
                $6, $7::jsonb, $8::jsonb, '{{}}'::jsonb, $9::timestamptz, 0, $10, NOW(), 0,
                NOW(), NOW()
            )
            RETURNING {ORGANIZATION_DELETION_JOB_COLUMNS}
            """,
            deletion_job_id,
            organization_id,
            requested_by_account_id,
            idempotency_key,
            request_hash,
            plan_token,
            json.dumps(plan_snapshot),
            json.dumps(options),
            not_before_at,
            max(1, int(max_attempts)),
        )
        if not rows:
            raise RuntimeError("organization deletion job was not created")
        return OrganizationDeletionJobRecord.from_row(dict(rows[0]))

    async def mark_organization_deletion_pending(
        self,
        *,
        organization_id: str,
        deletion_job_id: str,
        not_before_at: datetime,
    ) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_organizationtable
            SET lifecycle_state = 'deletion_pending',
                lifecycle_version = lifecycle_version + 1,
                deletion_requested_at = NOW(),
                deletion_not_before_at = $3::timestamptz,
                deletion_job_id = $2,
                updated_at = NOW()
            WHERE organization_id = $1 AND lifecycle_state = 'active'
            RETURNING organization_id
            """,
            organization_id,
            deletion_job_id,
            not_before_at,
        )
        return bool(rows)

    async def restore_organization(self, *, organization_id: str, deletion_job_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_organizationtable
            SET lifecycle_state = 'active',
                lifecycle_version = lifecycle_version + 1,
                deletion_requested_at = NULL,
                deletion_not_before_at = NULL,
                deletion_job_id = NULL,
                updated_at = NOW()
            WHERE organization_id = $1
              AND deletion_job_id = $2
              AND lifecycle_state IN ('deletion_pending', 'deletion_failed')
            RETURNING organization_id
            """,
            organization_id,
            deletion_job_id,
        )
        return bool(rows)

    async def mark_job_restored(self, *, deletion_job_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_organizationdeletionjob
            SET status = 'restored', phase = 'restored', restored_at = NOW(),
                locked_by = NULL, lease_expires_at = NULL, updated_at = NOW()
            WHERE deletion_job_id = $1
              AND status IN ('pending', 'processing', 'waiting', 'failed')
            RETURNING deletion_job_id
            """,
            deletion_job_id,
        )
        return bool(rows)

    async def increment_lifecycle_generation(self) -> int:
        if self.prisma is None:
            raise RuntimeError("organization lifecycle generation is unavailable")
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_organizationlifecyclegeneration
            SET generation = generation + 1, updated_at = NOW()
            WHERE singleton_id = 1
            RETURNING generation
            """
        )
        if not rows:
            raise RuntimeError("organization lifecycle generation is unavailable")
        return int(rows[0].get("generation") or 0)

    async def lifecycle_generation(self) -> int:
        if self.prisma is None:
            raise RuntimeError("organization lifecycle generation is unavailable")
        rows = await self.prisma.query_raw(
            """
            SELECT generation
            FROM deltallm_organizationlifecyclegeneration
            WHERE singleton_id = 1
            """
        )
        if not rows:
            raise RuntimeError("organization lifecycle generation is unavailable")
        return int(rows[0].get("generation") or 0)

    async def organization_lifecycle_state(self, organization_id: str) -> str | None:
        if self.prisma is None:
            raise RuntimeError("organization lifecycle repository is unavailable")
        rows = await self.prisma.query_raw(
            """
            SELECT lifecycle_state
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        return str(rows[0].get("lifecycle_state") or "active") if rows else None

    async def team_organization_lifecycle(
        self,
        team_id: str,
    ) -> TeamOrganizationLifecycle | None:
        if self.prisma is None:
            raise RuntimeError("organization lifecycle repository is unavailable")
        rows = await self.prisma.query_raw(
            """
            SELECT
                t.organization_id,
                CASE
                    WHEN t.organization_id IS NULL THEN 'active'
                    WHEN o.organization_id IS NULL THEN 'missing'
                    ELSE o.lifecycle_state
                END AS lifecycle_state
            FROM deltallm_teamtable t
            LEFT JOIN deltallm_organizationtable o
              ON o.organization_id = t.organization_id
            WHERE t.team_id = $1
            LIMIT 1
            """,
            team_id,
        )
        if not rows:
            return None
        row = rows[0]
        organization_id = str(row.get("organization_id") or "").strip() or None
        lifecycle_state = row.get("lifecycle_state")
        if not lifecycle_state:
            lifecycle_state = "missing" if organization_id else "active"
        return TeamOrganizationLifecycle(
            organization_id=organization_id,
            lifecycle_state=str(lifecycle_state).strip().lower(),
        )

    async def tombstone_exists(self, organization_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            SELECT organization_id
            FROM deltallm_organizationtombstone
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        return bool(rows)


__all__ = ["OrganizationDeletionRepository"]
