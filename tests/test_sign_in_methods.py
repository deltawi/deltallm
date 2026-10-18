from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.auth.roles import PlatformRole
from src.auth.sign_in_settings import SSOSignInSettings
from src.config import AppConfig, GeneralSettings
from src.models.external_auth import ExternalWorkspaceContext
from src.models.platform_auth import PlatformAuthContext
from tests.test_auth import (
    _StubIdentityService,
    _StubSelfRegistrationProvisioner,
    _self_registration_app_config,
    _start_sso_and_callback,
)


def _config(**general: Any) -> AppConfig:
    data = _self_registration_app_config(enabled=False).model_dump()
    data["general_settings"].update({"enable_sso": True, **general})
    return AppConfig.model_validate(data)


def _context(*, auth_method: str = "password", **overrides: Any) -> PlatformAuthContext:
    values: dict[str, Any] = {
        "account_id": "acct-1",
        "email": "admin@example.com",
        "role": PlatformRole.ADMIN,
        "auth_method": auth_method,
    }
    values.update(overrides)
    return PlatformAuthContext(**values)


# --- Settings -----------------------------------------------------------------


def test_sign_in_settings_default_to_open_and_normalize_domains() -> None:
    settings = GeneralSettings()
    assert settings.password_login_enabled is True
    assert settings.sso_sign_in == SSOSignInSettings()
    rules = SSOSignInSettings(allowed_email_domains=["Example.com", " example.com ", "", "b.org"])
    assert rules.allowed_email_domains == ["example.com", "b.org"]
    with pytest.raises(ValidationError, match="bare email domains"):
        SSOSignInSettings(allowed_email_domains=["@example.com"])
    with pytest.raises(ValidationError):
        SSOSignInSettings(allowed_email_domains=["example.com"], unknown=True)


def test_domain_gate_needs_a_verified_email_in_the_list() -> None:
    open_rules = SSOSignInSettings()
    assert open_rules.refusal("a@anything.io", email_verified=None) is None
    rules = SSOSignInSettings(allowed_email_domains=["example.com"])
    assert rules.refusal("A@Example.com", email_verified=True) is None
    assert rules.refusal("a@sub.example.com", email_verified=True) == "email_domain"
    assert rules.refusal("a@other.com", email_verified=None) == "email_domain"
    assert rules.refusal("a@example.com", email_verified=None) == "unverified_email"
    assert rules.refusal("a@example.com", email_verified=False) == "unverified_email"


def test_cross_field_rules_reject_lockout_and_conflicts() -> None:
    with pytest.raises(ValidationError, match="requires enable_sso"):
        GeneralSettings(password_login_enabled=False)
    assert GeneralSettings(password_login_enabled=False, enable_sso=True).enable_sso is True
    with pytest.raises(ValidationError, match="allow_new_accounts"):
        GeneralSettings(
            sso_sign_in={"allow_new_accounts": False},
            self_registration={
                "enabled": True,
                "allowed_domains": ["example.com"],
                "default_org": {"id": "org"},
                "default_team": {"id": "team"},
            },
        )


# --- Password sign-in off -----------------------------------------------------


@pytest.mark.asyncio
async def test_password_endpoints_refuse_when_password_login_is_off(client, test_app) -> None:
    test_app.state.app_config = _config(password_login_enabled=False)

    login = await client.post(
        "/auth/internal/login", json={"email": "a@example.com", "password": "x" * 12}
    )
    forgot = await client.post("/auth/internal/forgot-password", json={"email": "a@example.com"})
    validate = await client.get("/auth/internal/reset-password/some-token")
    reset = await client.post(
        "/auth/internal/reset-password", json={"token": "t", "new_password": "x" * 16}
    )

    for response in (login, forgot, validate, reset):
        assert response.status_code == 403
        assert response.json()["detail"] == "Password sign-in is turned off. Sign in with SSO."


@pytest.mark.asyncio
async def test_sso_config_reports_password_login(client, test_app) -> None:
    test_app.state.sso_auth_handler = None
    test_app.state.app_config = _config(password_login_enabled=False)
    off = await client.get("/auth/sso-config")
    test_app.state.app_config = _config()
    on = await client.get("/auth/sso-config")

    assert off.json()["password_login_enabled"] is False
    assert on.json()["password_login_enabled"] is True


