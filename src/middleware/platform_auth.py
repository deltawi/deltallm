from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, status

from src.auth.roles import ORG_ROLE_PERMISSIONS, TEAM_ROLE_PERMISSIONS, Permission, has_platform_permission
from src.models.platform_auth import PlatformAuthContext

SESSION_COOKIE_NAME = "deltallm_session"


async def attach_platform_auth_context(request: Request) -> None:
    request.state.platform_auth = None

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return

    service = getattr(request.app.state, "platform_identity_service", None)
    if service is None:
        return

    context = await service.get_context_for_session(token)
    if context is None:
        return

    request.state.platform_auth = context


def get_platform_auth_context(request: Request) -> PlatformAuthContext | None:
    value = getattr(request.state, "platform_auth", None)
    if isinstance(value, PlatformAuthContext):
        return value
    return None


def require_platform_permission(permission: str) -> Callable[[Request], Any]:
    async def _require(request: Request) -> PlatformAuthContext:
        context = get_platform_auth_context(request)
        if context is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
        if not has_platform_permission(context.role, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return context

    return _require


def has_platform_admin_session(request: Request) -> bool:
    context = get_platform_auth_context(request)
    if context is None:
        return False
    return has_platform_permission(context.role, Permission.PLATFORM_ADMIN)


def has_scoped_permission(
    context: PlatformAuthContext,
    permission: str,
    organization_id: str | None = None,
    team_id: str | None = None,
) -> bool:
    if has_platform_permission(context.role, Permission.PLATFORM_ADMIN):
        return True

    if organization_id:
        for membership in context.organization_memberships:
            if str(membership.get("organization_id")) != organization_id:
                continue
            role = str(membership.get("role") or "")
            if permission in ORG_ROLE_PERMISSIONS.get(role, set()):
                return True

    if team_id:
        for membership in context.team_memberships:
            if str(membership.get("team_id")) != team_id:
                continue
            role = str(membership.get("role") or "")
            if permission in TEAM_ROLE_PERMISSIONS.get(role, set()):
                return True

    return False
