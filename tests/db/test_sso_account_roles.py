from __future__ import annotations

from src.auth.sso_identity import SSOIdentityAssertion, SSOSubjectSource

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
import os
from uuid import uuid4

import pytest
from prisma import Prisma

from src.services.platform_identity_service import (
    AccountInactiveError,
    LoginResult,
    LoginSessionCreationError,
    PlatformIdentityService,
)
from src.auth.sso_identity import SSOIdentityOwnershipError
from src.services.self_registration_provisioning import SelfRegistrationProvisioningService
from tests.services.test_self_registration_provisioning import _enabled_settings

pytestmark = pytest.mark.postgres


@dataclass
class SSODatabases:
    login: Prisma
    writer: Prisma
    email: str
    other_email: str
    subject: str
    organization_id: str
    team_id: str


@pytest.fixture
async def databases() -> AsyncIterator[SSODatabases]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is required for PostgreSQL SSO tests")
    login = Prisma(datasource={"url": url})
    writer = Prisma(datasource={"url": url})
    suffix = uuid4().hex
    context = SSODatabases(
        login=login,
        writer=writer,
        email=f"sso-{suffix}@example.com",
        other_email=f"sso-other-{suffix}@example.com",
        subject=f"subject-{suffix}",
        organization_id=f"org-{suffix}",
        team_id=f"team-{suffix}",
    )
    await login.connect()
    try:
        await writer.connect()
        try:
            yield context
        finally:
            await login.execute_raw(
                "DELETE FROM deltallm_platformaccount WHERE email IN ($1, $2)",
                context.email,
                context.other_email,
            )
            await login.execute_raw(
                "DELETE FROM deltallm_usertable WHERE user_email IN ($1, $2)",
                context.email,
                context.other_email,
            )
            await login.execute_raw(
                "DELETE FROM deltallm_teamtable WHERE team_id = $1", context.team_id
            )
            await login.execute_raw(
                "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
                context.organization_id,
            )
            await writer.disconnect()
    finally:
        await login.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("initial_role", ["org_user", "platform_admin"])
