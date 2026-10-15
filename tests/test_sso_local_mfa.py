from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import GeneralSettings
from src.models.external_auth import ExternalWorkspaceContext
from src.models.platform_auth import PlatformAuthContext
from tests.test_auth import (
    _StubIdentityService,
    _StubSelfRegistrationProvisioner,
    _self_registration_app_config,
    _start_sso_and_callback,
)


def _context(**overrides: object) -> PlatformAuthContext:
    values: dict[str, object] = {
        "account_id": "acct-1",
        "email": "user@example.com",
        "role": "org_user",
    }
    values.update(overrides)
    return PlatformAuthContext(**values)


def test_sso_local_mfa_setting_defaults_and_values() -> None:
    assert GeneralSettings().sso_local_mfa == "optional"
    assert GeneralSettings(sso_local_mfa="trust_idp").sso_local_mfa == "trust_idp"
    for invalid in ("off", False, "required"):
        with pytest.raises(ValidationError):
            GeneralSettings(sso_local_mfa=invalid)


def test_mfa_enrollment_is_suggested_only_for_password_sessions_without_mfa() -> None:
    assert _context().mfa_enrollment_suggested is True
    assert _context(auth_method="sso").mfa_enrollment_suggested is False
    assert _context(mfa_enabled=True).mfa_enrollment_suggested is False
    workspace = ExternalWorkspaceContext.model_construct()
    assert _context(external_workspace=workspace).mfa_enrollment_suggested is False


@pytest.mark.asyncio
async def test_session_endpoint_does_not_prompt_mfa_enrollment_for_sso_sessions(
    client, test_app
) -> None:
    class _ContextIdentityService:
        def __init__(self, context: PlatformAuthContext) -> None:
            self.context = context

        async def get_context_for_session(self, token: str, **_kwargs: object):
            del token
            return self.context

    cookies = {"deltallm_session": "session-token"}
    test_app.state.platform_identity_service = _ContextIdentityService(_context())
    password = await client.get("/auth/me", cookies=cookies)
    test_app.state.platform_identity_service = _ContextIdentityService(_context(auth_method="sso"))
    sso = await client.get("/auth/me", cookies=cookies)

    assert password.status_code == 200
    assert password.json()["mfa_prompt"] is True
    assert sso.status_code == 200
    assert sso.json()["mfa_prompt"] is False


def _with_sso_local_mfa(config, value: str):  # noqa: ANN001, ANN202
    config.general_settings.sso_local_mfa = value
    return config


@pytest.mark.asyncio
@pytest.mark.parametrize(("setting", "expected"), [("optional", False), ("trust_idp", True)])
async def test_sso_callback_passes_idp_mfa_trust_to_every_login_path(
    client, test_app, setting: str, expected: bool
) -> None:
    identity = _StubIdentityService()
    identity.add_account(account_id="acct-existing", email="developer@example.com")
    provisioner = _StubSelfRegistrationProvisioner(identity_service=identity)
    test_app.state.platform_identity_service = identity
    test_app.state.self_registration_provisioning_service = provisioner
    test_app.state.audit_service = None

    test_app.state.app_config = _with_sso_local_mfa(
        _self_registration_app_config(enabled=False), setting
    )
    legacy = await _start_sso_and_callback(
        client=client, test_app=test_app, state="legacy", email="legacy@example.com"
    )
    client.cookies.clear()
    test_app.state.app_config = _with_sso_local_mfa(
        _self_registration_app_config(enabled=True), setting
    )
    existing = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="existing",
        email="developer@example.com",
        subject="existing-subject",
    )
    client.cookies.clear()
    created = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="created",
        email="new@example.com",
        subject="new-subject",
    )

    assert [legacy.status_code, existing.status_code, created.status_code] == [302, 302, 302]
    assert identity.legacy_upserts[0]["trust_idp_mfa"] is expected
    assert identity.trust_idp_mfa_calls == [expected]
    assert provisioner.trust_idp_mfa_calls == [expected]
