from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.db.tier_records import (
    OrganizationTierAssignmentRecord,
    assignment_select_sql,
    json_param,
    to_assignment_record,
)
from src.services.tiers import positive_weight, validate_effective_window


class TierAssignmentRepositoryMixin:
    prisma: Any | None

    async def list_org_assignments(
        self,
        organization_id: str,
        *,
        enabled: bool | None = None,
    ) -> list[OrganizationTierAssignmentRecord]:
        if self.prisma is None:
            return []

        params: list[Any] = [organization_id]
        enabled_sql = ""
        if enabled is not None:
            params.append(enabled)
            enabled_sql = f" AND a.enabled = ${len(params)}"

        rows = await self.prisma.query_raw(
            f"""
            {assignment_select_sql()}
            WHERE a.organization_id = $1
            {enabled_sql}
            ORDER BY
                a.enabled DESC,
                a.assignment_type ASC,
                a.weight DESC,
                a.created_at DESC
            """,
            *params,
        )
        return [to_assignment_record(row) for row in rows]

    async def get_org_assignment(
        self,
        assignment_id: str,
    ) -> OrganizationTierAssignmentRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            f"""
            {assignment_select_sql()}
            WHERE a.assignment_id = $1
            LIMIT 1
            """,
            assignment_id,
        )
        return to_assignment_record(rows[0]) if rows else None

    async def upsert_org_assignment(
        self,
        *,
        organization_id: str,
        tier_id: str,
        tier_version_id: str | None = None,
        assignment_id: str | None = None,
        assignment_type: str = "primary",
        enabled: bool = True,
        weight: int = 1,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrganizationTierAssignmentRecord | None:
        if self.prisma is None:
            return None
        starts_at, ends_at = validate_effective_window(starts_at, ends_at)
        weight = positive_weight(weight)
        requires_active_version_check = self._should_check_active_assignment_version(
            enabled=enabled,
            ends_at=ends_at,
        )
        requires_primary_check = self._should_check_primary_assignment(
            assignment_type=assignment_type,
            enabled=enabled,
        )
        if requires_active_version_check or requires_primary_check:
            self.require_transactions("upsert_org_assignment")
        if self.supports_transactions():
            async with self.prisma.tx() as tx:
                return await self.with_db(tx)._upsert_org_assignment_in_tx(
                    organization_id=organization_id,
                    tier_id=tier_id,
                    tier_version_id=tier_version_id,
                    assignment_id=assignment_id,
                    assignment_type=assignment_type,
                    enabled=enabled,
                    weight=weight,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    metadata=metadata,
                    requires_active_version_check=requires_active_version_check,
                )

        return await self._upsert_org_assignment_in_tx(
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            assignment_id=assignment_id,
            assignment_type=assignment_type,
            enabled=enabled,
            weight=weight,
            starts_at=starts_at,
            ends_at=ends_at,
            metadata=metadata,
            requires_active_version_check=requires_active_version_check,
        )

    async def _upsert_org_assignment_in_tx(
        self,
        *,
        organization_id: str,
        tier_id: str,
        tier_version_id: str | None,
        assignment_id: str | None,
        assignment_type: str,
        enabled: bool,
        weight: int,
        starts_at: datetime | None,
        ends_at: datetime | None,
        metadata: dict[str, Any] | None,
        requires_active_version_check: bool,
    ) -> OrganizationTierAssignmentRecord | None:
        if requires_active_version_check:
            await self._lock_tier_for_assignment_version_check(tier_id)
            if tier_version_id is not None:
                await self._ensure_active_tier_version_for_assignment(
                    tier_id=tier_id,
                    tier_version_id=tier_version_id,
                )
            else:
                await self._ensure_tier_has_active_version_for_assignment(tier_id)

        if self._should_check_primary_assignment(assignment_type=assignment_type, enabled=enabled):
            await self._lock_primary_assignment_namespace(organization_id)
            overlapping_count = await self._count_overlapping_primary_assignments(
                organization_id=organization_id,
                assignment_id=assignment_id,
                starts_at=starts_at,
                ends_at=ends_at,
            )
            if overlapping_count:
                raise ValueError("organization can only have one active primary tier assignment")

        if assignment_id:
            rows = await self.prisma.query_raw(
                """
                UPDATE deltallm_organizationtierassignment
                SET organization_id = $2,
                    tier_id = $3,
                    tier_version_id = $4,
                    assignment_type = $5,
                    enabled = $6,
                    weight = $7,
                    starts_at = $8::timestamp,
                    ends_at = $9::timestamp,
                    metadata = $10::jsonb,
                    updated_at = NOW()
                WHERE assignment_id = $1
                RETURNING assignment_id
                """,
                assignment_id,
                organization_id,
                tier_id,
                tier_version_id,
                assignment_type,
                enabled,
                weight,
                starts_at,
                ends_at,
                json_param(metadata),
            )
            if not rows:
                return None
            return await self.get_org_assignment(str(rows[0].get("assignment_id") or ""))

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_organizationtierassignment (
                assignment_id,
                organization_id,
                tier_id,
                tier_version_id,
                assignment_type,
                enabled,
                weight,
                starts_at,
                ends_at,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                gen_random_uuid()::text,
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7::timestamp,
                $8::timestamp,
                $9::jsonb,
                NOW(),
                NOW()
            )
            RETURNING assignment_id
            """,
            organization_id,
            tier_id,
            tier_version_id,
            assignment_type,
            enabled,
            weight,
            starts_at,
            ends_at,
            json_param(metadata),
        )
        if not rows:
            return None
        return await self.get_org_assignment(str(rows[0].get("assignment_id") or ""))

    @staticmethod
    def _should_check_primary_assignment(*, assignment_type: str, enabled: bool) -> bool:
        return enabled and str(assignment_type or "").strip().lower() == "primary"

    @staticmethod
    def _should_check_active_assignment_version(
        *,
        enabled: bool,
        ends_at: datetime | None,
    ) -> bool:
        if not enabled:
            return False
        return ends_at is None or ends_at > datetime.now(UTC)

    async def _ensure_active_tier_version_for_assignment(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
    ) -> None:
        rows = await self.prisma.query_raw(
            """
            SELECT
                v.tier_id AS version_tier_id,
                v.status
            FROM deltallm_tierversion v
            WHERE v.tier_version_id = $1
            LIMIT 1
            FOR SHARE OF v
            """,
            tier_version_id,
        )
        if not rows:
            raise ValueError("tier_version_id must reference an existing tier version")

        row = rows[0]
        if str(row.get("version_tier_id") or "") != tier_id:
            raise ValueError("tier_version_id must belong to tier_id")
        if str(row.get("status") or "").strip().lower() != "active":
            raise ValueError("tier_version_id must reference an active tier version")

    async def _lock_tier_for_assignment_version_check(self, tier_id: str) -> None:
        rows = await self.prisma.query_raw(
            """
            SELECT tier_id
            FROM deltallm_tier
            WHERE tier_id = $1
            FOR UPDATE
            """,
            tier_id,
        )
        if not rows:
            raise ValueError("tier_id must reference an existing tier")

    async def _ensure_tier_has_active_version_for_assignment(self, tier_id: str) -> None:
        rows = await self.prisma.query_raw(
            """
            SELECT v.tier_version_id
            FROM deltallm_tierversion v
            WHERE v.tier_id = $1
              AND v.status = 'active'
            LIMIT 1
            FOR SHARE OF v
            """,
            tier_id,
        )
        if not rows:
            raise ValueError("enabled tier assignments require an active tier version")

    async def _lock_primary_assignment_namespace(self, organization_id: str) -> None:
        await self.prisma.query_raw(
            """
            SELECT pg_advisory_xact_lock(
                hashtext('tier-primary-assignment'),
                hashtext($1::text)
            ) AS locked
            """,
            organization_id,
        )

    async def _count_overlapping_primary_assignments(
        self,
        *,
        organization_id: str,
        assignment_id: str | None,
        starts_at: datetime | None,
        ends_at: datetime | None,
    ) -> int:
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS overlap_count
            FROM deltallm_organizationtierassignment
            WHERE organization_id = $1
              AND assignment_type = 'primary'
              AND enabled = TRUE
              AND ($2::text IS NULL OR assignment_id <> $2)
              AND ($3::timestamp IS NULL OR ends_at IS NULL OR ends_at > $3::timestamp)
              AND ($4::timestamp IS NULL OR starts_at IS NULL OR starts_at < $4::timestamp)
            """,
            organization_id,
            assignment_id,
            starts_at,
            ends_at,
        )
        return int((rows[0] if rows else {}).get("overlap_count") or 0)

    async def delete_org_assignment(self, assignment_id: str) -> bool:
        if self.prisma is None:
            return False

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_organizationtierassignment
            WHERE assignment_id = $1
            RETURNING assignment_id
            """,
            assignment_id,
        )
        return bool(rows)
