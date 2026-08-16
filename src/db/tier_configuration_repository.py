from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.db.tier_records import (
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    TierVersionRecord,
    json_param,
    parse_datetime,
    to_capacity_pool_record,
    to_model_policy_record,
    to_version_record,
)


class TierConfigurationMutationError(ValueError):
    """Base error for an expected, transaction-safe configuration rejection."""


class TierConfigurationVersionNotFoundError(TierConfigurationMutationError):
    pass


class TierConfigurationChildNotFoundError(TierConfigurationMutationError):
    pass


class TierConfigurationVersionNotDraftError(TierConfigurationMutationError):
    pass


class TierConfigurationStaleError(TierConfigurationMutationError):
    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        super().__init__("tier configuration changed after it was loaded")
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class TierConfigurationIdentityImmutableError(TierConfigurationMutationError):
    pass


class TierConfigurationPoolReferenceError(TierConfigurationMutationError):
    pass


class TierConfigurationPoolInUseError(TierConfigurationMutationError):
    pass


@dataclass(frozen=True, slots=True)
class TierModelPolicyMutationResult:
    policy: TierModelPolicyRecord | None
    configuration_revision: int
    version_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class TierCapacityPoolMutationResult:
    pool: TierCapacityPoolRecord | None
    configuration_revision: int
    version_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class TierModelPolicyBulkMutationResult:
    affected_count: int
    configuration_revision: int
    version_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class TierModelPolicyPage:
    records: tuple[TierModelPolicyRecord, ...]
    total: int
    configuration_revision: int
    version_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class TierCapacityPoolPage:
    records: tuple[TierCapacityPoolRecord, ...]
    total: int
    configuration_revision: int
    version_updated_at: datetime | None


