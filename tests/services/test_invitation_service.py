from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.db.invitations import PlatformInvitationRecord
from src.services.invitation_service import InvitationService
from src.services.platform_identity_service import LoginResult


class FakeInvitationRepository:
    def __init__(self) -> None:
        self.records: dict[str, PlatformInvitationRecord] = {}

    async def create(self, record: PlatformInvitationRecord) -> PlatformInvitationRecord:
        invitation_id = record.invitation_id or f"inv-{len(self.records) + 1}"
        stored = replace(record, invitation_id=invitation_id, created_at=datetime.now(tz=UTC), updated_at=datetime.now(tz=UTC))
        self.records[invitation_id] = stored
        return stored

    async def get_by_id(self, invitation_id: str) -> PlatformInvitationRecord | None:
        return self.records.get(invitation_id)

    async def get_latest_pending_by_account_id(self, account_id: str) -> PlatformInvitationRecord | None:
        for record in self.records.values():
            if record.account_id == account_id and record.status in {"pending", "sent"}:
                return record
        return None

    async def list_pending_by_account_id(self, account_id: str):  # noqa: ANN201
        return [
            record
            for record in self.records.values()
            if record.account_id == account_id and record.status in {"pending", "sent"}
        ]

    async def update_pending(self, **kwargs):  # noqa: ANN003, ANN201
        record = self.records[kwargs["invitation_id"]]
        updated = replace(
            record,
            status="pending",
            invite_scope_type=kwargs["invite_scope_type"],
            invited_by_account_id=kwargs["invited_by_account_id"],
            expires_at=kwargs["expires_at"],
            accepted_at=None,
            cancelled_at=None,
            metadata=kwargs["metadata"],
        )
        self.records[record.invitation_id] = updated
        return updated

    async def mark_sent(self, **kwargs):  # noqa: ANN003, ANN201
        record = self.records[kwargs["invitation_id"]]
        updated = replace(
            record,
            status="sent",
            message_email_id=kwargs["message_email_id"],
            expires_at=kwargs["expires_at"],
            metadata=kwargs.get("metadata") or record.metadata,
        )
        self.records[record.invitation_id] = updated
        return updated

    async def mark_accepted(self, invitation_id: str) -> bool:
        record = self.records[invitation_id]
        self.records[invitation_id] = replace(record, status="accepted", accepted_at=datetime.now(tz=UTC))
        return True

    async def mark_cancelled(self, invitation_id: str) -> bool:
        record = self.records[invitation_id]
        self.records[invitation_id] = replace(record, status="cancelled", cancelled_at=datetime.now(tz=UTC))
        return True

    async def mark_expired(self, invitation_id: str) -> bool:
        record = self.records[invitation_id]
        self.records[invitation_id] = replace(record, status="expired")
        return True

    async def list_all(self, *, status: str | None = None, search: str | None = None):  # noqa: ANN201
        items = list(self.records.values())
        if status:
            items = [item for item in items if item.status == status]
        if search:
            items = [item for item in items if search.lower() in item.email.lower()]
        return items


class FakeTokenService:
    def __init__(self) -> None:
        self.by_raw_token: dict[str, SimpleNamespace] = {}
        self.invalidated: list[tuple[str, str | None, str | None]] = []
        self.consumed_token_ids: list[str] = []

    async def invalidate_active_tokens(
        self,
        *,
        purpose: str,
        account_id: str | None = None,
        invitation_id: str | None = None,
        exclude_token_id: str | None = None,
    ) -> int:
        del exclude_token_id
        self.invalidated.append((purpose, account_id, invitation_id))
        return 1

    async def issue_invitation_token(self, *, account_id: str, email: str, invitation_id: str, created_by_account_id: str | None):  # noqa: ANN001, ANN201
        del created_by_account_id
        raw = f"raw-{invitation_id}"
        record = SimpleNamespace(token_id=f"tok-{invitation_id}", expires_at=datetime.now(tz=UTC) + timedelta(hours=72))
        self.by_raw_token[raw] = SimpleNamespace(token_id=record.token_id, account_id=account_id, email=email, invitation_id=invitation_id, expires_at=record.expires_at)
        return SimpleNamespace(raw_token=raw, record=record)

    async def validate_token(self, *, purpose: str, raw_token: str):  # noqa: ANN201
        del purpose
        return self.by_raw_token.get(raw_token)

    async def claim_token(self, *, purpose: str, raw_token: str):  # noqa: ANN201
        del purpose
        token = self.by_raw_token.pop(raw_token, None)
        if token is None:
            return None
        return token

    async def consume_token(self, *, token_id: str) -> bool:
        self.consumed_token_ids.append(token_id)
        for raw_token, value in list(self.by_raw_token.items()):
            if value.token_id == token_id:
                self.by_raw_token.pop(raw_token, None)
                return True
        return False

    def build_action_url(self, *, path: str, raw_token: str) -> str:
        return f"https://gateway.example.com{path}?token={raw_token}"


