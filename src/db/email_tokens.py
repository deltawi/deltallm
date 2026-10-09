from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return None


@dataclass
class EmailTokenRecord:
    token_id: str
    purpose: str
    token_hash: str
    account_id: str
    email: str
    expires_at: datetime
    invitation_id: str | None = None
    consumed_at: datetime | None = None
    created_by_account_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EmailTokenRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def create(self, record: EmailTokenRecord) -> EmailTokenRecord:
        if self.prisma is None:
            return record
        token_id = record.token_id or str(uuid4())
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_emailtoken (
                token_id, purpose, token_hash, account_id, email, invitation_id,
                expires_at, consumed_at, created_by_account_id, created_at, updated_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                $7::timestamptz, $8::timestamptz, $9, NOW(), NOW()
            )
            RETURNING
                token_id, purpose, token_hash, account_id, email, invitation_id,
                expires_at, consumed_at, created_by_account_id, created_at, updated_at
            """,
            token_id,
            record.purpose,
            record.token_hash,
            record.account_id,
            record.email,
            record.invitation_id,
            record.expires_at,
            record.consumed_at,
            record.created_by_account_id,
        )
        return self._row_to_record(rows[0])

    async def get_active_by_hash(self, *, purpose: str, token_hash: str) -> EmailTokenRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                token_id, purpose, token_hash, account_id, email, invitation_id,
                expires_at, consumed_at, created_by_account_id, created_at, updated_at
            FROM deltallm_emailtoken
            WHERE purpose = $1
              AND token_hash = $2
              AND consumed_at IS NULL
              AND expires_at > NOW()
            LIMIT 1
            """,
            purpose,
            token_hash,
        )
        if not rows:
            return None
        return self._row_to_record(rows[0])

    async def claim_active_by_hash(self, *, purpose: str, token_hash: str) -> EmailTokenRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_emailtoken
            SET consumed_at = NOW(),
                updated_at = NOW()
            WHERE purpose = $1
              AND token_hash = $2
              AND consumed_at IS NULL
              AND expires_at > NOW()
            RETURNING
                token_id, purpose, token_hash, account_id, email, invitation_id,
                expires_at, consumed_at, created_by_account_id, created_at, updated_at
            """,
            purpose,
            token_hash,
        )
        if not rows:
            return None
        return self._row_to_record(rows[0])

    async def consume(self, token_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_emailtoken
            SET consumed_at = NOW(),
                updated_at = NOW()
            WHERE token_id = $1
              AND consumed_at IS NULL
            RETURNING token_id
            """,
            token_id,
        )
        return bool(rows)

    async def invalidate_active(
        self,
        *,
        purpose: str,
        account_id: str | None = None,
        invitation_id: str | None = None,
        exclude_token_id: str | None = None,
    ) -> int:
        if self.prisma is None:
            return 0
        clauses = ["purpose = $1", "consumed_at IS NULL", "expires_at > NOW()"]
        params: list[Any] = [purpose]
        if account_id:
            params.append(account_id)
            clauses.append(f"account_id = ${len(params)}")
        if invitation_id:
            params.append(invitation_id)
            clauses.append(f"invitation_id = ${len(params)}")
        if exclude_token_id:
            params.append(exclude_token_id)
            clauses.append(f"token_id <> ${len(params)}")
        rows = await self.prisma.query_raw(
            f"""
            UPDATE deltallm_emailtoken
            SET consumed_at = NOW(),
                updated_at = NOW()
            WHERE {' AND '.join(clauses)}
            RETURNING token_id
            """,
            *params,
        )
        return len(rows)

    def _row_to_record(self, row: dict[str, Any]) -> EmailTokenRecord:
        return EmailTokenRecord(
            token_id=str(row.get("token_id") or ""),
            purpose=str(row.get("purpose") or ""),
            token_hash=str(row.get("token_hash") or ""),
            account_id=str(row.get("account_id") or ""),
            email=str(row.get("email") or ""),
            invitation_id=str(row.get("invitation_id") or "") or None,
            expires_at=_coerce_datetime(row.get("expires_at")) or datetime.now(tz=UTC),
            consumed_at=_coerce_datetime(row.get("consumed_at")),
            created_by_account_id=str(row.get("created_by_account_id") or "") or None,
            created_at=_coerce_datetime(row.get("created_at")),
            updated_at=_coerce_datetime(row.get("updated_at")),
        )