async def test_sso_login_preserves_concurrent_role_edit(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
    linked: bool,
    initial_role: str,
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    account = await service.ensure_account(email=databases.email, role=initial_role, is_active=True)
    account_id = account["account_id"]
    prior_login = await service.create_login_result_for_account(account_id)
    assert prior_login is not None
    if linked:
        await service.link_sso_identity(
            account_id=account_id,
            email=databases.email,
            provider="oidc",
            subject=databases.subject,
        )
    edited_role = "platform_admin" if initial_role == "org_user" else "org_user"
    lookup = PlatformIdentityService.get_account_by_sso_identity

    async def lookup_then_edit(
        identity: PlatformIdentityService, *, provider: str, subject: str
    ) -> dict[str, object] | None:
        snapshot = await lookup(identity, provider=provider, subject=subject)
        # Commit the admin edit on a different connection after the login's read.
        await databases.writer.execute_raw(
            "UPDATE deltallm_platformaccount SET role = $2 WHERE account_id = $1",
            account_id,
            edited_role,
        )
        return snapshot

    monkeypatch.setattr(PlatformIdentityService, "get_account_by_sso_identity", lookup_then_edit)
    result = await service.upsert_sso_account(
        identity=SSOIdentityAssertion(
            email=databases.email,
            provider="oidc",
            subject=databases.subject,
            email_verified=True,
            subject_source=SSOSubjectSource.PROVIDER,
        ),
        is_platform_admin=initial_role == "platform_admin",
    )
    assert result is not None and result.context.role == edited_role
    persisted = await service.get_account_by_id(account_id)
    assert persisted is not None and persisted["role"] == edited_role
    existing_session = await service.get_context_for_session(prior_login.session_token)
    assert existing_session is not None and existing_session.role == edited_role


@pytest.mark.asyncio
async def test_concurrent_first_logins_share_one_account_and_initial_role(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = asyncio.Barrier(2)
    lookup = PlatformIdentityService.get_account_by_sso_identity

    async def lookup_before_insert(
        identity: PlatformIdentityService, *, provider: str, subject: str
    ) -> dict[str, object] | None:
        snapshot = await lookup(identity, provider=provider, subject=subject)
        assert snapshot is None
        await asyncio.wait_for(barrier.wait(), timeout=5)
        return snapshot

    monkeypatch.setattr(
        PlatformIdentityService, "get_account_by_sso_identity", lookup_before_insert
    )
    services = [
        PlatformIdentityService(databases.login, salt="test-salt"),
        PlatformIdentityService(databases.writer, salt="test-salt"),
    ]
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(
                service.upsert_sso_account(
                    identity=SSOIdentityAssertion(
                        email=databases.email,
                        provider="oidc",
                        subject=databases.subject,
                        email_verified=True,
                        subject_source=SSOSubjectSource.PROVIDER,
                    ),
                    is_platform_admin=listed,
                )
            )
            for service, listed in zip(services, [False, True], strict=True)
        ]
    first, second = (task.result() for task in tasks)
    assert first is not None and second is not None
    assert first.context.account_id == second.context.account_id
    assert first.context.role == second.context.role
    accounts = await databases.login.query_raw(
        "SELECT account_id, role FROM deltallm_platformaccount WHERE email = $1", databases.email
    )
    assert accounts == [{"account_id": first.context.account_id, "role": first.context.role}]
    identities = await databases.login.query_raw(
        "SELECT account_id FROM deltallm_platformidentity WHERE provider = $1 AND subject = $2",
        "oidc",
        databases.subject,
    )
    assert identities == [{"account_id": first.context.account_id}]


@pytest.mark.asyncio
async def test_racing_identity_claim_rolls_back_losing_account(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = asyncio.Barrier(2)
    lookup = PlatformIdentityService.get_account_by_sso_identity

    async def lookup_before_claim(
        identity: PlatformIdentityService, *, provider: str, subject: str
    ) -> dict[str, object] | None:
        snapshot = await lookup(identity, provider=provider, subject=subject)
        assert snapshot is None
        await asyncio.wait_for(barrier.wait(), timeout=5)
        return snapshot

    monkeypatch.setattr(PlatformIdentityService, "get_account_by_sso_identity", lookup_before_claim)
    first = PlatformIdentityService(databases.login, salt="test-salt")
    second = PlatformIdentityService(databases.writer, salt="test-salt")
    outcomes = await asyncio.gather(
        first.upsert_sso_account(
            identity=SSOIdentityAssertion(
                email=databases.email,
                provider="oidc",
                subject=databases.subject,
                email_verified=True,
                subject_source=SSOSubjectSource.PROVIDER,
            ),
            is_platform_admin=True,
        ),
        second.upsert_sso_account(
            identity=SSOIdentityAssertion(
                email=databases.other_email,
                provider="oidc",
                subject=databases.subject,
                email_verified=True,
                subject_source=SSOSubjectSource.PROVIDER,
            ),
            is_platform_admin=False,
        ),
        return_exceptions=True,
    )
    successes = [outcome for outcome in outcomes if isinstance(outcome, LoginResult)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, ValueError)]
    assert len(successes) == len(failures) == 1
    assert "already linked" in str(failures[0])
    accounts = await databases.login.query_raw(
        "SELECT account_id FROM deltallm_platformaccount WHERE email IN ($1, $2)",
        databases.email,
        databases.other_email,
    )
    assert accounts == [{"account_id": successes[0].context.account_id}]


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
async def test_concurrent_account_disable_is_not_undone_by_login(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
    linked: bool,
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    account = await service.ensure_account(email=databases.email, is_active=True)
    account_id = account["account_id"]
    if linked:
        await service.link_sso_identity(
            account_id=account_id,
            email=databases.email,
            provider="oidc",
            subject=databases.subject,
        )
    lookup = PlatformIdentityService.get_account_by_sso_identity

    async def lookup_then_disable(
        identity: PlatformIdentityService, *, provider: str, subject: str
    ) -> dict[str, object] | None:
        snapshot = await lookup(identity, provider=provider, subject=subject)
        await databases.writer.execute_raw(
            "UPDATE deltallm_platformaccount SET is_active = false WHERE account_id = $1",
            account_id,
        )
        return snapshot

    monkeypatch.setattr(PlatformIdentityService, "get_account_by_sso_identity", lookup_then_disable)
    with pytest.raises((AccountInactiveError, LoginSessionCreationError)):
        await service.upsert_sso_account(
            identity=SSOIdentityAssertion(
                email=databases.email,
                provider="oidc",
                subject=databases.subject,
                email_verified=True,
                subject_source=SSOSubjectSource.PROVIDER,
            ),
            is_platform_admin=True,
        )
    persisted = await service.get_account_by_id(account_id)
    assert persisted is not None
    assert persisted["is_active"] is False and persisted["role"] == "org_user"
    assert (
        await databases.login.query_raw(
            "SELECT session_id FROM deltallm_platformsession WHERE account_id = $1", account_id
        )
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [TimeoutError, asyncio.CancelledError])
async def test_failed_login_rolls_back_identity_changes(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    account = await service.ensure_account(
        email=databases.email, role="platform_admin", is_active=True
    )
    await service.link_sso_identity(
        account_id=account["account_id"],
        email=databases.email,
        provider="oidc",
        subject=databases.subject,
    )

    async def fail_session(
        identity: PlatformIdentityService,
        *,
        account_id: str,
        mfa_verified: bool,
    ) -> str:
        raise error_type("session interrupted")

    monkeypatch.setattr(PlatformIdentityService, "create_session_for_account", fail_session)
    with pytest.raises(error_type, match="session interrupted"):
        await service.upsert_sso_account(
            identity=SSOIdentityAssertion(
                email=databases.other_email,
                provider="oidc",
                subject=databases.subject,
                email_verified=True,
                subject_source=SSOSubjectSource.PROVIDER,
            ),
            is_platform_admin=False,
        )
    assert await service.get_account_by_id(account["account_id"]) == account
    identities = await databases.login.query_raw(
        "SELECT email FROM deltallm_platformidentity WHERE provider = $1 AND subject = $2",
        "oidc",
        databases.subject,
    )
    assert identities == [{"email": databases.email}]


def identity_for(databases: SSODatabases, *, verified: bool | None = True) -> SSOIdentityAssertion:
    return SSOIdentityAssertion(
        provider="oidc",
        subject=databases.subject,
        email=databases.email,
        email_verified=verified,
        subject_source=SSOSubjectSource.PROVIDER,
    )


async def create_default_targets(databases: SSODatabases) -> None:
    await databases.login.execute_raw(
        "INSERT INTO deltallm_organizationtable (id, organization_id, updated_at) "
        "VALUES (gen_random_uuid(), $1, NOW())",
        databases.organization_id,
    )
    await databases.login.execute_raw(
        "INSERT INTO deltallm_teamtable (team_id, organization_id, models, updated_at) "
        "VALUES ($1, $2, ARRAY[]::text[], NOW())",
        databases.team_id,
        databases.organization_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("self_registration", [False, True])
@pytest.mark.parametrize("verified", [False, None])
async def test_unverified_login_cannot_adopt_racing_admin(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
    self_registration: bool,
    verified: bool | None,
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    writer = PlatformIdentityService(databases.writer, salt="test-salt")
    lookup = PlatformIdentityService.get_account_by_sso_identity

    async def lookup_then_create(identity, *, provider: str, subject: str):
        snapshot = await lookup(identity, provider=provider, subject=subject)
        assert snapshot is None
        await writer.ensure_account(email=databases.email, role="platform_admin", is_active=True)
        return snapshot

    monkeypatch.setattr(PlatformIdentityService, "get_account_by_sso_identity", lookup_then_create)
    with pytest.raises(SSOIdentityOwnershipError):
        if self_registration:
            settings = _enabled_settings()
            settings.require_email_verification = False
            settings.default_org.id = databases.organization_id
            settings.default_team.id = databases.team_id
            provisioner = SelfRegistrationProvisioningService(
                db_client=databases.login, platform_identity_service=service
            )
            await provisioner.provision_sso_from_defaults(
                identity=identity_for(databases, verified=verified), settings=settings
            )
        else:
            await service.upsert_sso_account(
                identity=identity_for(databases, verified=verified), is_platform_admin=False
            )
    account = await service.get_account_by_email(databases.email)
    assert (
        account is not None and account["role"] == "platform_admin" and account["is_active"] is True
    )
    assert (
        await databases.login.query_raw(
            "SELECT identity_id FROM deltallm_platformidentity WHERE subject = $1",
            databases.subject,
        )
        == []
    )
    assert (
        await databases.login.query_raw(
            "SELECT session_id FROM deltallm_platformsession WHERE account_id = $1",
            account["account_id"],
        )
        == []
    )
    assert (
        await databases.login.query_raw(
            "SELECT user_id FROM deltallm_usertable WHERE user_email = $1", databases.email
        )
        == []
    )
    metadata = await databases.login.query_raw(
        "SELECT metadata FROM deltallm_platformaccount WHERE account_id = $1", account["account_id"]
    )
    assert metadata == [{"metadata": None}]


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("edit_timing", ["before", "after"])
async def test_default_memberships_preserve_concurrent_admin_edits(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
    linked: bool,
    edit_timing: str,
) -> None:
    import src.services.platform_identity_service as platform_module

    await create_default_targets(databases)
    service = PlatformIdentityService(databases.login, salt="test-salt")
    writer = PlatformIdentityService(databases.writer, salt="test-salt")
    account = await service.ensure_account(email=databases.email, role="org_user", is_active=True)
    account_id = account["account_id"]
    await service.upsert_team_membership(
        account_id=account_id, team_id=databases.team_id, role="team_viewer"
    )
    await service.upsert_organization_membership(
        account_id=account_id, organization_id=databases.organization_id, role="org_member"
    )
    if linked:
        await service.link_sso_identity(
            account_id=account_id, email=databases.email, provider="oidc", subject=databases.subject
        )

    async def edit_roles() -> None:
        # Change existing rows without reacquiring the account FK lock through an INSERT.
        await databases.writer.execute_raw(
            "UPDATE deltallm_teammembership SET role = 'team_admin' WHERE account_id = $1 AND team_id = $2",
            account_id,
            databases.team_id,
        )
        await databases.writer.execute_raw(
            "UPDATE deltallm_organizationmembership SET role = 'org_owner' WHERE account_id = $1 AND organization_id = $2",
            account_id,
            databases.organization_id,
        )

    if edit_timing == "before":
        lock_team = platform_module.lock_sso_default_team

        async def lock_then_edit(db, *, team_id: str):
            result = await lock_team(db, team_id=team_id)
            await edit_roles()
            return result

        monkeypatch.setattr(platform_module, "lock_sso_default_team", lock_then_edit)
    else:
        seed_team = platform_module.seed_team_membership

        async def seed_then_edit(db, **kwargs):
            await seed_team(db, **kwargs)
            await edit_roles()

        monkeypatch.setattr(platform_module, "seed_team_membership", seed_then_edit)

    result = await service.upsert_sso_account(
        identity=identity_for(databases), is_platform_admin=True, team_id=databases.team_id
    )
    assert result is not None and result.context.role == "org_user"
    assert result.context.team_memberships == [{"team_id": databases.team_id, "role": "team_admin"}]
    assert result.context.organization_memberships == [
        {"organization_id": databases.organization_id, "role": "org_owner"}
    ]
    persisted = await writer.get_context_for_session(result.session_token)
    assert persisted is not None and persisted.team_memberships == result.context.team_memberships


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError, asyncio.CancelledError])
async def test_failure_after_org_membership_seed_rolls_back_login(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    import src.services.platform_identity_service as platform_module

    await create_default_targets(databases)

    async def fail_team(db, **kwargs):
        rows = await db.query_raw(
            "SELECT membership_id FROM deltallm_organizationmembership WHERE organization_id = $1",
            databases.organization_id,
        )
        assert len(rows) == 1
        raise error_type("team write interrupted")

    monkeypatch.setattr(platform_module, "seed_team_membership", fail_team)
    service = PlatformIdentityService(databases.login, salt="test-salt")
    with pytest.raises(error_type, match="team write interrupted"):
        await service.upsert_sso_account(
            identity=identity_for(databases), is_platform_admin=False, team_id=databases.team_id
        )
    assert await service.get_account_by_email(databases.email) is None
    assert (
        await databases.login.query_raw(
            "SELECT membership_id FROM deltallm_organizationmembership WHERE organization_id = $1",
            databases.organization_id,
        )
        == []
    )
    assert (
        await databases.login.query_raw(
            "SELECT identity_id FROM deltallm_platformidentity WHERE subject = $1",
            databases.subject,
        )
        == []
    )


@pytest.mark.asyncio
async def test_unverified_callback_does_not_undo_concurrent_email_edit(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    account = await service.ensure_account(
        email=databases.email, role="platform_admin", is_active=True
    )
    await service.link_sso_identity(
        account_id=account["account_id"],
        email=databases.email,
        provider="oidc",
        subject=databases.subject,
    )
    lookup = PlatformIdentityService.get_account_by_sso_identity

    async def lookup_then_edit(identity, *, provider: str, subject: str):
        snapshot = await lookup(identity, provider=provider, subject=subject)
        await databases.writer.execute_raw(
            "UPDATE deltallm_platformaccount SET email = $2 WHERE account_id = $1",
            account["account_id"],
            databases.other_email,
        )
        return snapshot

    monkeypatch.setattr(PlatformIdentityService, "get_account_by_sso_identity", lookup_then_edit)
    result = await service.upsert_sso_account(
        identity=identity_for(databases, verified=False), is_platform_admin=False
    )
    assert result is not None and result.context.email == databases.other_email
    assert result.context.role == "platform_admin"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["org_user", "platform_admin"])
async def test_verified_self_registration_race_logs_into_existing_account_without_reprovisioning(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    writer = PlatformIdentityService(databases.writer, salt="test-salt")
    lookup = PlatformIdentityService.get_account_by_sso_identity

    async def lookup_then_create(identity, *, provider: str, subject: str):
        snapshot = await lookup(identity, provider=provider, subject=subject)
        assert snapshot is None
        await writer.ensure_account(email=databases.email, role=role, is_active=True)
        return snapshot

    monkeypatch.setattr(PlatformIdentityService, "get_account_by_sso_identity", lookup_then_create)
    provisioner = SelfRegistrationProvisioningService(
        db_client=databases.login, platform_identity_service=service
    )
    settings = _enabled_settings()
    settings.default_org.id = databases.organization_id
    settings.default_team.id = databases.team_id
    result = await provisioner.provision_sso_from_defaults(
        identity=identity_for(databases), settings=settings
    )
    assert result.provisioning is None
    assert result.login.context.role == role
    assert (
        result.login.context.organization_memberships == result.login.context.team_memberships == []
    )
    assert await databases.login.query_raw(
        "SELECT metadata FROM deltallm_platformaccount WHERE email = $1", databases.email
    ) == [{"metadata": None}]
    assert (
        await databases.login.query_raw(
            "SELECT user_id FROM deltallm_usertable WHERE user_email = $1", databases.email
        )
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("require_verification", [False, True])
async def test_new_self_registration_respects_creation_verification_policy(
    databases: SSODatabases,
    require_verification: bool,
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    provisioner = SelfRegistrationProvisioningService(
        db_client=databases.login, platform_identity_service=service
    )
    settings = _enabled_settings()
    settings.default_org.id = databases.organization_id
    settings.default_team.id = databases.team_id
    settings.require_email_verification = require_verification
    if require_verification:
        with pytest.raises(SSOIdentityOwnershipError):
            await provisioner.provision_sso_from_defaults(
                identity=identity_for(databases, verified=False), settings=settings
            )
        assert await service.get_account_by_email(databases.email) is None
        assert (
            await databases.login.query_raw(
                "SELECT identity_id FROM deltallm_platformidentity WHERE subject = $1",
                databases.subject,
            )
            == []
        )
    else:
        result = await provisioner.provision_sso_from_defaults(
            identity=identity_for(databases, verified=False), settings=settings
        )
        assert result.provisioning is not None
        assert result.login.context.role == "org_user"
        assert result.login.context.team_memberships == [
            {"team_id": databases.team_id, "role": "team_developer"}
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_entrypoint", [False, True])
async def test_email_match_must_still_belong_to_account_when_binding(
    databases: SSODatabases,
    monkeypatch: pytest.MonkeyPatch,
    existing_entrypoint: bool,
) -> None:
    import src.services.sso_account_service as account_module

    service = PlatformIdentityService(databases.login, salt="test-salt")
    account = await service.ensure_account(
        email=databases.email, role="platform_admin", is_active=True
    )
    refresh = account_module.refresh_sso_account

    async def edit_then_refresh(db, **kwargs):
        await databases.writer.execute_raw(
            "UPDATE deltallm_platformaccount SET email = $2 WHERE account_id = $1",
            account["account_id"],
            databases.other_email,
        )
        return await refresh(db, **kwargs)

    monkeypatch.setattr(account_module, "refresh_sso_account", edit_then_refresh)
    with pytest.raises(ValueError, match="SSO account changed"):
        if existing_entrypoint:
            await service.create_sso_login_for_existing_account(
                account_id=account["account_id"], identity=identity_for(databases)
            )
        else:
            await service.upsert_sso_account(
                identity=identity_for(databases), is_platform_admin=False
            )
    persisted = await service.get_account_by_id(account["account_id"])
    assert persisted is not None and persisted["email"] == databases.other_email
    assert persisted["role"] == "platform_admin"
    assert (
        await databases.login.query_raw(
            "SELECT identity_id FROM deltallm_platformidentity WHERE subject = $1",
            databases.subject,
        )
        == []
    )
    assert (
        await databases.login.query_raw(
            "SELECT session_id FROM deltallm_platformsession WHERE account_id = $1",
            account["account_id"],
        )
        == []
    )