class FakeOutboxService:
    def __init__(self, *, status: str = "queued", fail: bool = False) -> None:
        self.status = status
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    async def enqueue_template_email(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("enqueue failed")
        return SimpleNamespace(email_id=f"email-{len(self.calls)}", status=self.status)


class FakePlatformIdentityService:
    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, object]] = {}
        self.sso_accounts: set[str] = set()
        self.org_memberships: list[tuple[str, str, str]] = []
        self.team_memberships: list[tuple[str, str, str]] = []
        self.active_calls: list[str] = []
        self.password_calls: list[tuple[str, str]] = []
        self.last_login_calls: list[str] = []

    def normalize_email(self, email: str | None) -> str:
        return str(email or "").strip().lower()

    def validate_password_policy(self, raw_password: str) -> None:
        if len(raw_password) < 12:
            raise ValueError("password must be at least 12 characters")

    async def ensure_account(self, *, email: str, role: str = "org_user", is_active: bool = False):  # noqa: ANN001, ANN201
        account = next((item for item in self.accounts.values() if item["email"] == email), None)
        if account is None:
            account_id = f"acct-{len(self.accounts) + 1}"
            account = {
                "account_id": account_id,
                "email": email,
                "role": role,
                "is_active": is_active,
                "password_hash": None,
            }
            self.accounts[account_id] = account
        return account

    async def upsert_organization_membership(self, *, account_id: str, organization_id: str, role: str) -> None:
        self.org_memberships.append((account_id, organization_id, role))

    async def upsert_team_membership(self, *, account_id: str, team_id: str, role: str) -> None:
        self.team_memberships.append((account_id, team_id, role))

    async def get_account_by_id(self, account_id: str):  # noqa: ANN201
        return self.accounts.get(account_id)

    async def get_account_auth_state(self, account_id: str):  # noqa: ANN201
        account = self.accounts.get(account_id)
        if account is None:
            return None
        return SimpleNamespace(
            account_id=account_id,
            email=account["email"],
            has_local_password=bool(account.get("password_hash")),
            has_sso_identity=account_id in self.sso_accounts,
        )

    async def set_password(self, *, account_id: str, new_password: str) -> None:
        self.password_calls.append((account_id, new_password))
        self.accounts[account_id]["password_hash"] = "set"

    async def set_account_active(self, account_id: str, *, is_active: bool) -> None:
        self.active_calls.append(account_id)
        self.accounts[account_id]["is_active"] = is_active

    async def mark_last_login(self, account_id: str) -> None:
        self.last_login_calls.append(account_id)

    async def create_login_result_for_account(self, account_id: str):  # noqa: ANN201
        account = self.accounts[account_id]
        return LoginResult(
            context=SimpleNamespace(
                account_id=account_id,
                email=account["email"],
                role="org_user",
                mfa_enabled=bool(account.get("mfa_enabled", False)),
                force_password_change=False,
            ),
            session_token="session-token",
            mfa_required=False,
            mfa_prompt=not bool(account.get("mfa_enabled", False)),
        )


class FakeDB:
    async def query_raw(self, query: str, *params):  # noqa: ANN001, ANN201
        if "FROM deltallm_teamtable" in query:
            return [{"team_id": params[0], "team_alias": "Engineering", "organization_id": "org-1"}]
        if "FROM deltallm_organizationtable" in query:
            return [{"organization_id": params[0], "organization_name": "Acme"}]
        if "SELECT email FROM deltallm_platformaccount" in query:
            return [{"email": "admin@example.com"}]
        if "SELECT account_id, email FROM deltallm_platformaccount" in query:
            return [{"account_id": params[0], "email": "admin@example.com"}]
        return []


def _config():
    return SimpleNamespace(general_settings=SimpleNamespace(instance_name="DeltaLLM", invitation_token_ttl_hours=72))


