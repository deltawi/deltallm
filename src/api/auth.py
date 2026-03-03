from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from src.audit.actions import AuditAction
from src.api.audit import emit_control_audit_event
from src.middleware.platform_auth import SESSION_COOKIE_NAME, get_platform_auth_context
from src.models.errors import RateLimitError
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
_AUTH_INTERNAL_LOGIN_IP_LIMIT_PER_MINUTE = 20
_AUTH_INTERNAL_LOGIN_EMAIL_LIMIT_PER_MINUTE = 10
_AUTH_SSO_CALLBACK_IP_LIMIT_PER_MINUTE = 20


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


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_hop = forwarded_for.split(",", 1)[0].strip()
        if first_hop:
            return first_hop
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def _enforce_auth_rate_limit(
    request: Request,
    *,
    scope: str,
    entity_id: str,
    limit_per_minute: int,
    detail: str,
) -> None:
    limiter = getattr(request.app.state, "limit_counter", None)
    if limiter is None:
        return
    try:
        await limiter.check_rate_limit(scope=scope, entity_id=entity_id, limit=limit_per_minute)
    except RateLimitError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if getattr(exc, "retry_after", None) else None
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers=headers,
        ) from exc


@router.post("/internal/login", response_model=InternalLoginResponse)
async def internal_login(request: Request, payload: InternalLoginRequest) -> Response:
    request_start = perf_counter()
    normalized_email = payload.email.strip().lower()
    try:
        await _enforce_auth_rate_limit(
            request,
            scope="auth_internal_login_ip",
            entity_id=_client_ip(request),
            limit_per_minute=_AUTH_INTERNAL_LOGIN_IP_LIMIT_PER_MINUTE,
            detail="Too many login attempts; please try again later",
        )
        if normalized_email:
            await _enforce_auth_rate_limit(
                request,
                scope="auth_internal_login_email",
                entity_id=normalized_email,
                limit_per_minute=_AUTH_INTERNAL_LOGIN_EMAIL_LIMIT_PER_MINUTE,
                detail="Too many login attempts for this account; please try again later",
            )

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
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_INTERNAL_LOGIN,
            status="success",
            actor_id=login.context.account_id,
            resource_type="session",
            request_payload={"email": normalized_email},
            response_payload={"mfa_required": login.mfa_required},
            critical=True,
        )
        return response
    except Exception as exc:
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_INTERNAL_LOGIN,
            status="error",
            resource_type="session",
            request_payload={"email": normalized_email},
            error=exc,
            critical=True,
        )
        raise


@router.post("/internal/logout")
async def internal_logout(request: Request) -> Response:
    request_start = perf_counter()
    context = get_platform_auth_context(request)
    try:
        service = getattr(request.app.state, "platform_identity_service", None)
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if service is not None and token:
            await service.revoke_session(token)

        response = JSONResponse({"logged_out": True})
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_INTERNAL_LOGOUT,
            status="success",
            actor_id=context.account_id if context is not None else None,
            resource_type="session",
            response_payload={"logged_out": True},
            critical=True,
        )
        return response
    except Exception as exc:
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_INTERNAL_LOGOUT,
            status="error",
            actor_id=context.account_id if context is not None else None,
            resource_type="session",
            error=exc,
            critical=True,
        )
        raise


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
    request_start = perf_counter()
    context = get_platform_auth_context(request)
    try:
        if context is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

        service = getattr(request.app.state, "platform_identity_service", None)
        if service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")

        enrollment = await service.start_mfa_enrollment(context.account_id)
        if enrollment is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to start MFA enrollment")

        secret, otpauth_url = enrollment
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_MFA_ENROLL_START,
            status="success",
            actor_id=context.account_id,
            resource_type="mfa",
            response_payload={"started": True},
            critical=True,
        )
        return MFAStartResponse(secret=secret, otpauth_url=otpauth_url)
    except Exception as exc:
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_MFA_ENROLL_START,
            status="error",
            actor_id=context.account_id if context is not None else None,
            resource_type="mfa",
            error=exc,
            critical=True,
        )
        raise


