from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from src.services.platform_identity_service import (
    AccountInactiveError,
    LoginSessionCreationError,
    PlatformIdentityService,
)


def test_totp_uri_uses_the_configured_brand_name() -> None:
    service = PlatformIdentityService(db_client=None, salt="salt-key")
    service.totp_issuer = "Acme AI"

    uri = service._totp_uri(secret="ABCDEFGHIJKLMNOP", account_name="acct-1")

    assert uri.startswith("otpauth://totp/Acme%20AI%3Aacct-1?")
    assert "issuer=Acme%20AI" in uri


class FakePlatformIdentityDB:
    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, object]] = {}
        self.identities: dict[tuple[str, str], dict[str, object]] = {}
        self.sessions: dict[str, dict[str, object]] = {}
        self.fail_session_insert = False
        self.fail_last_login_update = False
        self.hide_next_email_lookup = False

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if "from deltallm_platformsession s join deltallm_platformaccount a" in normalized:
            token_hash = str(params[0])
            session = self.sessions.get(token_hash)
            if session is None:
                return []
            account = self.accounts.get(str(session.get("account_id") or ""))
            if account is None:
                return []
            return [
                {
                    "account_id": account["account_id"],
                    "mfa_verified": session["mfa_verified"],
                    "expires_at": session["expires_at"],
                    "email": account["email"],
                    "role": account["role"],
                    "force_password_change": account["force_password_change"],
                    "mfa_enabled": account["mfa_enabled"],
                    "is_active": account["is_active"],
                }
            ]
        if "from deltallm_platformidentity identity_row" in normalized:
            provider = str(params[0])
            subject = str(params[1])
            identity = self.identities.get((provider, subject))
            if identity is None:
                return []
            account = self.accounts.get(str(identity.get("account_id") or ""))
            return [dict(account)] if account else []
        if "from deltallm_platformidentity" in normalized and "where provider = $1" in normalized:
            provider = str(params[0])
            subject = str(params[1])
            identity = self.identities.get((provider, subject))
            return [{"account_id": identity["account_id"]}] if identity else []
        if "from deltallm_platformaccount" in normalized and "where account_id = $1" in normalized:
            account_id = str(params[0])
            account = self.accounts.get(account_id)
            return [dict(account)] if account else []
        if "WHERE lower(email) = lower($1)" in query:
            if self.hide_next_email_lookup:
                self.hide_next_email_lookup = False
                return []
            email = str(params[0]).strip().lower()
            for row in self.accounts.values():
                if str(row.get("email") or "").lower() == email:
                    return [dict(row)]
            return []
        if "from deltallm_organizationmembership" in normalized:
            return []
        if "from deltallm_teammembership" in normalized:
            return []
        return []

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if "insert into deltallm_platformsession" in normalized:
            if self.fail_session_insert:
                raise RuntimeError("session insert failed")
            account_id = str(params[0])
            token_hash = str(params[1])
            self.sessions[token_hash] = {
                "account_id": account_id,
                "mfa_verified": bool(params[2]),
                "expires_at": params[3],
                "last_seen_at": None,
            }
            return 1
        if "update deltallm_platformsession set last_seen_at" in normalized:
            token_hash = str(params[0])
            if token_hash in self.sessions:
                self.sessions[token_hash]["last_seen_at"] = "now"
            return 1
        if "update deltallm_platformaccount set last_login_at" in normalized:
            if self.fail_last_login_update:
                raise RuntimeError("last login update failed")
            account_id = str(params[0])
            row = self.accounts.get(account_id)
            if row is None:
                return 0
            row["last_login_at"] = "now"
            return 1
        if "INSERT INTO deltallm_platformaccount" in query:
            email = str(params[0]).strip().lower()
            role = str(params[1])
            is_active = bool(params[2]) if len(params) > 2 else True
            existing_account_id = self._account_id_for_email(email)
            if existing_account_id is not None and "on conflict (email)" in normalized:
                self.accounts[existing_account_id]["role"] = role
                self.accounts[existing_account_id]["is_active"] = is_active
                return 1
            account_id = f"acct-{len(self.accounts) + 1}"
            self.add_account(account_id=account_id, email=email, role=role, is_active=is_active)
            return 1
        if "insert into deltallm_platformidentity" in normalized:
            account_id = str(params[0])
            provider = str(params[1])
            subject = str(params[2])
            email = str(params[3]).strip().lower()
            existing = self.identities.get((provider, subject))
            if existing is not None and existing.get("account_id") != account_id:
                return 0
            self.identities[(provider, subject)] = {
                "account_id": account_id,
                "provider": provider,
                "subject": subject,
                "email": email,
            }
            return 1
        if (
            "update deltallm_platformaccount" in normalized
            and "set email = $2" in normalized
            and "where account_id = $1" in normalized
        ):
            account_id = str(params[0])
            email = str(params[1]).strip().lower()
            role = params[2]
            is_active = params[3] if len(params) > 3 else None
            existing_account_id = self._account_id_for_email(email)
            if existing_account_id is not None and existing_account_id != account_id:
                raise ValueError("duplicate email")
            row = self.accounts.get(account_id)
            if row is None:
                return 0
            row["email"] = email
            if role is not None:
                row["role"] = str(role)
            if is_active is not None:
                row["is_active"] = bool(is_active)
            return 1
        if "SET password_hash = $1" in query:
            password_hash = str(params[0])
            account_id = str(params[1])
            row = self.accounts.get(account_id)
            if row is None:
                return 0
            row["password_hash"] = password_hash
            return 1
        return 0

    def add_account(
        self,
        *,
        account_id: str,
        email: str,
        role: str = "org_user",
        is_active: bool = True,
    ) -> None:
        self.accounts[account_id] = {
            "account_id": account_id,
            "email": email.strip().lower(),
            "role": role,
            "is_active": is_active,
            "force_password_change": False,
            "mfa_enabled": False,
            "password_hash": None,
            "created_at": None,
            "updated_at": None,
            "last_login_at": None,
        }

    def _account_id_for_email(self, email: str) -> str | None:
        normalized_email = email.strip().lower()
        for account_id, row in self.accounts.items():
            if str(row.get("email") or "").lower() == normalized_email:
                return account_id
        return None


