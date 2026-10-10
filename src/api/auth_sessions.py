from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from src.auth.roles import PLATFORM_ROLE_PERMISSIONS, PlatformRole
from src.middleware.external_auth import require_unmixed_external_auth, external_session_unavailable
from src.middleware.platform_auth import get_master_session_status, get_platform_auth_context
from src.models.external_auth import ExternalWorkspaceResponse
from src.models.platform_auth import CurrentSessionResponse
from src.services.identity.master_session_service import (
    MASTER_SESSION_COOKIE_NAME,
    MasterSessionStatus,
)
from src.services.ui.ui_authorization import build_ui_access, effective_permissions_for_context

router = APIRouter(tags=["auth"])
_AUTH_SERVICE_UNAVAILABLE_HEADERS = {
    "Cache-Control": "no-store",
    "Retry-After": "5",
    "Vary": "Cookie",
}


@router.get("/me", response_model=CurrentSessionResponse)
async def auth_me(request: Request, response: Response) -> CurrentSessionResponse:
    require_unmixed_external_auth(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Cookie"
    master_session_status = get_master_session_status(request)
    if master_session_status == MasterSessionStatus.INVALID:
        response.delete_cookie(MASTER_SESSION_COOKIE_NAME, path="/")
    if master_session_status == MasterSessionStatus.ACTIVE:
        effective_permissions = sorted(PLATFORM_ROLE_PERMISSIONS.get(PlatformRole.ADMIN, set()))
        return CurrentSessionResponse(
            authenticated=True,
            auth_mode="master_key",
            role=PlatformRole.ADMIN,
            effective_permissions=effective_permissions,
            ui_access=build_ui_access(
                authenticated=True,
                effective_permissions=effective_permissions,
                organization_memberships=[],
            ),
        )

    context = get_platform_auth_context(request)
    if context is None:
        if master_session_status == MasterSessionStatus.UNAVAILABLE or external_session_unavailable(
            request
        ):
            raise HTTPException(
                status_code=503,
                detail="Authentication service unavailable",
                headers=_AUTH_SERVICE_UNAVAILABLE_HEADERS,
            )
        return CurrentSessionResponse(authenticated=False)

    effective_permissions = effective_permissions_for_context(context)
    organization_memberships = [dict(item) for item in (context.organization_memberships or [])]
    team_memberships = [dict(item) for item in (context.team_memberships or [])]
    general_settings = getattr(
        getattr(request.app.state, "app_config", None), "general_settings", None
    )
    spend_reporting_v2_enabled = bool(
        getattr(general_settings, "spend_reporting_v2_enabled", False)
    )
    return CurrentSessionResponse(
        authenticated=True,
        auth_mode="session",
        account_id=context.account_id,
        email=context.email,
        role=context.role,
        effective_permissions=effective_permissions,
        ui_access=build_ui_access(
            authenticated=True,
            effective_permissions=effective_permissions,
            organization_memberships=organization_memberships,
            spend_reporting_v2_enabled=spend_reporting_v2_enabled,
            external_customer=context.external_workspace is not None,
        ),
        organization_memberships=organization_memberships,
        team_memberships=team_memberships,
        mfa_enabled=context.mfa_enabled,
        mfa_verified=context.mfa_verified,
        mfa_prompt=not context.mfa_enabled and context.external_workspace is None,
        force_password_change=context.force_password_change,
        session_source="external_customer" if context.external_workspace else "operator",
        expires_at=context.session_expires_at,
        workspace=ExternalWorkspaceResponse.model_validate(
            context.external_workspace.model_dump(
                include={
                    "integration_id",
                    "binding_id",
                    "organization_id",
                    "team_id",
                    "inference_user_id",
                }
            )
        )
        if context.external_workspace is not None
        else None,
    )
