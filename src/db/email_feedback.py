from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return None


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


@dataclass(frozen=True)
class EmailSuppressionRecord:
    email_address: str
    provider: str
    reason: str
    source: str
    provider_message_id: str | None = None
    webhook_event_id: str | None = None
    metadata: dict[str, Any] | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class EmailWebhookEventRecord:
    webhook_event_id: str
    provider: str
    event_type: str
    recipient_address: str | None = None
    provider_message_id: str | None = None
    email_id: str | None = None
    payload_json: dict[str, Any] | None = None
    occurred_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EmailFeedbackRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def create_webhook_event(self, record: EmailWebhookEventRecord) -> bool:
        if self.prisma is None:
            return True
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_emailwebhookevent (
                webhook_event_id, provider, event_type, recipient_address,
                provider_message_id, email_id, payload_json, occurred_at, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::timestamptz, NOW(), NOW())
            ON CONFLICT (webhook_event_id)
            DO NOTHING
            RETURNING webhook_event_id
            """,
            record.webhook_event_id,
            record.provider,
            record.event_type,
            record.recipient_address,
            record.provider_message_id,
            record.email_id,
            json.dumps(record.payload_json) if record.payload_json is not None else None,
            record.occurred_at,
        )
        return bool(rows)

    async def resolve_email_id_by_provider_message_id(
        self,
        *,
        provider: str,
        provider_message_id: str | None,
    ) -> str | None:
        if self.prisma is None or not provider_message_id:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT email_id
            FROM deltallm_emailoutbox
            WHERE provider = $1
              AND last_provider_message_id = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            provider,
            provider_message_id,
        )
        if not rows:
            return None
        return str(rows[0].get("email_id") or "") or None

    async def upsert_suppression(
        self,
        *,
        email_address: str,
        provider: str,
        reason: str,
        source: str,
        provider_message_id: str | None = None,
        webhook_event_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EmailSuppressionRecord:
        normalized_email = str(email_address or "").strip().lower()
        if self.prisma is None:
            return EmailSuppressionRecord(
                email_address=normalized_email,
                provider=provider,
                reason=reason,
                source=source,
                provider_message_id=provider_message_id,
                webhook_event_id=webhook_event_id,
                metadata=metadata,
            )
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_emailsuppression (
                email_address, provider, reason, source, provider_message_id, webhook_event_id,
                metadata, first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW(), NOW(), NOW(), NOW())
            ON CONFLICT (email_address)
            DO UPDATE SET
                provider = EXCLUDED.provider,
                reason = EXCLUDED.reason,
                source = EXCLUDED.source,
                provider_message_id = EXCLUDED.provider_message_id,
                webhook_event_id = EXCLUDED.webhook_event_id,
                metadata = EXCLUDED.metadata,
                last_seen_at = NOW(),
                updated_at = NOW()
            RETURNING
                email_address, provider, reason, source, provider_message_id, webhook_event_id,
                metadata, first_seen_at, last_seen_at, created_at, updated_at
            """,
            normalized_email,
            provider,
            reason,
            source,
            provider_message_id,
            webhook_event_id,
            json.dumps(metadata) if metadata is not None else None,
        )
        return self._suppression_from_row(rows[0])

    async def get_suppressed_addresses(self, addresses: list[str]) -> set[str]:
        normalized = sorted({str(item or "").strip().lower() for item in addresses if str(item or "").strip()})
        if self.prisma is None or not normalized:
            return set()
        placeholders = ", ".join(f"${index + 1}" for index in range(len(normalized)))
        rows = await self.prisma.query_raw(
            f"""
            SELECT email_address
            FROM deltallm_emailsuppression
            WHERE email_address IN ({placeholders})
            """,
            *normalized,
        )
        return {str(row.get("email_address") or "").strip().lower() for row in rows if row.get("email_address")}

    async def list_suppressions(self, *, limit: int = 100, search: str | None = None) -> list[EmailSuppressionRecord]:
        if self.prisma is None:
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if search and search.strip():
            params.append(f"%{search.strip().lower()}%")
            clauses.append(f"email_address ILIKE ${len(params)}")
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                email_address, provider, reason, source, provider_message_id, webhook_event_id,
                metadata, first_seen_at, last_seen_at, created_at, updated_at
            FROM deltallm_emailsuppression
            {where_sql}
            ORDER BY last_seen_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        return [self._suppression_from_row(row) for row in rows]

    async def remove_suppression(self, email_address: str) -> bool:
        if self.prisma is None:
            return False
        deleted = await self.prisma.execute_raw(
            "DELETE FROM deltallm_emailsuppression WHERE email_address = $1",
            str(email_address or "").strip().lower(),
        )
        return int(deleted or 0) > 0

    def _suppression_from_row(self, row: Any) -> EmailSuppressionRecord:
        data = dict(row)
        return EmailSuppressionRecord(
            email_address=str(data.get("email_address") or ""),
            provider=str(data.get("provider") or ""),
            reason=str(data.get("reason") or ""),
            source=str(data.get("source") or ""),
            provider_message_id=str(data.get("provider_message_id") or "") or None,
            webhook_event_id=str(data.get("webhook_event_id") or "") or None,
            metadata=_parse_json_object(data.get("metadata")),
            first_seen_at=_coerce_datetime(data.get("first_seen_at")),
            last_seen_at=_coerce_datetime(data.get("last_seen_at")),
            created_at=_coerce_datetime(data.get("created_at")),
            updated_at=_coerce_datetime(data.get("updated_at")),
        )