@router.post("/mfa/enroll/confirm")
async def mfa_enroll_confirm(request: Request, payload: MFAVerifyRequest) -> dict[str, bool]:
    request_start = perf_counter()
    context = get_platform_auth_context(request)
    try:
        if context is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

        service = getattr(request.app.state, "platform_identity_service", None)
        if service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")

        ok = await service.confirm_mfa_enrollment(context.account_id, payload.code)
        if not ok:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MFA code")
        response = {"mfa_enabled": True}
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_MFA_ENROLL_CONFIRM,
            status="success",
            actor_id=context.account_id,
            resource_type="mfa",
            response_payload=response,
            critical=True,
        )
        return response
    except Exception as exc:
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_MFA_ENROLL_CONFIRM,
            status="error",
            actor_id=context.account_id if context is not None else None,
            resource_type="mfa",
            error=exc,
            critical=True,
        )
        raise


@router.post("/internal/change-password")
async def internal_change_password(request: Request, payload: ChangePasswordRequest) -> dict[str, bool]:
    request_start = perf_counter()
    context = get_platform_auth_context(request)
    try:
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
        response = {"changed": True}
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_PASSWORD_CHANGE,
            status="success",
            actor_id=context.account_id,
            resource_type="account",
            response_payload=response,
            critical=True,
        )
        return response
    except Exception as exc:
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_PASSWORD_CHANGE,
            status="error",
            actor_id=context.account_id if context is not None else None,
            resource_type="account",
            request_payload={"current_password": "provided", "new_password": "provided"},
            error=exc,
            critical=True,
        )
        raise


@router.get("/sso-config")
async def sso_config(request: Request):
    handler = getattr(request.app.state, "sso_auth_handler", None)
    if handler is None:
        return {"sso_enabled": False}
    app_config = getattr(request.app.state, "app_config", None)
    provider = str(getattr(getattr(app_config, "general_settings", None), "sso_provider", "oidc"))
    return {"sso_enabled": True, "provider": provider}


_sso_pending_states: dict[str, tuple[float, str]] = {}
_SSO_STATE_TTL_SECONDS = 600


def _cleanup_expired_states() -> None:
    import time
    now = time.time()
    expired = [k for k, v in _sso_pending_states.items() if now - v[0] > _SSO_STATE_TTL_SECONDS]
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
    code_verifier, code_challenge = handler.generate_pkce_pair()
    _sso_pending_states[state] = (time.time(), code_verifier)

    return {"authorize_url": handler.get_authorize_url(state, code_challenge=code_challenge)}


@router.get("/callback")
async def auth_callback(request: Request, code: str = Query(default=""), state: str = Query(default="")) -> Response:
    request_start = perf_counter()
    try:
        handler = getattr(request.app.state, "sso_auth_handler", None)
        if handler is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO is not enabled")

        if not code:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code")

        if not state:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing state")

        _cleanup_expired_states()
        state_entry = _sso_pending_states.get(state)
        if state_entry is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired SSO state")
        _sso_pending_states.pop(state, None)
        await _enforce_auth_rate_limit(
            request,
            scope="auth_sso_callback_ip",
            entity_id=_client_ip(request),
            limit_per_minute=_AUTH_SSO_CALLBACK_IP_LIMIT_PER_MINUTE,
            detail="Too many SSO callback attempts; please try again later",
        )

        _, code_verifier = state_entry
        response_payload = await handler.handle_callback(code, code_verifier=code_verifier)
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
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_SSO_CALLBACK,
            status="success",
            actor_id=getattr(getattr(login, "context", None), "account_id", None),
            resource_type="session",
            request_payload={"provider": provider},
            response_payload={"redirect": "/"},
            critical=True,
        )
        return response
    except Exception as exc:
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_SSO_CALLBACK,
            status="error",
            resource_type="session",
            error=exc,
            critical=True,
        )
        raise
