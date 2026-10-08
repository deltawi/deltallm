from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
import secrets
from typing import Protocol

from src.auth.external_policy import EXTERNAL_SESSION_PREFIX

from src.auth.roles import PLATFORM_ROLE_PERMISSIONS, PlatformRole
from src.db.platform_sessions import PlatformSessionRepository
from src.models.platform_auth import PlatformAuthContext


class ExternalSessionResolver(Protocol):
    async def get_context(self, token_hash: str) -> PlatformAuthContext | None: ...
    async def logout(self, token_hash: str, *, correlation_id: str) -> None: ...
    async def verify_mfa(
        self, *, token_hash: str, code: str, verify_code: Callable[[str, str], bool]
    ) -> bool: ...


class PlatformSessionService:
    """Own session tokens and policy without changing the caller's transaction."""

    def __init__(
        self,
        repository: PlatformSessionRepository | None,
        *,
        salt: str,
        lifetime: timedelta,
    ) -> None:
        if not salt:
            raise ValueError("Session salt is required")
        self.external: ExternalSessionResolver | None = None
        self.repository = repository
        self.salt = salt
        self.lifetime = lifetime

    def hash_token(self, token: str) -> str:
        return hashlib.sha256(f"{self.salt}:session:{token}".encode("utf-8")).hexdigest()

    async def create(self, *, account_id: str, mfa_verified: bool) -> str:
        if self.repository is None:
            raise RuntimeError("Session database is unavailable")
        token = f"psk_{secrets.token_urlsafe(32)}"
        await self.repository.create(
            account_id=account_id,
            token_hash=self.hash_token(token),
            mfa_verified=mfa_verified,
            expires_at=datetime.now(UTC) + self.lifetime,
        )
        return token

    async def get_context(self, token: str) -> PlatformAuthContext | None:
        if not token:
            return None
        token_hash = self.hash_token(token)
        if token.startswith(EXTERNAL_SESSION_PREFIX):
            return (
                await self.external.get_context(token_hash) if self.external is not None else None
            )
        if self.repository is None:
            return None
        row = await self.repository.get_active(token_hash)
        if row is None or not row.is_active:
            return None
        await self.repository.touch(token_hash)
        role = PlatformRole.ADMIN if row.role == "platform_co_admin" else row.role
        organizations = await self.repository.organization_memberships(row.account_id)
        teams = await self.repository.team_memberships(row.account_id)
        return PlatformAuthContext(
            account_id=row.account_id,
            email=row.email,
            role=role,
            mfa_enabled=row.mfa_enabled,
            mfa_verified=row.mfa_verified,
            force_password_change=row.force_password_change,
            permissions=sorted(PLATFORM_ROLE_PERMISSIONS.get(role, set())),
            organization_memberships=[
                {"organization_id": item.organization_id, "role": item.role}
                for item in organizations
            ],
            team_memberships=[{"team_id": item.team_id, "role": item.role} for item in teams],
            session_expires_at=row.expires_at,
        )

    async def revoke(self, token: str) -> None:
        if token.startswith(EXTERNAL_SESSION_PREFIX):
            if self.external is not None:
                await self.external.logout(
                    self.hash_token(token), correlation_id=secrets.token_hex(16)
                )
            return
        if self.repository is not None:
            await self.repository.revoke(self.hash_token(token))

    async def revoke_for_account(self, account_id: str) -> None:
        if self.repository is not None:
            await self.repository.revoke_for_account(account_id)

    async def mark_mfa_verified(self, token: str) -> bool:
        if self.repository is None or not token:
            return False
        if token.startswith(EXTERNAL_SESSION_PREFIX):
            return False
        return await self.repository.mark_mfa_verified(self.hash_token(token))

    async def verify_mfa(
        self, *, token: str, code: str, verify_code: Callable[[str, str], bool]
    ) -> bool:
        if self.repository is None or not token:
            return False
        if token.startswith(EXTERNAL_SESSION_PREFIX):
            if self.external is None:
                return False
            return await self.external.verify_mfa(
                token_hash=self.hash_token(token), code=code, verify_code=verify_code
            )
        row = await self.repository.get_mfa(self.hash_token(token))
        if row is None or not row.is_active or not row.enabled or row.secret is None:
            return False
        if not verify_code(row.secret, code):
            return False
        return await self.mark_mfa_verified(token)
