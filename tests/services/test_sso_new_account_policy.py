from __future__ import annotations

import pytest

from src.auth.sso_identity import (
    SSOAccountCreationDisabledError,
    SSOIdentityAssertion,
    SSOIdentityOwnershipError,
    SSOSubjectSource,
)
from src.services.identity.platform_identity_service import PlatformIdentityService
from tests.services.test_platform_identity_service import TransactionalFakePlatformIdentityDB

pytestmark = pytest.mark.asyncio


def _identity(*, email: str = "person@example.com", verified: bool | None = True):
    return SSOIdentityAssertion(
        provider="oidc",
        subject="subject",
        email=email,
        email_verified=verified,
        subject_source=SSOSubjectSource.PROVIDER,
    )


async def test_unknown_person_cannot_create_an_account() -> None:
    db = TransactionalFakePlatformIdentityDB()
    service = PlatformIdentityService(db, salt="test-salt")

    with pytest.raises(SSOAccountCreationDisabledError):
        await service.upsert_sso_account(
            identity=_identity(), is_platform_admin=False, allow_create=False
        )

    assert db.accounts == {}
    assert db.identities == {}


async def test_existing_account_signs_in_and_links_by_verified_email() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="person@example.com", role="org_user")
    service = PlatformIdentityService(db, salt="test-salt")

    login = await service.upsert_sso_account(
        identity=_identity(), is_platform_admin=False, allow_create=False
    )

    assert login is not None and login.context.account_id == "account"
    assert db.identities[("oidc", "subject")]["account_id"] == "account"


async def test_linked_subject_signs_in_without_email_match() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="old@example.com", role="org_user")
    service = PlatformIdentityService(db, salt="test-salt")
    await service.link_sso_identity(
        account_id="account", email="old@example.com", provider="oidc", subject="subject"
    )

    login = await service.upsert_sso_account(
        identity=_identity(email="new@example.com", verified=None),
        is_platform_admin=False,
        allow_create=False,
    )

    assert login is not None and login.context.account_id == "account"


async def test_existing_account_still_needs_verified_email_to_link() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="person@example.com", role="org_user")
    service = PlatformIdentityService(db, salt="test-salt")

    with pytest.raises(SSOIdentityOwnershipError):
        await service.upsert_sso_account(
            identity=_identity(verified=None), is_platform_admin=False, allow_create=False
        )

    assert db.identities == {}