class TransactionalFakePlatformIdentityDB(FakePlatformIdentityDB):
    def __init__(self) -> None:
        super().__init__()
        self.hide_next_identity_join_lookup = False

    def tx(self) -> "_FakeTransaction":
        return _FakeTransaction(self)

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if (
            self.hide_next_identity_join_lookup
            and "from deltallm_platformidentity identity_row" in normalized
        ):
            self.hide_next_identity_join_lookup = False
            return []
        return await super().query_raw(query, *params)


class _FakeTransaction:
    def __init__(self, db: TransactionalFakePlatformIdentityDB) -> None:
        self.db = db
        self.snapshot: dict[str, object] = {}

    async def __aenter__(self) -> TransactionalFakePlatformIdentityDB:
        self.snapshot = {
            "accounts": copy.deepcopy(self.db.accounts),
            "identities": copy.deepcopy(self.db.identities),
            "sessions": copy.deepcopy(self.db.sessions),
        }
        return self.db

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        if exc_type is not None:
            self.db.accounts = self.snapshot["accounts"]
            self.db.identities = self.snapshot["identities"]
            self.db.sessions = self.snapshot["sessions"]
        return False


class PasswordWriteDroppingIdentityService(PlatformIdentityService):
    async def set_password(self, *, account_id: str, new_password: str) -> None:
        self.validate_password_policy(new_password)
        del account_id


class LoginRecordingIdentityService(PlatformIdentityService):
    def __init__(self, *, db_client, salt: str = "salt-key") -> None:  # noqa: ANN001
        super().__init__(db_client=db_client, salt=salt)
        self.login_account_ids: list[str] = []

    async def create_login_result_for_account(self, account_id: str):  # noqa: ANN201
        self.login_account_ids.append(account_id)
        return SimpleNamespace(
            session_token=f"session-{account_id}",
            context=SimpleNamespace(account_id=account_id, mfa_enabled=False),
        )


@pytest.mark.asyncio
async def test_create_account_rejects_missing_password_write() -> None:
    db = FakePlatformIdentityDB()
    service = PasswordWriteDroppingIdentityService(db_client=db, salt="salt-key")

    with pytest.raises(RuntimeError, match="failed to set account password"):
        await service.create_account(
            email="user@example.com",
            password="very-secure-password",
        )


@pytest.mark.asyncio
async def test_link_sso_identity_does_not_mutate_account_role_or_active_state() -> None:
    db = FakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="user@example.com", role="org_user", is_active=False)
    service = PlatformIdentityService(db_client=db, salt="salt-key")

    await service.link_sso_identity(
        account_id="acct-1",
        email="User@Example.com",
        provider="oidc",
        subject="subject-1",
    )

    assert db.identities[("oidc", "subject-1")] == {
        "account_id": "acct-1",
        "provider": "oidc",
        "subject": "subject-1",
        "email": "user@example.com",
    }
    assert db.accounts["acct-1"]["role"] == "org_user"
    assert db.accounts["acct-1"]["is_active"] is False
    account = await service.get_account_by_sso_identity(provider="oidc", subject="subject-1")
    assert account is not None
    assert account["account_id"] == "acct-1"


