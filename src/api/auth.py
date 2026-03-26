from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from src.audit.actions import AuditAction
from src.api.audit import emit_control_audit_event
from src.db.email import EmailOutboxRepository
from src.db.email_feedback import EmailFeedbackRepository
from src.middleware.platform_auth import SESSION_COOKIE_NAME, get_platform_auth_context
from src.models.errors import RateLimitError
from src.auth.roles import TeamRole
from src.db.email_tokens import EmailTokenRepository
from src.models.platform_auth import (
    ChangePasswordRequest,
    CurrentSessionResponse,
    ForgotPasswordRequest,
    InternalLoginRequest,
    InternalLoginResponse,
    InvitationAcceptRequest,
    InvitationAcceptResponse,
    InvitationTokenResponse,
    MFAStartResponse,
    MFAVerifyRequest,
    ResetPasswordRequest,
    ResetPasswordTokenResponse,
)
from src.services.ui_authorization import build_ui_access, effective_permissions_for_context
from src.services.sso_state_store import SSOStateStoreError

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)
_AUTH_INTERNAL_LOGIN_IP_LIMIT_PER_MINUTE = 20
_AUTH_INTERNAL_LOGIN_EMAIL_LIMIT_PER_MINUTE = 10
_AUTH_SSO_CALLBACK_IP_LIMIT_PER_MINUTE = 20
_AUTH_FORGOT_PASSWORD_IP_LIMIT_PER_MINUTE = 10
_AUTH_FORGOT_PASSWORD_EMAIL_LIMIT_PER_MINUTE = 5
_AUTH_TOKEN_VALIDATE_IP_LIMIT_PER_MINUTE = 30
_AUTH_RESET_PASSWORD_IP_LIMIT_PER_MINUTE = 10
_AUTH_MFA_VERIFY_IP_LIMIT_PER_MINUTE = 20


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


def _token_service_for_db(service: Any, db_client: Any) -> Any:
    if hasattr(service, "with_repository"):
        return service.with_repository(EmailTokenRepository(db_client))
    return service


def _identity_service_for_db(service: Any, db_client: Any) -> Any:
    if hasattr(service, "with_db"):
        return service.with_db(db_client)
    return service


