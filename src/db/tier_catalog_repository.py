from __future__ import annotations

from typing import Any

from src.db.tier_records import (
    TierRecord,
    json_param,
    to_tier_record,
)


_TIER_CATALOG_SELECT = """
    SELECT
        t.tier_id,
        t.tier_key,
        t.name,
        t.description,
        t.enabled,
        t.metadata,
        t.created_at,
        t.updated_at,
        active.tier_version_id AS active_version_id,
        active.version_number AS active_version_number,
        active.configuration_revision AS active_configuration_revision,
        active.model_policy_count AS active_model_policy_count,
        active.capacity_pool_count AS active_capacity_pool_count,
        active.created_by_account_id AS active_created_by_account_id,
        active.created_by_kind AS active_created_by_kind,
        active.created_by_email AS active_created_by_email,
        active.source_tier_version_id AS active_source_tier_version_id,
        active.created_at AS active_created_at,
        active.updated_at AS active_updated_at,
        draft.tier_version_id AS draft_version_id,
        draft.version_number AS draft_version_number,
        draft.configuration_revision AS draft_configuration_revision,
        draft.model_policy_count AS draft_model_policy_count,
        draft.capacity_pool_count AS draft_capacity_pool_count,
        draft.created_by_account_id AS draft_created_by_account_id,
        draft.created_by_kind AS draft_created_by_kind,
        draft.created_by_email AS draft_created_by_email,
        draft.source_tier_version_id AS draft_source_tier_version_id,
        draft.created_at AS draft_created_at,
        draft.updated_at AS draft_updated_at,
        COALESCE(version_stats.draft_count, 0)::int AS draft_count,
        COALESCE(version_stats.version_count, 0)::int AS version_count,
        COALESCE(assignment_stats.assignment_count, 0)::int AS assignment_count,
        COALESCE(assignment_stats.live_assignment_count, 0)::int AS live_assignment_count,
        COALESCE(assignment_stats.organization_count, 0)::int AS organization_count,
        GREATEST(
            t.updated_at,
            COALESCE(version_stats.last_version_activity_at, t.updated_at)
        ) AS last_activity_at
    FROM deltallm_tier t
    LEFT JOIN LATERAL (
        SELECT
            v.tier_version_id,
            v.version_number,
            v.configuration_revision,
            v.created_by_account_id,
            v.created_by_kind,
            creator.email AS created_by_email,
            v.source_tier_version_id,
            v.created_at,
            v.updated_at,
            (
                SELECT COUNT(*)::int
                FROM deltallm_tiermodelpolicy policy
                WHERE policy.tier_version_id = v.tier_version_id
            ) AS model_policy_count,
            (
                SELECT COUNT(*)::int
                FROM deltallm_tiercapacitypool pool
                WHERE pool.tier_version_id = v.tier_version_id
            ) AS capacity_pool_count
        FROM deltallm_tierversion v
        LEFT JOIN deltallm_platformaccount creator
            ON creator.account_id = v.created_by_account_id
        WHERE v.tier_id = t.tier_id
          AND v.status = 'active'
        ORDER BY v.version_number DESC
        LIMIT 1
    ) active ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            v.tier_version_id,
            v.version_number,
            v.configuration_revision,
            v.created_by_account_id,
            v.created_by_kind,
            creator.email AS created_by_email,
            v.source_tier_version_id,
            v.created_at,
            v.updated_at,
            (
                SELECT COUNT(*)::int
                FROM deltallm_tiermodelpolicy policy
                WHERE policy.tier_version_id = v.tier_version_id
            ) AS model_policy_count,
            (
                SELECT COUNT(*)::int
                FROM deltallm_tiercapacitypool pool
                WHERE pool.tier_version_id = v.tier_version_id
            ) AS capacity_pool_count
        FROM deltallm_tierversion v
        LEFT JOIN deltallm_platformaccount creator
            ON creator.account_id = v.created_by_account_id
        WHERE v.tier_id = t.tier_id
          AND v.status = 'draft'
        ORDER BY v.version_number DESC
        LIMIT 1
    ) draft ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            COUNT(*)::int AS version_count,
            COUNT(*) FILTER (WHERE v.status = 'draft')::int AS draft_count,
            MAX(v.updated_at) AS last_version_activity_at
        FROM deltallm_tierversion v
        WHERE v.tier_id = t.tier_id
    ) version_stats ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            COUNT(*)::int AS assignment_count,
            COUNT(*) FILTER (
                WHERE assignment.enabled = TRUE
                  AND (assignment.ends_at IS NULL OR assignment.ends_at > NOW())
            )::int AS live_assignment_count,
            COUNT(DISTINCT assignment.organization_id) FILTER (
                WHERE assignment.enabled = TRUE
                  AND (assignment.ends_at IS NULL OR assignment.ends_at > NOW())
            )::int AS organization_count
        FROM deltallm_organizationtierassignment assignment
        WHERE assignment.tier_id = t.tier_id
    ) assignment_stats ON TRUE
"""


