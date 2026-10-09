from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.db.email_tokens import EmailTokenRecord
from src.email.models import EmailConfigurationError
from src.services.email_token_service import EmailTokenService


class FakeEmailTokenRepository:
    def __init__(self) -> None:
        self.records: dict[str, EmailTokenRecord] = {}

    async def create(self, record: EmailTokenRecord) -> EmailTokenRecord:
        token_id = record.token_id or f"tok-{len(self.records) + 1}"
        stored = replace(record, token_id=token_id)
        self.records[token_id] = stored
        return stored

    async def get_active_by_hash(self, *, purpose: str, token_hash: str) -> EmailTokenRecord | None:
        for record in self.records.values():
            if record.purpose == purpose and record.token_hash == token_hash and record.consumed_at is None:
                return record
        return None

    async def consume(self, token_id: str) -> bool:
        record = self.records.get(token_id)
        if record is None or record.consumed_at is not None:
            return False
        self.records[token_id] = replace(record, consumed_at=datetime.now(tz=UTC))
        return True

    async def claim_active_by_hash(self, *, purpose: str, token_hash: str) -> EmailTokenRecord | None:
        for token_id, record in list(self.records.items()):
            if record.purpose != purpose or record.token_hash != token_hash or record.consumed_at is not None:
                continue
            claimed = replace(record, consumed_at=datetime.now(tz=UTC))
            self.records[token_id] = claimed
            return claimed
        return None

    async def invalidate_active(
        self,
        *,
        purpose: str,
        account_id: str | None = None,
        invitation_id: str | None = None,
        exclude_token_id: str | None = None,
    ) -> int:
        count = 0
        for token_id, record in list(self.records.items()):
            if record.purpose != purpose or record.consumed_at is not None:
                continue
            if account_id and record.account_id != account_id:
                continue
            if invitation_id and record.invitation_id != invitation_id:
                continue
            if exclude_token_id and record.token_id == exclude_token_id:
                continue
            self.records[token_id] = replace(record, consumed_at=record.expires_at)
            count += 1
        return count


def _config(**overrides):
    general_settings = SimpleNamespace(
        invitation_token_ttl_hours=72,
        password_reset_token_ttl_minutes=60,
        email_base_url="https://gateway.example.com",
    )
    for key, value in overrides.items():
        setattr(general_settings, key, value)
    return SimpleNamespace(general_settings=general_settings)


@pytest.mark.asyncio
async def test_issue_validate_consume_invitation_token() -> None:
    repository = FakeEmailTokenRepository()
    service = EmailTokenService(repository=repository, salt="test-salt", config_getter=lambda: _config())

    issued = await service.issue_invitation_token(
        account_id="acct-1",
        email="user@example.com",
        invitation_id="inv-1",
        created_by_account_id="acct-admin",
    )

    assert issued.raw_token.startswith("etk_")
    validated = await service.validate_token(purpose="invite_accept", raw_token=issued.raw_token)
    assert validated is not None
    assert validated.account_id == "acct-1"
    assert validated.invitation_id == "inv-1"

    claimed = await service.claim_token(purpose="invite_accept", raw_token=issued.raw_token)
    assert claimed is not None
    assert claimed.token_id == validated.token_id
    assert await service.validate_token(purpose="invite_accept", raw_token=issued.raw_token) is None


@pytest.mark.asyncio
async def test_issue_password_reset_token_and_invalidate_all() -> None:
    repository = FakeEmailTokenRepository()
    service = EmailTokenService(repository=repository, salt="test-salt", config_getter=lambda: _config())

    first = await service.issue_password_reset_token(account_id="acct-1", email="user@example.com")
    second = await service.issue_password_reset_token(account_id="acct-1", email="user@example.com")

    assert await service.invalidate_active_tokens(purpose="password_reset", account_id="acct-1") == 2
    assert await service.validate_token(purpose="password_reset", raw_token=first.raw_token) is None
    assert await service.validate_token(purpose="password_reset", raw_token=second.raw_token) is None


def test_build_action_url_uses_configured_base_url() -> None:
    service = EmailTokenService(repository=FakeEmailTokenRepository(), salt="test-salt", config_getter=lambda: _config())

    assert service.build_action_url(path="/accept-invite", raw_token="abc") == "https://gateway.example.com/accept-invite?token=abc"


def test_build_action_url_requires_email_base_url() -> None:
    service = EmailTokenService(
        repository=FakeEmailTokenRepository(),
        salt="test-salt",
        config_getter=lambda: _config(email_base_url=None),
    )

    with pytest.raises(EmailConfigurationError, match="email_base_url is required"):
        service.build_action_url(path="/accept-invite", raw_token="abc")


def test_build_action_url_requires_absolute_email_base_url() -> None:
    service = EmailTokenService(
        repository=FakeEmailTokenRepository(),
        salt="test-salt",
        config_getter=lambda: _config(email_base_url="gateway.example.com"),
    )

    with pytest.raises(EmailConfigurationError, match="email_base_url must be an absolute http\\(s\\) URL"):
        service.build_action_url(path="/accept-invite", raw_token="abc")
