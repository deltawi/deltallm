from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.db.tier_records import (
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    TierPolicyAssignmentRecord,
    TierPolicyLoadResult,
    parse_datetime,
    to_capacity_pool_record,
    to_model_policy_record,
    to_tier_policy_assignment_record,
)


class TierPolicyRepositoryUnavailableError(RuntimeError):
    pass


class TierPolicyRepositoryMixin:
    prisma: Any | None

    def _require_tier_policy_prisma(self) -> Any:
        if self.prisma is None:
            raise TierPolicyRepositoryUnavailableError("tier policy repository database unavailable")
        return self.prisma

    async def load_active_tier_policy_inputs(
        self,
        *,
        reference_time: datetime | None = None,
    ) -> TierPolicyLoadResult:
        self._require_tier_policy_prisma()
        assignments = await self.list_active_tier_policy_assignments(
            reference_time=reference_time,
        )
        version_ids = [
            assignment.effective_tier_version_id
            for assignment in assignments
            if assignment.effective_tier_version_id
        ]
        model_policies = await self.list_model_policies_for_tier_versions(version_ids)
        capacity_pools = await self.list_capacity_pools_for_tier_versions(version_ids)
        next_transition_at = await self.get_next_tier_policy_assignment_transition(
            reference_time=reference_time,
        )
        return TierPolicyLoadResult(
            assignments=tuple(assignments),
            model_policies=tuple(model_policies),
            capacity_pools=tuple(capacity_pools),
            next_transition_at=next_transition_at,
        )

    async def list_active_tier_policy_assignments(
        self,
        *,
        reference_time: datetime | None = None,
    ) -> list[TierPolicyAssignmentRecord]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            """
            SELECT
                a.assignment_id,
                a.organization_id,
                a.tier_id,
                a.tier_version_id,
                resolved_version.tier_version_id AS effective_tier_version_id,
                a.assignment_type,
                a.enabled,
                a.weight,
                a.starts_at,
                a.ends_at,
                a.metadata,
                a.created_at,
                a.updated_at,
                t.tier_key,
                t.name AS tier_name,
                resolved_version.version_number AS tier_version_number,
                resolved_version.status AS tier_version_status
            FROM deltallm_organizationtierassignment a
            JOIN deltallm_tier t ON t.tier_id = a.tier_id
            JOIN LATERAL (
                SELECT v.tier_version_id, v.version_number, v.status
                FROM deltallm_tierversion v
                WHERE v.tier_id = a.tier_id
                  AND v.status = 'active'
                  AND (
                      a.tier_version_id IS NULL
                      OR v.tier_version_id = a.tier_version_id
                  )
                ORDER BY v.version_number DESC, v.tier_version_id ASC
                LIMIT 1
            ) AS resolved_version ON TRUE
            WHERE a.enabled = TRUE
              AND t.enabled = TRUE
              AND (a.starts_at IS NULL OR a.starts_at <= $1::timestamp)
              AND (a.ends_at IS NULL OR a.ends_at > $1::timestamp)
            ORDER BY
                a.organization_id ASC,
                CASE a.assignment_type
                    WHEN 'override' THEN 3
                    WHEN 'addon' THEN 2
                    ELSE 1
                END DESC,
                a.weight DESC,
                a.created_at ASC,
                a.assignment_id ASC
            """,
            _timestamp_param(reference_time),
        )
        return [to_tier_policy_assignment_record(row) for row in rows]

    async def get_next_tier_policy_assignment_transition(
        self,
        *,
        reference_time: datetime | None = None,
    ) -> datetime | None:
        prisma = self._require_tier_policy_prisma()
        rows = await prisma.query_raw(
            """
            SELECT MIN(transition_at) AS next_transition_at
            FROM (
                SELECT a.starts_at AS transition_at
                FROM deltallm_organizationtierassignment a
                JOIN deltallm_tier t ON t.tier_id = a.tier_id
                JOIN LATERAL (
                    SELECT v.tier_version_id
                    FROM deltallm_tierversion v
                    WHERE v.tier_id = a.tier_id
                      AND v.status = 'active'
                      AND (
                          a.tier_version_id IS NULL
                          OR v.tier_version_id = a.tier_version_id
                      )
                    ORDER BY v.version_number DESC, v.tier_version_id ASC
                    LIMIT 1
                ) AS resolved_version ON TRUE
                WHERE a.enabled = TRUE
                  AND t.enabled = TRUE
                  AND a.starts_at IS NOT NULL
                  AND a.starts_at > $1::timestamp

                UNION ALL

                SELECT a.ends_at AS transition_at
                FROM deltallm_organizationtierassignment a
                JOIN deltallm_tier t ON t.tier_id = a.tier_id
                JOIN LATERAL (
                    SELECT v.tier_version_id
                    FROM deltallm_tierversion v
                    WHERE v.tier_id = a.tier_id
                      AND v.status = 'active'
                      AND (
                          a.tier_version_id IS NULL
                          OR v.tier_version_id = a.tier_version_id
                      )
                    ORDER BY v.version_number DESC, v.tier_version_id ASC
                    LIMIT 1
                ) AS resolved_version ON TRUE
                WHERE a.enabled = TRUE
                  AND t.enabled = TRUE
                  AND a.ends_at IS NOT NULL
                  AND a.ends_at > $1::timestamp
            ) transitions
            """,
            _timestamp_param(reference_time),
        )
        if not rows:
            return None
        return parse_datetime(rows[0].get("next_transition_at"))

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

    async def list_model_policies_for_tier_versions(
        self,
        tier_version_ids: list[str],
    ) -> list[TierModelPolicyRecord]:
        if self.prisma is None:
            return []

        version_ids = _unique_non_empty(tier_version_ids)
        if not version_ids:
            return []

        rows = await self.prisma.query_raw(
            f"""
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
            WHERE tier_version_id IN ({_placeholders(len(version_ids))})
            ORDER BY tier_version_id ASC, priority DESC, callable_key ASC
            """,
            *version_ids,
        )
        return [to_model_policy_record(row) for row in rows]

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

    async def list_capacity_pools_for_tier_versions(
        self,
        tier_version_ids: list[str],
    ) -> list[TierCapacityPoolRecord]:
        if self.prisma is None:
            return []

        version_ids = _unique_non_empty(tier_version_ids)
        if not version_ids:
            return []

        rows = await self.prisma.query_raw(
            f"""
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
            WHERE tier_version_id IN ({_placeholders(len(version_ids))})
            ORDER BY tier_version_id ASC, pool_key ASC, callable_key ASC
            """,
            *version_ids,
        )
        return [to_capacity_pool_record(row) for row in rows]

def _timestamp_param(value: datetime | None) -> datetime:
    normalized = value or datetime.now(tz=UTC)
    if normalized.tzinfo is not None:
        return normalized.astimezone(UTC).replace(tzinfo=None)
    return normalized


def _unique_non_empty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _placeholders(count: int) -> str:
    return ", ".join(f"${index}" for index in range(1, count + 1))
