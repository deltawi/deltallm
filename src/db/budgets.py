from __future__ import annotations

from typing import Any


class BudgetRepository:
    """Read all budget scopes needed for one request in one database round trip."""

    def __init__(self, prisma_client: Any | None) -> None:
        self.prisma = prisma_client

    async def get_snapshot(
        self,
        *,
        api_key: str | None,
        user_id: str | None,
        team_id: str | None,
        organization_id: str | None,
        model: str | None,
    ) -> list[dict[str, Any]]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            WITH requested AS (
                SELECT
                    $1::text AS api_key,
                    $2::text AS user_id,
                    $3::text AS team_id,
                    $4::text AS organization_id,
                    $5::text AS model
            ), entity_budgets AS (
                SELECT
                    1 AS evaluation_order,
                    'key'::text AS entity_type,
                    k.token::text AS entity_id,
                    k.max_budget::text AS max_budget,
                    NULL::text AS soft_budget,
                    COALESCE(k.spend_exact, k.spend::numeric)::text AS spend,
                    k.budget_duration,
                    k.budget_reset_at,
                    k.metadata
                FROM requested r
                JOIN deltallm_verificationtoken k ON k.token = r.api_key
                WHERE r.api_key IS NOT NULL

                UNION ALL

                SELECT 2, 'user', u.user_id, u.max_budget::text, NULL::text,
                       COALESCE(u.spend_exact, u.spend::numeric)::text,
                       u.budget_duration, u.budget_reset_at, u.metadata
                FROM requested r
                JOIN deltallm_usertable u ON u.user_id = r.user_id
                WHERE r.user_id IS NOT NULL

                UNION ALL

                SELECT 3, 'team', t.team_id, t.max_budget::text, NULL::text,
                       COALESCE(t.spend_exact, t.spend::numeric)::text,
                       t.budget_duration, t.budget_reset_at, t.metadata
                FROM requested r
                JOIN deltallm_teamtable t ON t.team_id = r.team_id
                WHERE r.team_id IS NOT NULL

                UNION ALL

                SELECT 4, 'org', o.organization_id, o.max_budget::text, o.soft_budget::text,
                       COALESCE(o.spend_exact, o.spend::numeric)::text,
                       o.budget_duration, o.budget_reset_at, o.metadata
                FROM requested r
                JOIN deltallm_organizationtable o ON o.organization_id = r.organization_id
                WHERE r.organization_id IS NOT NULL
            ), team_model_budget AS (
                SELECT
                    5 AS evaluation_order,
                    'team_model'::text AS entity_type,
                    (t.team_id || '/' || r.model)::text AS entity_id,
                    t.model_max_budget ->> r.model AS max_budget,
                    NULL::text AS soft_budget,
                    CASE WHEN tm.reconciled_at IS NOT NULL THEN
                        COALESCE(tm.spend_exact, tm.spend::numeric)::text
                    END AS spend,
                    NULL::text AS budget_duration,
                    NULL::timestamp AS budget_reset_at,
                    NULL::jsonb AS metadata
                FROM requested r
                JOIN deltallm_teamtable t ON t.team_id = r.team_id
                LEFT JOIN LATERAL (
                    SELECT counter.spend_exact, counter.spend, counter.reconciled_at
                    FROM deltallm_teammodelspend counter
                    WHERE counter.team_id = t.team_id AND counter.model = r.model
                    LIMIT 1
                ) tm ON TRUE
                WHERE r.team_id IS NOT NULL
                  AND r.model IS NOT NULL
                  AND t.model_max_budget ? r.model
            )
            SELECT * FROM entity_budgets
            UNION ALL
            SELECT * FROM team_model_budget
            ORDER BY evaluation_order ASC
            """,
            api_key,
            user_id,
            team_id,
            organization_id,
            model,
        )
        return [dict(row) for row in rows]

    async def get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        table_map = {
            "key": ("deltallm_verificationtoken", "token", "NULL AS soft_budget", "metadata"),
            "user": ("deltallm_usertable", "user_id", "NULL AS soft_budget", "metadata"),
            "team": ("deltallm_teamtable", "team_id", "NULL AS soft_budget", "metadata"),
            "org": (
                "deltallm_organizationtable",
                "organization_id",
                "soft_budget::text AS soft_budget",
                "metadata",
            ),
        }
        table_info = table_map.get(entity_type)
        if table_info is None:
            return None

        table, column, soft_budget_expr, metadata_expr = table_info
        rows = await self.prisma.query_raw(
            f"""
            SELECT {column} AS entity_id, max_budget::text AS max_budget, {soft_budget_expr},
                   COALESCE(spend_exact, spend::numeric)::text AS spend,
                   budget_duration, budget_reset_at, {metadata_expr} AS metadata
            FROM {table}
            WHERE {column} = $1
            LIMIT 1
            """,
            entity_id,
        )
        if not rows:
            return None
        return dict(rows[0])

    async def get_team_model_limits(self, team_id: str) -> list[dict[str, Any]]:
        rows = await self.prisma.query_raw(
            """
            SELECT model_max_budget
            FROM deltallm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            team_id,
        )
        return [dict(row) for row in rows]

    async def get_team_model_counter(self, team_id: str, model: str) -> list[dict[str, Any]]:
        counter_rows = await self.prisma.query_raw(
            """
            SELECT CASE WHEN reconciled_at IS NOT NULL THEN
                COALESCE(spend_exact, spend::numeric)::text END AS spend
            FROM deltallm_teammodelspend
            WHERE team_id = $1 AND model = $2
            LIMIT 1
            """,
            team_id,
            model,
        )
        return [dict(row) for row in counter_rows]
