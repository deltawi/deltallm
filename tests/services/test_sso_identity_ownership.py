from __future__ import annotations

from src.auth.sso_identity import SSOIdentityAssertion, SSOSubjectSource

import copy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from src.auth.sso_identity import SSOIdentityOwnershipError
from src.services.platform_identity_service import PlatformIdentityService
from tests.services.test_platform_identity_service import TransactionalFakePlatformIdentityDB


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
async def test_demoted_listed_admin_keeps_scoped_roles(linked: bool) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="admin@example.com", role="org_user")
    db.teams["team"] = {"team_id": "team", "organization_id": "org"}
    db.organizations["org"] = {"organization_id": "org", "lifecycle_state": "active"}
    service = PlatformIdentityService(db, salt="test-salt")
    await service.upsert_team_membership(account_id="account", team_id="team", role="team_admin")
    await service.upsert_organization_membership(
        account_id="account", organization_id="org", role="org_owner"
    )
    if linked:
        await service.link_sso_identity(
            account_id="account", email="admin@example.com", provider="oidc", subject="subject"
        )

    login = await service.upsert_sso_account(
        identity=SSOIdentityAssertion(
            email="admin@example.com",
            provider="oidc",
            subject="subject",
            email_verified=True,
            subject_source=SSOSubjectSource.PROVIDER,
        ),
        is_platform_admin=True,
        team_id="team",
    )

    assert login is not None and login.context.role == "org_user"
    assert db.team_memberships[("account", "team")]["role"] == "team_admin"
    assert db.organization_memberships[("account", "org")]["role"] == "org_owner"


def assertion(
    *,
    email: str = "person@example.com",
    verified: bool | None = True,
    source: SSOSubjectSource = SSOSubjectSource.PROVIDER,
) -> SSOIdentityAssertion:
    return SSOIdentityAssertion(
        provider="oidc",
        subject="subject" if source is SSOSubjectSource.PROVIDER else email,
        email=email,
        email_verified=verified,
        subject_source=source,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_entrypoint", [False, True])
@pytest.mark.parametrize("other_subject", [False, True])
@pytest.mark.parametrize("role", ["org_user", "platform_admin"])
@pytest.mark.parametrize("verified", [False, None])
async def test_unverified_email_cannot_bind_existing_account(
    existing_entrypoint: bool,
    other_subject: bool,
    role: str,
    verified: bool | None,
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="person@example.com", role=role)
    service = PlatformIdentityService(db, salt="test-salt")
    if other_subject:
        await service.link_sso_identity(
            account_id="account", email="person@example.com", provider="oidc", subject="original"
        )
    accounts_before, identities_before = copy.deepcopy(db.accounts), copy.deepcopy(db.identities)
    with pytest.raises(SSOIdentityOwnershipError):
        if existing_entrypoint:
            await service.create_sso_login_for_existing_account(
                account_id="account", identity=assertion(verified=verified)
            )
        else:
            await service.upsert_sso_account(
                identity=assertion(verified=verified), is_platform_admin=False
            )
    assert db.accounts == accounts_before
    assert db.identities == identities_before
    assert db.sessions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, None])
@pytest.mark.parametrize("existing_entrypoint", [False, True])
async def test_established_subject_preserves_unverified_recovery_email(
    verified: bool | None,
    existing_entrypoint: bool,
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="old@example.com", role="platform_admin")
    service = PlatformIdentityService(db, salt="test-salt")
    await service.link_sso_identity(
        account_id="account", email="old@example.com", provider="oidc", subject="subject"
    )
    identity = assertion(email="unverified@example.com", verified=verified)
    if existing_entrypoint:
        login = await service.create_sso_login_for_existing_account(
            account_id="account", identity=identity
        )
    else:
        login = await service.upsert_sso_account(identity=identity, is_platform_admin=False)
    assert login is not None
    assert login.context.email == "old@example.com"
    assert login.context.role == "platform_admin"
    assert db.accounts["account"]["email"] == "old@example.com"
    assert db.identities[("oidc", "subject")]["email"] == "old@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("verified", [False, None])
async def test_email_fallback_never_bypasses_verification(
    linked: bool, verified: bool | None
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    service = PlatformIdentityService(db, salt="test-salt")
    identity = assertion(source=SSOSubjectSource.EMAIL, verified=verified)
    if linked:
        await service.upsert_sso_account(
            identity=replace(identity, email_verified=True), is_platform_admin=False
        )
    accounts_before, sessions_before = copy.deepcopy(db.accounts), copy.deepcopy(db.sessions)
    identities_before = copy.deepcopy(db.identities)
    with pytest.raises(SSOIdentityOwnershipError):
        await service.upsert_sso_account(identity=identity, is_platform_admin=False)
    assert db.accounts == accounts_before
    assert db.identities == identities_before
    assert db.sessions == sessions_before


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, None])
async def test_new_admin_requires_verified_email(verified: bool | None) -> None:
    db = TransactionalFakePlatformIdentityDB()
    service = PlatformIdentityService(db, salt="test-salt")
    with pytest.raises(SSOIdentityOwnershipError):
        await service.upsert_sso_account(
            identity=assertion(verified=verified), is_platform_admin=True
        )
    assert db.accounts == db.identities == db.sessions == {}


