from __future__ import annotations

from typing import Any

from src.db.tier_records import (
    TierVersionRecord,
    json_param,
    tier_version_select_sql,
    to_version_record,
)


class TierVersionCloneRepositoryMixin:
    prisma: Any | None

    async def clone_tier_version(
        self,
        *,
        tier_id: str,
        source_tier_version_id: str,
        created_by_account_id: str | None = None,
        created_by_kind: str = "unknown",
    ) -> TierVersionRecord | None:
        if self.prisma is None:
            return None
        created_by_kind = str(created_by_kind or "unknown").strip().lower()
        if created_by_kind not in {"account", "master_key", "system", "unknown"}:
            raise ValueError("created_by_kind is invalid")
        if created_by_kind == "account" and not created_by_account_id:
            raise ValueError("account-created tier versions require created_by_account_id")
        if created_by_kind != "account" and created_by_account_id is not None:
            raise ValueError("created_by_account_id requires created_by_kind=account")
        self.require_transactions("clone_tier_version")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._clone_tier_version_in_tx(
                tier_id=tier_id,
                source_tier_version_id=source_tier_version_id,
                created_by_account_id=created_by_account_id,
                created_by_kind=created_by_kind,
            )

    async def _clone_tier_version_in_tx(
        self,
        *,
        tier_id: str,
        source_tier_version_id: str,
        created_by_account_id: str | None,
        created_by_kind: str,
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

        source_rows = await self.prisma.query_raw(
            f"""
            {tier_version_select_sql()}
            WHERE v.tier_id = $1
              AND v.tier_version_id = $2
            LIMIT 1
            """,
            tier_id,
            source_tier_version_id,
        )
        if not source_rows:
            return None
        source = to_version_record(source_rows[0])

        created_rows = await self.prisma.query_raw(
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
                $4,
                $5::jsonb,
                NOW(),
                NOW()
            FROM deltallm_tierversion
            WHERE tier_id = $1
            RETURNING tier_version_id
            """,
            tier_id,
            created_by_account_id,
            created_by_kind,
            source_tier_version_id,
            json_param(source.metadata),
        )
        if not created_rows:
            return None
        cloned_tier_version_id = str(created_rows[0]["tier_version_id"])

        await self._copy_capacity_pools_to_cloned_version(
            source_tier_version_id=source_tier_version_id,
            cloned_tier_version_id=cloned_tier_version_id,
        )
        await self._copy_model_policies_to_cloned_version(
            source_tier_version_id=source_tier_version_id,
            cloned_tier_version_id=cloned_tier_version_id,
        )

        rows = await self.prisma.query_raw(
            f"""
            {tier_version_select_sql()}
            WHERE v.tier_version_id = $1
            LIMIT 1
            """,
            cloned_tier_version_id,
        )
        return to_version_record(rows[0]) if rows else None

    async def _copy_capacity_pools_to_cloned_version(
        self,
        *,
        source_tier_version_id: str,
        cloned_tier_version_id: str,
    ) -> None:
        await self.prisma.execute_raw(
            """
            INSERT INTO deltallm_tiercapacitypool (
                tier_capacity_pool_id,
                tier_version_id,
                pool_key,
                callable_key,
                rpm_capacity,
                tpm_capacity,
                max_parallel_requests,
                strategy,
                saturation_threshold,
                burst_multiplier,
                metadata,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid()::text,
                $2,
                pool_key,
                callable_key,
                rpm_capacity,
                tpm_capacity,
                max_parallel_requests,
                strategy,
                saturation_threshold,
                burst_multiplier,
                metadata,
                NOW(),
                NOW()
            FROM deltallm_tiercapacitypool
            WHERE tier_version_id = $1
            ORDER BY pool_key ASC, callable_key ASC
            """,
            source_tier_version_id,
            cloned_tier_version_id,
        )

    async def _copy_model_policies_to_cloned_version(
        self,
        *,
        source_tier_version_id: str,
        cloned_tier_version_id: str,
    ) -> None:
        await self.prisma.execute_raw(
            """
            INSERT INTO deltallm_tiermodelpolicy (
                tier_model_policy_id,
                tier_version_id,
                callable_key,
                enabled,
                access_mode,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                max_parallel_requests,
                batch_rpm_limit,
                batch_tpm_limit,
                pricing,
                capacity_pool_key,
                priority,
                metadata,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid()::text,
                $2,
                callable_key,
                enabled,
                access_mode,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                max_parallel_requests,
                batch_rpm_limit,
                batch_tpm_limit,
                pricing,
                capacity_pool_key,
                priority,
                metadata,
                NOW(),
                NOW()
            FROM deltallm_tiermodelpolicy
            WHERE tier_version_id = $1
            ORDER BY priority DESC, callable_key ASC
            """,
            source_tier_version_id,
            cloned_tier_version_id,
        )
