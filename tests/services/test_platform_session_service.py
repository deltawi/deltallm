from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib

import pytest

from src.db.identity.platform_sessions import PlatformSessionRecord, SessionMFARecord
from src.services.identity.platform_identity_service import PlatformIdentityService
from src.services.identity.platform_session_service import PlatformSessionService
from tests.services.test_platform_identity_service import TransactionalFakePlatformIdentityDB


@pytest.mark.asyncio
async def test_session_format_hash_expiry_and_current_context_remain_compatible() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="operator@example.com", role="platform_co_admin")
    db.accounts["account"]["mfa_enabled"] = True
    db.accounts["account"]["force_password_change"] = True
    db.organization_memberships[("account", "org")] = {
        "organization_id": "org",
        "role": "org_member",
    }
    db.team_memberships[("account", "team")] = {"team_id": "team", "role": "team_developer"}
    service = PlatformIdentityService(db, salt="legacy-salt", session_ttl_hours=3)
    before = datetime.now(UTC)
    token = await service.create_session_for_account(account_id="account", mfa_verified=False)
    after = datetime.now(UTC)

    assert token.startswith("psk_") and len(token) >= 40
    approved_hash = hashlib.sha256(f"legacy-salt:session:{token}".encode()).hexdigest()
    assert list(db.sessions) == [approved_hash]
    stored = db.sessions[approved_hash]
    assert before + timedelta(hours=3) <= stored["expires_at"] <= after + timedelta(hours=3)
    assert token not in str(stored)
    context = await service.get_context_for_session(token)
    assert context is not None
    assert context.account_id == "account" and context.role == "platform_admin"
    assert context.mfa_enabled and not context.mfa_verified and context.force_password_change
    assert context.organization_memberships == [{"organization_id": "org", "role": "org_member"}]
    assert context.team_memberships == [{"team_id": "team", "role": "team_developer"}]
    assert stored["last_seen_at"] == "now"


@pytest.mark.asyncio
async def test_account_disable_is_checked_on_each_session_use() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="operator@example.com")
    service = PlatformIdentityService(db, salt="test-salt")
    token = await service.create_session_for_account(account_id="account", mfa_verified=False)
    assert await service.get_context_for_session(token) is not None
    db.accounts["account"]["is_active"] = False
    assert await service.get_context_for_session(token) is None


@pytest.mark.asyncio
async def test_transaction_clone_uses_the_callers_session_repository() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="account", email="operator@example.com")
    service = PlatformIdentityService(db, salt="test-salt", session_ttl_hours=2)
    service.totp_issuer = "Customer brand"
    with pytest.raises(RuntimeError, match="transaction interrupted"):
        async with db.tx() as tx:
            scoped = service.with_db(tx)
            assert scoped.sessions.repository.db is tx
            assert scoped.sessions.lifetime == timedelta(hours=2)
            assert scoped.totp_issuer == "Customer brand"
            await scoped.create_session_for_account(account_id="account", mfa_verified=False)
            raise RuntimeError("transaction interrupted")
    assert db.sessions == {}


@pytest.mark.asyncio
async def test_unavailable_repository_never_issues_or_verifies_a_session() -> None:
    service = PlatformSessionService(None, salt="test-salt", lifetime=timedelta(hours=1))
    with pytest.raises(RuntimeError, match="database is unavailable"):
        await service.create(account_id="account", mfa_verified=False)
    assert await service.get_context("psk_test") is None
    assert not await service.mark_mfa_verified("psk_test")
    assert not await service.verify_mfa(
        token="psk_test", code="123456", verify_code=lambda secret, code: True
    )


def test_session_hash_is_separate_from_api_key_and_other_deployments() -> None:
    first = PlatformSessionService(None, salt="first", lifetime=timedelta(hours=1))
    second = PlatformSessionService(None, salt="second", lifetime=timedelta(hours=1))
    assert first.hash_token("psk_test") != second.hash_token("psk_test")
    assert first.hash_token("psk_test") != hashlib.sha256(b"first:psk_test").hexdigest()
    with pytest.raises(ValueError, match="salt is required"):
        PlatformSessionService(None, salt="", lifetime=timedelta(hours=1))
    with pytest.raises(ValueError, match="salt is required"):
        PlatformIdentityService(None, salt="")


@pytest.mark.parametrize(
    "expiry",
    ["2026-10-06T05:00:00+02:00", datetime(2026, 10, 6, 3), datetime(2026, 10, 6, 3, tzinfo=UTC)],
)
def test_repository_maps_expiry_to_an_utc_instant(expiry: datetime | str) -> None:
    row = PlatformSessionRecord.from_row(
        {
            "account_id": "account",
            "expires_at": expiry,
            "is_active": True,
        }
    )
    assert row.expires_at == datetime(2026, 10, 6, 3, tzinfo=UTC)
    assert not row.mfa_enabled and not row.mfa_verified


@pytest.mark.parametrize("is_active", [None, "true", 1, False])
def test_repository_requires_a_real_active_boolean(is_active: object) -> None:
    row = PlatformSessionRecord.from_row(
        {
            "account_id": "account",
            "expires_at": datetime.now(UTC),
            "is_active": is_active,
        }
    )
    assert not row.is_active


def test_mfa_secret_is_excluded_from_record_representation() -> None:
    row = SessionMFARecord(is_active=True, enabled=True, secret="sensitive-totp-secret")
    assert "sensitive-totp-secret" not in repr(row)
    assert row.secret == "sensitive-totp-secret"