def _outbox_service_for_db(service: Any, db_client: Any) -> Any:
    if hasattr(service, "with_repository"):
        return service.with_repository(
            EmailOutboxRepository(db_client),
            feedback_repository=EmailFeedbackRepository(db_client),
        )
    return service


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

    effective_permissions = effective_permissions_for_context(context)
    return CurrentSessionResponse(
        authenticated=True,
        account_id=context.account_id,
        email=context.email,
        role=context.role,
        effective_permissions=effective_permissions,
        ui_access=build_ui_access(
            authenticated=True,
            effective_permissions=effective_permissions,
        ),
        organization_memberships=[dict(item) for item in (context.organization_memberships or [])],
        team_memberships=[dict(item) for item in (context.team_memberships or [])],
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
        session_token = request.cookies.get(SESSION_COOKIE_NAME)
        if session_token:
            await service.mark_session_mfa_verified(session_token)
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


@router.post("/mfa/verify")
async def mfa_verify(request: Request, payload: MFAVerifyRequest) -> dict[str, bool]:
    request_start = perf_counter()
    context = get_platform_auth_context(request)
    try:
        await _enforce_auth_rate_limit(
            request,
            scope="auth_mfa_verify_ip",
            entity_id=_client_ip(request),
            limit_per_minute=_AUTH_MFA_VERIFY_IP_LIMIT_PER_MINUTE,
            detail="Too many MFA verification attempts; please try again later",
        )
        if context is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

        service = getattr(request.app.state, "platform_identity_service", None)
        if service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")
        if not context.mfa_enabled:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not enabled for this account")
        if context.mfa_verified:
            return {"mfa_verified": True}

        session_token = request.cookies.get(SESSION_COOKIE_NAME)
        if not session_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

        ok = await service.verify_mfa_for_session(session_token=session_token, code=payload.code)
        if not ok:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MFA code")
        response = {"mfa_verified": True}
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_MFA_VERIFY,
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
            action=AuditAction.AUTH_MFA_VERIFY,
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

        service = getattr(request.app.state, "platform_identity_service", None)
        if service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")
        try:
            service.validate_password_policy(payload.new_password)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

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


@router.get("/invitations/{token}", response_model=InvitationTokenResponse)
async def get_invitation_token(request: Request, token: str) -> InvitationTokenResponse:
    await _enforce_auth_rate_limit(
        request,
        scope="auth_invitation_validate_ip",
        entity_id=_client_ip(request),
        limit_per_minute=_AUTH_TOKEN_VALIDATE_IP_LIMIT_PER_MINUTE,
        detail="Too many invitation validation attempts; please try again later",
    )
    service = getattr(request.app.state, "invitation_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invitation service unavailable")
    invitation = await service.describe_invitation_token(token)
    if invitation is None:
        return InvitationTokenResponse(valid=False)
    return InvitationTokenResponse(
        valid=True,
        invitation_id=invitation.invitation_id,
        email=invitation.email,
        status=invitation.status,
        invite_scope_type=invitation.invite_scope_type,
        inviter_email=invitation.inviter_email,
        expires_at=invitation.expires_at,
        metadata=invitation.metadata or {},
        password_required=invitation.password_required,
    )


@router.post("/invitations/accept", response_model=InvitationAcceptResponse)
async def accept_invitation(request: Request, payload: InvitationAcceptRequest) -> Response:
    request_start = perf_counter()
    try:
        await _enforce_auth_rate_limit(
            request,
            scope="auth_invitation_accept_ip",
            entity_id=_client_ip(request),
            limit_per_minute=_AUTH_RESET_PASSWORD_IP_LIMIT_PER_MINUTE,
            detail="Too many invitation attempts; please try again later",
        )
        service = getattr(request.app.state, "invitation_service", None)
        if service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invitation service unavailable")

        try:
            login = await service.accept_invitation(raw_token=payload.token, password=payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if login is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation is invalid or expired")

        ttl_hours = getattr(getattr(request.app.state, "app_config", None), "general_settings", None)
        ttl = int(getattr(ttl_hours, "auth_session_ttl_hours", 12) * 3600)
        response_payload = InvitationAcceptResponse(
            accepted=True,
            session_established=login.session_established,
            next_step=login.next_step,
            account_id=login.account_id,
            email=login.email,
            role=login.role,
            mfa_enabled=login.mfa_enabled,
            mfa_required=login.mfa_required,
            mfa_prompt=login.mfa_prompt,
            force_password_change=login.force_password_change,
        ).model_dump()
        response = JSONResponse(status_code=status.HTTP_200_OK, content=response_payload)
        if login.session_established and login.session_token:
            _set_session_cookie(response, login.session_token, ttl)
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_INVITATION_ACCEPT,
            status="success",
            actor_id=login.account_id,
            resource_type="invitation",
            response_payload={"accepted": True, "session_established": login.session_established, "next_step": login.next_step},
            critical=True,
        )
        return response
    except Exception as exc:
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_INVITATION_ACCEPT,
            status="error",
            resource_type="invitation",
            error=exc,
            critical=True,
        )
        raise