@pytest.mark.asyncio
async def test_link_sso_identity_rejects_subject_claimed_by_other_account() -> None:
    db = FakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="one@example.com")
    db.add_account(account_id="acct-2", email="two@example.com")
    service = PlatformIdentityService(db_client=db, salt="salt-key")

    await service.link_sso_identity(
        account_id="acct-1",
        email="one@example.com",
        provider="oidc",
        subject="subject-1",
    )

    with pytest.raises(ValueError, match="already linked"):
        await service.link_sso_identity(
            account_id="acct-2",
            email="two@example.com",
            provider="oidc",
            subject="subject-1",
        )


@pytest.mark.asyncio
async def test_upsert_sso_account_rejects_claimed_subject_before_mutating_account() -> None:
    db = FakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="user@example.com", role="org_user", is_active=False)
    db.add_account(account_id="acct-2", email="other@example.com")
    db.identities[("oidc", "subject-1")] = {
        "account_id": "acct-2",
        "provider": "oidc",
        "subject": "subject-1",
        "email": "other@example.com",
    }
    service = PlatformIdentityService(db_client=db, salt="salt-key")

    with pytest.raises(ValueError, match="already linked"):
        await service.upsert_sso_account(
            email="user@example.com",
            is_platform_admin=True,
            provider="oidc",
            subject="subject-1",
        )

    assert db.accounts["acct-1"]["role"] == "org_user"
    assert db.accounts["acct-1"]["is_active"] is False


@pytest.mark.asyncio
async def test_upsert_sso_account_uses_linked_subject_when_provider_email_changes() -> None:
    db = FakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="old@example.com", role="org_user", is_active=False)
    db.identities[("oidc", "subject-1")] = {
        "account_id": "acct-1",
        "provider": "oidc",
        "subject": "subject-1",
        "email": "old@example.com",
    }
    service = LoginRecordingIdentityService(db_client=db)

    login = await service.upsert_sso_account(
        email="New@Example.com",
        is_platform_admin=False,
        provider="oidc",
        subject="subject-1",
    )

    assert login is not None
    assert login.session_token == "session-acct-1"
    assert service.login_account_ids == ["acct-1"]
    assert db.accounts["acct-1"]["email"] == "new@example.com"
    assert db.accounts["acct-1"]["role"] == "org_user"
    assert db.accounts["acct-1"]["is_active"] is True
    assert db.accounts["acct-1"]["last_login_at"] == "now"
    assert db.identities[("oidc", "subject-1")] == {
        "account_id": "acct-1",
        "provider": "oidc",
        "subject": "subject-1",
        "email": "new@example.com",
    }


@pytest.mark.asyncio
async def test_upsert_sso_account_rejects_linked_subject_email_owned_by_other_account() -> None:
    db = FakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="old@example.com", role="org_user", is_active=False)
    db.add_account(account_id="acct-2", email="new@example.com", role="org_user", is_active=True)
    db.identities[("oidc", "subject-1")] = {
        "account_id": "acct-1",
        "provider": "oidc",
        "subject": "subject-1",
        "email": "old@example.com",
    }
    service = LoginRecordingIdentityService(db_client=db)

    with pytest.raises(ValueError, match="email is already linked"):
        await service.upsert_sso_account(
            email="new@example.com",
            is_platform_admin=True,
            provider="oidc",
            subject="subject-1",
        )

    assert service.login_account_ids == []
    assert db.accounts["acct-1"]["email"] == "old@example.com"
    assert db.accounts["acct-1"]["role"] == "org_user"
    assert db.accounts["acct-1"]["is_active"] is False
    assert db.accounts["acct-2"]["email"] == "new@example.com"
    assert db.identities[("oidc", "subject-1")]["email"] == "old@example.com"


@pytest.mark.asyncio
async def test_reconcile_sso_identity_updates_email_without_role_or_active_mutation() -> None:
    db = FakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="old@example.com", role="org_user", is_active=False)
    service = PlatformIdentityService(db_client=db, salt="salt-key")

    account = await service.reconcile_sso_identity_for_account(
        account_id="acct-1",
        email="New@Example.com",
        provider="oidc",
        subject="subject-1",
    )

    assert account["account_id"] == "acct-1"
    assert account["email"] == "new@example.com"
    assert db.accounts["acct-1"]["role"] == "org_user"
    assert db.accounts["acct-1"]["is_active"] is False
    assert db.identities[("oidc", "subject-1")] == {
        "account_id": "acct-1",
        "provider": "oidc",
        "subject": "subject-1",
        "email": "new@example.com",
    }


