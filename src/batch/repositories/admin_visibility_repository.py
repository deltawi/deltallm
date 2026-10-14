"""SQL for batch visibility in the admin batch routes.

Column names are static. Every scope value is a bound parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class BatchOwnerScope:
    """Read-only access to batches an account created, in its current memberships.

    Ownership is the immutable ``created_by_owner_account_id`` snapshot taken when
    the batch was created. Batches without that snapshot (legacy rows, service
    account keys) are never visible through this scope.
    """

    account_id: str
    organization_ids: tuple[str, ...] = ()
    team_ids: tuple[str, ...] = ()

    def covers(
        self,
        *,
        owner_account_id: str | None,
        team_id: str | None,
        organization_id: str | None,
    ) -> bool:
        if str(owner_account_id or "").strip() != self.account_id:
            return False
        return bool(
            (team_id and team_id in self.team_ids)
            or (organization_id and organization_id in self.organization_ids)
        )


def _placeholders(params: list[Any], values: Sequence[str]) -> str:
    placeholders = ", ".join(f"${len(params) + index + 1}" for index in range(len(values)))
    params.extend(values)
    return placeholders


def append_batch_visibility_clause(
    *,
    clauses: list[str],
    params: list[Any],
    operator_team_ids: Sequence[str],
    operator_org_ids: Sequence[str],
    owner: BatchOwnerScope | None,
    job_alias: str = "",
) -> bool:
    """Append the non-admin visibility predicate. Return False when nothing is visible."""

    team_column = f"{job_alias}created_by_team_id"
    org_column = f"{job_alias}created_by_organization_id"
    scope_clauses: list[str] = []

    if operator_team_ids:
        team_placeholders = _placeholders(params, operator_team_ids)
        scope_clauses.append(f"{team_column} IN ({team_placeholders})")

    if operator_org_ids:
        org_placeholders = _placeholders(params, operator_org_ids)
        scope_clauses.append(
            f"({org_column} IN ({org_placeholders}) "
            f"OR {team_column} IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN ({org_placeholders})))"
        )

    if owner is not None and (owner.team_ids or owner.organization_ids):
        params.append(owner.account_id)
        owner_predicate = f"{job_alias}created_by_owner_account_id = ${len(params)}"
        membership_clauses: list[str] = []
        if owner.team_ids:
            team_placeholders = _placeholders(params, owner.team_ids)
            membership_clauses.append(f"{team_column} IN ({team_placeholders})")
        if owner.organization_ids:
            org_placeholders = _placeholders(params, owner.organization_ids)
            # ANY(ARRAY(...)) runs the team lookup once, so PostgreSQL can combine
            # the team and organization indexes instead of scanning every job.
            membership_clauses.append(
                f"{org_column} IN ({org_placeholders}) "
                f"OR {team_column} = ANY(ARRAY(SELECT team_id FROM deltallm_teamtable WHERE organization_id IN ({org_placeholders})))"
            )
        scope_clauses.append(f"({owner_predicate} AND ({' OR '.join(membership_clauses)}))")

    if not scope_clauses:
        return False

    clauses.append("(" + " OR ".join(scope_clauses) + ")")
    return True


async def find_batch_scope_row(db: Any, batch_id: str) -> dict[str, Any] | None:
    rows = await db.query_raw(
        """
        SELECT batch_id, status, created_by_team_id, created_by_organization_id,
               created_by_owner_account_id
        FROM deltallm_batch_job
        WHERE batch_id = $1
        LIMIT 1
        """,
        batch_id,
    )
    return dict(rows[0]) if rows else None


async def resolve_batch_ownership(db: Any, job: dict[str, Any]) -> dict[str, str | None]:
    team_id = str(job.get("created_by_team_id") or "").strip() or None
    organization_id = (
        str(job.get("organization_id") or job.get("created_by_organization_id") or "").strip()
        or None
    )
    if organization_id is None and team_id:
        org_rows = await db.query_raw(
            "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1",
            team_id,
        )
        organization_id = (
            str((org_rows[0] if org_rows else {}).get("organization_id") or "").strip() or None
        )
    return {
        "created_by_team_id": team_id,
        "organization_id": organization_id,
    }
