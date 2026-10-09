from __future__ import annotations

from dataclasses import dataclass

from src.db.platform_accounts import PlatformAccountDatabase
from src.models.external_auth import ExternalWorkspaceContext

_TEAM_COLUMNS = """t.team_id, t.team_alias, t.organization_id, t.max_budget, t.spend,
    t.rpm_limit, t.tpm_limit, t.output_tpm_limit, t.rph_limit, t.rpd_limit, t.tpd_limit,
    t.model_rpm_limit, t.model_tpm_limit, t.model_output_tpm_limit, t.blocked,
    t.self_service_keys_enabled, t.self_service_max_keys_per_user,
    t.self_service_budget_ceiling, t.self_service_require_expiry, t.self_service_max_expiry_days,
    o.lifecycle_state AS organization_lifecycle_state, t.created_at, t.updated_at,
    (SELECT COUNT(*) FROM deltallm_teammembership tm WHERE tm.team_id = t.team_id) AS member_count"""


@dataclass(frozen=True, slots=True)
class TeamDirectoryScope:
    is_platform_admin: bool
    organization_ids: tuple[str, ...]
    team_ids: tuple[str, ...]
    external_workspace: ExternalWorkspaceContext | None = None


@dataclass(frozen=True, slots=True)
class TeamDirectoryPage:
    rows: list[dict[str, object]]
    total: int


def _scope_filter(scope: TeamDirectoryScope) -> tuple[str, list[object]]:
    if scope.is_platform_admin and scope.external_workspace is None:
        return "", []
    workspace = scope.external_workspace
    if workspace is not None:
        if (
            workspace.organization_id not in scope.organization_ids
            and workspace.team_id not in scope.team_ids
        ):
            return "FALSE", []
        # Organization membership cannot widen a customer's registered team.
        return "t.organization_id = $1 AND t.team_id = $2", [
            workspace.organization_id,
            workspace.team_id,
        ]
    clauses: list[str] = []
    params: list[object] = []
    for column, ids in (
        ("t.organization_id", scope.organization_ids),
        ("t.team_id", scope.team_ids),
    ):
        if ids:
            placeholders = ", ".join(f"${len(params) + i + 1}" for i in range(len(ids)))
            params.extend(ids)
            clauses.append(f"{column} IN ({placeholders})")
    return "(" + " OR ".join(clauses) + ")" if clauses else "FALSE", params


class TeamDirectoryRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def list(
        self,
        scope: TeamDirectoryScope,
        *,
        search: str | None,
        organization_id: str | None,
        limit: int,
        offset: int,
    ) -> TeamDirectoryPage:
        predicate, params = _scope_filter(scope)
        if predicate == "FALSE":
            return TeamDirectoryPage([], 0)
        clauses = [predicate] if predicate else []
        if search:
            params.append(f"%{search}%")
            clauses.append(f"(t.team_alias ILIKE ${len(params)} OR t.team_id ILIKE ${len(params)})")
        if organization_id:
            params.append(organization_id)
            clauses.append(f"t.organization_id = ${len(params)}")
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        counts = await self.db.query_raw(
            f"SELECT COUNT(*) AS total FROM deltallm_teamtable t {where_sql}", *params
        )
        total = int((counts[0] if counts else {}).get("total") or 0)
        params.extend((limit, offset))
        rows = await self.db.query_raw(
            f"""
            SELECT {_TEAM_COLUMNS}
            FROM deltallm_teamtable t
            LEFT JOIN deltallm_organizationtable o ON o.organization_id = t.organization_id
            {where_sql}
            ORDER BY t.created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return TeamDirectoryPage(rows, total)

    async def list_for_organization(
        self, organization_id: str, *, bound_team_id: str | None
    ) -> list[dict[str, object]]:
        return await self.db.query_raw(
            """
            SELECT t.team_id, t.team_alias, t.max_budget, t.spend, t.rpm_limit, t.tpm_limit,
                   t.output_tpm_limit, t.blocked, t.created_at, t.updated_at,
                   (SELECT COUNT(*) FROM deltallm_teammembership tm WHERE tm.team_id = t.team_id) AS member_count
            FROM deltallm_teamtable t
            WHERE t.organization_id = $1 AND ($2::text IS NULL OR t.team_id = $2)
            ORDER BY t.created_at DESC
            """,
            organization_id,
            bound_team_id,
        )
