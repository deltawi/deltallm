"""HTTP edge checks for the sign-in methods that an operator allows.

`general_settings.password_login_enabled` turns email + password sign-in on or off.
`general_settings.sso_sign_in` decides who can sign in with SSO. The master key always
works, so an operator can still sign in when the identity provider is down.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request, status

from src.auth.sso_identity import SSOIdentityAssertion
from src.auth.sign_in_settings import password_login_enabled, sso_sign_in_settings
from src.models.platform_auth import PlatformAuthContext

if TYPE_CHECKING:
    from src.services.identity.sso_group_access import SSOGroupAccessPlan

PASSWORD_LOGIN_DISABLED_DETAIL = "Password sign-in is turned off. Sign in with SSO."
SSO_EMAIL_DOMAIN_DENIED_DETAIL = "Your email domain is not allowed to sign in with SSO."


def request_general_settings(request: Request) -> Any:
    app_config = getattr(request.app.state, "app_config", None)
    return getattr(app_config, "general_settings", None)


def require_password_login(request: Request) -> None:
    """Refuse a password sign-in or password reset when password sign-in is off."""

    if not password_login_enabled(request_general_settings(request)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=PASSWORD_LOGIN_DISABLED_DETAIL
        )


def session_allowed(general_settings: Any, context: PlatformAuthContext) -> bool:
    """A password session ends as soon as password sign-in is off.

    Trusted Console customer sessions are not operator password sessions, so they stay.
    """

    return (
        password_login_enabled(general_settings)
        or context.auth_method != "password"
        or context.external_workspace is not None
    )


def require_sso_email_admitted(general_settings: Any, identity: SSOIdentityAssertion) -> None:
    """Apply `sso_sign_in.allowed_email_domains` to every SSO sign-in, admins included."""

    settings = sso_sign_in_settings(general_settings)
    if not settings.admits_email(identity.email, email_verified=identity.email_verified):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=SSO_EMAIL_DOMAIN_DENIED_DETAIL
        )


def sso_may_create_account(
    general_settings: Any,
    *,
    is_platform_admin: bool,
    group_access: SSOGroupAccessPlan | None,
) -> bool:
    """Whether this SSO sign-in can create a new account.

    With `allow_new_accounts: false`, the operator still names new people in two ways:
    `sso_admin_email_list` and group sync mappings or admin values. Both arrive here as
    `is_platform_admin` or as grants in `group_access`.
    """

    if sso_sign_in_settings(general_settings).allow_new_accounts:
        return True
    return is_platform_admin or bool(group_access and group_access.has_grants)
