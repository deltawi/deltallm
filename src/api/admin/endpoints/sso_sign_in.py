"""Platform-admin API for the sign-in methods: password sign-in and who can sign in with SSO."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.api.admin.endpoints.common import emit_admin_mutation_audit
from src.audit.actions import AuditAction
from src.auth.email_domains import email_domain
from src.auth.roles import Permission
from src.auth.sign_in_policy import request_general_settings
from src.auth.sign_in_settings import (
    SSOSignInSettings,
    password_login_enabled,
    sso_sign_in_settings,
)
from src.config_runtime.dynamic import (
    DynamicConfigRestartRequiredError,
    DynamicConfigValidationError,
)
from src.middleware.admin import require_admin_permission
from src.middleware.platform_auth import get_platform_auth_context

router = APIRouter(tags=["Admin SSO"])
_PLATFORM_ADMIN = [Depends(require_admin_permission(Permission.PLATFORM_ADMIN))]

CallerSignIn = Literal["password", "sso", "master_key"]


class SignInSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password_login_enabled: bool = True
    sso_sign_in: SSOSignInSettings = Field(default_factory=SSOSignInSettings)


class SignInSettingsResponse(SignInSettingsUpdate):
    sso_available: bool
    self_registration_enabled: bool
    caller_sign_in: CallerSignIn
    caller_email_domain: str | None


def _current(request: Request) -> SignInSettingsUpdate:
    general = request_general_settings(request)
    return SignInSettingsUpdate(
        password_login_enabled=password_login_enabled(general),
        sso_sign_in=sso_sign_in_settings(general),
    )


def _caller(request: Request) -> tuple[CallerSignIn, str | None]:
    context = get_platform_auth_context(request)
    if context is None:
        return "master_key", None
    return context.auth_method, email_domain(context.email)


def _self_registration_enabled(request: Request) -> bool:
    settings = getattr(request_general_settings(request), "self_registration", None)
    return bool(getattr(settings, "enabled", False))


def _response(request: Request, settings: SignInSettingsUpdate) -> SignInSettingsResponse:
    caller_sign_in, caller_domain = _caller(request)
    return SignInSettingsResponse(
        **settings.model_dump(),
        sso_available=getattr(request.app.state, "sso_auth_handler", None) is not None,
        self_registration_enabled=_self_registration_enabled(request),
        caller_sign_in=caller_sign_in,
        caller_email_domain=caller_domain,
    )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message}
    )


def _require_no_lockout(request: Request, payload: SignInSettingsUpdate) -> None:
    """Refuse settings that would lock the admin who saves them out of the Admin UI.

    The master key still works in every case; these checks only catch a mistake early.
    """

    caller_sign_in, caller_domain = _caller(request)
    if not payload.password_login_enabled:
        if getattr(request.app.state, "sso_auth_handler", None) is None:
            raise _conflict(
                "sso_unavailable",
                "SSO is not running, so password sign-in must stay on.",
            )
        if caller_sign_in == "password":
            raise _conflict(
                "password_session",
                "You signed in with a password. Sign in with SSO first, then turn off "
                "password sign-in.",
            )
    domains = payload.sso_sign_in.allowed_email_domains
    if caller_sign_in == "sso" and domains and caller_domain not in domains:
        raise _conflict(
            "own_domain_excluded",
            f"Add your own email domain ({caller_domain}) to the list, or you cannot sign in "
            "with SSO again.",
        )
    if not payload.sso_sign_in.allow_new_accounts and _self_registration_enabled(request):
        raise _conflict(
            "self_registration_enabled",
            "Self-registration creates new accounts. Turn off self_registration in "
            "config.yaml first.",
        )


@router.get(
    "/ui/api/settings/sso-sign-in",
    response_model=SignInSettingsResponse,
    dependencies=_PLATFORM_ADMIN,
)
async def get_sign_in_settings(request: Request) -> SignInSettingsResponse:
    return _response(request, _current(request))


@router.put(
    "/ui/api/settings/sso-sign-in",
    response_model=SignInSettingsResponse,
    dependencies=_PLATFORM_ADMIN,
)
async def update_sign_in_settings(
    request: Request, payload: SignInSettingsUpdate
) -> SignInSettingsResponse:
    request_start = perf_counter()
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    if dynamic_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable"
        )
    _require_no_lockout(request, payload)
    before: dict[str, Any] = _current(request).model_dump(mode="json")
    after: dict[str, Any] = payload.model_dump(mode="json")
    try:
        await dynamic_config.update_config({"general_settings": after}, updated_by="admin_api")
    except DynamicConfigRestartRequiredError as exc:
        raise _conflict("restart_required", str(exc)) from exc
    except DynamicConfigValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_SETTINGS_UPDATE,
        resource_type="sso_sign_in",
        request_payload=after,
        before=before,
        after=after,
    )
    return _response(request, payload)