class TierCatalogRepositoryMixin:
    prisma: Any | None

    async def list_tiers(
        self,
        *,
        search: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TierRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if search:
            params.append(f"%{search}%")
            clauses.append(
                f"(t.tier_key ILIKE ${len(params)} OR t.name ILIKE ${len(params)} "
                f"OR COALESCE(t.description, '') ILIKE ${len(params)})"
            )
        if enabled is not None:
            params.append(enabled)
            clauses.append(f"t.enabled = ${len(params)}")

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_tier t {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            {_TIER_CATALOG_SELECT}
            {where_sql}
            ORDER BY t.created_at DESC, t.tier_key ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [to_tier_record(row) for row in rows], total

    async def get_tier(self, tier_id: str) -> TierRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            f"""
            {_TIER_CATALOG_SELECT}
            WHERE t.tier_id = $1
            LIMIT 1
            """,
            tier_id,
        )
        return to_tier_record(rows[0]) if rows else None

    async def get_tier_by_key(self, tier_key: str) -> TierRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            f"""
            {_TIER_CATALOG_SELECT}
            WHERE t.tier_key = $1
            LIMIT 1
            """,
            tier_key,
        )
        return to_tier_record(rows[0]) if rows else None

    async def create_tier(
        self,
        *,
        tier_key: str,
        name: str,
        description: str | None,
        enabled: bool,
        metadata: dict[str, Any] | None,
    ) -> TierRecord:
        if self.prisma is None:
            return TierRecord(
                tier_id="",
                tier_key=tier_key,
                name=name,
                description=description,
                enabled=enabled,
                metadata=metadata,
            )

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_tier (
                tier_id,
                tier_key,
                name,
                description,
                enabled,
                metadata,
                created_at,
                updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5::jsonb, NOW(), NOW())
            RETURNING
                tier_id,
                tier_key,
                name,
                description,
                enabled,
                metadata,
                created_at,
                updated_at,
                NULL::text AS active_version_id,
                0::int AS draft_count,
                0::int AS version_count,
                0::int AS assignment_count,
                0::int AS live_assignment_count,
                0::int AS organization_count,
                updated_at AS last_activity_at
            """,
            tier_key,
            name,
            description,
            enabled,
            json_param(metadata),
        )
        return to_tier_record(rows[0])

    async def update_tier(
        self,
        tier_id: str,
        *,
        tier_key: str,
        name: str,
        description: str | None,
        enabled: bool,
        metadata: dict[str, Any] | None,
    ) -> TierRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_tier
            SET tier_key = $2,
                name = $3,
                description = $4,
                enabled = $5,
                metadata = $6::jsonb,
                updated_at = NOW()
            WHERE tier_id = $1
            RETURNING tier_id
            """,
            tier_id,
            tier_key,
            name,
            description,
            enabled,
            json_param(metadata),
        )
        if not rows:
            return None
        return await self.get_tier(tier_id)

    async def delete_tier(self, tier_id: str) -> bool:
        if self.prisma is None:
            return False

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_tier
            WHERE tier_id = $1
            RETURNING tier_id
            """,
            tier_id,
        )
        return bool(rows)

    async def count_tier_assignments(self, tier_id: str) -> int:
        if self.prisma is None:
            return 0

        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS count
            FROM deltallm_organizationtierassignment
            WHERE tier_id = $1
            """,
            tier_id,
        )
        return int((rows[0] if rows else {}).get("count") or 0)

    async def count_active_tier_assignments(self, tier_id: str) -> int:
        if self.prisma is None:
            return 0

        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS count
            FROM deltallm_organizationtierassignment
            WHERE tier_id = $1
              AND enabled = TRUE
              AND (starts_at IS NULL OR starts_at <= NOW())
              AND (ends_at IS NULL OR ends_at > NOW())
            """,
            tier_id,
        )
        return int((rows[0] if rows else {}).get("count") or 0)

    async def count_live_or_scheduled_tier_assignments(self, tier_id: str) -> int:
        if self.prisma is None:
            return 0

        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS count
            FROM deltallm_organizationtierassignment
            WHERE tier_id = $1
              AND enabled = TRUE
              AND (ends_at IS NULL OR ends_at > NOW())
            """,
            tier_id,
        )
        return int((rows[0] if rows else {}).get("count") or 0)

    async def count_live_or_scheduled_tier_organizations(self, tier_id: str) -> int:
        if self.prisma is None:
            return 0

        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(DISTINCT organization_id)::int AS count
            FROM deltallm_organizationtierassignment
            WHERE tier_id = $1
              AND enabled = TRUE
              AND (ends_at IS NULL OR ends_at > NOW())
            """,
            tier_id,
        )
        return int((rows[0] if rows else {}).get("count") or 0)
