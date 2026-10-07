from __future__ import annotations

import hmac
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from src.auth.external_policy import CUSTOMER_PERMISSION_CEILING
from src.auth.roles import (
    ORG_ROLE_PERMISSIONS,
    TEAM_ROLE_PERMISSIONS,
    Permission,
    has_platform_permission,
)
from src.middleware.external_auth import external_session_unavailable, require_unmixed_external_auth
from src.middleware import platform_auth
from src.middleware.platform_auth import (
    get_configured_master_key,
    has_master_key_session,
    master_key_session_unavailable,
    requires_mfa_verification,
)
from src.models.external_auth import ExternalWorkspaceContext
from src.models.platform_auth import PlatformAuthContext


@dataclass
class AuthScope:
    is_platform_admin: bool = False
    org_ids: list[str] = field(default_factory=list)
    team_ids: list[str] = field(default_factory=list)
    org_permissions_by_id: dict[str, set[str]] = field(default_factory=dict)
    team_permissions_by_id: dict[str, set[str]] = field(default_factory=dict)
    granted_permissions: set[str] = field(default_factory=set)
    effective_permissions: set[str] = field(default_factory=set)
    account_id: str | None = None
    external_workspace: ExternalWorkspaceContext | None = None


def require_organization_directory_access(scope: AuthScope, organization_id: str) -> None:
    if scope.external_workspace is not None or (
        not scope.is_platform_admin and organization_id not in scope.org_ids
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def get_auth_scope(
    request: Request,
    authorization: str | None = None,
    x_master_key: str | None = None,
    required_permission: str | None = None,
    any_permission: list[str] | None = None,
) -> AuthScope:
    require_unmixed_external_auth(request, authorization=authorization, x_master_key=x_master_key)
    configured = get_configured_master_key(request)
    provided = x_master_key
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(" ", 1)[1].strip()
    if configured and provided and hmac.compare_digest(provided, configured):
        return AuthScope(is_platform_admin=True)
    if has_master_key_session(request):
        return AuthScope(is_platform_admin=True)
    context = platform_auth.get_platform_auth_context(request)
    if context is None:
        if master_key_session_unavailable(request) or external_session_unavailable(request):
            raise HTTPException(
                status_code=503,
                detail="Authentication service unavailable",
                headers={"Cache-Control": "no-store", "Retry-After": "5", "Vary": "Cookie"},
            )
        raise HTTPException(status_code=401, detail="Authentication required")
    if context.external_workspace is not None and context.force_password_change:
        raise HTTPException(status_code=403, detail="Password change required")
    if requires_mfa_verification(context):
        raise HTTPException(status_code=403, detail="MFA verification required")
    if context.external_workspace is None and has_platform_permission(
        context.role, Permission.PLATFORM_ADMIN
    ):
        return AuthScope(is_platform_admin=True, account_id=context.account_id)
    return _context_scope(
        context, [required_permission] if required_permission else any_permission or []
    )


def _permission_maps(
    context: PlatformAuthContext,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    organizations: dict[str, set[str]] = {}
    teams: dict[str, set[str]] = {}
    workspace = context.external_workspace
    for membership in context.organization_memberships:
        scope_id = str(membership.get("organization_id") or "").strip()
        if scope_id and (workspace is None or scope_id == workspace.organization_id):
            permissions = set(ORG_ROLE_PERMISSIONS.get(str(membership.get("role") or ""), set()))
            if workspace is not None:
                permissions &= CUSTOMER_PERMISSION_CEILING & set(context.permissions)
            organizations.setdefault(scope_id, set()).update(permissions)
    for membership in context.team_memberships:
        scope_id = str(membership.get("team_id") or "").strip()
        if scope_id and (workspace is None or scope_id == workspace.team_id):
            permissions = set(TEAM_ROLE_PERMISSIONS.get(str(membership.get("role") or ""), set()))
            if workspace is not None:
                permissions &= CUSTOMER_PERMISSION_CEILING & set(context.permissions)
            teams.setdefault(scope_id, set()).update(permissions)
    return organizations, teams


def _context_scope(context: PlatformAuthContext, required: list[str]) -> AuthScope:
    organizations, teams = _permission_maps(context)
    effective = set().union(*organizations.values(), *teams.values())
    granted = effective & set(required)
    return AuthScope(
        org_ids=[
            key for key, values in organizations.items() if not required or values & set(required)
        ],
        team_ids=[key for key, values in teams.items() if not required or values & set(required)],
        org_permissions_by_id=organizations,
        team_permissions_by_id=teams,
        granted_permissions=granted,
        effective_permissions=effective,
        account_id=context.account_id,
        external_workspace=context.external_workspace,
    )
