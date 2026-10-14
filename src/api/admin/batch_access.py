from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException, Request, status

from src.api.admin.auth_scope import AuthScope
from src.batch.repositories.admin_visibility_repository import (
    BatchOwnerScope,
    append_batch_visibility_clause,
    find_batch_scope_row,
    resolve_batch_ownership,
)
from src.middleware.platform_auth import get_platform_auth_context

BatchListView = Literal["operator", "own"]


def require_operator_batch_access(request: Request) -> None:
    context = get_platform_auth_context(request)
    if context is not None and context.external_workspace is not None:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def resolve_batch_owner_scope(scope: AuthScope) -> BatchOwnerScope | None:
    """Own-batch scope for the memberships that the operator scope does not cover.

    ``scope`` is the ``key.read`` projection that the batch read routes use. Its
    ``org_ids`` and ``team_ids`` already show every batch in those scopes, so only
    the other memberships (for example org members and team viewers) get the
    owner predicate.
    """

    if scope.is_platform_admin or scope.external_workspace is not None:
        return None
    account_id = str(scope.account_id or "").strip()
    if not account_id:
        return None
    operator_org_ids = set(scope.org_ids)
    operator_team_ids = set(scope.team_ids)
    organization_ids = tuple(
        sorted(
            organization_id
            for organization_id, permissions in scope.org_permissions_by_id.items()
            if organization_id and permissions and organization_id not in operator_org_ids
        )
    )
    team_ids = tuple(
        sorted(
            team_id
            for team_id, permissions in scope.team_permissions_by_id.items()
            if team_id and permissions and team_id not in operator_team_ids
        )
    )
    if not organization_ids and not team_ids:
        return None
    return BatchOwnerScope(
        account_id=account_id,
        organization_ids=organization_ids,
        team_ids=team_ids,
    )


def batch_list_view(scope: AuthScope) -> BatchListView:
    """``own`` when the caller sees only the batches it created."""

    if scope.is_platform_admin or scope.org_ids or scope.team_ids:
        return "operator"
    return "own"


def append_batch_scope_clause(
    *, clauses: list[str], params: list[Any], scope: AuthScope, job_alias: str = ""
) -> bool:
    if scope.is_platform_admin:
        return True
    return append_batch_visibility_clause(
        clauses=clauses,
        params=params,
        operator_team_ids=scope.team_ids,
        operator_org_ids=scope.org_ids,
        owner=resolve_batch_owner_scope(scope),
        job_alias=job_alias,
    )


def _scope_covers_batch_ownership(scope: AuthScope, ownership: dict[str, str | None]) -> bool:
    if scope.is_platform_admin:
        return True
    team_id = ownership.get("created_by_team_id")
    organization_id = ownership.get("organization_id")
    if team_id and scope.team_ids and team_id in scope.team_ids:
        return True
    return bool(organization_id and scope.org_ids and organization_id in scope.org_ids)


async def load_batch_scope_row(db: Any, batch_id: str) -> dict[str, Any]:
    job = await find_batch_scope_row(db, batch_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return job


async def enforce_batch_update_scope(
    *,
    db: Any,
    scope: AuthScope,
    job: dict[str, Any],
) -> dict[str, str | None]:
    ownership = await resolve_batch_ownership(db, job)
    if _scope_covers_batch_ownership(scope, ownership):
        return ownership
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


async def enforce_batch_read_scope(
    *,
    db: Any,
    scope: AuthScope,
    job: dict[str, Any],
) -> dict[str, str | None]:
    """Operator scope, or the account's own batch in one of its current memberships."""

    ownership = await resolve_batch_ownership(db, job)
    if _scope_covers_batch_ownership(scope, ownership):
        return ownership
    owner_scope = resolve_batch_owner_scope(scope)
    if owner_scope is not None and owner_scope.covers(
        owner_account_id=job.get("created_by_owner_account_id"),
        team_id=ownership.get("created_by_team_id"),
        organization_id=ownership.get("organization_id"),
    ):
        return ownership
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


async def enforce_webhook_delivery_scope(
    *,
    db: Any,
    scope: AuthScope,
    deliveries: Any,
) -> dict[str, str | None]:
    ownership_scopes = {
        (
            str(delivery.created_by_team_id or "").strip() or None,
            str(delivery.created_by_organization_id or "").strip() or None,
        )
        for delivery in deliveries
    }
    if len(ownership_scopes) != 1:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    team_id, organization_id = next(iter(ownership_scopes))
    return await enforce_batch_update_scope(
        db=db,
        scope=scope,
        job={
            "created_by_team_id": team_id,
            "created_by_organization_id": organization_id,
        },
    )