@router.post("/internal/forgot-password")
async def forgot_password(request: Request, payload: ForgotPasswordRequest) -> dict[str, bool]:
    request_start = perf_counter()
    normalized_email = payload.email.strip().lower()
    generic_response = {"requested": True}
    try:
        await _enforce_auth_rate_limit(
            request,
            scope="auth_forgot_password_ip",
            entity_id=_client_ip(request),
            limit_per_minute=_AUTH_FORGOT_PASSWORD_IP_LIMIT_PER_MINUTE,
            detail="Too many password reset requests; please try again later",
        )
        if normalized_email:
            await _enforce_auth_rate_limit(
                request,
                scope="auth_forgot_password_email",
                entity_id=normalized_email,
                limit_per_minute=_AUTH_FORGOT_PASSWORD_EMAIL_LIMIT_PER_MINUTE,
                detail="Too many password reset requests for this account; please try again later",
            )

        identity_service = getattr(request.app.state, "platform_identity_service", None)
        token_service = getattr(request.app.state, "email_token_service", None)
        outbox_service = getattr(request.app.state, "email_outbox_service", None)
        if identity_service is None or token_service is None or outbox_service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")
        app_config = getattr(request.app.state, "app_config", None)
        general_settings = getattr(app_config, "general_settings", None)
        instance_name = str(getattr(general_settings, "instance_name", "DeltaLLM") or "DeltaLLM")

        account = await identity_service.get_account_by_email(normalized_email)
        if account and account.get("password_hash"):
            account_id = str(account.get("account_id") or "")
            db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
            try:
                if db is not None and hasattr(db, "tx"):
                    async with db.tx() as tx:
                        tx_token_service = _token_service_for_db(token_service, tx)
                        tx_outbox_service = _outbox_service_for_db(outbox_service, tx)
                        token_issue = await tx_token_service.issue_password_reset_token(
                            account_id=account_id,
                            email=normalized_email,
                        )
                        queued = await tx_outbox_service.enqueue_template_email(
                            template_key="reset_password",
                            to_addresses=(normalized_email,),
                            payload_json={
                                "instance_name": instance_name,
                                "reset_url": tx_token_service.build_action_url(path="/reset-password", raw_token=token_issue.raw_token),
                            },
                            kind="transactional",
                        )
                        if queued.status != "queued":
                            raise ValueError("password reset email cannot be delivered to the requested recipient")
                        await tx_token_service.invalidate_active_tokens(
                            purpose="password_reset",
                            account_id=account_id,
                            exclude_token_id=token_issue.record.token_id,
                        )
                else:
                    token_issue = await token_service.issue_password_reset_token(
                        account_id=account_id,
                        email=normalized_email,
                    )
                    try:
                        queued = await outbox_service.enqueue_template_email(
                            template_key="reset_password",
                            to_addresses=(normalized_email,),
                            payload_json={
                                "instance_name": instance_name,
                                "reset_url": token_service.build_action_url(path="/reset-password", raw_token=token_issue.raw_token),
                            },
                            kind="transactional",
                        )
                        if queued.status != "queued":
                            raise ValueError("password reset email cannot be delivered to the requested recipient")
                    except Exception:
                        await token_service.consume_token(token_id=token_issue.record.token_id)
                        raise
                    await token_service.invalidate_active_tokens(
                        purpose="password_reset",
                        account_id=account_id,
                        exclude_token_id=token_issue.record.token_id,
                    )
            except Exception:
                logger.warning(
                    "failed to queue password reset email",
                    extra={"account_id": account_id, "email": normalized_email},
                    exc_info=True,
                )
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_PASSWORD_RESET_REQUEST,
            status="success",
            resource_type="account",
            request_payload={"email": normalized_email},
            response_payload=generic_response,
            critical=True,
        )
        return generic_response
    except Exception as exc:
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_PASSWORD_RESET_REQUEST,
            status="error",
            resource_type="account",
            request_payload={"email": normalized_email},
            error=exc,
            critical=True,
        )
        raise


@router.get("/internal/reset-password/{token}", response_model=ResetPasswordTokenResponse)
async def get_reset_password_token(request: Request, token: str) -> ResetPasswordTokenResponse:
    await _enforce_auth_rate_limit(
        request,
        scope="auth_reset_password_validate_ip",
        entity_id=_client_ip(request),
        limit_per_minute=_AUTH_TOKEN_VALIDATE_IP_LIMIT_PER_MINUTE,
        detail="Too many reset validation attempts; please try again later",
    )
    token_service = getattr(request.app.state, "email_token_service", None)
    identity_service = getattr(request.app.state, "platform_identity_service", None)
    if token_service is None or identity_service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")
    token_record = await token_service.validate_token(purpose="password_reset", raw_token=token)
    if token_record is None:
        return ResetPasswordTokenResponse(valid=False)
    account = await identity_service.get_account_by_id(token_record.account_id)
    return ResetPasswordTokenResponse(
        valid=True,
        email=str((account or {}).get("email") or token_record.email),
        expires_at=token_record.expires_at,
    )


