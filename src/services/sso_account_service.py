from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from src.auth.roles import PlatformRole
from src.auth.sso_identity import (
    AccountInactiveError,
    LoginSessionCreationError,
    SSOAccountMatch,
    SSOIdentityAssertion,
)
from src.db.platform_accounts import (
    PlatformAccountDatabase,
    PlatformAccountRecord,
    insert_platform_account_if_absent,
    refresh_sso_account,
)


class SSOIdentityStore(Protocol):
    async def get_account_by_sso_identity(
        self, *, provider: str, subject: str
    ) -> Mapping[str, object] | None: ...

    async def get_account_by_email(self, email: str) -> Mapping[str, object] | None: ...

    async def get_account_by_id(self, account_id: str) -> Mapping[str, object] | None: ...

    async def link_sso_identity(
        self, *, account_id: str, email: str, provider: str, subject: str
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SSOAccountResolution:
    account: PlatformAccountRecord
    match: SSOAccountMatch


class SSOAccountService:
    """Resolve and bind an SSO account inside the caller's login transaction."""

    def __init__(self, db: PlatformAccountDatabase, identities: SSOIdentityStore) -> None:
        self.db = db
        self.identities = identities

    async def resolve(
        self,
        identity: SSOIdentityAssertion,
        *,
        initial_role: str = PlatformRole.ORG_USER,
        expected_account_id: str | None = None,
    ) -> SSOAccountResolution:
        linked = await self.identities.get_account_by_sso_identity(
            provider=identity.provider, subject=identity.subject
        )
        if linked is not None:
            account = PlatformAccountRecord.from_row(linked)
            match = SSOAccountMatch.SUBJECT
            if expected_account_id is not None and account.account_id != expected_account_id:
                raise ValueError("SSO identity is already linked to another account")
        else:
            resolved = await self._resolve_unlinked(
                identity, initial_role=initial_role, expected_account_id=expected_account_id
            )
            account, match = resolved.account, resolved.match
        self._require_active(account)
        identity.require_ownership(match, role=account.role)
        if match is not SSOAccountMatch.CREATED:
            account = await self._refresh_existing(identity, account, match=match)
            self._require_active(account)
        await self.identities.link_sso_identity(
            account_id=account.account_id,
            email=account.email,
            provider=identity.provider,
            subject=identity.subject,
        )
        return SSOAccountResolution(account=account, match=match)

    async def _resolve_unlinked(
        self,
        identity: SSOIdentityAssertion,
        *,
        initial_role: str,
        expected_account_id: str | None,
    ) -> SSOAccountResolution:
        if expected_account_id is not None:
            row = await self.identities.get_account_by_id(expected_account_id)
            if row is None:
                raise RuntimeError("SSO account not found")
            account = PlatformAccountRecord.from_row(row)
            self._require_active(account)
            if account.email.strip().lower() != identity.email:
                raise ValueError("SSO email does not match account")
        else:
            created = await insert_platform_account_if_absent(
                self.db, email=identity.email, role=initial_role, is_active=True
            )
            if created is not None:
                return SSOAccountResolution(created, SSOAccountMatch.CREATED)
            row = await self.identities.get_account_by_email(identity.email)
            if row is None:
                raise LoginSessionCreationError("Failed to establish session")
            account = PlatformAccountRecord.from_row(row)
        return SSOAccountResolution(account, SSOAccountMatch.EMAIL)

    async def _refresh_existing(
        self,
        identity: SSOIdentityAssertion,
        account: PlatformAccountRecord,
        *,
        match: SSOAccountMatch,
    ) -> PlatformAccountRecord:
        verified_email = identity.email if identity.email_verified is True else None
        if verified_email is not None and verified_email != account.email:
            owner = await self.identities.get_account_by_email(verified_email)
            if owner is not None and owner["account_id"] != account.account_id:
                raise ValueError("SSO email is already linked to another account")
        return await refresh_sso_account(
            self.db,
            account_id=account.account_id,
            verified_email=verified_email,
            matched_email=identity.email if match is SSOAccountMatch.EMAIL else None,
        )

    @staticmethod
    def _require_active(account: PlatformAccountRecord) -> None:
        if not account.is_active:
            raise AccountInactiveError("Account is inactive")
