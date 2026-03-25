from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import secrets
from typing import Any

from src.db.email_tokens import EmailTokenRecord, EmailTokenRepository


@dataclass(frozen=True)
class TokenIssueResult:
    raw_token: str
    record: EmailTokenRecord


class EmailTokenService:
    def __init__(self, *, repository: EmailTokenRepository, salt: str, config_getter) -> None:  # noqa: ANN001
        self.repository = repository
        self.salt = salt
        self._config_getter = config_getter

    def with_repository(self, repository: EmailTokenRepository) -> EmailTokenService:
        return EmailTokenService(
            repository=repository,
            salt=self.salt,
            config_getter=self._config_getter,
        )

    async def issue_invitation_token(
        self,
        *,
        account_id: str,
        email: str,
        invitation_id: str,
        created_by_account_id: str | None,
    ) -> TokenIssueResult:
        ttl_hours = int(getattr(self._general_settings(), "invitation_token_ttl_hours", 72) or 72)
        return await self._issue_token(
            purpose="invite_accept",
            account_id=account_id,
            email=email,
            invitation_id=invitation_id,
            created_by_account_id=created_by_account_id,
            expires_at=datetime.now(tz=UTC) + timedelta(hours=ttl_hours),
        )

    async def issue_password_reset_token(
        self,
        *,
        account_id: str,
        email: str,
        created_by_account_id: str | None = None,
    ) -> TokenIssueResult:
        ttl_minutes = int(getattr(self._general_settings(), "password_reset_token_ttl_minutes", 60) or 60)
        return await self._issue_token(
            purpose="password_reset",
            account_id=account_id,
            email=email,
            invitation_id=None,
            created_by_account_id=created_by_account_id,
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=ttl_minutes),
        )

    async def validate_token(self, *, purpose: str, raw_token: str) -> EmailTokenRecord | None:
        token_hash = self._hash_token(purpose=purpose, raw_token=raw_token)
        return await self.repository.get_active_by_hash(purpose=purpose, token_hash=token_hash)

    async def claim_token(self, *, purpose: str, raw_token: str) -> EmailTokenRecord | None:
        token_hash = self._hash_token(purpose=purpose, raw_token=raw_token)
        return await self.repository.claim_active_by_hash(purpose=purpose, token_hash=token_hash)

    async def consume_token(self, *, token_id: str) -> bool:
        return await self.repository.consume(token_id)

    async def invalidate_active_tokens(
        self,
        *,
        purpose: str,
        account_id: str | None = None,
        invitation_id: str | None = None,
        exclude_token_id: str | None = None,
    ) -> int:
        return await self.repository.invalidate_active(
            purpose=purpose,
            account_id=account_id,
            invitation_id=invitation_id,
            exclude_token_id=exclude_token_id,
        )

    def build_action_url(self, *, path: str, raw_token: str) -> str:
        base_url = str(getattr(self._general_settings(), "email_base_url", "") or "").rstrip("/")
        suffix = f"{path}?token={raw_token}"
        if base_url:
            return f"{base_url}{suffix}"
        return suffix

    async def _issue_token(
        self,
        *,
        purpose: str,
        account_id: str,
        email: str,
        invitation_id: str | None,
        created_by_account_id: str | None,
        expires_at: datetime,
    ) -> TokenIssueResult:
        raw_token = f"etk_{secrets.token_urlsafe(32)}"
        record = await self.repository.create(
            EmailTokenRecord(
                token_id="",
                purpose=purpose,
                token_hash=self._hash_token(purpose=purpose, raw_token=raw_token),
                account_id=account_id,
                email=email,
                invitation_id=invitation_id,
                expires_at=expires_at,
                created_by_account_id=created_by_account_id,
            )
        )
        return TokenIssueResult(raw_token=raw_token, record=record)

    def _hash_token(self, *, purpose: str, raw_token: str) -> str:
        return hashlib.sha256(f"{self.salt}:email-token:{purpose}:{raw_token}".encode("utf-8")).hexdigest()

    def _general_settings(self) -> Any:
        cfg = self._config_getter()
        return getattr(cfg, "general_settings", None)
