from __future__ import annotations

from typing import Any

from src.db.tier_records import (
    TierRecord,
    json_param,
    to_tier_record,
)


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
                f"(tier_key ILIKE ${len(params)} OR name ILIKE ${len(params)} "
                f"OR COALESCE(description, '') ILIKE ${len(params)})"
            )
        if enabled is not None:
            params.append(enabled)
            clauses.append(f"enabled = ${len(params)}")

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_tier {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                t.tier_id,
                t.tier_key,
                t.name,
                t.description,
                t.enabled,
                t.metadata,
                t.created_at,
                t.updated_at,
                (
                    SELECT v.tier_version_id
                    FROM deltallm_tierversion v
                    WHERE v.tier_id = t.tier_id
                      AND v.status = 'active'
                    ORDER BY v.version_number DESC
                    LIMIT 1
                ) AS active_version_id,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_tierversion v
                    WHERE v.tier_id = t.tier_id
                ) AS version_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_organizationtierassignment a
                    WHERE a.tier_id = t.tier_id
                ) AS assignment_count
            FROM deltallm_tier t
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
            """
            SELECT
                t.tier_id,
                t.tier_key,
                t.name,
                t.description,
                t.enabled,
                t.metadata,
                t.created_at,
                t.updated_at,
                (
                    SELECT v.tier_version_id
                    FROM deltallm_tierversion v
                    WHERE v.tier_id = t.tier_id
                      AND v.status = 'active'
                    ORDER BY v.version_number DESC
                    LIMIT 1
                ) AS active_version_id,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_tierversion v
                    WHERE v.tier_id = t.tier_id
                ) AS version_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_organizationtierassignment a
                    WHERE a.tier_id = t.tier_id
                ) AS assignment_count
            FROM deltallm_tier t
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
            """
            SELECT
                t.tier_id,
                t.tier_key,
                t.name,
                t.description,
                t.enabled,
                t.metadata,
                t.created_at,
                t.updated_at,
                (
                    SELECT v.tier_version_id
                    FROM deltallm_tierversion v
                    WHERE v.tier_id = t.tier_id
                      AND v.status = 'active'
                    ORDER BY v.version_number DESC
                    LIMIT 1
                ) AS active_version_id,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_tierversion v
                    WHERE v.tier_id = t.tier_id
                ) AS version_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_organizationtierassignment a
                    WHERE a.tier_id = t.tier_id
                ) AS assignment_count
            FROM deltallm_tier t
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
                0::int AS version_count,
                0::int AS assignment_count
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
            RETURNING
                tier_id,
                tier_key,
                name,
                description,
                enabled,
                metadata,
                created_at,
                updated_at,
                (
                    SELECT v.tier_version_id
                    FROM deltallm_tierversion v
                    WHERE v.tier_id = deltallm_tier.tier_id
                      AND v.status = 'active'
                    ORDER BY v.version_number DESC
                    LIMIT 1
                ) AS active_version_id,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_tierversion v
                    WHERE v.tier_id = deltallm_tier.tier_id
                ) AS version_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_organizationtierassignment a
                    WHERE a.tier_id = deltallm_tier.tier_id
                ) AS assignment_count
            """,
            tier_id,
            tier_key,
            name,
            description,
            enabled,
            json_param(metadata),
        )
        return to_tier_record(rows[0]) if rows else None

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
