from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class PlatformAccountDatabase(Protocol):
    async def execute_raw(self, query: str, *params: object) -> int: ...

    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]: ...


@dataclass(frozen=True, slots=True)
class PlatformAccountRecord:
    account_id: str
    email: str
    role: str
    is_active: bool

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> PlatformAccountRecord:
        return cls(
            account_id=str(row["account_id"]),
            email=str(row["email"]),
            role=str(row["role"]),
            is_active=row.get("is_active") is True,
        )


async def insert_platform_account_if_absent(
    db: PlatformAccountDatabase, *, email: str, role: str, is_active: bool
) -> PlatformAccountRecord | None:
    """An inserted row is proof of creation; a conflict grants no identity ownership."""
    rows = await db.query_raw(
        """
        INSERT INTO deltallm_platformaccount (
            account_id, email, role, is_active, force_password_change, mfa_enabled, created_at, updated_at
        )
        VALUES (gen_random_uuid(), $1, $2, $3, false, false, NOW(), NOW())
        ON CONFLICT (email) DO NOTHING
        RETURNING account_id, email, role, is_active
        """,
        email,
        role,
        is_active,
    )
    return PlatformAccountRecord.from_row(rows[0]) if rows else None


async def refresh_sso_account(
    db: PlatformAccountDatabase,
    *,
    account_id: str,
    verified_email: str | None,
    matched_email: str | None,
) -> PlatformAccountRecord:
    """Lock and return current authorization state without replacing admin decisions."""
    rows = await db.query_raw(
        """
        UPDATE deltallm_platformaccount
        SET email = COALESCE($2::text, email), updated_at = NOW()
        WHERE account_id = $1
          AND ($3::text IS NULL OR lower(email) = lower($3))
        RETURNING account_id, email, role, is_active
        """,
        account_id,
        verified_email,
        matched_email,
    )
    if not rows:
        raise ValueError("SSO account changed; please sign in again")
    return PlatformAccountRecord.from_row(rows[0])


async def ensure_platform_account(
    db: PlatformAccountDatabase, *, email: str, role: str, is_active: bool
) -> None:
    """Seed account defaults without replacing durable administrator decisions."""
    await db.execute_raw(
        """
        INSERT INTO deltallm_platformaccount (
            account_id, email, role, is_active, force_password_change, mfa_enabled, created_at, updated_at
        )
        VALUES (gen_random_uuid(), $1, $2, $3, false, false, NOW(), NOW())
        ON CONFLICT (email)
        DO UPDATE SET updated_at = NOW()
        """,
        email,
        role,
        is_active,
    )
