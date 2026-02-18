from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from src.middleware.platform_auth import SESSION_COOKIE_NAME, get_platform_auth_context
from src.models.platform_auth import (
    CurrentSessionResponse,
    InternalLoginRequest,
    InternalLoginResponse,
    MFAStartResponse,
    MFAVerifyRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        secure=False,
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


@router.get("/login")
async def auth_login(request: Request, state: str = Query(default="")):
    handler = getattr(request.app.state, "sso_auth_handler", None)
    if handler is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO is not enabled")

    if not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing state")

    return {"authorize_url": handler.get_authorize_url(state)}


@router.get("/callback")
async def auth_callback(request: Request, code: str = Query(default=""), state: str | None = Query(default=None)) -> Response:
    handler = getattr(request.app.state, "sso_auth_handler", None)
    if handler is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO is not enabled")

    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code")

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
    )
    if login is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to establish session")

    ttl = int(getattr(getattr(app_config, "general_settings", None), "auth_session_ttl_hours", 12) * 3600)
    response_data = {
        "account_id": login.context.account_id,
        "email": login.context.email,
        "role": login.context.role,
        "mfa_enabled": login.context.mfa_enabled,
        "mfa_prompt": login.mfa_prompt,
    }
    if state is not None:
        response_data["state"] = state

    response = JSONResponse(status_code=status.HTTP_200_OK, content=response_data)
    _set_session_cookie(response, login.session_token, ttl)
    return response
