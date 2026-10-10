from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.db.identity.platform_accounts import PlatformAccountDatabase


@dataclass(frozen=True, slots=True)
class PlatformSessionRecord:
    account_id: str
    email: str
    role: str
    is_active: bool
    mfa_enabled: bool
    mfa_verified: bool
    force_password_change: bool
    expires_at: datetime

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> PlatformSessionRecord:
        expiry = row["expires_at"]
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if not isinstance(expiry, datetime):
            raise ValueError("Invalid stored session expiry")
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return cls(
            account_id=str(row["account_id"]),
            email=str(row.get("email") or ""),
            role=str(row.get("role") or "org_user"),
            is_active=row.get("is_active") is True,
            mfa_enabled=row.get("mfa_enabled") is True,
            mfa_verified=row.get("mfa_verified") is True,
            force_password_change=row.get("force_password_change") is True,
            expires_at=expiry.astimezone(UTC),
        )


@dataclass(frozen=True, slots=True)
class OrganizationSessionMembership:
    organization_id: str
    role: str


@dataclass(frozen=True, slots=True)
class TeamSessionMembership:
    team_id: str
    role: str


@dataclass(frozen=True, slots=True)
class SessionMFARecord:
    is_active: bool
    enabled: bool
    secret: str | None = field(repr=False)


class PlatformSessionRepository:
    """Read current account state and persist opaque platform sessions."""

    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def get_active(self, token_hash: str) -> PlatformSessionRecord | None:
        rows = await self.db.query_raw(
            """
            SELECT
                s.account_id,
                s.mfa_verified,
                s.expires_at,
                a.email,
                a.role,
                a.force_password_change,
                a.mfa_enabled,
                a.is_active
            FROM deltallm_platformsession s
            JOIN deltallm_platformaccount a ON a.account_id = s.account_id
            WHERE s.session_token_hash = $1
              AND s.external_parent_id IS NULL
              AND s.revoked_at IS NULL
              AND s.expires_at > NOW()
            LIMIT 1
            """,
            token_hash,
        )
        return PlatformSessionRecord.from_row(rows[0]) if rows else None

    async def touch(self, token_hash: str) -> None:
        await self.db.execute_raw(
            "UPDATE deltallm_platformsession SET last_seen_at = NOW() WHERE session_token_hash = $1",
            token_hash,
        )

    async def organization_memberships(
        self, account_id: str
    ) -> list[OrganizationSessionMembership]:
        rows = await self.db.query_raw(
            """
            SELECT m.organization_id, m.role
            FROM deltallm_organizationmembership m
            JOIN deltallm_organizationtable o
              ON o.organization_id = m.organization_id
            WHERE m.account_id = $1
              AND o.lifecycle_state = 'active'
            """,
            account_id,
        )
        return [
            OrganizationSessionMembership(str(row["organization_id"]), str(row["role"]))
            for row in rows
        ]

    async def team_memberships(self, account_id: str) -> list[TeamSessionMembership]:
        rows = await self.db.query_raw(
            """
            SELECT m.team_id, m.role
            FROM deltallm_teammembership m
            JOIN deltallm_teamtable t ON t.team_id = m.team_id
            LEFT JOIN deltallm_organizationtable o
              ON o.organization_id = t.organization_id
            WHERE m.account_id = $1
              AND (t.organization_id IS NULL OR o.lifecycle_state = 'active')
            """,
            account_id,
        )
        return [TeamSessionMembership(str(row["team_id"]), str(row["role"])) for row in rows]

    async def create(
        self, *, account_id: str, token_hash: str, mfa_verified: bool, expires_at: datetime
    ) -> None:
        await self.db.execute_raw(
            """
            INSERT INTO deltallm_platformsession (
                session_id, account_id, session_token_hash, mfa_verified,
                expires_at, created_at, updated_at, last_seen_at
            )
            VALUES (gen_random_uuid(), $1, $2, $3, $4::timestamptz, NOW(), NOW(), NOW())
            """,
            account_id,
            token_hash,
            mfa_verified,
            expires_at,
        )

    async def revoke(self, token_hash: str) -> None:
        await self.db.execute_raw(
            "UPDATE deltallm_platformsession SET revoked_at = NOW(), updated_at = NOW() WHERE session_token_hash = $1",
            token_hash,
        )

    async def revoke_for_account(self, account_id: str) -> None:
        await self.db.execute_raw(
            """
            WITH parents AS (
                UPDATE deltallm_externalauthparentsession p
                SET revoked_at = COALESCE(p.revoked_at, NOW()), updated_at = NOW()
                FROM deltallm_externalauthsubject s
                WHERE s.subject_id = p.subject_id AND s.account_id = $1
                RETURNING p.parent_id
            ) UPDATE deltallm_platformsession
            SET revoked_at = NOW(), updated_at = NOW()
            WHERE account_id = $1
              AND revoked_at IS NULL
            """,
            account_id,
        )

    async def mark_mfa_verified(self, token_hash: str) -> bool:
        rows = await self.db.query_raw(
            """
            UPDATE deltallm_platformsession
            SET mfa_verified = true,
                updated_at = NOW(),
                last_seen_at = NOW()
            WHERE session_token_hash = $1
              AND revoked_at IS NULL
              AND expires_at > NOW()
            RETURNING session_id
            """,
            token_hash,
        )
        return bool(rows)

    async def get_mfa(self, token_hash: str, *, external: bool = False) -> SessionMFARecord | None:
        rows = await self.db.query_raw(
            """
            SELECT a.mfa_enabled, a.mfa_secret, a.is_active
            FROM deltallm_platformsession s
            JOIN deltallm_platformaccount a ON a.account_id = s.account_id
            WHERE s.session_token_hash = $1
              AND (s.external_parent_id IS NOT NULL) = $2
              AND s.revoked_at IS NULL
              AND s.expires_at > NOW()
            LIMIT 1
            """,
            token_hash,
            external,
        )
        if not rows:
            return None
        row = rows[0]
        secret = row.get("mfa_secret")
        return SessionMFARecord(
            is_active=row.get("is_active") is True,
            enabled=row.get("mfa_enabled") is True,
            secret=secret if isinstance(secret, str) else None,
        )
