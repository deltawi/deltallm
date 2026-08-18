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


class TierActivationConfigurationChangedError(ValueError):
    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        super().__init__("draft configuration changed after activation preview")
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class TierActivationActiveVersionChangedError(ValueError):
    def __init__(
        self,
        *,
        expected_active_version_id: str | None,
        current_active_version_id: str | None,
    ) -> None:
        super().__init__("active tier version changed after activation preview")
        self.expected_active_version_id = expected_active_version_id
        self.current_active_version_id = current_active_version_id


def _validate_version_creator(
    *,
    created_by_account_id: str | None,
    created_by_kind: str,
) -> str:
    normalized_kind = str(created_by_kind or "unknown").strip().lower()
    if normalized_kind not in {"account", "master_key", "system", "unknown"}:
        raise ValueError("created_by_kind is invalid")
    if normalized_kind == "account" and not created_by_account_id:
        raise ValueError("account-created tier versions require created_by_account_id")
    if normalized_kind != "account" and created_by_account_id is not None:
        raise ValueError("created_by_account_id requires created_by_kind=account")
    return normalized_kind


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

    async def list_tier_versions_page(
        self,
        tier_id: str,
        *,
        statuses: tuple[str, ...] = (),
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[TierVersionRecord], int]:
        if self.prisma is None:
            return [], 0
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        normalized_statuses = tuple(
            dict.fromkeys(str(value or "").strip().lower() for value in statuses)
        )
        if any(status not in {"draft", "active", "archived"} for status in normalized_statuses):
            raise ValueError("tier version status filter is invalid")

        params: list[Any] = [tier_id]
        status_clause = ""
        if normalized_statuses:
            params.extend(normalized_statuses)
            placeholders = ", ".join(f"${index}" for index in range(2, len(params) + 1))
            status_clause = f" AND v.status IN ({placeholders})"
        count_rows = await self.prisma.query_raw(
            f"""
            SELECT COUNT(*)::int AS total
            FROM deltallm_tierversion v
            WHERE v.tier_id = $1{status_clause}
            """,
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)
        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            {tier_version_select_sql()}
            WHERE v.tier_id = $1{status_clause}
            ORDER BY v.version_number DESC, v.tier_version_id ASC
            LIMIT ${len(page_params) - 1}
            OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [to_version_record(row) for row in rows], total

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

    async def count_non_expired_enabled_assignments_pinned_to_version(
        self,
        tier_version_id: str,
    ) -> int:
        if self.prisma is None:
            return 0
        return await self._count_non_expired_enabled_assignments_pinned_to_version(
            tier_version_id
        )

    async def create_tier_version(
        self,
        *,
        tier_id: str,
        version_number: int,
        status: str = "draft",
        published_at: datetime | None = None,
        published_by_account_id: str | None = None,
        created_by_account_id: str | None = None,
        created_by_kind: str = "unknown",
        source_tier_version_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TierVersionRecord:
        version_number = positive_int_or_none(version_number, "version_number")
        if version_number is None:
            raise ValueError("version_number must be a positive integer")
        status = str(status or "draft").strip().lower()
        if status != "draft":
            raise ValueError(
                "tier versions must be created as draft; use activate_tier_version to activate"
            )
        if published_at is not None or published_by_account_id is not None:
            raise ValueError("draft tier versions cannot include publish metadata")
        created_by_kind = _validate_version_creator(
            created_by_account_id=created_by_account_id,
            created_by_kind=created_by_kind,
        )

        if self.prisma is None:
            return TierVersionRecord(
                tier_version_id="",
                tier_id=tier_id,
                version_number=version_number,
                status=status,
                configuration_revision=0,
                published_at=published_at,
                published_by_account_id=published_by_account_id,
                created_by_account_id=created_by_account_id,
                created_by_kind=created_by_kind,
                source_tier_version_id=source_tier_version_id,
                metadata=metadata,
            )

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_tierversion (
                tier_version_id,
                tier_id,
                version_number,
                status,
                configuration_revision,
                published_at,
                published_by_account_id,
                created_by_account_id,
                created_by_kind,
                source_tier_version_id,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                gen_random_uuid()::text,
                $1,
                $2,
                $3,
                0,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9::jsonb,
                NOW(),
                NOW()
            )
            RETURNING
                tier_version_id,
                tier_id,
                version_number,
                status,
                configuration_revision,
                published_at,
                published_by_account_id,
                created_by_account_id,
                created_by_kind,
                source_tier_version_id,
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
            created_by_account_id,
            created_by_kind,
            source_tier_version_id,
            json_param(metadata),
        )
        return to_version_record(rows[0])

    async def create_next_tier_version(
        self,
        *,
        tier_id: str,
        created_by_account_id: str | None = None,
        created_by_kind: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> TierVersionRecord | None:
        created_by_kind = _validate_version_creator(
            created_by_account_id=created_by_account_id,
            created_by_kind=created_by_kind,
        )
        if self.prisma is None:
            return None
        self.require_transactions("create_next_tier_version")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._create_next_tier_version_in_tx(
                tier_id=tier_id,
                created_by_account_id=created_by_account_id,
                created_by_kind=created_by_kind,
                metadata=metadata,
            )

    async def _create_next_tier_version_in_tx(
        self,
        *,
        tier_id: str,
        created_by_account_id: str | None,
        created_by_kind: str,
        metadata: dict[str, Any] | None,
    ) -> TierVersionRecord | None:
        tier_rows = await self.prisma.query_raw(
            """
            SELECT tier_id
            FROM deltallm_tier
            WHERE tier_id = $1
            FOR UPDATE
            """,
            tier_id,
        )
        if not tier_rows:
            return None

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_tierversion (
                tier_version_id,
                tier_id,
                version_number,
                status,
                configuration_revision,
                published_at,
                published_by_account_id,
                created_by_account_id,
                created_by_kind,
                source_tier_version_id,
                metadata,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid()::text,
                $1,
                COALESCE(MAX(version_number), 0) + 1,
                'draft',
                0,
                NULL,
                NULL,
                $2,
                $3,
                NULL,
                $4::jsonb,
                NOW(),
                NOW()
            FROM deltallm_tierversion
            WHERE tier_id = $1
            RETURNING
                tier_version_id,
                tier_id,
                version_number,
                status,
                configuration_revision,
                published_at,
                published_by_account_id,
                created_by_account_id,
                created_by_kind,
                source_tier_version_id,
                metadata,
                created_at,
                updated_at,
                0::int AS model_policy_count,
                0::int AS capacity_pool_count,
                0::int AS assignment_count
            """,
            tier_id,
            created_by_account_id,
            created_by_kind,
            json_param(metadata),
        )
        return to_version_record(rows[0]) if rows else None

    async def activate_tier_version(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        expected_active_version_id: str | None,
        published_by_account_id: str | None = None,
    ) -> TierVersionRecord | None:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise ValueError("expected_revision must be a non-negative integer")
        if expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if self.prisma is None:
            return None
        self.require_transactions("activate_tier_version")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._activate_tier_version_in_tx(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                expected_revision=expected_revision,
                expected_active_version_id=expected_active_version_id,
                published_by_account_id=published_by_account_id,
            )

    async def _activate_tier_version_in_tx(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        expected_active_version_id: str | None,
        published_by_account_id: str | None,
    ) -> TierVersionRecord | None:
        if not await self._lock_tier_for_version_change(tier_id):
            return None
        version = await self._get_tier_version_for_update(tier_version_id)
        if version is None or version.tier_id != tier_id:
            return None
        if version.status != "draft":
            raise ValueError("only draft tier versions can be activated")
        if version.configuration_revision != expected_revision:
            raise TierActivationConfigurationChangedError(
                expected_revision=expected_revision,
                current_revision=version.configuration_revision,
            )
        return await self._activate_locked_tier_version(
            version,
            published_by_account_id=published_by_account_id,
            expected_active_version_id=expected_active_version_id,
        )

    async def _activate_locked_tier_version(
        self,
        version: TierVersionRecord,
        *,
        published_by_account_id: str | None,
        expected_active_version_id: str | None,
    ) -> TierVersionRecord | None:
        tier_version_id = version.tier_version_id

        current_active_version_id = await self._get_current_active_version_id_for_update(
            tier_id=version.tier_id,
            exclude_tier_version_id=tier_version_id,
        )
        if current_active_version_id != expected_active_version_id:
            raise TierActivationActiveVersionChangedError(
                expected_active_version_id=expected_active_version_id,
                current_active_version_id=current_active_version_id,
            )
        if current_active_version_id is not None:
            pinned_assignment_count = (
                await self._count_non_expired_enabled_assignments_pinned_to_version(
                    current_active_version_id
                )
            )
            if pinned_assignment_count:
                raise ValueError(
                    "cannot activate tier version while enabled assignments are pinned "
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
                v.configuration_revision,
                v.published_at,
                v.published_by_account_id,
                v.created_by_account_id,
                v.created_by_kind,
                v.source_tier_version_id,
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
                v.configuration_revision,
                v.published_at,
                v.published_by_account_id,
                v.created_by_account_id,
                v.created_by_kind,
                v.source_tier_version_id,
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
