from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.auth.sso_identity import (
    AccountInactiveError,
    SSOAccountEligibilityError,
    SSOAccountLinkRequiredError,
    SSOAccountMatch,
    SSOAccountResolutionPolicy,
    SSOIdentityAssertion,
    SSOSubjectSource,
)
from src.services.identity.platform_identity_service import PlatformIdentityService
from src.services.identity.sso_account_service import SSOAccountService
from tests.db import test_sso_account_roles as sso_fixtures
from tests.db.test_sso_account_roles import SSODatabases

pytestmark = pytest.mark.postgres
databases = sso_fixtures.databases


def external_identity(state: SSODatabases) -> SSOIdentityAssertion:
    return SSOIdentityAssertion(
        provider="external:test-issuer",
        subject=state.subject,
        email=state.email,
        email_verified=True,
        subject_source=SSOSubjectSource.PROVIDER,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["org_user", "platform_admin"])
async def test_external_email_collision_requires_approval_without_mutation(
    databases: SSODatabases, role: str
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    account = await service.ensure_account(email=databases.email, role=role, is_active=True)
    before = await service.get_account_by_id(account["account_id"])
    with pytest.raises(SSOAccountLinkRequiredError):
        async with databases.login.tx() as tx:
            await SSOAccountService(tx, service.with_db(tx)).resolve(
                external_identity(databases), policy=SSOAccountResolutionPolicy.EXTERNAL_CUSTOMER
            )
    assert await service.get_account_by_id(account["account_id"]) == before
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


@pytest.mark.asyncio
async def test_external_creation_and_repeat_resolution_share_the_existing_identity_owner(
    databases: SSODatabases,
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    results = []
    for _ in range(2):
        async with databases.login.tx() as tx:
            results.append(
                await SSOAccountService(tx, service.with_db(tx)).resolve(
                    external_identity(databases),
                    policy=SSOAccountResolutionPolicy.EXTERNAL_CUSTOMER,
                )
            )
    assert results[0].match is SSOAccountMatch.CREATED
    assert results[1].match is SSOAccountMatch.SUBJECT
    assert results[0].account.account_id == results[1].account.account_id
    assert await databases.login.query_raw(
        "SELECT account_id FROM deltallm_platformidentity WHERE provider = $1 AND subject = $2",
        "external:test-issuer",
        databases.subject,
    ) == [{"account_id": results[0].account.account_id}]
    assert (
        await databases.login.query_raw(
            "SELECT session_id FROM deltallm_platformsession WHERE account_id = $1",
            results[0].account.account_id,
        )
        == []
    )


@pytest.mark.asyncio
async def test_explicit_link_does_not_create_a_new_account_or_session(
    databases: SSODatabases,
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    account = await service.ensure_account(email=databases.email, is_active=True)
    async with databases.login.tx() as tx:
        result = await SSOAccountService(tx, service.with_db(tx)).resolve(
            external_identity(databases),
            expected_account_id=account["account_id"],
            policy=SSOAccountResolutionPolicy.EXTERNAL_CUSTOMER,
        )
    assert result.match is SSOAccountMatch.EMAIL
    assert result.account.account_id == account["account_id"]
    assert await databases.login.query_raw(
        "SELECT account_id FROM deltallm_platformaccount WHERE email = $1", databases.email
    ) == [{"account_id": account["account_id"]}]
    assert (
        await databases.login.query_raw(
            "SELECT session_id FROM deltallm_platformsession WHERE account_id = $1",
            account["account_id"],
        )
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["role", "inactive"])
async def test_external_resolution_rechecks_concurrent_administrator_change(
    databases: SSODatabases, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    account = await service.ensure_account(email=databases.email, is_active=True)
    await service.link_sso_identity(
        account_id=account["account_id"],
        email=databases.email,
        provider="external:test-issuer",
        subject=databases.subject,
    )
    lookup = PlatformIdentityService.get_account_by_sso_identity

    async def lookup_then_edit(
        identities: PlatformIdentityService, *, provider: str, subject: str
    ) -> dict[str, object] | None:
        row = await lookup(identities, provider=provider, subject=subject)
        if change == "role":
            await databases.writer.execute_raw(
                "UPDATE deltallm_platformaccount SET role = 'platform_admin' WHERE account_id = $1",
                account["account_id"],
            )
        else:
            await databases.writer.execute_raw(
                "UPDATE deltallm_platformaccount SET is_active = false WHERE account_id = $1",
                account["account_id"],
            )
        return row

    monkeypatch.setattr(PlatformIdentityService, "get_account_by_sso_identity", lookup_then_edit)
    error = SSOAccountEligibilityError if change == "role" else AccountInactiveError
    with pytest.raises(error):
        async with databases.login.tx() as tx:
            await SSOAccountService(tx, service.with_db(tx)).resolve(
                external_identity(databases), policy=SSOAccountResolutionPolicy.EXTERNAL_CUSTOMER
            )
    persisted = await service.get_account_by_id(account["account_id"])
    assert persisted is not None
    if change == "role":
        assert persisted["role"] == "platform_admin"
    else:
        assert persisted["is_active"] is False


@pytest.mark.asyncio
async def test_session_revocation_is_visible_to_a_separate_connection(
    databases: SSODatabases,
) -> None:
    writer = PlatformIdentityService(databases.writer, salt="test-salt")
    reader = PlatformIdentityService(databases.login, salt="test-salt")
    account = await writer.ensure_account(email=databases.email, is_active=True)
    token = await writer.create_session_for_account(
        account_id=account["account_id"], mfa_verified=False
    )
    assert await reader.get_context_for_session(token) is not None
    await writer.revoke_session(token)
    assert await reader.get_context_for_session(token) is None
    assert not await reader.mark_session_mfa_verified(token)


@pytest.mark.asyncio
async def test_account_session_revocation_does_not_revoke_another_account(
    databases: SSODatabases,
) -> None:
    writer = PlatformIdentityService(databases.writer, salt="test-salt")
    reader = PlatformIdentityService(databases.login, salt="test-salt")
    own = await writer.ensure_account(email=databases.email, is_active=True)
    other = await writer.ensure_account(email=databases.other_email, is_active=True)
    own_tokens = [
        await writer.create_session_for_account(account_id=own["account_id"], mfa_verified=False)
        for _ in range(2)
    ]
    other_token = await writer.create_session_for_account(
        account_id=other["account_id"], mfa_verified=False
    )
    await writer.revoke_all_sessions_for_account(own["account_id"])
    for token in own_tokens:
        assert await reader.get_context_for_session(token) is None
    assert await reader.get_context_for_session(other_token) is not None


@pytest.mark.asyncio
async def test_session_expiry_and_account_deactivation_deny_mfa(
    databases: SSODatabases,
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    account = await service.ensure_account(email=databases.email, is_active=True)
    secret = service._generate_totp_secret()
    await databases.login.execute_raw(
        "UPDATE deltallm_platformaccount SET mfa_enabled = true, mfa_secret = $2 WHERE account_id = $1",
        account["account_id"],
        secret,
    )
    token = await service.create_session_for_account(
        account_id=account["account_id"], mfa_verified=False
    )
    assert await service.verify_mfa_for_session(
        session_token=token, code=service._totp_code(secret)
    )
    context = await service.get_context_for_session(token)
    assert context is not None and context.mfa_verified
    await service.set_account_active(account["account_id"], is_active=False)
    assert await service.get_context_for_session(token) is None
    assert not await service.verify_mfa_for_session(
        session_token=token, code=service._totp_code(secret)
    )
    await service.set_account_active(account["account_id"], is_active=True)
    await databases.login.execute_raw(
        "UPDATE deltallm_platformsession SET expires_at = $2::timestamptz WHERE session_token_hash = $1",
        service._hash_session_token(token),
        datetime(2000, 1, 1, tzinfo=UTC),
    )
    assert await service.get_context_for_session(token) is None
    assert not await service.mark_session_mfa_verified(token)
    assert not await service.verify_mfa_for_session(
        session_token=token, code=service._totp_code(secret)
    )


@pytest.mark.asyncio
async def test_session_creation_uses_the_caller_transaction(
    databases: SSODatabases,
) -> None:
    service = PlatformIdentityService(databases.login, salt="test-salt")
    account = await service.ensure_account(email=databases.email, is_active=True)
    token = ""
    with pytest.raises(RuntimeError, match="cancelled mutation"):
        async with databases.login.tx() as tx:
            token = await service.with_db(tx).create_session_for_account(
                account_id=account["account_id"], mfa_verified=False
            )
            assert await service.with_db(tx).get_context_for_session(token) is not None
            raise RuntimeError("cancelled mutation")
    assert await service.get_context_for_session(token) is None