class TierConfigurationRepositoryMixin:
    prisma: Any | None

    async def get_model_policy_for_version(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_model_policy_id: str,
    ) -> TierModelPolicyRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            SELECT {_prefixed_columns(_MODEL_POLICY_COLUMNS, "p")}
            FROM deltallm_tiermodelpolicy p
            JOIN deltallm_tierversion v
              ON v.tier_version_id = p.tier_version_id
            WHERE p.tier_model_policy_id = $1
              AND p.tier_version_id = $2
              AND v.tier_id = $3
            LIMIT 1
            """,
            tier_model_policy_id,
            tier_version_id,
            tier_id,
        )
        return to_model_policy_record(rows[0]) if rows else None

    async def get_capacity_pool_for_version(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_capacity_pool_id: str,
    ) -> TierCapacityPoolRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            SELECT {_prefixed_columns(_CAPACITY_POOL_COLUMNS, "p")}
            FROM deltallm_tiercapacitypool p
            JOIN deltallm_tierversion v
              ON v.tier_version_id = p.tier_version_id
            WHERE p.tier_capacity_pool_id = $1
              AND p.tier_version_id = $2
              AND v.tier_id = $3
            LIMIT 1
            """,
            tier_capacity_pool_id,
            tier_version_id,
            tier_id,
        )
        return to_capacity_pool_record(rows[0]) if rows else None

    async def list_model_policies_page(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        search: str | None = None,
        enabled: bool | None = None,
        access_mode: str | None = None,
        capacity_pool_key: str | None = None,
        sort: str = "priority",
        order: str = "desc",
        limit: int = 10,
        offset: int = 0,
    ) -> TierModelPolicyPage | None:
        if self.prisma is None:
            return None
        limit, offset = _page_bounds(limit, offset)
        sort_column = {
            "callable_key": "p.callable_key",
            "priority": "p.priority",
            "updated_at": "p.updated_at",
        }.get(sort)
        if sort_column is None:
            raise ValueError("model policy sort is invalid")
        order_sql = _sort_order(order)
        version_scope = await self._get_configuration_version_scope(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
        )
        if version_scope is None:
            return None
        revision, version_updated_at = version_scope

        clauses = ["p.tier_version_id = $1"]
        params: list[Any] = [tier_version_id]
        if search:
            params.append(f"%{search.strip()}%")
            clauses.append(
                f"(p.callable_key ILIKE ${len(params)} "
                f"OR COALESCE(p.capacity_pool_key, '') ILIKE ${len(params)})"
            )
        if enabled is not None:
            params.append(enabled)
            clauses.append(f"p.enabled = ${len(params)}")
        if access_mode:
            params.append(access_mode)
            clauses.append(f"p.access_mode = ${len(params)}")
        if capacity_pool_key:
            params.append(capacity_pool_key)
            clauses.append(f"p.capacity_pool_key = ${len(params)}")
        where_sql = " AND ".join(clauses)
        count_rows = await self.prisma.query_raw(
            f"""
            SELECT COUNT(*)::int AS total
            FROM deltallm_tiermodelpolicy p
            WHERE {where_sql}
            """,
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)
        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT {_prefixed_columns(_MODEL_POLICY_COLUMNS, "p")}
            FROM deltallm_tiermodelpolicy p
            WHERE {where_sql}
            ORDER BY {sort_column} {order_sql}, p.tier_model_policy_id ASC
            LIMIT ${len(page_params) - 1}
            OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return TierModelPolicyPage(
            records=tuple(to_model_policy_record(row) for row in rows),
            total=total,
            configuration_revision=revision,
            version_updated_at=version_updated_at,
        )

    async def list_capacity_pools_page(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        search: str | None = None,
        callable_key: str | None = None,
        strategy: str | None = None,
        sort: str = "pool_key",
        order: str = "asc",
        limit: int = 10,
        offset: int = 0,
    ) -> TierCapacityPoolPage | None:
        if self.prisma is None:
            return None
        limit, offset = _page_bounds(limit, offset)
        sort_column = {
            "pool_key": "p.pool_key",
            "callable_key": "p.callable_key",
            "updated_at": "p.updated_at",
        }.get(sort)
        if sort_column is None:
            raise ValueError("capacity pool sort is invalid")
        order_sql = _sort_order(order)
        version_scope = await self._get_configuration_version_scope(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
        )
        if version_scope is None:
            return None
        revision, version_updated_at = version_scope

        clauses = ["p.tier_version_id = $1"]
        params: list[Any] = [tier_version_id]
        if search:
            params.append(f"%{search.strip()}%")
            clauses.append(
                f"(p.pool_key ILIKE ${len(params)} "
                f"OR p.callable_key ILIKE ${len(params)})"
            )
        if callable_key:
            params.append(callable_key.strip())
            clauses.append(f"p.callable_key = ${len(params)}")
        if strategy:
            params.append(strategy)
            clauses.append(f"p.strategy = ${len(params)}")
        where_sql = " AND ".join(clauses)
        count_rows = await self.prisma.query_raw(
            f"""
            SELECT COUNT(*)::int AS total
            FROM deltallm_tiercapacitypool p
            WHERE {where_sql}
            """,
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)
        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT {_prefixed_columns(_CAPACITY_POOL_COLUMNS, "p")}
            FROM deltallm_tiercapacitypool p
            WHERE {where_sql}
            ORDER BY {sort_column} {order_sql}, p.tier_capacity_pool_id ASC
            LIMIT ${len(page_params) - 1}
            OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return TierCapacityPoolPage(
            records=tuple(to_capacity_pool_record(row) for row in rows),
            total=total,
            configuration_revision=revision,
            version_updated_at=version_updated_at,
        )

    async def _get_configuration_version_scope(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
    ) -> tuple[int, datetime | None] | None:
        rows = await self.prisma.query_raw(
            """
            SELECT configuration_revision, updated_at
            FROM deltallm_tierversion
            WHERE tier_version_id = $1
              AND tier_id = $2
            LIMIT 1
            """,
            tier_version_id,
            tier_id,
        )
        if not rows:
            return None
        return int(rows[0].get("configuration_revision") or 0), parse_datetime(
            rows[0].get("updated_at")
        )

    async def lock_draft_version_for_configuration_mutation(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
    ) -> TierVersionRecord:
        expected_revision = _expected_revision(expected_revision)
        rows = await self.prisma.query_raw(
            """
            SELECT
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
                v.updated_at
            FROM deltallm_tierversion v
            WHERE v.tier_version_id = $1
              AND v.tier_id = $2
            LIMIT 1
            FOR UPDATE
            """,
            tier_version_id,
            tier_id,
        )
        if not rows:
            raise TierConfigurationVersionNotFoundError("tier version not found")

        version = to_version_record(rows[0])
        if version.status != "draft":
            raise TierConfigurationVersionNotDraftError(
                "tier version configuration can only be changed while draft"
            )
        if version.configuration_revision != expected_revision:
            raise TierConfigurationStaleError(
                expected_revision=expected_revision,
                current_revision=version.configuration_revision,
            )
        return version

    async def create_model_policy(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        policy: TierModelPolicyRecord,
    ) -> TierModelPolicyMutationResult:
        self._require_configuration_transactions("create_model_policy")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._create_model_policy_in_tx(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                expected_revision=expected_revision,
                policy=policy,
            )

    async def _create_model_policy_in_tx(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        policy: TierModelPolicyRecord,
    ) -> TierModelPolicyMutationResult:
        await self.lock_draft_version_for_configuration_mutation(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            expected_revision=expected_revision,
        )
        await self._require_policy_capacity_pool(
            tier_version_id=tier_version_id,
            callable_key=policy.callable_key,
            capacity_pool_key=policy.capacity_pool_key,
        )
        rows = await self.prisma.query_raw(
            f"""
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
                $1, $2, $3, $4, $5, $6, $7, $8, $9,
                $10, $11, $12, $13::jsonb, $14, $15, $16::jsonb,
                NOW(), NOW()
            )
            RETURNING {_MODEL_POLICY_COLUMNS}
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
        if not rows:
            raise RuntimeError("model policy insert did not return a row")
        revision, updated_at = await self._bump_configuration_revision(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
        )
        return TierModelPolicyMutationResult(
            policy=to_model_policy_record(rows[0]),
            configuration_revision=revision,
            version_updated_at=updated_at,
        )

    async def update_model_policy(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_model_policy_id: str,
        expected_revision: int,
        policy: TierModelPolicyRecord,
    ) -> TierModelPolicyMutationResult:
        self._require_configuration_transactions("update_model_policy")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._update_model_policy_in_tx(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                tier_model_policy_id=tier_model_policy_id,
                expected_revision=expected_revision,
                policy=policy,
            )

    async def _update_model_policy_in_tx(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_model_policy_id: str,
        expected_revision: int,
        policy: TierModelPolicyRecord,
    ) -> TierModelPolicyMutationResult:
        await self.lock_draft_version_for_configuration_mutation(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            expected_revision=expected_revision,
        )
        existing_rows = await self.prisma.query_raw(
            """
            SELECT callable_key
            FROM deltallm_tiermodelpolicy
            WHERE tier_model_policy_id = $1
              AND tier_version_id = $2
            LIMIT 1
            FOR UPDATE
            """,
            tier_model_policy_id,
            tier_version_id,
        )
        if not existing_rows:
            raise TierConfigurationChildNotFoundError("model policy not found")
        callable_key = str(existing_rows[0].get("callable_key") or "")
        if policy.callable_key != callable_key:
            raise TierConfigurationIdentityImmutableError("callable_key cannot be changed")
        await self._require_policy_capacity_pool(
            tier_version_id=tier_version_id,
            callable_key=callable_key,
            capacity_pool_key=policy.capacity_pool_key,
        )
        rows = await self.prisma.query_raw(
            f"""
            UPDATE deltallm_tiermodelpolicy
            SET
                enabled = $3,
                access_mode = $4,
                rpm_limit = $5,
                tpm_limit = $6,
                rph_limit = $7,
                rpd_limit = $8,
                tpd_limit = $9,
                max_parallel_requests = $10,
                batch_rpm_limit = $11,
                batch_tpm_limit = $12,
                pricing = $13::jsonb,
                capacity_pool_key = $14,
                priority = $15,
                metadata = $16::jsonb,
                updated_at = NOW()
            WHERE tier_model_policy_id = $1
              AND tier_version_id = $2
            RETURNING {_MODEL_POLICY_COLUMNS}
            """,
            tier_model_policy_id,
            tier_version_id,
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
        if not rows:
            raise TierConfigurationChildNotFoundError("model policy not found")
        revision, updated_at = await self._bump_configuration_revision(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
        )
        return TierModelPolicyMutationResult(
            policy=to_model_policy_record(rows[0]),
            configuration_revision=revision,
            version_updated_at=updated_at,
        )

    async def delete_model_policy(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_model_policy_id: str,
        expected_revision: int,
    ) -> TierModelPolicyMutationResult:
        self._require_configuration_transactions("delete_model_policy")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._delete_model_policy_in_tx(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                tier_model_policy_id=tier_model_policy_id,
                expected_revision=expected_revision,
            )

    async def _delete_model_policy_in_tx(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_model_policy_id: str,
        expected_revision: int,
    ) -> TierModelPolicyMutationResult:
        await self.lock_draft_version_for_configuration_mutation(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            expected_revision=expected_revision,
        )
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_tiermodelpolicy
            WHERE tier_model_policy_id = $1
              AND tier_version_id = $2
            RETURNING tier_model_policy_id
            """,
            tier_model_policy_id,
            tier_version_id,
        )
        if not rows:
            raise TierConfigurationChildNotFoundError("model policy not found")
        revision, updated_at = await self._bump_configuration_revision(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
        )
        return TierModelPolicyMutationResult(
            policy=None,
            configuration_revision=revision,
            version_updated_at=updated_at,
        )

    async def bulk_update_model_policy_limits(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        update_rpm_limit: bool,
        rpm_limit: int | None,
        update_tpm_limit: bool,
        tpm_limit: int | None,
        tier_model_policy_ids: tuple[str, ...] | None = None,
        search: str | None = None,
        enabled: bool | None = None,
        access_mode: str | None = None,
        capacity_pool_key: str | None = None,
    ) -> TierModelPolicyBulkMutationResult:
        self._require_configuration_transactions("bulk_update_model_policy_limits")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._bulk_update_model_policy_limits_in_tx(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                expected_revision=expected_revision,
                update_rpm_limit=update_rpm_limit,
                rpm_limit=rpm_limit,
                update_tpm_limit=update_tpm_limit,
                tpm_limit=tpm_limit,
                tier_model_policy_ids=tier_model_policy_ids,
                search=search,
                enabled=enabled,
                access_mode=access_mode,
                capacity_pool_key=capacity_pool_key,
            )

    async def _bulk_update_model_policy_limits_in_tx(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        update_rpm_limit: bool,
        rpm_limit: int | None,
        update_tpm_limit: bool,
        tpm_limit: int | None,
        tier_model_policy_ids: tuple[str, ...] | None,
        search: str | None,
        enabled: bool | None,
        access_mode: str | None,
        capacity_pool_key: str | None,
    ) -> TierModelPolicyBulkMutationResult:
        if not update_rpm_limit and not update_tpm_limit:
            raise ValueError("at least one limit must be supplied")
        version = await self.lock_draft_version_for_configuration_mutation(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            expected_revision=expected_revision,
        )
        clauses = ["tier_version_id = $1"]
        params: list[Any] = [tier_version_id]
        if tier_model_policy_ids is not None:
            policy_ids = tuple(dict.fromkeys(_nonblank_ids(tier_model_policy_ids)))
            if not policy_ids:
                raise ValueError("tier_model_policy_ids must not be empty")
            params.append(list(policy_ids))
            scoped_rows = await self.prisma.query_raw(
                """
                SELECT tier_model_policy_id
                FROM deltallm_tiermodelpolicy
                WHERE tier_version_id = $1
                  AND tier_model_policy_id = ANY($2::text[])
                FOR UPDATE
                """,
                *params,
            )
            if len(scoped_rows) != len(policy_ids):
                raise TierConfigurationChildNotFoundError("model policy not found")
            clauses.append("tier_model_policy_id = ANY($2::text[])")
        else:
            if search:
                params.append(f"%{search.strip()}%")
                clauses.append(
                    f"(callable_key ILIKE ${len(params)} OR "
                    f"COALESCE(capacity_pool_key, '') ILIKE ${len(params)})"
                )
            if enabled is not None:
                params.append(enabled)
                clauses.append(f"enabled = ${len(params)}")
            if access_mode:
                params.append(access_mode)
                clauses.append(f"access_mode = ${len(params)}")
            if capacity_pool_key:
                params.append(capacity_pool_key)
                clauses.append(f"capacity_pool_key = ${len(params)}")

        assignments: list[str] = []
        if update_rpm_limit:
            params.append(rpm_limit)
            assignments.append(f"rpm_limit = ${len(params)}")
        if update_tpm_limit:
            params.append(tpm_limit)
            assignments.append(f"tpm_limit = ${len(params)}")
        assignments.append("updated_at = NOW()")
        rows = await self.prisma.query_raw(
            f"""
            UPDATE deltallm_tiermodelpolicy
            SET {", ".join(assignments)}
            WHERE {" AND ".join(clauses)}
            RETURNING tier_model_policy_id
            """,
            *params,
        )
        if not rows:
            return TierModelPolicyBulkMutationResult(
                affected_count=0,
                configuration_revision=version.configuration_revision,
                version_updated_at=version.updated_at,
            )
        revision, updated_at = await self._bump_configuration_revision(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
        )
        return TierModelPolicyBulkMutationResult(
            affected_count=len(rows),
            configuration_revision=revision,
            version_updated_at=updated_at,
        )

    async def create_capacity_pool(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        pool: TierCapacityPoolRecord,
    ) -> TierCapacityPoolMutationResult:
        self._require_configuration_transactions("create_capacity_pool")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._create_capacity_pool_in_tx(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                expected_revision=expected_revision,
                pool=pool,
            )

    async def _create_capacity_pool_in_tx(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        pool: TierCapacityPoolRecord,
    ) -> TierCapacityPoolMutationResult:
        await self.lock_draft_version_for_configuration_mutation(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            expected_revision=expected_revision,
        )
        rows = await self.prisma.query_raw(
            f"""
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
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb,
                NOW(), NOW()
            )
            RETURNING {_CAPACITY_POOL_COLUMNS}
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
        if not rows:
            raise RuntimeError("capacity pool insert did not return a row")
        revision, updated_at = await self._bump_configuration_revision(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
        )
        return TierCapacityPoolMutationResult(
            pool=to_capacity_pool_record(rows[0]),
            configuration_revision=revision,
            version_updated_at=updated_at,
        )

    async def update_capacity_pool(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_capacity_pool_id: str,
        expected_revision: int,
        pool: TierCapacityPoolRecord,
    ) -> TierCapacityPoolMutationResult:
        self._require_configuration_transactions("update_capacity_pool")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._update_capacity_pool_in_tx(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                tier_capacity_pool_id=tier_capacity_pool_id,
                expected_revision=expected_revision,
                pool=pool,
            )

    async def _update_capacity_pool_in_tx(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_capacity_pool_id: str,
        expected_revision: int,
        pool: TierCapacityPoolRecord,
    ) -> TierCapacityPoolMutationResult:
        await self.lock_draft_version_for_configuration_mutation(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            expected_revision=expected_revision,
        )
        existing_rows = await self.prisma.query_raw(
            """
            SELECT pool_key, callable_key
            FROM deltallm_tiercapacitypool
            WHERE tier_capacity_pool_id = $1
              AND tier_version_id = $2
            LIMIT 1
            FOR UPDATE
            """,
            tier_capacity_pool_id,
            tier_version_id,
        )
        if not existing_rows:
            raise TierConfigurationChildNotFoundError("capacity pool not found")
        existing = existing_rows[0]
        if (
            pool.pool_key != str(existing.get("pool_key") or "")
            or pool.callable_key != str(existing.get("callable_key") or "")
        ):
            raise TierConfigurationIdentityImmutableError(
                "pool_key and callable_key cannot be changed"
            )
        rows = await self.prisma.query_raw(
            f"""
            UPDATE deltallm_tiercapacitypool
            SET
                rpm_capacity = $3,
                tpm_capacity = $4,
                max_parallel_requests = $5,
                strategy = $6,
                saturation_threshold = $7,
                burst_multiplier = $8,
                metadata = $9::jsonb,
                updated_at = NOW()
            WHERE tier_capacity_pool_id = $1
              AND tier_version_id = $2
            RETURNING {_CAPACITY_POOL_COLUMNS}
            """,
            tier_capacity_pool_id,
            tier_version_id,
            pool.rpm_capacity,
            pool.tpm_capacity,
            pool.max_parallel_requests,
            pool.strategy,
            pool.saturation_threshold,
            pool.burst_multiplier,
            json_param(pool.metadata),
        )
        if not rows:
            raise TierConfigurationChildNotFoundError("capacity pool not found")
        revision, updated_at = await self._bump_configuration_revision(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
        )
        return TierCapacityPoolMutationResult(
            pool=to_capacity_pool_record(rows[0]),
            configuration_revision=revision,
            version_updated_at=updated_at,
        )

    async def delete_capacity_pool(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_capacity_pool_id: str,
        expected_revision: int,
    ) -> TierCapacityPoolMutationResult:
        self._require_configuration_transactions("delete_capacity_pool")
        async with self.prisma.tx() as tx:
            return await self.with_db(tx)._delete_capacity_pool_in_tx(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                tier_capacity_pool_id=tier_capacity_pool_id,
                expected_revision=expected_revision,
            )

    async def _delete_capacity_pool_in_tx(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_capacity_pool_id: str,
        expected_revision: int,
    ) -> TierCapacityPoolMutationResult:
        await self.lock_draft_version_for_configuration_mutation(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            expected_revision=expected_revision,
        )
        existing_rows = await self.prisma.query_raw(
            """
            SELECT pool_key, callable_key
            FROM deltallm_tiercapacitypool
            WHERE tier_capacity_pool_id = $1
              AND tier_version_id = $2
            LIMIT 1
            FOR UPDATE
            """,
            tier_capacity_pool_id,
            tier_version_id,
        )
        if not existing_rows:
            raise TierConfigurationChildNotFoundError("capacity pool not found")
        pool_key = str(existing_rows[0].get("pool_key") or "")
        callable_key = str(existing_rows[0].get("callable_key") or "")
        reference_rows = await self.prisma.query_raw(
            """
            SELECT tier_model_policy_id
            FROM deltallm_tiermodelpolicy
            WHERE tier_version_id = $1
              AND capacity_pool_key = $2
              AND callable_key = $3
            LIMIT 1
            """,
            tier_version_id,
            pool_key,
            callable_key,
        )
        if reference_rows:
            raise TierConfigurationPoolInUseError(
                "capacity pool is referenced by a model policy"
            )
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_tiercapacitypool
            WHERE tier_capacity_pool_id = $1
              AND tier_version_id = $2
            RETURNING tier_capacity_pool_id
            """,
            tier_capacity_pool_id,
            tier_version_id,
        )
        if not rows:
            raise TierConfigurationChildNotFoundError("capacity pool not found")
        revision, updated_at = await self._bump_configuration_revision(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
        )
        return TierCapacityPoolMutationResult(
            pool=None,
            configuration_revision=revision,
            version_updated_at=updated_at,
        )

    async def _require_policy_capacity_pool(
        self,
        *,
        tier_version_id: str,
        callable_key: str,
        capacity_pool_key: str | None,
    ) -> None:
        if capacity_pool_key is None:
            return
        rows = await self.prisma.query_raw(
            """
            SELECT tier_capacity_pool_id
            FROM deltallm_tiercapacitypool
            WHERE tier_version_id = $1
              AND pool_key = $2
              AND callable_key = $3
            LIMIT 1
            """,
            tier_version_id,
            capacity_pool_key,
            callable_key,
        )
        if not rows:
            raise TierConfigurationPoolReferenceError(
                "capacity_pool_key must reference a pool for the same callable_key"
            )

    async def _bump_configuration_revision(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
    ) -> tuple[int, datetime | None]:
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_tierversion
            SET
                configuration_revision = configuration_revision + 1,
                updated_at = NOW()
            WHERE tier_version_id = $1
              AND tier_id = $2
            RETURNING configuration_revision, updated_at
            """,
            tier_version_id,
            tier_id,
        )
        if not rows:
            raise RuntimeError("locked tier version disappeared before revision bump")
        return int(rows[0].get("configuration_revision") or 0), parse_datetime(
            rows[0].get("updated_at")
        )

    def _require_configuration_transactions(self, operation: str) -> None:
        if self.prisma is None:
            raise RuntimeError(f"{operation} requires a database")
        self.require_transactions(operation)


def _expected_revision(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected_revision must be a non-negative integer")
    return value


def _page_bounds(limit: int, offset: int) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 10 <= limit <= 100:
        raise ValueError("limit must be between 10 and 100")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    return limit, offset


def _sort_order(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"asc", "desc"}:
        raise ValueError("order must be asc or desc")
    return normalized.upper()


def _nonblank_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(str(value or "").strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError("tier_model_policy_ids must contain nonblank IDs")
    return normalized


def _prefixed_columns(columns: str, alias: str) -> str:
    return ",\n".join(
        f"{alias}.{column.strip()}"
        for column in columns.split(",")
        if column.strip()
    )


_MODEL_POLICY_COLUMNS = """
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
"""

_CAPACITY_POOL_COLUMNS = """
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
"""


__all__ = [
    "TierCapacityPoolPage",
    "TierCapacityPoolMutationResult",
    "TierConfigurationChildNotFoundError",
    "TierConfigurationIdentityImmutableError",
    "TierConfigurationMutationError",
    "TierConfigurationPoolInUseError",
    "TierConfigurationPoolReferenceError",
    "TierConfigurationRepositoryMixin",
    "TierConfigurationStaleError",
    "TierConfigurationVersionNotDraftError",
    "TierConfigurationVersionNotFoundError",
    "TierModelPolicyMutationResult",
    "TierModelPolicyBulkMutationResult",
    "TierModelPolicyPage",
]
