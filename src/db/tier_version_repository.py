from __future__ import annotations

from datetime import datetime
from typing import Any

from src.db.tier_records import (
    TierVersionRecord,
    json_param,
    tier_version_select_sql,
    to_version_record,
)
from src.services.tiers import positive_int_or_none


class TierVersionRepositoryMixin:
    prisma: Any | None

    async def list_tier_versions(self, tier_id: str) -> list[TierVersionRecord]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            f"""
            {tier_version_select_sql()}
            WHERE v.tier_id = $1
            ORDER BY v.version_number DESC
            """,
            tier_id,
        )
        return [to_version_record(row) for row in rows]

    async def get_tier_version(self, tier_version_id: str) -> TierVersionRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            f"""
            {tier_version_select_sql()}
            WHERE v.tier_version_id = $1
            LIMIT 1
            """,
            tier_version_id,
        )
        return to_version_record(rows[0]) if rows else None

    async def get_active_tier_version(self, tier_id: str) -> TierVersionRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            f"""
            {tier_version_select_sql()}
            WHERE v.tier_id = $1
              AND v.status = 'active'
            ORDER BY v.version_number DESC
            LIMIT 1
            """,
            tier_id,
        )
        return to_version_record(rows[0]) if rows else None

    async def _count_non_expired_unpinned_enabled_assignments(self, tier_id: str) -> int:
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS assignment_count
            FROM deltallm_organizationtierassignment
            WHERE tier_id = $1
              AND enabled = TRUE
              AND tier_version_id IS NULL
              AND (ends_at IS NULL OR ends_at > NOW())
            """,
            tier_id,
        )
        return int((rows[0] if rows else {}).get("assignment_count") or 0)

    async def _count_non_expired_enabled_assignments_pinned_to_version(
        self,
        tier_version_id: str,
    ) -> int:
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS assignment_count
            FROM deltallm_organizationtierassignment
            WHERE tier_version_id = $1
              AND enabled = TRUE
              AND (ends_at IS NULL OR ends_at > NOW())
            """,
            tier_version_id,
        )
        return int((rows[0] if rows else {}).get("assignment_count") or 0)

    async def create_tier_version(
        self,
        *,
        tier_id: str,
        version_number: int,
        status: str = "draft",
        published_at: datetime | None = None,
        published_by_account_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TierVersionRecord:
        version_number = positive_int_or_none(version_number, "version_number")
        if version_number is None:
            raise ValueError("version_number must be a positive integer")
        status = str(status or "draft").strip().lower()
        if status != "draft":
            raise ValueError(
                "tier versions must be created as draft; use publish_tier_version to activate"
            )
        if published_at is not None or published_by_account_id is not None:
            raise ValueError("draft tier versions cannot include publish metadata")

        if self.prisma is None:
            return TierVersionRecord(
                tier_version_id="",
                tier_id=tier_id,
                version_number=version_number,
                status=status,
                published_at=published_at,
                published_by_account_id=published_by_account_id,
                metadata=metadata,
            )

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_tierversion (
                tier_version_id,
                tier_id,
                version_number,
                status,
                published_at,
                published_by_account_id,
                metadata,
                created_at,
                updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5, $6::jsonb, NOW(), NOW())
            RETURNING
                tier_version_id,
                tier_id,
                version_number,
                status,
                published_at,
                published_by_account_id,
                metadata,
                created_at,
                updated_at,
                0::int AS model_policy_count,
                0::int AS capacity_pool_count,
                0::int AS assignment_count
            """,
            tier_id,
            version_number,
            status,
            published_at,
            published_by_account_id,
            json_param(metadata),
        )
        return to_version_record(rows[0])

    async def publish_tier_version(
        self,
        tier_version_id: str,
        *,
        published_by_account_id: str | None = None,
    ) -> TierVersionRecord | None:
        if self.prisma is None:
            return None
        self.require_transactions("publish_tier_version")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._publish_tier_version_in_tx(
                tier_version_id,
                published_by_account_id=published_by_account_id,
            )

    async def _publish_tier_version_in_tx(
        self,
        tier_version_id: str,
        *,
        published_by_account_id: str | None,
    ) -> TierVersionRecord | None:
        version = await self._lock_tier_then_version_for_lifecycle(tier_version_id)
        if version is None:
            return None
        if version.status != "draft":
            raise ValueError("only draft tier versions can be published")

        current_active_version_id = await self._get_current_active_version_id_for_update(
            tier_id=version.tier_id,
            exclude_tier_version_id=tier_version_id,
        )
        if current_active_version_id is not None:
            pinned_assignment_count = (
                await self._count_non_expired_enabled_assignments_pinned_to_version(
                    current_active_version_id
                )
            )
            if pinned_assignment_count:
                raise ValueError(
                    "cannot publish tier version while enabled assignments are pinned "
                    "to the current active version"
                )

        await self.prisma.execute_raw(
            """
            UPDATE deltallm_tierversion
            SET status = 'archived',
                updated_at = NOW()
            WHERE tier_id = $1
              AND status = 'active'
              AND tier_version_id <> $2
            """,
            version.tier_id,
            tier_version_id,
        )
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_tierversion AS v
            SET status = 'active',
                published_at = NOW(),
                published_by_account_id = $2,
                updated_at = NOW()
            WHERE v.tier_version_id = $1
            RETURNING
                v.tier_version_id,
                v.tier_id,
                v.version_number,
                v.status,
                v.published_at,
                v.published_by_account_id,
                v.metadata,
                v.created_at,
                v.updated_at,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_tiermodelpolicy p
                    WHERE p.tier_version_id = v.tier_version_id
                ) AS model_policy_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_tiercapacitypool p
                    WHERE p.tier_version_id = v.tier_version_id
                ) AS capacity_pool_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_organizationtierassignment a
                    WHERE a.tier_version_id = v.tier_version_id
                ) AS assignment_count
            """,
            tier_version_id,
            published_by_account_id,
        )
        return to_version_record(rows[0]) if rows else None

    async def _get_current_active_version_id_for_update(
        self,
        *,
        tier_id: str,
        exclude_tier_version_id: str,
    ) -> str | None:
        rows = await self.prisma.query_raw(
            """
            SELECT v.tier_version_id
            FROM deltallm_tierversion v
            WHERE v.tier_id = $1
              AND v.status = 'active'
              AND v.tier_version_id <> $2
            LIMIT 1
            FOR UPDATE OF v
            """,
            tier_id,
            exclude_tier_version_id,
        )
        if not rows:
            return None
        return str(rows[0].get("tier_version_id") or "") or None

    async def _lock_tier_for_version_change(self, tier_id: str) -> bool:
        rows = await self.prisma.query_raw(
            """
            SELECT tier_id
            FROM deltallm_tier
            WHERE tier_id = $1
            FOR UPDATE
            """,
            tier_id,
        )
        return bool(rows)

    async def _lock_tier_then_version_for_lifecycle(
        self,
        tier_version_id: str,
    ) -> TierVersionRecord | None:
        version_snapshot = await self.get_tier_version(tier_version_id)
        if version_snapshot is None:
            return None

        if not await self._lock_tier_for_version_change(version_snapshot.tier_id):
            return None

        version = await self._get_tier_version_for_update(tier_version_id)
        if version is None:
            return None
        if version.tier_id != version_snapshot.tier_id:
            raise ValueError("tier version changed while acquiring lifecycle lock")
        return version

    async def _get_tier_version_for_update(
        self,
        tier_version_id: str,
    ) -> TierVersionRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            f"""
            {tier_version_select_sql()}
            WHERE v.tier_version_id = $1
            LIMIT 1
            FOR UPDATE OF v
            """,
            tier_version_id,
        )
        return to_version_record(rows[0]) if rows else None

    async def archive_tier_version(self, tier_version_id: str) -> TierVersionRecord | None:
        if self.prisma is None:
            return None
        self.require_transactions("archive_tier_version")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._archive_tier_version_in_tx(tier_version_id)

    async def _archive_tier_version_in_tx(
        self,
        tier_version_id: str,
    ) -> TierVersionRecord | None:
        version = await self._lock_tier_then_version_for_lifecycle(tier_version_id)
        if version is None:
            return None
        if version.status == "active":
            unpinned_assignment_count = await self._count_non_expired_unpinned_enabled_assignments(
                version.tier_id
            )
            if unpinned_assignment_count:
                raise ValueError(
                    "cannot archive active tier version while enabled assignments follow this tier"
                )
            pinned_assignment_count = (
                await self._count_non_expired_enabled_assignments_pinned_to_version(tier_version_id)
            )
            if pinned_assignment_count:
                raise ValueError(
                    "cannot archive active tier version while enabled assignments are pinned "
                    "to this version"
                )

        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_tierversion AS v
            SET status = 'archived',
                updated_at = NOW()
            WHERE v.tier_version_id = $1
            RETURNING
                v.tier_version_id,
                v.tier_id,
                v.version_number,
                v.status,
                v.published_at,
                v.published_by_account_id,
                v.metadata,
                v.created_at,
                v.updated_at,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_tiermodelpolicy p
                    WHERE p.tier_version_id = v.tier_version_id
                ) AS model_policy_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_tiercapacitypool p
                    WHERE p.tier_version_id = v.tier_version_id
                ) AS capacity_pool_count,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_organizationtierassignment a
                    WHERE a.tier_version_id = v.tier_version_id
                ) AS assignment_count
            """,
            tier_version_id,
        )
        return to_version_record(rows[0]) if rows else None
