"""Scope-gating logic for the admin MCP endpoints.

Houses the view-capability dataclass plus the predicates/validators that
decide whether the caller's :class:`AuthScope` is allowed to read, mutate,
or operate on a given MCP server / approval record.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from src.api.admin.endpoints.common import (
    AuthScope,
    ResolvedScopeTarget,
    resolve_runtime_scope_target,
)
from src.db.mcp import MCPApprovalRequestRecord, MCPServerRecord

from src.api.admin.endpoints.mcp.dependencies import _db_or_503
from src.api.admin.endpoints.mcp.validators import (
    _normalize_scope_id,
    _validate_owner_scope_type,
)


@dataclass(frozen=True)
class MCPServerCapabilities:
    can_mutate: bool
    can_operate: bool
    can_manage_scope_config: bool


async def _validate_scope_target(request: Request, *, scope_type: str, scope_id: str) -> ResolvedScopeTarget:
    return await resolve_runtime_scope_target(
        _db_or_503(request),
        scope_type=scope_type,
        scope_id=scope_id,
    )


async def _validate_owner_scope(
    request: Request,
    *,
    scope: AuthScope,
    owner_scope_type: str,
    owner_scope_id: str | None,
) -> tuple[str, str | None]:
    if owner_scope_type == "global":
        if owner_scope_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="owner_scope_id must be omitted for global servers")
        if not scope.is_platform_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only platform admins can create global MCP servers")
        return owner_scope_type, None

    if not owner_scope_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="owner_scope_id is required for organization-owned servers")

    await _validate_scope_target(request, scope_type="organization", scope_id=owner_scope_id)
    if not scope.is_platform_admin and owner_scope_id not in scope.org_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions for the selected organization")
    return owner_scope_type, owner_scope_id


async def _resolve_server_create_owner_scope(
    request: Request,
    *,
    scope: AuthScope,
    payload: dict[str, Any],
) -> tuple[str, str | None]:
    requested_type = _validate_owner_scope_type(
        payload.get("owner_scope_type") if scope.is_platform_admin else payload.get("owner_scope_type", "organization")
    )
    requested_id = (
        _normalize_scope_id(payload.get("owner_scope_id"), field_name="owner_scope_id")
        if payload.get("owner_scope_id") is not None
        else None
    )
    if scope.is_platform_admin:
        return await _validate_owner_scope(
            request,
            scope=scope,
            owner_scope_type=requested_type,
            owner_scope_id=requested_id,
        )

    if not scope.org_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    if requested_type != "organization":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only organization-owned MCP servers can be created in scoped mode")
    owner_scope_id = requested_id
    if owner_scope_id is None:
        if len(scope.org_ids) == 1:
            owner_scope_id = scope.org_ids[0]
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="owner_scope_id is required when you manage multiple organizations")
    return await _validate_owner_scope(
        request,
        scope=scope,
        owner_scope_type="organization",
        owner_scope_id=owner_scope_id,
    )


def _server_owned_by_scope(server: MCPServerRecord, scope: AuthScope) -> bool:
    if scope.is_platform_admin:
        return True
    if server.owner_scope_type == "organization":
        return bool(server.owner_scope_id and server.owner_scope_id in scope.org_ids)
    return False


def _server_mutable_by_scope(server: MCPServerRecord, scope: AuthScope) -> bool:
    return _server_owned_by_scope(server, scope)


def _server_operable_by_scope(server: MCPServerRecord, scope: AuthScope) -> bool:
    return _server_owned_by_scope(server, scope)


def _server_scope_config_writable_by_scope(server: MCPServerRecord, scope: AuthScope) -> bool:
    return _server_owned_by_scope(server, scope) or scope.is_platform_admin or server.owner_scope_type == "global"


def _server_view_capabilities(
    server: MCPServerRecord,
    *,
    manage_scope: AuthScope,
    is_visible: bool,
) -> MCPServerCapabilities:
    can_mutate = _server_mutable_by_scope(server, manage_scope)
    can_delegate_global = bool(
        is_visible
        and server.owner_scope_type == "global"
        and (manage_scope.is_platform_admin or bool(manage_scope.org_ids))
    )
    return MCPServerCapabilities(
        can_mutate=can_mutate,
        can_operate=can_mutate or can_delegate_global,
        can_manage_scope_config=can_mutate or can_delegate_global,
    )


async def _validate_scoped_server_target_write(
    request: Request,
    *,
    scope: AuthScope,
    server: MCPServerRecord,
    scope_type: str,
    scope_id: str,
) -> dict[str, str | None]:
    target = await _validate_scope_target(request, scope_type=scope_type, scope_id=scope_id)
    target_organization_id = str(target.organization_id or "")
    if scope.is_platform_admin:
        return {"organization_id": target.organization_id, "team_id": target.team_id}
    if not target_organization_id or target_organization_id not in scope.org_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions for the selected scope")
    if server.owner_scope_type == "organization":
        if server.owner_scope_id != target_organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization-owned MCP servers can only be scoped within their owner organization",
            )
        return target
    if server.owner_scope_type == "global":
        return {"organization_id": target.organization_id, "team_id": target.team_id}
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


async def _validate_scoped_scope_target_write(
    request: Request,
    *,
    scope: AuthScope,
    scope_type: str,
    scope_id: str,
) -> dict[str, str | None]:
    target = await _validate_scope_target(request, scope_type=scope_type, scope_id=scope_id)
    if scope.is_platform_admin:
        return {"organization_id": target.organization_id, "team_id": target.team_id}
    target_organization_id = str(target.organization_id or "")
    if not target_organization_id or target_organization_id not in scope.org_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions for the selected scope")
    return {"organization_id": target.organization_id, "team_id": target.team_id}


async def _approval_visible_to_scope(request: Request, scope: AuthScope, approval: MCPApprovalRequestRecord) -> bool:
    if scope.is_platform_admin:
        return True
    if approval.scope_type == "organization":
        return approval.scope_id in scope.org_ids
    if approval.scope_type == "team":
        if approval.scope_id in scope.team_ids:
            return True
        if not scope.org_ids:
            return False
        db = _db_or_503(request)
        rows = await db.query_raw(
            """
            SELECT organization_id
            FROM deltallm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            approval.scope_id,
        )
        organization_id = str((rows[0] if rows else {}).get("organization_id") or "")
        return bool(organization_id and organization_id in scope.org_ids)
    if approval.scope_type == "api_key":
        db = _db_or_503(request)
        rows = await db.query_raw(
            """
            SELECT vt.team_id, t.organization_id
            FROM deltallm_verificationtoken vt
            LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
            WHERE vt.token = $1
            LIMIT 1
            """,
            approval.scope_id,
        )
        row = rows[0] if rows else {}
        team_id = str(row.get("team_id") or "")
        organization_id = str(row.get("organization_id") or "")
        return bool((team_id and team_id in scope.team_ids) or (organization_id and organization_id in scope.org_ids))
    if approval.scope_type == "user":
        db = _db_or_503(request)
        rows = await db.query_raw(
            """
            SELECT u.team_id, t.organization_id
            FROM deltallm_usertable u
            LEFT JOIN deltallm_teamtable t ON u.team_id = t.team_id
            WHERE u.user_id = $1
            LIMIT 1
            """,
            approval.scope_id,
        )
        row = rows[0] if rows else {}
        team_id = str(row.get("team_id") or "")
        organization_id = str(row.get("organization_id") or "")
        return bool((team_id and team_id in scope.team_ids) or (organization_id and organization_id in scope.org_ids))
    return False


__all__ = [
    "MCPServerCapabilities",
    "_validate_scope_target",
    "_validate_owner_scope",
    "_resolve_server_create_owner_scope",
    "_server_owned_by_scope",
    "_server_mutable_by_scope",
    "_server_operable_by_scope",
    "_server_scope_config_writable_by_scope",
    "_server_view_capabilities",
    "_validate_scoped_server_target_write",
    "_validate_scoped_scope_target_write",
    "_approval_visible_to_scope",
]