@pytest.mark.asyncio
async def test_create_invitation_creates_pending_account_memberships_and_email() -> None:
    identity_service = FakePlatformIdentityService()
    service = InvitationService(
        db_client=FakeDB(),
        repository=FakeInvitationRepository(),
        token_service=FakeTokenService(),
        outbox_service=FakeOutboxService(),
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    response = await service.create_invitation(
        email="User@Example.com",
        invited_by_account_id="acct-admin",
        organization_id="org-1",
        organization_role="org_admin",
        team_id="team-1",
        team_role="team_developer",
    )

    assert response["email"] == "user@example.com"
    assert response["status"] == "sent"
    assert identity_service.org_memberships == []
    assert identity_service.team_memberships == []


@pytest.mark.asyncio
async def test_create_invitation_keeps_distinct_pending_records_per_scope() -> None:
    repository = FakeInvitationRepository()
    identity_service = FakePlatformIdentityService()
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=FakeTokenService(),
        outbox_service=FakeOutboxService(),
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    first = await service.create_invitation(
        email="user@example.com",
        invited_by_account_id="acct-admin",
        organization_id="org-1",
        organization_role="org_member",
    )
    second = await service.create_invitation(
        email="user@example.com",
        invited_by_account_id="acct-admin",
        organization_id="org-2",
        organization_role="org_member",
    )

    assert first["invitation_id"] != second["invitation_id"]
    assert len(repository.records) == 2


@pytest.mark.asyncio
async def test_create_invitation_rejects_suppressed_recipient_delivery_and_consumes_new_token() -> None:
    repository = FakeInvitationRepository()
    token_service = FakeTokenService()
    outbox_service = FakeOutboxService(status="cancelled")
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=token_service,
        outbox_service=outbox_service,
        platform_identity_service=FakePlatformIdentityService(),
        config_getter=_config,
    )

    with pytest.raises(ValueError, match="invitation email cannot be delivered"):
        await service.create_invitation(
            email="user@example.com",
            invited_by_account_id="acct-admin",
            organization_id="org-1",
            organization_role="org_member",
        )

    assert len(repository.records) == 1
    stored = repository.records["inv-1"]
    assert stored.status == "pending"
    assert token_service.consumed_token_ids == ["tok-inv-1"]
    assert token_service.invalidated == []


@pytest.mark.asyncio
async def test_accept_invitation_requires_password_when_account_has_none() -> None:
    repository = FakeInvitationRepository()
    token_service = FakeTokenService()
    outbox_service = FakeOutboxService()
    identity_service = FakePlatformIdentityService()
    await identity_service.ensure_account(email="user@example.com", is_active=False)
    invitation = await repository.create(
        PlatformInvitationRecord(
            invitation_id="inv-1",
            account_id="acct-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={"organization_invites": [{"organization_id": "org-1", "role": "org_member"}]},
        )
    )
    token_service.by_raw_token["raw-inv-1"] = SimpleNamespace(
        token_id="tok-inv-1",
        account_id="acct-1",
        email="user@example.com",
        invitation_id=invitation.invitation_id,
        expires_at=invitation.expires_at,
    )
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=token_service,
        outbox_service=outbox_service,
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    with pytest.raises(ValueError, match="password is required"):
        await service.accept_invitation(raw_token="raw-inv-1", password=None)

    login = await service.accept_invitation(raw_token="raw-inv-1", password="very-secure-password")

    assert login is not None
    assert login.session_established is True
    assert identity_service.password_calls == [("acct-1", "very-secure-password")]
    assert identity_service.active_calls == ["acct-1"]
    assert identity_service.last_login_calls == ["acct-1"]
    assert identity_service.org_memberships == [("acct-1", "org-1", "org_member")]


@pytest.mark.asyncio
async def test_accept_invitation_rejects_replay_after_claim() -> None:
    repository = FakeInvitationRepository()
    token_service = FakeTokenService()
    identity_service = FakePlatformIdentityService()
    await identity_service.ensure_account(email="user@example.com", is_active=False)
    invitation = await repository.create(
        PlatformInvitationRecord(
            invitation_id="inv-1",
            account_id="acct-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={"organization_invites": [{"organization_id": "org-1", "role": "org_member"}]},
        )
    )
    token_service.by_raw_token["raw-inv-1"] = SimpleNamespace(
        token_id="tok-inv-1",
        account_id="acct-1",
        email="user@example.com",
        invitation_id=invitation.invitation_id,
        expires_at=invitation.expires_at,
    )
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=token_service,
        outbox_service=FakeOutboxService(),
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    first = await service.accept_invitation(raw_token="raw-inv-1", password="very-secure-password")
    second = await service.accept_invitation(raw_token="raw-inv-1", password="very-secure-password")

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_accept_invitation_existing_password_accepts_without_password_change() -> None:
    repository = FakeInvitationRepository()
    token_service = FakeTokenService()
    identity_service = FakePlatformIdentityService()
    account = await identity_service.ensure_account(email="user@example.com", is_active=True)
    account["password_hash"] = "existing-hash"
    invitation = await repository.create(
        PlatformInvitationRecord(
            invitation_id="inv-1",
            account_id="acct-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={"organization_invites": [{"organization_id": "org-1", "role": "org_member"}]},
        )
    )
    token_service.by_raw_token["raw-inv-1"] = SimpleNamespace(
        token_id="tok-inv-1",
        account_id="acct-1",
        email="user@example.com",
        invitation_id=invitation.invitation_id,
        expires_at=invitation.expires_at,
    )
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=token_service,
        outbox_service=FakeOutboxService(),
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    login = await service.accept_invitation(raw_token="raw-inv-1", password=None)

    assert login is not None
    assert login.session_established is True
    assert identity_service.password_calls == []
    assert identity_service.org_memberships == [("acct-1", "org-1", "org_member")]