@router.post("/internal/reset-password")
async def reset_password(request: Request, payload: ResetPasswordRequest) -> dict[str, bool]:
    request_start = perf_counter()
    try:
        await _enforce_auth_rate_limit(
            request,
            scope="auth_reset_password_ip",
            entity_id=_client_ip(request),
            limit_per_minute=_AUTH_RESET_PASSWORD_IP_LIMIT_PER_MINUTE,
            detail="Too many reset attempts; please try again later",
        )
        token_service = getattr(request.app.state, "email_token_service", None)
        identity_service = getattr(request.app.state, "platform_identity_service", None)
        if token_service is None or identity_service is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")
        try:
            identity_service.validate_password_policy(payload.new_password)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
        if db is not None and hasattr(db, "tx"):
            async with db.tx() as tx:
                tx_token_service = _token_service_for_db(token_service, tx)
                tx_identity_service = _identity_service_for_db(identity_service, tx)
                token_record = await tx_token_service.claim_token(purpose="password_reset", raw_token=payload.token)
                if token_record is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid or expired")
                await tx_identity_service.set_password(account_id=token_record.account_id, new_password=payload.new_password)
                await tx_token_service.invalidate_active_tokens(
                    purpose="password_reset",
                    account_id=token_record.account_id,
                )
                await tx_identity_service.revoke_all_sessions_for_account(token_record.account_id)
        else:
            token_record = await token_service.claim_token(purpose="password_reset", raw_token=payload.token)
            if token_record is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid or expired")
            await identity_service.set_password(account_id=token_record.account_id, new_password=payload.new_password)
            await token_service.invalidate_active_tokens(
                purpose="password_reset",
                account_id=token_record.account_id,
            )
            await identity_service.revoke_all_sessions_for_account(token_record.account_id)

        response = {"changed": True}
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_PASSWORD_RESET_COMPLETE,
            status="success",
            actor_id=token_record.account_id,
            resource_type="account",
            response_payload=response,
            critical=True,
        )
        return response
    except Exception as exc:
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUTH_PASSWORD_RESET_COMPLETE,
            status="error",
            resource_type="account",
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


@router.get("/login")
async def auth_login(request: Request, state: str = Query(default="")):
    handler = getattr(request.app.state, "sso_auth_handler", None)
    state_store = getattr(request.app.state, "sso_state_store", None)
    if handler is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO is not enabled")
    if state_store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO state storage unavailable")

    if not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing state")

    code_verifier, code_challenge = handler.generate_pkce_pair()
    try:
        await state_store.store_code_verifier(state=state, code_verifier=code_verifier)
    except SSOStateStoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO state storage unavailable") from exc

    return {"authorize_url": handler.get_authorize_url(state, code_challenge=code_challenge)}


@router.get("/callback")
async def auth_callback(request: Request, code: str = Query(default=""), state: str = Query(default="")) -> Response:
    request_start = perf_counter()
    try:
        handler = getattr(request.app.state, "sso_auth_handler", None)
        state_store = getattr(request.app.state, "sso_state_store", None)
        if handler is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO is not enabled")
        if state_store is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO state storage unavailable")

        if not code:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code")

        if not state:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing state")

        await _enforce_auth_rate_limit(
            request,
            scope="auth_sso_callback_ip",
            entity_id=_client_ip(request),
            limit_per_minute=_AUTH_SSO_CALLBACK_IP_LIMIT_PER_MINUTE,
            detail="Too many SSO callback attempts; please try again later",
        )
        try:
            code_verifier = await state_store.pop_code_verifier(state=state)
        except SSOStateStoreError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO state storage unavailable") from exc
        if code_verifier is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired SSO state")

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
