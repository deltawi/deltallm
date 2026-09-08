from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from src.services.platform_identity_service import PlatformIdentityService
from tests.services.test_platform_identity_service import TransactionalFakePlatformIdentityDB
from tests.test_auth import _self_registration_app_config, _start_sso_and_callback


@pytest.mark.asyncio
@pytest.mark.parametrize("self_registration", [False, True])
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize(
    ("initial_role", "edited_role", "listed"),
    [("org_user", "platform_admin", False), ("platform_admin", "org_user", True)],
)
async def test_people_access_role_edit_survives_sso_login(
    client,
    test_app,
    self_registration: bool,
    linked: bool,
    initial_role: str,
    edited_role: str,
    listed: bool,
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="person@example.com", role=initial_role)
    service = PlatformIdentityService(db_client=db, salt="test-salt")
    test_app.state.platform_identity_service = service
    test_app.state.prisma_manager = SimpleNamespace(client=db)
    config = _self_registration_app_config(
        enabled=self_registration,
        admin_emails=["person@example.com"] if listed else [],
    )
    config.general_settings.master_key = "test-master-key"
    test_app.state.app_config = config
    if linked:
        await service.link_sso_identity(
            account_id="acct-1", email="person@example.com", provider="oidc", subject="subject-1"
        )

    saved = await client.post(
        "/ui/api/rbac/accounts",
        headers={"X-Master-Key": "test-master-key"},
        json={"email": "person@example.com", "role": edited_role, "is_active": True},
    )
    assert saved.status_code == 200
    assert saved.json()["role"] == edited_role

    for attempt in range(2):
        response = await _start_sso_and_callback(
            client=client,
            test_app=test_app,
            state=f"role-edit-{attempt}",
            email="Person@Example.com",
        )
        assert response.status_code == 302
        assert db.accounts["acct-1"]["role"] == edited_role
        me = await client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == edited_role


@pytest.mark.asyncio
@pytest.mark.parametrize("self_registration", [False, True])
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("listed", [False, True])
async def test_sso_login_cannot_reactivate_disabled_account(
    client,
    test_app,
    self_registration: bool,
    linked: bool,
    listed: bool,
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="person@example.com", is_active=False)
    service = PlatformIdentityService(db_client=db, salt="test-salt")
    test_app.state.platform_identity_service = service
    test_app.state.app_config = _self_registration_app_config(
        enabled=self_registration,
        admin_emails=["person@example.com"] if listed else [],
    )
    if linked:
        await service.link_sso_identity(
            account_id="acct-1", email="person@example.com", provider="oidc", subject="subject-1"
        )
    identities_before = dict(db.identities)

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="disabled-account",
        email="person@example.com",
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Account is inactive"
    assert db.accounts["acct-1"]["is_active"] is False
    assert db.accounts["acct-1"]["role"] == "org_user"
    assert db.identities == identities_before
    assert db.sessions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("email_verified", [False, None])
async def test_unverified_new_subject_cannot_claim_existing_admin(
    client, test_app, email_verified: bool | None
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="admin", email="admin@example.com", role="platform_admin")
    before = copy.deepcopy(db.accounts)
    test_app.state.platform_identity_service = PlatformIdentityService(db, salt="test-salt")
    test_app.state.app_config = _self_registration_app_config(enabled=False, admin_emails=[])

    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="unverified-claim",
        email="admin@example.com",
        subject="unknown-subject",
        email_verified=email_verified,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "SSO email is not verified"
    assert "deltallm_session=" not in response.headers.get("set-cookie", "")
    assert db.accounts == before
    assert db.identities == {}
    assert db.sessions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("self_registration", [False, True])
@pytest.mark.parametrize("listed", [False, True])
@pytest.mark.parametrize("role", ["platform_admin", "org_user"])
async def test_signup_verification_setting_cannot_bypass_existing_account_ownership(
    client,
    test_app,
    self_registration: bool,
    listed: bool,
    role: str,
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="person", email="person@example.com", role=role)
    service = PlatformIdentityService(db, salt="test-salt")
    await service.link_sso_identity(
        account_id="person", email="person@example.com", provider="oidc", subject="original"
    )
    before = copy.deepcopy(db.identities)
    test_app.state.platform_identity_service = service
    config = _self_registration_app_config(
        enabled=self_registration,
        admin_emails=["person@example.com"] if listed else [],
    )
    config.general_settings.self_registration.require_email_verification = False
    test_app.state.app_config = config
    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="additional-unverified-identity",
        email="person@example.com",
        subject="unknown",
        email_verified=False,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "SSO email is not verified"
    assert db.identities == before
    assert db.accounts["person"]["role"] == role
    assert db.sessions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("self_registration", [False, True])
async def test_email_fallback_existing_binding_requires_verification(
    client,
    test_app,
    self_registration: bool,
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="person", email="person@example.com", role="platform_admin")
    service = PlatformIdentityService(db, salt="test-salt")
    await service.link_sso_identity(
        account_id="person",
        email="person@example.com",
        provider="oidc",
        subject="person@example.com",
    )
    test_app.state.platform_identity_service = service
    test_app.state.app_config = _self_registration_app_config(enabled=self_registration)
    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="fallback-binding",
        email="person@example.com",
        subject="person@example.com",
        subject_source="email",
        email_verified=False,
    )
    assert response.status_code == 403
    assert db.sessions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("self_registration", [False, True])
async def test_established_subject_with_missing_verification_keeps_admin_role(
    client,
    test_app,
    self_registration: bool,
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="person", email="person@example.com", role="platform_admin")
    service = PlatformIdentityService(db, salt="test-salt")
    await service.link_sso_identity(
        account_id="person", email="person@example.com", provider="oidc", subject="subject-1"
    )
    test_app.state.platform_identity_service = service
    test_app.state.app_config = _self_registration_app_config(enabled=self_registration)
    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="established-subject",
        email="person@example.com",
        email_verified=None,
    )
    assert response.status_code == 302
    me = await client.get("/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "platform_admin"


@pytest.mark.asyncio
async def test_self_registration_ownership_denial_is_not_a_provisioning_error(
    client, test_app
) -> None:
    from src.auth.sso_identity import SSOIdentityOwnershipError
    from tests.test_auth import _StubSelfRegistrationProvisioner

    db = TransactionalFakePlatformIdentityDB()
    test_app.state.platform_identity_service = PlatformIdentityService(db, salt="test-salt")
    test_app.state.self_registration_provisioning_service = _StubSelfRegistrationProvisioner(
        error=SSOIdentityOwnershipError()
    )
    config = _self_registration_app_config(enabled=True)
    config.general_settings.self_registration.require_email_verification = False
    test_app.state.app_config = config
    response = await _start_sso_and_callback(
        client=client,
        test_app=test_app,
        state="provisioning-race-denial",
        email="person@example.com",
        email_verified=False,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "SSO email is not verified"
    assert db.sessions == {}