@pytest.mark.asyncio
async def test_create_sso_login_for_existing_account_reconciles_and_creates_session_atomically() -> (
    None
):
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="old@example.com", role="org_user", is_active=True)
    service = PlatformIdentityService(db_client=db, salt="salt-key")

    login = await service.create_sso_login_for_existing_account(
        account_id="acct-1",
        email="New@Example.com",
        provider="oidc",
        subject="subject-1",
    )

    assert login is not None
    assert login.context.account_id == "acct-1"
    assert login.context.email == "new@example.com"
    assert login.session_token.startswith("psk_")
    assert db.accounts["acct-1"]["email"] == "new@example.com"
    assert db.accounts["acct-1"]["last_login_at"] == "now"
    assert db.identities[("oidc", "subject-1")] == {
        "account_id": "acct-1",
        "provider": "oidc",
        "subject": "subject-1",
        "email": "new@example.com",
    }
    assert len(db.sessions) == 1


@pytest.mark.asyncio
async def test_create_sso_login_for_existing_account_rolls_back_reconcile_when_session_creation_fails() -> (
    None
):
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="old@example.com", role="org_user", is_active=True)
    db.fail_session_insert = True
    service = PlatformIdentityService(db_client=db, salt="salt-key")

    with pytest.raises(RuntimeError, match="session insert failed"):
        await service.create_sso_login_for_existing_account(
            account_id="acct-1",
            email="New@Example.com",
            provider="oidc",
            subject="subject-1",
        )

    assert db.accounts["acct-1"]["email"] == "old@example.com"
    assert db.accounts["acct-1"]["last_login_at"] is None
    assert db.identities == {}
    assert db.sessions == {}


@pytest.mark.asyncio
async def test_create_sso_login_for_existing_account_rejects_inactive_account_before_linking() -> (
    None
):
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="old@example.com", role="org_user", is_active=False)
    service = PlatformIdentityService(db_client=db, salt="salt-key")

    with pytest.raises(AccountInactiveError, match="Account is inactive"):
        await service.create_sso_login_for_existing_account(
            account_id="acct-1",
            email="New@Example.com",
            provider="oidc",
            subject="subject-1",
        )

    assert db.accounts["acct-1"]["email"] == "old@example.com"
    assert db.identities == {}
    assert db.sessions == {}


@pytest.mark.asyncio
async def test_upsert_sso_account_rolls_back_if_identity_conflict_races() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.add_account(account_id="acct-1", email="user@example.com", role="org_user", is_active=False)
    db.add_account(account_id="acct-2", email="other@example.com")
    db.identities[("oidc", "subject-1")] = {
        "account_id": "acct-2",
        "provider": "oidc",
        "subject": "subject-1",
        "email": "other@example.com",
    }
    db.hide_next_identity_join_lookup = True
    service = PlatformIdentityService(db_client=db, salt="salt-key")

    with pytest.raises(ValueError, match="already linked"):
        await service.upsert_sso_account(
            email="user@example.com",
            is_platform_admin=True,
            provider="oidc",
            subject="subject-1",
        )

    assert db.accounts["acct-1"]["role"] == "org_user"
    assert db.accounts["acct-1"]["is_active"] is False
    assert db.identities[("oidc", "subject-1")]["account_id"] == "acct-2"


@pytest.mark.asyncio
async def test_upsert_sso_account_rolls_back_if_last_login_update_fails() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.fail_last_login_update = True
    service = PlatformIdentityService(db_client=db, salt="salt-key")

    with pytest.raises(RuntimeError, match="last login update failed"):
        await service.upsert_sso_account(
            email="user@example.com",
            is_platform_admin=False,
            provider="oidc",
            subject="subject-1",
        )

    assert db.accounts == {}
    assert db.identities == {}
    assert db.sessions == {}


@pytest.mark.asyncio
async def test_upsert_sso_account_rolls_back_if_account_reload_fails() -> None:
    db = TransactionalFakePlatformIdentityDB()
    db.hide_next_email_lookup = True
    service = PlatformIdentityService(db_client=db, salt="salt-key")

    with pytest.raises(LoginSessionCreationError, match="Failed to establish session"):
        await service.upsert_sso_account(
            email="user@example.com",
            is_platform_admin=False,
            provider="oidc",
            subject="subject-1",
        )

    assert db.accounts == {}
    assert db.identities == {}
    assert db.sessions == {}
