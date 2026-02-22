from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from src.middleware.platform_auth import SESSION_COOKIE_NAME, get_platform_auth_context
from src.auth.roles import TeamRole
from src.models.platform_auth import (
    ChangePasswordRequest,
    CurrentSessionResponse,
    InternalLoginRequest,
    InternalLoginResponse,
    MFAStartResponse,
    MFAVerifyRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _is_production() -> bool:
    import os
    return os.getenv("REPL_SLUG") is not None or os.getenv("REPLIT_DEPLOYMENT") == "1"


def _set_session_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    is_prod = _is_production()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        path="/",
    )


@router.post("/internal/login", response_model=InternalLoginResponse)
async def internal_login(request: Request, payload: InternalLoginRequest) -> Response:
    service = getattr(request.app.state, "platform_identity_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")

    login = await service.login_internal(email=payload.email, password=payload.password, mfa_code=payload.mfa_code)
    if login is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials or MFA code")

    ttl_hours = getattr(getattr(request.app.state, "app_config", None), "general_settings", None)
    ttl = int(getattr(ttl_hours, "auth_session_ttl_hours", 12) * 3600)

    response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content=InternalLoginResponse(
            account_id=login.context.account_id,
            email=login.context.email,
            role=login.context.role,
            mfa_enabled=login.context.mfa_enabled,
            mfa_required=login.mfa_required,
            mfa_prompt=login.mfa_prompt,
            force_password_change=login.context.force_password_change,
        ).model_dump(),
    )
    _set_session_cookie(response, login.session_token, ttl)
    return response


@router.post("/internal/logout")
async def internal_logout(request: Request) -> Response:
    service = getattr(request.app.state, "platform_identity_service", None)
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if service is not None and token:
        await service.revoke_session(token)

    response = JSONResponse({"logged_out": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@router.get("/me", response_model=CurrentSessionResponse)
async def auth_me(request: Request) -> CurrentSessionResponse:
    context = get_platform_auth_context(request)
    if context is None:
        return CurrentSessionResponse(authenticated=False)

    return CurrentSessionResponse(
        authenticated=True,
        account_id=context.account_id,
        email=context.email,
        role=context.role,
        mfa_enabled=context.mfa_enabled,
        mfa_verified=context.mfa_verified,
        mfa_prompt=not context.mfa_enabled,
        force_password_change=context.force_password_change,
    )


@router.post("/mfa/enroll/start", response_model=MFAStartResponse)
async def mfa_enroll_start(request: Request) -> MFAStartResponse:
    context = get_platform_auth_context(request)
    if context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    service = getattr(request.app.state, "platform_identity_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")

    enrollment = await service.start_mfa_enrollment(context.account_id)
    if enrollment is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to start MFA enrollment")

    secret, otpauth_url = enrollment
    return MFAStartResponse(secret=secret, otpauth_url=otpauth_url)


@router.post("/mfa/enroll/confirm")
async def mfa_enroll_confirm(request: Request, payload: MFAVerifyRequest) -> dict[str, bool]:
    context = get_platform_auth_context(request)
    if context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    service = getattr(request.app.state, "platform_identity_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")

    ok = await service.confirm_mfa_enrollment(context.account_id, payload.code)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MFA code")

    return {"mfa_enabled": True}


@router.post("/internal/change-password")
async def internal_change_password(request: Request, payload: ChangePasswordRequest) -> dict[str, bool]:
    context = get_platform_auth_context(request)
    if context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    if len(payload.new_password) < 12:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="new_password must be at least 12 characters")

    service = getattr(request.app.state, "platform_identity_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")

    ok = await service.change_password(
        account_id=context.account_id,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid current password")

    return {"changed": True}


@router.get("/sso-config")
async def sso_config(request: Request):
    handler = getattr(request.app.state, "sso_auth_handler", None)
    if handler is None:
        return {"sso_enabled": False}
    app_config = getattr(request.app.state, "app_config", None)
    provider = str(getattr(getattr(app_config, "general_settings", None), "sso_provider", "oidc"))
    return {"sso_enabled": True, "provider": provider}


_sso_pending_states: dict[str, float] = {}
_SSO_STATE_TTL_SECONDS = 600


def _cleanup_expired_states() -> None:
    import time
    now = time.time()
    expired = [k for k, v in _sso_pending_states.items() if now - v > _SSO_STATE_TTL_SECONDS]
    for k in expired:
        _sso_pending_states.pop(k, None)


@router.get("/login")
async def auth_login(request: Request, state: str = Query(default="")):
    import time
    handler = getattr(request.app.state, "sso_auth_handler", None)
    if handler is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO is not enabled")

    if not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing state")

    _cleanup_expired_states()
    _sso_pending_states[state] = time.time()

    return {"authorize_url": handler.get_authorize_url(state)}


@router.get("/callback")
async def auth_callback(request: Request, code: str = Query(default=""), state: str = Query(default="")) -> Response:
    handler = getattr(request.app.state, "sso_auth_handler", None)
    if handler is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO is not enabled")

    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code")

    if not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing state")

    _cleanup_expired_states()
    if state not in _sso_pending_states:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired SSO state")
    _sso_pending_states.pop(state, None)

    response_payload = await handler.handle_callback(code)
    email = response_payload.get("email")
    if not isinstance(email, str) or not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid SSO email")

    app_config = getattr(request.app.state, "app_config", None)
    admins = set(getattr(getattr(app_config, "general_settings", None), "sso_admin_email_list", []) or [])
    identity_service = getattr(request.app.state, "platform_identity_service", None)

    if identity_service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")

    provider = str(getattr(getattr(app_config, "general_settings", None), "sso_provider", "sso"))
    subject = response_payload.get("user_id") or response_payload.get("email")
    login = await identity_service.upsert_sso_account(
        email=email,
        is_platform_admin=email in admins,
        provider=provider,
        subject=str(subject) if subject else None,
        team_id=response_payload.get("team_id"),
        default_team_role=TeamRole.VIEWER,
    )
    if login is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to establish session")

    ttl = int(getattr(getattr(app_config, "general_settings", None), "auth_session_ttl_hours", 12) * 3600)

    response = RedirectResponse(url="/", status_code=302)
    _set_session_cookie(response, login.session_token, ttl)
    return response
