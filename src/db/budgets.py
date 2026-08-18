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
                    k.max_budget,
                    NULL::double precision AS soft_budget,
                    COALESCE(k.spend_exact, k.spend::numeric)::double precision AS spend,
                    k.budget_duration,
                    k.budget_reset_at,
                    k.metadata
                FROM requested r
                JOIN deltallm_verificationtoken k ON k.token = r.api_key
                WHERE r.api_key IS NOT NULL

                UNION ALL

                SELECT 2, 'user', u.user_id, u.max_budget, NULL::double precision,
                       COALESCE(u.spend_exact, u.spend::numeric)::double precision,
                       u.budget_duration, u.budget_reset_at, u.metadata
                FROM requested r
                JOIN deltallm_usertable u ON u.user_id = r.user_id
                WHERE r.user_id IS NOT NULL

                UNION ALL

                SELECT 3, 'team', t.team_id, t.max_budget, NULL::double precision,
                       COALESCE(t.spend_exact, t.spend::numeric)::double precision,
                       t.budget_duration, t.budget_reset_at, t.metadata
                FROM requested r
                JOIN deltallm_teamtable t ON t.team_id = r.team_id
                WHERE r.team_id IS NOT NULL

                UNION ALL

                SELECT 4, 'org', o.organization_id, o.max_budget, o.soft_budget,
                       COALESCE(o.spend_exact, o.spend::numeric)::double precision,
                       o.budget_duration, o.budget_reset_at, o.metadata
                FROM requested r
                JOIN deltallm_organizationtable o ON o.organization_id = r.organization_id
                WHERE r.organization_id IS NOT NULL
            ), team_model_budget AS (
                SELECT
                    5 AS evaluation_order,
                    'team_model'::text AS entity_type,
                    (t.team_id || '/' || r.model)::text AS entity_id,
                    NULLIF(t.model_max_budget ->> r.model, '')::double precision AS max_budget,
                    NULL::double precision AS soft_budget,
                    COALESCE(
                        COALESCE(tm.spend_exact, tm.spend::numeric)::double precision,
                        (
                            SELECT COALESCE(
                                SUM(COALESCE(e.spend_exact, e.spend::numeric)),
                                0
                            )::double precision
                            FROM deltallm_spendlog_events e
                            WHERE e.team_id = t.team_id AND e.model = r.model
                        ),
                        0
                    ) AS spend,
                    NULL::text AS budget_duration,
                    NULL::timestamp AS budget_reset_at,
                    NULL::jsonb AS metadata
                FROM requested r
                JOIN deltallm_teamtable t ON t.team_id = r.team_id
                LEFT JOIN deltallm_teammodelspend tm
                  ON tm.team_id = t.team_id AND tm.model = r.model
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
