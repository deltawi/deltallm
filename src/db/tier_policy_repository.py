from __future__ import annotations

from typing import Any

from src.db.tier_records import (
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    json_param,
    to_capacity_pool_record,
    to_model_policy_record,
)


class TierPolicyRepositoryMixin:
    prisma: Any | None

    async def list_model_policies(self, tier_version_id: str) -> list[TierModelPolicyRecord]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            """
            SELECT
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
            FROM deltallm_tiermodelpolicy
            WHERE tier_version_id = $1
            ORDER BY priority DESC, callable_key ASC
            """,
            tier_version_id,
        )
        return [to_model_policy_record(row) for row in rows]

    async def replace_model_policies(
        self,
        tier_version_id: str,
        policies: list[TierModelPolicyRecord],
    ) -> list[TierModelPolicyRecord]:
        if self.prisma is None:
            return []
        self.require_transactions("replace_model_policies")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._replace_model_policies_in_tx(
                tier_version_id,
                policies,
            )

    async def _replace_model_policies_in_tx(
        self,
        tier_version_id: str,
        policies: list[TierModelPolicyRecord],
    ) -> list[TierModelPolicyRecord]:
        await self._ensure_draft_tier_version_for_mutation(tier_version_id)
        await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_tiermodelpolicy
            WHERE tier_version_id = $1
            """,
            tier_version_id,
        )
        created: list[TierModelPolicyRecord] = []
        for policy in policies:
            rows = await self.prisma.query_raw(
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
                VALUES (
                    gen_random_uuid()::text,
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7,
                    $8,
                    $9,
                    $10,
                    $11,
                    $12,
                    $13::jsonb,
                    $14,
                    $15,
                    $16::jsonb,
                    NOW(),
                    NOW()
                )
                RETURNING
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
                """,
                tier_version_id,
                policy.callable_key,
                policy.enabled,
                policy.access_mode,
                policy.rpm_limit,
                policy.tpm_limit,
                policy.rph_limit,
                policy.rpd_limit,
                policy.tpd_limit,
                policy.max_parallel_requests,
                policy.batch_rpm_limit,
                policy.batch_tpm_limit,
                json_param(policy.pricing),
                policy.capacity_pool_key,
                policy.priority,
                json_param(policy.metadata),
            )
            if rows:
                created.append(to_model_policy_record(rows[0]))
        return created

    async def list_capacity_pools(self, tier_version_id: str) -> list[TierCapacityPoolRecord]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            """
            SELECT
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
            FROM deltallm_tiercapacitypool
            WHERE tier_version_id = $1
            ORDER BY pool_key ASC, callable_key ASC
            """,
            tier_version_id,
        )
        return [to_capacity_pool_record(row) for row in rows]

    async def replace_capacity_pools(
        self,
        tier_version_id: str,
        pools: list[TierCapacityPoolRecord],
    ) -> list[TierCapacityPoolRecord]:
        _ensure_unique_capacity_pool_refs(pools)
        if self.prisma is None:
            return []
        self.require_transactions("replace_capacity_pools")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._replace_capacity_pools_in_tx(
                tier_version_id,
                pools,
            )

    async def _replace_capacity_pools_in_tx(
        self,
        tier_version_id: str,
        pools: list[TierCapacityPoolRecord],
    ) -> list[TierCapacityPoolRecord]:
        await self._ensure_draft_tier_version_for_mutation(tier_version_id)
        created: list[TierCapacityPoolRecord] = []
        for pool in pools:
            rows = await self.prisma.query_raw(
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
                VALUES (
                    gen_random_uuid()::text,
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7,
                    $8,
                    $9,
                    $10::jsonb,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (tier_version_id, pool_key, callable_key)
                DO UPDATE SET
                    rpm_capacity = EXCLUDED.rpm_capacity,
                    tpm_capacity = EXCLUDED.tpm_capacity,
                    max_parallel_requests = EXCLUDED.max_parallel_requests,
                    strategy = EXCLUDED.strategy,
                    saturation_threshold = EXCLUDED.saturation_threshold,
                    burst_multiplier = EXCLUDED.burst_multiplier,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                RETURNING
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
                """,
                tier_version_id,
                pool.pool_key,
                pool.callable_key,
                pool.rpm_capacity,
                pool.tpm_capacity,
                pool.max_parallel_requests,
                pool.strategy,
                pool.saturation_threshold,
                pool.burst_multiplier,
                json_param(pool.metadata),
            )
            if rows:
                created.append(to_capacity_pool_record(rows[0]))
        await self._delete_omitted_capacity_pools(tier_version_id, pools)
        return created

    async def _delete_omitted_capacity_pools(
        self,
        tier_version_id: str,
        pools: list[TierCapacityPoolRecord],
    ) -> None:
        if not pools:
            await self.prisma.execute_raw(
                """
                DELETE FROM deltallm_tiercapacitypool
                WHERE tier_version_id = $1
                """,
                tier_version_id,
            )
            return

        params: list[Any] = [tier_version_id]
        keep_clauses: list[str] = []
        for pool in pools:
            params.extend([pool.pool_key, pool.callable_key])
            pool_key_param = len(params) - 1
            callable_key_param = len(params)
            keep_clauses.append(
                f"(pool_key = ${pool_key_param} AND callable_key = ${callable_key_param})"
            )

        await self.prisma.execute_raw(
            f"""
            DELETE FROM deltallm_tiercapacitypool
            WHERE tier_version_id = $1
              AND NOT ({" OR ".join(keep_clauses)})
            """,
            *params,
        )

    async def _ensure_draft_tier_version_for_mutation(self, tier_version_id: str) -> None:
        rows = await self.prisma.query_raw(
            """
            SELECT status
            FROM deltallm_tierversion
            WHERE tier_version_id = $1
            LIMIT 1
            FOR UPDATE
            """,
            tier_version_id,
        )
        if not rows:
            raise ValueError("tier version does not exist")
        if str(rows[0].get("status") or "").strip().lower() != "draft":
            raise ValueError("tier version policies can only be changed while draft")


def _ensure_unique_capacity_pool_refs(pools: list[TierCapacityPoolRecord]) -> None:
    seen: set[tuple[str, str]] = set()
    for pool in pools:
        ref = (pool.pool_key, pool.callable_key)
        if ref in seen:
            raise ValueError("capacity pools must have unique pool_key and callable_key pairs")
        seen.add(ref)