@pytest.mark.asyncio
async def test_describe_invitation_token_does_not_require_password_for_sso_only_account() -> None:
    repository = FakeInvitationRepository()
    token_service = FakeTokenService()
    identity_service = FakePlatformIdentityService()
    account = await identity_service.ensure_account(email="user@example.com", is_active=True)
    identity_service.sso_accounts.add(str(account["account_id"]))
    invitation = await repository.create(
        PlatformInvitationRecord(
            invitation_id="inv-1",
            account_id="acct-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={"organization_invites": [{"organization_id": "org-1", "role": "org_member"}]},
        )
    )
    token_service.by_raw_token["raw-inv-1"] = SimpleNamespace(
        token_id="tok-inv-1",
        account_id="acct-1",
        email="user@example.com",
        invitation_id=invitation.invitation_id,
        expires_at=invitation.expires_at,
    )
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=token_service,
        outbox_service=FakeOutboxService(),
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    description = await service.describe_invitation_token("raw-inv-1")

    assert description is not None
    assert description.password_required is False


@pytest.mark.asyncio
async def test_accept_invitation_allows_sso_only_account_without_password() -> None:
    repository = FakeInvitationRepository()
    token_service = FakeTokenService()
    identity_service = FakePlatformIdentityService()
    account = await identity_service.ensure_account(email="user@example.com", is_active=True)
    identity_service.sso_accounts.add(str(account["account_id"]))
    invitation = await repository.create(
        PlatformInvitationRecord(
            invitation_id="inv-1",
            account_id="acct-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={"organization_invites": [{"organization_id": "org-1", "role": "org_member"}]},
        )
    )
    token_service.by_raw_token["raw-inv-1"] = SimpleNamespace(
        token_id="tok-inv-1",
        account_id="acct-1",
        email="user@example.com",
        invitation_id=invitation.invitation_id,
        expires_at=invitation.expires_at,
    )
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=token_service,
        outbox_service=FakeOutboxService(),
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    login = await service.accept_invitation(raw_token="raw-inv-1", password=None)

    assert login is not None
    assert login.session_established is True
    assert identity_service.password_calls == []
    assert identity_service.org_memberships == [("acct-1", "org-1", "org_member")]


@pytest.mark.asyncio
async def test_accept_invitation_rejects_password_for_sso_only_account() -> None:
    repository = FakeInvitationRepository()
    token_service = FakeTokenService()
    identity_service = FakePlatformIdentityService()
    account = await identity_service.ensure_account(email="user@example.com", is_active=True)
    identity_service.sso_accounts.add(str(account["account_id"]))
    invitation = await repository.create(
        PlatformInvitationRecord(
            invitation_id="inv-1",
            account_id="acct-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={"organization_invites": [{"organization_id": "org-1", "role": "org_member"}]},
        )
    )
    token_service.by_raw_token["raw-inv-1"] = SimpleNamespace(
        token_id="tok-inv-1",
        account_id="acct-1",
        email="user@example.com",
        invitation_id=invitation.invitation_id,
        expires_at=invitation.expires_at,
    )
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=token_service,
        outbox_service=FakeOutboxService(),
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    with pytest.raises(ValueError, match="password cannot be changed"):
        await service.accept_invitation(raw_token="raw-inv-1", password="very-secure-password")

    assert identity_service.password_calls == []


@pytest.mark.asyncio
async def test_accept_invitation_rejects_password_change_for_existing_password_account() -> None:
    repository = FakeInvitationRepository()
    token_service = FakeTokenService()
    identity_service = FakePlatformIdentityService()
    account = await identity_service.ensure_account(email="user@example.com", is_active=True)
    account["password_hash"] = "existing-hash"
    invitation = await repository.create(
        PlatformInvitationRecord(
            invitation_id="inv-1",
            account_id="acct-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={"organization_invites": [{"organization_id": "org-1", "role": "org_member"}]},
        )
    )
    token_service.by_raw_token["raw-inv-1"] = SimpleNamespace(
        token_id="tok-inv-1",
        account_id="acct-1",
        email="user@example.com",
        invitation_id=invitation.invitation_id,
        expires_at=invitation.expires_at,
    )
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=token_service,
        outbox_service=FakeOutboxService(),
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    with pytest.raises(ValueError, match="password cannot be changed"):
        await service.accept_invitation(raw_token="raw-inv-1", password="very-secure-password")

    assert identity_service.password_calls == []
    assert repository.records["inv-1"].status == "sent"


