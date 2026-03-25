from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _parse_json_object(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return None


@dataclass
class PlatformInvitationRecord:
    invitation_id: str
    account_id: str
    email: str
    status: str
    invite_scope_type: str
    expires_at: datetime
    invited_by_account_id: str | None = None
    message_email_id: str | None = None
    accepted_at: datetime | None = None
    cancelled_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InvitationRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def create(self, record: PlatformInvitationRecord) -> PlatformInvitationRecord:
        if self.prisma is None:
            return record
        invitation_id = record.invitation_id or str(uuid4())
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_platforminvitation (
                invitation_id, account_id, email, status, invite_scope_type,
                invited_by_account_id, message_email_id, expires_at, accepted_at,
                cancelled_at, metadata, created_at, updated_at
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8::timestamptz, $9::timestamptz,
                $10::timestamptz, $11::jsonb, NOW(), NOW()
            )
            RETURNING
                invitation_id, account_id, email, status, invite_scope_type,
                invited_by_account_id, message_email_id, expires_at, accepted_at,
                cancelled_at, metadata, created_at, updated_at
            """,
            invitation_id,
            record.account_id,
            record.email,
            record.status,
            record.invite_scope_type,
            record.invited_by_account_id,
            record.message_email_id,
            record.expires_at,
            record.accepted_at,
            record.cancelled_at,
            json.dumps(record.metadata) if record.metadata is not None else None,
        )
        return self._row_to_record(rows[0])

    async def get_by_id(self, invitation_id: str) -> PlatformInvitationRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                invitation_id, account_id, email, status, invite_scope_type,
                invited_by_account_id, message_email_id, expires_at, accepted_at,
                cancelled_at, metadata, created_at, updated_at
            FROM deltallm_platforminvitation
            WHERE invitation_id = $1
            LIMIT 1
            """,
            invitation_id,
        )
        if not rows:
            return None
        return self._row_to_record(rows[0])

    async def get_latest_pending_by_account_id(self, account_id: str) -> PlatformInvitationRecord | None:
        pending = await self.list_pending_by_account_id(account_id)
        return pending[0] if pending else None

    async def list_pending_by_account_id(self, account_id: str) -> list[PlatformInvitationRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT
                invitation_id, account_id, email, status, invite_scope_type,
                invited_by_account_id, message_email_id, expires_at, accepted_at,
                cancelled_at, metadata, created_at, updated_at
            FROM deltallm_platforminvitation
            WHERE account_id = $1
              AND status IN ('pending', 'sent')
            ORDER BY created_at DESC
            """,
            account_id,
        )
        return [self._row_to_record(row) for row in rows]

    async def update_pending(
        self,
        *,
        invitation_id: str,
        invite_scope_type: str,
        invited_by_account_id: str | None,
        expires_at: datetime,
        metadata: dict[str, Any] | None,
    ) -> PlatformInvitationRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_platforminvitation
            SET status = 'pending',
                invite_scope_type = $2,
                invited_by_account_id = $3,
                expires_at = $4::timestamptz,
                accepted_at = NULL,
                cancelled_at = NULL,
                metadata = $5::jsonb,
                updated_at = NOW()
            WHERE invitation_id = $1
            RETURNING
                invitation_id, account_id, email, status, invite_scope_type,
                invited_by_account_id, message_email_id, expires_at, accepted_at,
                cancelled_at, metadata, created_at, updated_at
            """,
            invitation_id,
            invite_scope_type,
            invited_by_account_id,
            expires_at,
            json.dumps(metadata) if metadata is not None else None,
        )
        if not rows:
            return None
        return self._row_to_record(rows[0])

    async def mark_sent(
        self,
        *,
        invitation_id: str,
        message_email_id: str,
        expires_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> PlatformInvitationRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_platforminvitation
            SET status = 'sent',
                message_email_id = $2,
                expires_at = $3::timestamptz,
                metadata = COALESCE($4::jsonb, metadata),
                updated_at = NOW()
            WHERE invitation_id = $1
            RETURNING
                invitation_id, account_id, email, status, invite_scope_type,
                invited_by_account_id, message_email_id, expires_at, accepted_at,
                cancelled_at, metadata, created_at, updated_at
            """,
            invitation_id,
            message_email_id,
            expires_at,
            json.dumps(metadata) if metadata is not None else None,
        )
        if not rows:
            return None
        return self._row_to_record(rows[0])

    async def mark_accepted(self, invitation_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_platforminvitation
            SET status = 'accepted',
                accepted_at = NOW(),
                updated_at = NOW()
            WHERE invitation_id = $1
              AND status IN ('pending', 'sent')
            RETURNING invitation_id
            """,
            invitation_id,
        )
        return bool(rows)

    async def mark_cancelled(self, invitation_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_platforminvitation
            SET status = 'cancelled',
                cancelled_at = NOW(),
                updated_at = NOW()
            WHERE invitation_id = $1
              AND status IN ('pending', 'sent', 'expired')
            RETURNING invitation_id
            """,
            invitation_id,
        )
        return bool(rows)

    async def mark_expired(self, invitation_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_platforminvitation
            SET status = 'expired',
                updated_at = NOW()
            WHERE invitation_id = $1
              AND status IN ('pending', 'sent')
            RETURNING invitation_id
            """,
            invitation_id,
        )
        return bool(rows)

    async def list_all(self, *, status: str | None = None, search: str | None = None) -> list[PlatformInvitationRecord]:
        if self.prisma is None:
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        if search:
            params.append(f"%{search.lower()}%")
            clauses.append(f"lower(email) LIKE ${len(params)}")
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                invitation_id, account_id, email, status, invite_scope_type,
                invited_by_account_id, message_email_id, expires_at, accepted_at,
                cancelled_at, metadata, created_at, updated_at
            FROM deltallm_platforminvitation
            {where_sql}
            ORDER BY created_at DESC
            """,
            *params,
        )
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: dict[str, Any]) -> PlatformInvitationRecord:
        return PlatformInvitationRecord(
            invitation_id=str(row.get("invitation_id") or ""),
            account_id=str(row.get("account_id") or ""),
            email=str(row.get("email") or ""),
            status=str(row.get("status") or ""),
            invite_scope_type=str(row.get("invite_scope_type") or ""),
            invited_by_account_id=str(row.get("invited_by_account_id") or "") or None,
            message_email_id=str(row.get("message_email_id") or "") or None,
            expires_at=_coerce_datetime(row.get("expires_at")) or datetime.now(tz=UTC),
            accepted_at=_coerce_datetime(row.get("accepted_at")),
            cancelled_at=_coerce_datetime(row.get("cancelled_at")),
            metadata=_parse_json_object(row.get("metadata")),
            created_at=_coerce_datetime(row.get("created_at")),
            updated_at=_coerce_datetime(row.get("updated_at")),
        )