class _ContextIdentityService:
    def __init__(self, context: PlatformAuthContext) -> None:
        self.context = context

    async def get_context_for_session(self, token: str, **_kwargs: object):
        del token
        return self.context


@pytest.mark.parametrize(
    ("context", "allowed"),
    [
        (_context(auth_method="password"), False),
        (_context(auth_method="sso"), True),
        (_context(external_workspace=ExternalWorkspaceContext.model_construct()), True),
    ],
)
@pytest.mark.asyncio
async def test_password_sessions_end_when_password_login_is_off(
    client, test_app, context: PlatformAuthContext, allowed: bool
) -> None:
    test_app.state.platform_identity_service = _ContextIdentityService(context)
    test_app.state.app_config = _config(password_login_enabled=False)

    from src.middleware.platform_auth import attach_platform_auth_context

    request = SimpleNamespace(
        app=test_app,
        state=SimpleNamespace(),
        cookies={"deltallm_session": "session-token"},
        headers={},
    )
    await attach_platform_auth_context(request)  # type: ignore[arg-type]
    assert (request.state.platform_auth is not None) is allowed

    test_app.state.app_config = _config()
    await attach_platform_auth_context(request)  # type: ignore[arg-type]
    assert request.state.platform_auth is context


# --- SSO sign-in rules --------------------------------------------------------


def _sso_app(test_app, **general: Any) -> _StubIdentityService:
    identity = _StubIdentityService()
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = _StubSelfRegistrationProvisioner()
    test_app.state.app_config = _config(**general)
    return identity


@pytest.mark.parametrize(
    ("email", "verified", "reason"),
    [
        ("person@other.com", True, "email domain is not allowed"),
        ("person@other.com", None, "email domain is not allowed"),
        ("person@example.com", None, "did not mark your email as verified"),
        ("person@example.com", False, "did not mark your email as verified"),
    ],
)
@pytest.mark.asyncio
async def test_domain_gate_refuses_before_any_account_change(
    client, test_app, email: str, verified: bool | None, reason: str
) -> None:
    identity = _sso_app(
        test_app,
        sso_sign_in={"allowed_email_domains": ["example.com"]},
        sso_admin_email_list=[email],
    )

    response = await _start_sso_and_callback(
        client=client, test_app=test_app, state="domain", email=email, email_verified=verified
    )

    assert response.status_code == 403
    assert reason in response.json()["detail"]
    assert identity.legacy_upserts == []
    assert identity.login_results == []


@pytest.mark.asyncio
async def test_domain_gate_admits_a_verified_email_in_the_list(client, test_app) -> None:
    identity = _sso_app(test_app, sso_sign_in={"allowed_email_domains": ["example.com"]})

    response = await _start_sso_and_callback(
        client=client, test_app=test_app, state="admit", email="Person@Example.com"
    )

    assert response.status_code == 302
    assert identity.legacy_upserts[0]["allow_create"] is True


@pytest.mark.parametrize(
    ("general", "allow_create"),
    [
        ({"sso_sign_in": {"allow_new_accounts": False}}, False),
        (
            {
                "sso_sign_in": {"allow_new_accounts": False},
                "sso_admin_email_list": ["person@example.com"],
            },
            True,
        ),
        (
            {
                "sso_sign_in": {"allow_new_accounts": False},
                "sso_group_sync": {"enabled": True, "claim": "groups", "admin_values": ["x"]},
            },
            False,
        ),
        ({}, True),
    ],
)
@pytest.mark.asyncio
async def test_new_account_rule_reaches_the_account_transaction(
    client, test_app, general: dict[str, Any], allow_create: bool
) -> None:
    identity = _sso_app(test_app, **general)

    response = await _start_sso_and_callback(
        client=client, test_app=test_app, state="new", email="person@example.com"
    )

    assert response.status_code == 302
    assert identity.legacy_upserts[0]["allow_create"] is allow_create


@pytest.mark.asyncio
async def test_refused_new_account_returns_403(client, test_app) -> None:
    from src.auth.sso_identity import SSOAccountCreationDisabledError

    identity = _sso_app(test_app, sso_sign_in={"allow_new_accounts": False})
    identity.upsert_sso_account = AsyncMock(side_effect=SSOAccountCreationDisabledError())

    response = await _start_sso_and_callback(
        client=client, test_app=test_app, state="refused", email="person@example.com"
    )

    assert response.status_code == 403
    assert response.json()["detail"].startswith("No account exists for this email")


