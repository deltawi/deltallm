from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.audit.actions import AuditAction
from src.auth.roles import Permission, validate_organization_role, validate_team_role
router = APIRouter(tags=["Admin Invitations"])


def _organization_ids(metadata: dict[str, Any] | None) -> set[str]:
    values: set[str] = set()
    for item in list((metadata or {}).get("organization_invites") or []):
        if isinstance(item, dict) and item.get("organization_id"):
            values.add(str(item["organization_id"]))
    for item in list((metadata or {}).get("team_invites") or []):
        if isinstance(item, dict) and item.get("organization_id"):
            values.add(str(item["organization_id"]))
    return values


def _team_ids(metadata: dict[str, Any] | None) -> set[str]:
    values: set[str] = set()
    for item in list((metadata or {}).get("team_invites") or []):
        if isinstance(item, dict) and item.get("team_id"):
            values.add(str(item["team_id"]))
    return values


def _can_manage_invitation(scope, invitation: dict[str, Any]) -> bool:  # noqa: ANN001
    if bool(getattr(scope, "is_platform_admin", False)):
        return True
    metadata = invitation.get("metadata")
    org_permissions = getattr(scope, "org_permissions_by_id", {}) or {}
    team_permissions = getattr(scope, "team_permissions_by_id", {}) or {}
    organization_invites = [item for item in list((metadata or {}).get("organization_invites") or []) if isinstance(item, dict)]
    team_invites = [item for item in list((metadata or {}).get("team_invites") or []) if isinstance(item, dict)]
    if not organization_invites and not team_invites:
        return False

    for item in organization_invites:
        organization_id = str(item.get("organization_id") or "")
        if not organization_id or Permission.ORG_UPDATE not in org_permissions.get(organization_id, set()):
            return False

    for item in team_invites:
        team_id = str(item.get("team_id") or "")
        organization_id = str(item.get("organization_id") or "")
        team_allowed = bool(team_id and Permission.TEAM_UPDATE in team_permissions.get(team_id, set()))
        org_allowed = bool(organization_id and Permission.ORG_UPDATE in org_permissions.get(organization_id, set()))
        if not (team_allowed or org_allowed):
            return False
    return True


async def _resolve_team_org_id(db, team_id: str) -> str | None:  # noqa: ANN001
    rows = await db.query_raw(
        "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1",
        team_id,
    )
    if not rows:
        return None
    return str(rows[0].get("organization_id") or "") or None


@router.get("/ui/api/invitations")
async def list_invitations(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
    _: str = Header(default=None, include_in_schema=False),
) -> dict[str, Any]:
    del _
    scope = get_auth_scope(request, authorization, x_master_key)
    service = getattr(request.app.state, "invitation_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invitation service unavailable")
    invitations = await service.list_invitations(status=status_filter, search=search)
    visible = invitations if scope.is_platform_admin else [item for item in invitations if _can_manage_invitation(scope, item)]
    page = visible[offset : offset + limit]
    return {
        "data": page,
        "pagination": {
            "total": len(visible),
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < len(visible),
        },
    }


@router.post("/ui/api/invitations")
async def create_invitation(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key)
    service = getattr(request.app.state, "invitation_service", None)
    db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if service is None or db is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invitation service unavailable")

    organization_id = str(payload.get("organization_id") or "").strip() or None
    team_id = str(payload.get("team_id") or "").strip() or None
    try:
        organization_role = (
            validate_organization_role(str(payload.get("organization_role") or payload.get("role") or "org_member"))
            if organization_id
            else "org_member"
        )
        team_role = (
            validate_team_role(str(payload.get("team_role") or payload.get("role") or "team_viewer"))
            if team_id
            else "team_viewer"
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    team_org_id = await _resolve_team_org_id(db, team_id) if team_id else None
    if team_id and not team_org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team not found")
    if organization_id and team_org_id and team_org_id != organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="team_id does not belong to organization_id")

    if not scope.is_platform_admin:
        org_permissions = scope.org_permissions_by_id or {}
        team_permissions = scope.team_permissions_by_id or {}
        if organization_id and Permission.ORG_UPDATE not in org_permissions.get(organization_id, set()):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        if team_id:
            team_allowed = Permission.TEAM_UPDATE in team_permissions.get(team_id, set())
            org_allowed = bool(team_org_id and Permission.ORG_UPDATE in org_permissions.get(team_org_id, set()))
            if not (team_allowed or org_allowed):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    try:
        response = await service.create_invitation(
            email=str(payload.get("email") or ""),
            invited_by_account_id=scope.account_id,
            organization_id=organization_id,
            organization_role=organization_role,
            team_id=team_id,
            team_role=team_role,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc

    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_INVITATION_CREATE,
        scope=scope,
        resource_type="invitation",
        resource_id=response["invitation_id"],
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.post("/ui/api/invitations/{invitation_id}/resend")
async def resend_invitation(
    request: Request,
    invitation_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key)
    service = getattr(request.app.state, "invitation_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invitation service unavailable")

    invitation = await service.get_invitation(invitation_id)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invitation not found")
    if not _can_manage_invitation(scope, invitation):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    try:
        response = await service.resend_invitation(invitation_id=invitation_id, invited_by_account_id=scope.account_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc

    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_INVITATION_RESEND,
        scope=scope,
        resource_type="invitation",
        resource_id=invitation_id,
        response_payload=response,
    )
    return response


@router.post("/ui/api/invitations/{invitation_id}/cancel")
async def cancel_invitation(
    request: Request,
    invitation_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key)
    service = getattr(request.app.state, "invitation_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invitation service unavailable")

    invitation = await service.get_invitation(invitation_id)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invitation not found")
    if not _can_manage_invitation(scope, invitation):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    try:
        cancelled = await service.cancel_invitation(invitation_id=invitation_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc

    response = {"cancelled": cancelled, "invitation_id": invitation_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_INVITATION_CANCEL,
        scope=scope,
        resource_type="invitation",
        resource_id=invitation_id,
        response_payload=response,
    )
    return response