@pytest.mark.asyncio
async def test_create_invitation_rejects_invalid_role_values() -> None:
    service = InvitationService(
        db_client=FakeDB(),
        repository=FakeInvitationRepository(),
        token_service=FakeTokenService(),
        outbox_service=FakeOutboxService(),
        platform_identity_service=FakePlatformIdentityService(),
        config_getter=_config,
    )

    with pytest.raises(ValueError, match="invalid organization role"):
        await service.create_invitation(
            email="user@example.com",
            invited_by_account_id="acct-admin",
            organization_id="org-1",
            organization_role="bad-role",
        )


@pytest.mark.asyncio
async def test_create_invitation_rejects_team_and_org_scope_mismatch() -> None:
    service = InvitationService(
        db_client=FakeDB(),
        repository=FakeInvitationRepository(),
        token_service=FakeTokenService(),
        outbox_service=FakeOutboxService(),
        platform_identity_service=FakePlatformIdentityService(),
        config_getter=_config,
    )

    with pytest.raises(ValueError, match="team_id does not belong to organization_id"):
        await service.create_invitation(
            email="user@example.com",
            invited_by_account_id="acct-admin",
            organization_id="org-2",
            organization_role="org_member",
            team_id="team-1",
            team_role="team_viewer",
        )


@pytest.mark.asyncio
async def test_accept_invitation_applies_team_and_org_memberships_on_accept() -> None:
    repository = FakeInvitationRepository()
    token_service = FakeTokenService()
    identity_service = FakePlatformIdentityService()
    await identity_service.ensure_account(email="user@example.com", is_active=True)
    invitation = await repository.create(
        PlatformInvitationRecord(
            invitation_id="inv-1",
            account_id="acct-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="mixed",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={
                "organization_invites": [{"organization_id": "org-1", "role": "org_admin"}],
                "team_invites": [{"team_id": "team-1", "organization_id": "org-1", "role": "team_developer"}],
            },
        )
    )
    token_service.by_raw_token["raw-inv-1"] = SimpleNamespace(
        token_id="tok-inv-1",
        account_id="acct-1",
        email="user@example.com",
        invitation_id=invitation.invitation_id,
        expires_at=invitation.expires_at,
    )
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=token_service,
        outbox_service=FakeOutboxService(),
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    login = await service.accept_invitation(raw_token="raw-inv-1", password="very-secure-password")

    assert login is not None
    assert login.session_established is True
    assert identity_service.org_memberships == [("acct-1", "org-1", "org_admin")]
    assert identity_service.team_memberships == [("acct-1", "team-1", "team_developer")]


@pytest.mark.asyncio
async def test_accept_invitation_does_not_create_session_for_mfa_enabled_account() -> None:
    repository = FakeInvitationRepository()
    token_service = FakeTokenService()
    identity_service = FakePlatformIdentityService()
    account = await identity_service.ensure_account(email="user@example.com", is_active=True)
    account["password_hash"] = "existing-hash"
    account["mfa_enabled"] = True
    invitation = await repository.create(
        PlatformInvitationRecord(
            invitation_id="inv-1",
            account_id="acct-1",
            email="user@example.com",
            status="sent",
            invite_scope_type="organization",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            metadata={"organization_invites": [{"organization_id": "org-1", "role": "org_member"}]},
        )
    )
    token_service.by_raw_token["raw-inv-1"] = SimpleNamespace(
        token_id="tok-inv-1",
        account_id="acct-1",
        email="user@example.com",
        invitation_id=invitation.invitation_id,
        expires_at=invitation.expires_at,
    )
    service = InvitationService(
        db_client=FakeDB(),
        repository=repository,
        token_service=token_service,
        outbox_service=FakeOutboxService(),
        platform_identity_service=identity_service,
        config_getter=_config,
    )

    result = await service.accept_invitation(raw_token="raw-inv-1", password=None)

    assert result is not None
    assert result.session_established is False
    assert result.next_step == "login"
    assert result.mfa_required is True
    assert identity_service.last_login_calls == []
    assert identity_service.org_memberships == [("acct-1", "org-1", "org_member")]