@pytest.mark.asyncio
async def test_new_ordinary_account_with_stable_subject_uses_legacy_signup_policy() -> None:
    db = TransactionalFakePlatformIdentityDB()
    service = PlatformIdentityService(db, salt="test-salt")
    for _ in range(2):
        login = await service.upsert_sso_account(
            identity=assertion(verified=None), is_platform_admin=False
        )
        assert login is not None and login.context.role == "org_user"
    assert len(db.accounts) == len(db.identities) == 1


@pytest.mark.asyncio
async def test_existing_account_id_is_not_proof_of_email_ownership() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="victim@example.com", role="platform_admin")
    service = PlatformIdentityService(db, salt="test-salt")
    with pytest.raises(ValueError, match="does not match account"):
        await service.create_sso_login_for_existing_account(
            account_id="account", identity=assertion()
        )
    assert db.identities == db.sessions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["team", "org", "both"])
async def test_defaults_seed_only_missing_memberships(missing: str) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="person@example.com")
    db.teams["team"] = {"team_id": "team", "organization_id": "org"}
    db.organizations["org"] = {"organization_id": "org", "lifecycle_state": "active"}
    service = PlatformIdentityService(db, salt="test-salt")
    if missing == "org":
        await service.upsert_team_membership(
            account_id="account", team_id="team", role="team_developer"
        )
    if missing == "team":
        await service.upsert_organization_membership(
            account_id="account", organization_id="org", role="org_admin"
        )
    for _ in range(2):
        await service.upsert_sso_account(
            identity=assertion(), is_platform_admin=False, team_id="team"
        )
    assert db.team_memberships[("account", "team")]["role"] == (
        "team_developer" if missing == "org" else "team_viewer"
    )
    assert db.organization_memberships[("account", "org")]["role"] == (
        "org_admin" if missing == "team" else "org_member"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["missing_team", "missing_org", "inactive_org"])
async def test_invalid_default_target_rolls_back_login(target: str) -> None:
    db = TransactionalFakePlatformIdentityDB()
    if target != "missing_team":
        db.teams["team"] = {"team_id": "team", "organization_id": "org"}
    if target == "inactive_org":
        db.organizations["org"] = {"organization_id": "org", "lifecycle_state": "deleting"}
    service = PlatformIdentityService(db, salt="test-salt")
    from src.services.organization_mutation_policy import OrganizationMutationError

    with pytest.raises((ValueError, OrganizationMutationError)):
        await service.upsert_sso_account(
            identity=assertion(), is_platform_admin=False, team_id="team"
        )
    assert db.accounts == db.identities == db.sessions == {}
    assert db.team_memberships == db.organization_memberships == {}


class CountingIdentityDatabase:
    def __init__(self, db: TransactionalFakePlatformIdentityDB, statements: list[str]) -> None:
        self.db = db
        self.statements = statements

    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]:
        self.statements.append(query.split()[0].upper())
        return await self.db.query_raw(query, *params)

    async def execute_raw(self, query: str, *params: object) -> int:
        self.statements.append(query.split()[0].upper())
        return await self.db.execute_raw(query, *params)

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[CountingIdentityDatabase]:
        async with self.db.tx() as db:
            yield CountingIdentityDatabase(db, self.statements)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "statement_budget"),
    [
        ("new", 10),
        ("email_match", 12),
        ("linked", 10),
        ("linked_email_change", 11),
        ("linked_default_team", 14),
    ],
)
async def test_sso_login_stays_within_measured_database_budget(
    case: str, statement_budget: int
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    setup = PlatformIdentityService(db, salt="test-salt")
    if case != "new":
        db.add_account(account_id="account", email="person@example.com")
        if case.startswith("linked"):
            await setup.link_sso_identity(
                account_id="account", email="person@example.com", provider="oidc", subject="subject"
            )
    email = "changed@example.com" if case == "linked_email_change" else "person@example.com"
    team_id = None
    if case == "linked_default_team":
        team_id = "team"
        db.teams["team"] = {"team_id": "team", "organization_id": "org"}
        db.organizations["org"] = {"organization_id": "org", "lifecycle_state": "active"}
    statements: list[str] = []
    service = PlatformIdentityService(CountingIdentityDatabase(db, statements), salt="test-salt")

    login = await service.upsert_sso_account(
        identity=assertion(email=email), is_platform_admin=False, team_id=team_id
    )

    assert login is not None and login.context.role == "org_user"
    # Budgets were measured against PostgreSQL; count driver calls, not fake internals.
    assert len(statements) <= statement_budget, statements
