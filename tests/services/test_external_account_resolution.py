from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from src.auth.sso_identity import (
    AccountInactiveError,
    SSOAccountEligibilityError,
    SSOAccountLinkRequiredError,
    SSOAccountMatch,
    SSOAccountResolutionPolicy,
    SSOIdentityAssertion,
    SSOIdentityOwnershipError,
    SSOSubjectSource,
)
from src.services.platform_identity_service import PlatformIdentityService
from src.services.sso_account_service import SSOAccountResolution, SSOAccountService
from tests.services.test_platform_identity_service import TransactionalFakePlatformIdentityDB


def external_identity() -> SSOIdentityAssertion:
    return SSOIdentityAssertion(
        provider="external:test-issuer",
        subject="clerk-subject",
        email="customer@example.com",
        email_verified=True,
        subject_source=SSOSubjectSource.PROVIDER,
    )


async def resolve(
    db: TransactionalFakePlatformIdentityDB,
    *,
    identity: SSOIdentityAssertion | None = None,
    account_id: str | None = None,
    initial_role: str = "org_user",
) -> SSOAccountResolution:
    async with db.tx() as tx:
        service = SSOAccountService(tx, PlatformIdentityService(tx, salt="test-salt"))
        return await service.resolve(
            identity or external_identity(),
            initial_role=initial_role,
            expected_account_id=account_id,
            policy=SSOAccountResolutionPolicy.EXTERNAL_CUSTOMER,
        )


@pytest.mark.asyncio
async def test_external_subject_creates_once_and_reuses_its_account() -> None:
    db = TransactionalFakePlatformIdentityDB()
    first = await resolve(db)
    second = await resolve(db)
    assert first.match is SSOAccountMatch.CREATED
    assert second.match is SSOAccountMatch.SUBJECT
    assert first.account.account_id == second.account.account_id
    assert first.account.role == second.account.role == "org_user"
    assert len(db.accounts) == len(db.identities) == 1
    assert db.sessions == db.team_memberships == db.organization_memberships == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["org_user", "platform_admin", "platform_co_admin"])
@pytest.mark.parametrize("already_linked", [False, True])
async def test_email_collision_never_links_or_edits_existing_account(
    role: str, already_linked: bool
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="existing", email="customer@example.com", role=role)
    if already_linked:
        await PlatformIdentityService(db, salt="test-salt").link_sso_identity(
            account_id="existing",
            email="customer@example.com",
            provider="oidc",
            subject="operator-subject",
        )
    before = copy.deepcopy((db.accounts, db.identities))
    with pytest.raises(SSOAccountLinkRequiredError) as error:
        await resolve(db)
    assert str(error.value) == "Account link approval is required"
    assert (db.accounts, db.identities) == before
    assert db.sessions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, None])
@pytest.mark.parametrize("linked", [False, True])
async def test_external_email_must_be_verified_even_for_an_established_subject(
    verified: bool | None, linked: bool
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    if linked:
        await resolve(db)
    before = copy.deepcopy((db.accounts, db.identities))
    with pytest.raises(SSOIdentityOwnershipError):
        await resolve(db, identity=replace(external_identity(), email_verified=verified))
    assert (db.accounts, db.identities) == before


@pytest.mark.asyncio
async def test_email_fallback_is_not_a_stable_external_subject() -> None:
    db = TransactionalFakePlatformIdentityDB()
    with pytest.raises(SSOAccountLinkRequiredError):
        await resolve(
            db,
            identity=replace(external_identity(), subject_source=SSOSubjectSource.EMAIL),
        )
    assert db.accounts == db.identities == {}


@pytest.mark.asyncio
async def test_explicit_link_uses_the_approved_account_only() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="approved", email="customer@example.com")
    resolved = await resolve(db, account_id="approved")
    assert resolved.account.account_id == "approved"
    assert resolved.match is SSOAccountMatch.EMAIL
    assert db.identities[("external:test-issuer", "clerk-subject")]["account_id"] == "approved"
    assert len(db.accounts) == 1
    assert db.sessions == {}


@pytest.mark.asyncio
async def test_approval_for_another_account_cannot_move_an_established_subject() -> None:
    db = TransactionalFakePlatformIdentityDB()
    first = await resolve(db)
    db.add_account(account_id="other", email="other@example.com")
    before = copy.deepcopy((db.accounts, db.identities))
    with pytest.raises(ValueError, match="already linked to another account"):
        await resolve(db, account_id="other")
    assert (db.accounts, db.identities) == before
    assert first.account.account_id != "other"


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("role", ["platform_admin", "platform_co_admin", "unknown"])
async def test_external_policy_rejects_privileged_or_unknown_account_role(
    linked: bool, role: str
) -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="customer@example.com", role=role)
    if linked:
        await PlatformIdentityService(db, salt="test-salt").link_sso_identity(
            account_id="account",
            email="customer@example.com",
            provider="external:test-issuer",
            subject="clerk-subject",
        )
    before = copy.deepcopy((db.accounts, db.identities))
    with pytest.raises(SSOAccountEligibilityError):
        await resolve(db, account_id=None if linked else "account")
    assert (db.accounts, db.identities) == before


@pytest.mark.asyncio
async def test_external_policy_cannot_seed_a_platform_administrator() -> None:
    db = TransactionalFakePlatformIdentityDB()
    with pytest.raises(SSOAccountEligibilityError):
        await resolve(db, initial_role="platform_admin")
    assert db.accounts == db.identities == {}


@pytest.mark.asyncio
async def test_external_policy_preserves_account_suspension() -> None:
    db = TransactionalFakePlatformIdentityDB()
    first = await resolve(db)
    db.accounts[first.account.account_id]["is_active"] = False
    before = copy.deepcopy((db.accounts, db.identities))
    with pytest.raises(AccountInactiveError):
        await resolve(db)
    assert (db.accounts, db.identities) == before


@pytest.mark.asyncio
async def test_policy_string_is_validated_before_account_resolution() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="existing", email="customer@example.com")
    service = SSOAccountService(db, PlatformIdentityService(db, salt="test-salt"))
    with pytest.raises(SSOAccountLinkRequiredError):
        await service.resolve(external_identity(), policy="external_customer")
    with pytest.raises(ValueError, match="not a valid SSOAccountResolutionPolicy"):
        await service.resolve(external_identity(), policy="unknown")
    assert db.identities == {}