# --- Admin API ----------------------------------------------------------------


def _admin_api(
    test_app, monkeypatch, context: PlatformAuthContext | None, **general: Any
) -> AsyncMock:
    for target in (
        "src.middleware.admin.get_platform_auth_context",
        "src.middleware.platform_auth.get_platform_auth_context",
        "src.api.admin.endpoints.sso_sign_in.get_platform_auth_context",
    ):
        monkeypatch.setattr(target, lambda request: context)
    test_app.state.app_config = _config(**general)
    test_app.state.sso_auth_handler = object()
    update = AsyncMock()
    test_app.state.dynamic_config_manager = SimpleNamespace(update_config=update)
    monkeypatch.setattr(
        "src.api.admin.endpoints.sso_sign_in.emit_admin_mutation_audit", AsyncMock()
    )
    return update


SSO_ONLY = {
    "password_login_enabled": False,
    "sso_sign_in": {"allow_new_accounts": False, "allowed_email_domains": ["example.com"]},
}


@pytest.mark.asyncio
async def test_admin_api_is_platform_admin_only(client, test_app, monkeypatch) -> None:
    update = _admin_api(test_app, monkeypatch, _context(role=PlatformRole.ORG_USER))
    assert (await client.get("/ui/api/settings/sso-sign-in")).status_code == 403
    assert (await client.put("/ui/api/settings/sso-sign-in", json=SSO_ONLY)).status_code == 403
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_api_reads_and_saves_from_an_sso_session(client, test_app, monkeypatch) -> None:
    update = _admin_api(test_app, monkeypatch, _context(auth_method="sso"))

    current = await client.get("/ui/api/settings/sso-sign-in")
    assert current.status_code == 200
    assert current.json() == {
        "password_login_enabled": True,
        "sso_sign_in": {"allow_new_accounts": True, "allowed_email_domains": []},
        "sso_available": True,
        "self_registration_enabled": False,
        "caller_sign_in": "sso",
        "caller_email_domain": "example.com",
    }

    saved = await client.put("/ui/api/settings/sso-sign-in", json=SSO_ONLY)
    assert saved.status_code == 200
    update.assert_awaited_once_with({"general_settings": SSO_ONLY}, updated_by="admin_api")


@pytest.mark.parametrize(
    ("context", "payload", "code"),
    [
        (_context(auth_method="password"), {"password_login_enabled": False}, "password_session"),
        (
            _context(auth_method="sso"),
            {"sso_sign_in": {"allowed_email_domains": ["other.com"]}},
            "own_domain_excluded",
        ),
    ],
)
@pytest.mark.asyncio
async def test_admin_api_refuses_settings_that_lock_the_caller_out(
    client, test_app, monkeypatch, context, payload, code
) -> None:
    update = _admin_api(test_app, monkeypatch, context)

    response = await client.put("/ui/api/settings/sso-sign-in", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == code
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_api_master_key_can_turn_off_passwords_only_with_sso_running(
    client, test_app, monkeypatch
) -> None:
    update = _admin_api(test_app, monkeypatch, None)
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}
    test_app.state.sso_auth_handler = None

    refused = await client.put(
        "/ui/api/settings/sso-sign-in", json={"password_login_enabled": False}, headers=headers
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "sso_unavailable"

    test_app.state.sso_auth_handler = object()
    saved = await client.put(
        "/ui/api/settings/sso-sign-in", json={"password_login_enabled": False}, headers=headers
    )
    assert saved.status_code == 200
    assert saved.json()["caller_sign_in"] == "master_key"
    update.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_api_refuses_closed_sign_up_with_self_registration(
    client, test_app, monkeypatch
) -> None:
    update = _admin_api(test_app, monkeypatch, _context(auth_method="sso"))
    test_app.state.app_config = _self_registration_app_config(enabled=True)

    response = await client.put(
        "/ui/api/settings/sso-sign-in", json={"sso_sign_in": {"allow_new_accounts": False}}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "self_registration_enabled"
    update.assert_not_awaited()
