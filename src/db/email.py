from __future__ import annotations

import json
from dataclasses import dataclass, field
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
class EmailOutboxRecord:
    email_id: str
    kind: str
    provider: str
    to_addresses: list[str]
    from_address: str
    subject: str
    text_body: str
    cc_addresses: list[str] = field(default_factory=list)
    bcc_addresses: list[str] = field(default_factory=list)
    reply_to: str | None = None
    template_key: str | None = None
    payload_json: dict[str, Any] | None = None
    html_body: str | None = None
    status: str = "queued"
    attempt_count: int = 0
    max_attempts: int = 5
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    last_provider_message_id: str | None = None
    created_by_account_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    sent_at: datetime | None = None


@dataclass(frozen=True)
class EmailOutboxSummaryRecord:
    status: str
    count: int


class EmailOutboxRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def enqueue(self, record: EmailOutboxRecord) -> EmailOutboxRecord:
        if self.prisma is None:
            return record
        email_id = record.email_id or str(uuid4())
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_emailoutbox (
                email_id, kind, provider, to_addresses, cc_addresses, bcc_addresses,
                from_address, reply_to, template_key, payload_json, subject, text_body, html_body,
                status, attempt_count, max_attempts, next_attempt_at, last_error,
                last_provider_message_id, created_by_account_id, created_at, updated_at, sent_at
            )
            VALUES (
                $1, $2, $3, $4::text[], $5::text[], $6::text[],
                $7, $8, $9, $10::jsonb, $11, $12, $13,
                $14, $15, $16, $17::timestamptz, $18,
                $19, $20, NOW(), NOW(), $21::timestamptz
            )
            RETURNING
                email_id, kind, provider, to_addresses, cc_addresses, bcc_addresses,
                from_address, reply_to, template_key, payload_json, subject, text_body, html_body,
                status, attempt_count, max_attempts, next_attempt_at, last_error,
                last_provider_message_id, created_by_account_id, created_at, updated_at, sent_at
            """,
            email_id,
            record.kind,
            record.provider,
            record.to_addresses,
            record.cc_addresses,
            record.bcc_addresses,
            record.from_address,
            record.reply_to,
            record.template_key,
            json.dumps(record.payload_json) if record.payload_json is not None else None,
            record.subject,
            record.text_body,
            record.html_body,
            record.status,
            record.attempt_count,
            record.max_attempts,
            record.next_attempt_at or datetime.now(tz=UTC),
            record.last_error,
            record.last_provider_message_id,
            record.created_by_account_id,
            record.sent_at,
        )
        return self._row_to_record(rows[0])

    async def claim_due(self, *, limit: int = 10) -> list[EmailOutboxRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            WITH due AS (
                SELECT email_id
                FROM deltallm_emailoutbox
                WHERE status IN ('queued', 'retrying')
                  AND next_attempt_at <= NOW()
                ORDER BY created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE deltallm_emailoutbox eo
            SET status = 'sending',
                attempt_count = eo.attempt_count + 1,
                updated_at = NOW()
            FROM due
            WHERE eo.email_id = due.email_id
            RETURNING
                eo.email_id, eo.kind, eo.provider, eo.to_addresses, eo.cc_addresses, eo.bcc_addresses,
                eo.from_address, eo.reply_to, eo.template_key, eo.payload_json, eo.subject, eo.text_body, eo.html_body,
                eo.status, eo.attempt_count, eo.max_attempts, eo.next_attempt_at, eo.last_error,
                eo.last_provider_message_id, eo.created_by_account_id, eo.created_at, eo.updated_at, eo.sent_at
            """,
            limit,
        )
        return [self._row_to_record(row) for row in rows]

    async def mark_sent(self, email_id: str, *, provider_message_id: str | None = None) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'sent',
                sent_at = NOW(),
                last_provider_message_id = $2,
                last_error = NULL,
                updated_at = NOW()
            WHERE email_id = $1
            """,
            email_id,
            provider_message_id,
        )

    async def mark_retry(self, email_id: str, *, error: str, next_attempt_at: datetime) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'retrying',
                last_error = $2,
                next_attempt_at = $3::timestamptz,
                updated_at = NOW()
            WHERE email_id = $1
            """,
            email_id,
            error[:4000],
            next_attempt_at,
        )

    async def mark_failed(self, email_id: str, *, error: str) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'failed',
                last_error = $2,
                updated_at = NOW()
            WHERE email_id = $1
            """,
            email_id,
            error[:4000],
        )

    async def update_recipients_and_payload(
        self,
        email_id: str,
        *,
        to_addresses: list[str],
        cc_addresses: list[str],
        bcc_addresses: list[str],
        payload_json: dict[str, Any] | None,
    ) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET to_addresses = $2::text[],
                cc_addresses = $3::text[],
                bcc_addresses = $4::text[],
                payload_json = $5::jsonb,
                updated_at = NOW()
            WHERE email_id = $1
            """,
            email_id,
            to_addresses,
            cc_addresses,
            bcc_addresses,
            json.dumps(payload_json) if payload_json is not None else None,
        )

    async def cancel(self, email_id: str, *, reason: str | None = None) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'cancelled',
                last_error = $2,
                updated_at = NOW()
            WHERE email_id = $1
            """,
            email_id,
            reason[:4000] if reason else None,
        )

    async def count_pending(self) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS count
            FROM deltallm_emailoutbox
            WHERE status IN ('queued', 'retrying')
            """
        )
        return int((rows[0] if rows else {}).get("count") or 0)

    async def get_by_email_id(self, email_id: str) -> EmailOutboxRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                email_id, kind, provider, to_addresses, cc_addresses, bcc_addresses,
                from_address, reply_to, template_key, payload_json, subject, text_body, html_body,
                status, attempt_count, max_attempts, next_attempt_at, last_error,
                last_provider_message_id, created_by_account_id, created_at, updated_at, sent_at
            FROM deltallm_emailoutbox
            WHERE email_id = $1
            LIMIT 1
            """,
            email_id,
        )
        if not rows:
            return None
        return self._row_to_record(rows[0])

    async def list_recent(self, *, limit: int = 20) -> list[EmailOutboxRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT
                email_id, kind, provider, to_addresses, cc_addresses, bcc_addresses,
                from_address, reply_to, template_key, payload_json, subject, text_body, html_body,
                status, attempt_count, max_attempts, next_attempt_at, last_error,
                last_provider_message_id, created_by_account_id, created_at, updated_at, sent_at
            FROM deltallm_emailoutbox
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [self._row_to_record(row) for row in rows]

    async def summarize_status_counts(self) -> list[EmailOutboxSummaryRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT status, COUNT(*)::int AS count
            FROM deltallm_emailoutbox
            GROUP BY status
            ORDER BY status ASC
            """
        )
        return [EmailOutboxSummaryRecord(status=str(row.get("status") or ""), count=int(row.get("count") or 0)) for row in rows]

    async def purge_terminal_before(self, *, before: datetime) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_emailoutbox
            WHERE status IN ('sent', 'failed', 'cancelled')
              AND updated_at < $1::timestamptz
            RETURNING email_id
            """,
            before,
        )
        return len(rows)

    def _row_to_record(self, row: dict[str, Any]) -> EmailOutboxRecord:
        return EmailOutboxRecord(
            email_id=str(row.get("email_id") or ""),
            kind=str(row.get("kind") or ""),
            provider=str(row.get("provider") or ""),
            to_addresses=[str(item) for item in list(row.get("to_addresses") or [])],
            cc_addresses=[str(item) for item in list(row.get("cc_addresses") or [])],
            bcc_addresses=[str(item) for item in list(row.get("bcc_addresses") or [])],
            from_address=str(row.get("from_address") or ""),
            reply_to=str(row.get("reply_to") or "") or None,
            template_key=str(row.get("template_key") or "") or None,
            payload_json=_parse_json_object(row.get("payload_json")),
            subject=str(row.get("subject") or ""),
            text_body=str(row.get("text_body") or ""),
            html_body=str(row.get("html_body") or "") or None,
            status=str(row.get("status") or "queued"),
            attempt_count=int(row.get("attempt_count") or 0),
            max_attempts=int(row.get("max_attempts") or 0),
            next_attempt_at=_coerce_datetime(row.get("next_attempt_at")),
            last_error=str(row.get("last_error") or "") or None,
            last_provider_message_id=str(row.get("last_provider_message_id") or "") or None,
            created_by_account_id=str(row.get("created_by_account_id") or "") or None,
            created_at=_coerce_datetime(row.get("created_at")),
            updated_at=_coerce_datetime(row.get("updated_at")),
            sent_at=_coerce_datetime(row.get("sent_at")),
        )
