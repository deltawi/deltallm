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
    delivery_locked_by: str | None = None
    delivery_claim_token: str | None = None
    delivery_lease_expires_at: datetime | None = None
    delivery_started_at: datetime | None = None
    delivery_blocked_at: datetime | None = None
    delivery_audit_status: str = "not_required"
    delivery_audit_event_id: str | None = None
    delivery_audit_attempt_count: int = 0
    delivery_audit_max_attempts: int = 10
    delivery_audit_next_attempt_at: datetime | None = None
    delivery_audit_last_error: str | None = None
    delivery_audit_locked_by: str | None = None
    delivery_audit_claim_token: str | None = None
    delivery_audit_lease_expires_at: datetime | None = None
    delivery_audit_blocked_at: datetime | None = None
    delivery_audit_replay_count: int = 0
    delivery_audit_last_replayed_at: datetime | None = None
    delivery_audit_last_replayed_by: str | None = None
    delivery_audited_at: datetime | None = None


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
                last_provider_message_id, created_by_account_id, created_at, updated_at, sent_at,
                delivery_audit_status, delivery_audit_event_id,
                delivery_audit_max_attempts, delivery_audit_next_attempt_at
            )
            VALUES (
                $1, $2, $3, $4::text[], $5::text[], $6::text[],
                $7, $8, $9, $10::jsonb, $11, $12, $13,
                $14, $15, $16, $17::timestamptz, $18,
                $19, $20, NOW(), NOW(), $21::timestamptz,
                $22, $23, $24, $25::timestamptz
            )
            RETURNING
                email_id, kind, provider, to_addresses, cc_addresses, bcc_addresses,
                from_address, reply_to, template_key, payload_json, subject, text_body, html_body,
                status, attempt_count, max_attempts, next_attempt_at, last_error,
                last_provider_message_id, created_by_account_id, created_at, updated_at, sent_at,
                delivery_audit_status, delivery_audit_event_id,
                delivery_audit_attempt_count, delivery_audit_max_attempts,
                delivery_audit_next_attempt_at, delivery_audit_last_error,
                delivery_locked_by, delivery_claim_token, delivery_lease_expires_at,
                delivery_started_at, delivery_blocked_at,
                delivery_audit_locked_by, delivery_audit_claim_token,
                delivery_audit_lease_expires_at, delivery_audit_blocked_at,
                delivery_audit_replay_count, delivery_audit_last_replayed_at,
                delivery_audit_last_replayed_by,
                delivery_audited_at
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
            record.delivery_audit_status,
            record.delivery_audit_event_id,
            record.delivery_audit_max_attempts,
            record.delivery_audit_next_attempt_at,
        )
        return self._row_to_record(rows[0])

    async def claim_due(
        self,
        *,
        limit: int = 10,
        worker_id: str = "email-worker",
        claim_token: str | None = None,
        lease_seconds: int = 60,
    ) -> list[EmailOutboxRecord]:
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
            SET status = 'claimed',
                delivery_locked_by = $2,
                delivery_claim_token = $3 || ':' || eo.email_id,
                delivery_lease_expires_at = NOW() + make_interval(secs => $4::double precision),
                updated_at = NOW()
            FROM due
            WHERE eo.email_id = due.email_id
            RETURNING
                eo.email_id, eo.kind, eo.provider, eo.to_addresses, eo.cc_addresses, eo.bcc_addresses,
                eo.from_address, eo.reply_to, eo.template_key, eo.payload_json, eo.subject, eo.text_body, eo.html_body,
                eo.status, eo.attempt_count, eo.max_attempts, eo.next_attempt_at, eo.last_error,
                eo.last_provider_message_id, eo.created_by_account_id, eo.created_at, eo.updated_at, eo.sent_at,
                eo.delivery_audit_status, eo.delivery_audit_event_id,
                eo.delivery_audit_attempt_count, eo.delivery_audit_max_attempts,
                eo.delivery_audit_next_attempt_at, eo.delivery_audit_last_error,
                eo.delivery_locked_by, eo.delivery_claim_token, eo.delivery_lease_expires_at,
                eo.delivery_started_at, eo.delivery_blocked_at,
                eo.delivery_audit_locked_by, eo.delivery_audit_claim_token,
                eo.delivery_audit_lease_expires_at, eo.delivery_audit_blocked_at,
                eo.delivery_audit_replay_count, eo.delivery_audit_last_replayed_at,
                eo.delivery_audit_last_replayed_by,
                eo.delivery_audited_at
            """,
            max(1, int(limit)),
            worker_id,
            claim_token or str(uuid4()),
            max(5, int(lease_seconds)),
        )
        return [self._row_to_record(row) for row in rows]

    async def begin_delivery_attempt(
        self, *, email_id: str, worker_id: str, claim_token: str
    ) -> bool:
        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'sending', attempt_count = attempt_count + 1,
                delivery_started_at = NOW(), updated_at = NOW()
            WHERE email_id = $1 AND status = 'claimed'
              AND delivery_locked_by = $2 AND delivery_claim_token = $3
              AND delivery_lease_expires_at > NOW()
            """,
            email_id,
            worker_id,
            claim_token,
        )
        return int(updated or 0) == 1

    async def renew_delivery_claim(
        self,
        *,
        email_id: str,
        worker_id: str,
        claim_token: str,
        lease_seconds: int,
    ) -> bool:
        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET delivery_lease_expires_at = NOW() + make_interval(secs => $4::double precision),
                updated_at = NOW()
            WHERE email_id = $1 AND status IN ('claimed', 'sending')
              AND delivery_locked_by = $2 AND delivery_claim_token = $3
              AND delivery_lease_expires_at > NOW()
            """,
            email_id,
            worker_id,
            claim_token,
            max(5, int(lease_seconds)),
        )
        return int(updated or 0) == 1

    async def recover_expired_delivery_claims(self, *, limit: int) -> int:
        """Recover bounded expired claims without retrying ambiguous provider work."""

        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            WITH expired AS (
                SELECT email_id, status
                FROM deltallm_emailoutbox
                WHERE (
                    status IN ('claimed', 'sending')
                    AND delivery_lease_expires_at <= NOW()
                ) OR (
                    status = 'sending' AND delivery_claim_token IS NULL
                    AND updated_at <= NOW() - INTERVAL '5 minutes'
                )
                ORDER BY updated_at, email_id
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE deltallm_emailoutbox eo
            SET status = CASE WHEN expired.status = 'claimed' THEN 'retrying' ELSE 'delivery_unknown' END,
                next_attempt_at = CASE WHEN expired.status = 'claimed' THEN NOW() ELSE eo.next_attempt_at END,
                last_error = CASE
                    WHEN expired.status = 'claimed' THEN 'delivery claim expired before provider attempt'
                    ELSE 'provider delivery outcome is unknown after claim expiry'
                END,
                delivery_blocked_at = CASE
                    WHEN expired.status = 'sending' THEN NOW() ELSE eo.delivery_blocked_at
                END,
                delivery_audit_status = CASE
                    WHEN expired.status = 'sending' AND eo.kind = 'test' THEN 'pending'
                    ELSE eo.delivery_audit_status
                END,
                delivery_audit_event_id = CASE
                    WHEN expired.status = 'sending' AND eo.kind = 'test'
                    THEN COALESCE(eo.delivery_audit_event_id, 'deltallm:email-delivery:' || eo.email_id)
                    ELSE eo.delivery_audit_event_id
                END,
                delivery_audit_next_attempt_at = CASE
                    WHEN expired.status = 'sending' AND eo.kind = 'test' THEN NOW()
                    ELSE eo.delivery_audit_next_attempt_at
                END,
                delivery_locked_by = NULL, delivery_claim_token = NULL,
                delivery_lease_expires_at = NULL, updated_at = NOW()
            FROM expired
            WHERE eo.email_id = expired.email_id
            RETURNING eo.email_id
            """,
            max(1, int(limit)),
        )
        return len(rows)

    async def release_delivery_claim(
        self,
        *,
        email_id: str,
        worker_id: str,
        claim_token: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        """Requeue work only while no provider attempt has begun."""

        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'retrying', last_error = $4,
                next_attempt_at = $5::timestamptz,
                delivery_locked_by = NULL, delivery_claim_token = NULL,
                delivery_lease_expires_at = NULL, updated_at = NOW()
            WHERE email_id = $1 AND status = 'claimed'
              AND delivery_locked_by = $2 AND delivery_claim_token = $3
              AND delivery_lease_expires_at > NOW()
            """,
            email_id,
            worker_id,
            claim_token,
            error[:4000],
            next_attempt_at,
        )
        return int(updated or 0) == 1

    async def mark_sent(
        self,
        email_id: str,
        *,
        worker_id: str,
        claim_token: str,
        provider_message_id: str | None = None,
        delivery_audit_event_id: str | None = None,
    ) -> bool:
        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'sent',
                sent_at = NOW(),
                last_provider_message_id = $2,
                last_error = NULL,
                delivery_audit_status = CASE WHEN kind = 'test' THEN 'pending' ELSE 'not_required' END,
                delivery_audit_event_id = COALESCE($3, delivery_audit_event_id),
                delivery_audit_next_attempt_at = CASE WHEN kind = 'test' THEN NOW() ELSE NULL END,
                delivery_audit_last_error = NULL,
                delivery_audit_locked_by = NULL,
                delivery_audit_claim_token = NULL,
                delivery_audit_lease_expires_at = NULL,
                delivery_locked_by = NULL,
                delivery_claim_token = NULL,
                delivery_lease_expires_at = NULL,
                updated_at = NOW()
            WHERE email_id = $1 AND status = 'sending'
              AND delivery_locked_by = $4 AND delivery_claim_token = $5
              AND delivery_lease_expires_at > NOW()
            """,
            email_id,
            provider_message_id,
            delivery_audit_event_id,
            worker_id,
            claim_token,
        )
        return int(updated or 0) == 1

    async def mark_retry(
        self,
        email_id: str,
        *,
        worker_id: str,
        claim_token: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'retrying',
                last_error = $2,
                next_attempt_at = $3::timestamptz,
                delivery_locked_by = NULL,
                delivery_claim_token = NULL,
                delivery_lease_expires_at = NULL,
                updated_at = NOW()
            WHERE email_id = $1 AND status = 'sending'
              AND delivery_locked_by = $4 AND delivery_claim_token = $5
              AND delivery_lease_expires_at > NOW()
            """,
            email_id,
            error[:4000],
            next_attempt_at,
            worker_id,
            claim_token,
        )
        return int(updated or 0) == 1

    async def mark_failed(
        self,
        email_id: str,
        *,
        worker_id: str,
        claim_token: str,
        error: str,
        delivery_audit_event_id: str | None = None,
    ) -> bool:
        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'failed',
                last_error = $2,
                delivery_audit_status = CASE WHEN kind = 'test' THEN 'pending' ELSE 'not_required' END,
                delivery_audit_event_id = COALESCE($3, delivery_audit_event_id),
                delivery_audit_next_attempt_at = CASE WHEN kind = 'test' THEN NOW() ELSE NULL END,
                delivery_audit_last_error = NULL,
                delivery_audit_locked_by = NULL,
                delivery_audit_claim_token = NULL,
                delivery_audit_lease_expires_at = NULL,
                delivery_locked_by = NULL,
                delivery_claim_token = NULL,
                delivery_lease_expires_at = NULL,
                updated_at = NOW()
            WHERE email_id = $1 AND status = 'sending'
              AND delivery_locked_by = $4 AND delivery_claim_token = $5
              AND delivery_lease_expires_at > NOW()
            """,
            email_id,
            error[:4000],
            delivery_audit_event_id,
            worker_id,
            claim_token,
        )
        return int(updated or 0) == 1

    async def mark_delivery_unknown(
        self,
        email_id: str,
        *,
        worker_id: str,
        claim_token: str,
        error: str,
        delivery_audit_event_id: str | None = None,
    ) -> bool:
        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'delivery_unknown', last_error = $2, delivery_blocked_at = NOW(),
                delivery_audit_status = CASE WHEN kind = 'test' THEN 'pending' ELSE 'not_required' END,
                delivery_audit_event_id = COALESCE($3, delivery_audit_event_id),
                delivery_audit_next_attempt_at = CASE WHEN kind = 'test' THEN NOW() ELSE NULL END,
                delivery_locked_by = NULL, delivery_claim_token = NULL,
                delivery_lease_expires_at = NULL, updated_at = NOW()
            WHERE email_id = $1 AND status = 'sending'
              AND delivery_locked_by = $4 AND delivery_claim_token = $5
              AND delivery_lease_expires_at > NOW()
            """,
            email_id,
            error[:4000],
            delivery_audit_event_id,
            worker_id,
            claim_token,
        )
        return int(updated or 0) == 1

    async def update_recipients_and_payload(
        self,
        email_id: str,
        *,
        to_addresses: list[str],
        cc_addresses: list[str],
        bcc_addresses: list[str],
        payload_json: dict[str, Any] | None,
        worker_id: str,
        claim_token: str,
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
            WHERE email_id = $1 AND status = 'claimed'
              AND delivery_locked_by = $6 AND delivery_claim_token = $7
              AND delivery_lease_expires_at > NOW()
            """,
            email_id,
            to_addresses,
            cc_addresses,
            bcc_addresses,
            json.dumps(payload_json) if payload_json is not None else None,
            worker_id,
            claim_token,
        )

    async def cancel(
        self,
        email_id: str,
        *,
        worker_id: str,
        claim_token: str,
        reason: str | None = None,
        delivery_audit_event_id: str | None = None,
    ) -> bool:
        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = 'cancelled',
                last_error = $2,
                delivery_audit_status = CASE WHEN kind = 'test' THEN 'pending' ELSE 'not_required' END,
                delivery_audit_event_id = COALESCE($3, delivery_audit_event_id),
                delivery_audit_next_attempt_at = CASE WHEN kind = 'test' THEN NOW() ELSE NULL END,
                delivery_audit_last_error = NULL,
                delivery_audit_locked_by = NULL,
                delivery_audit_claim_token = NULL,
                delivery_audit_lease_expires_at = NULL,
                delivery_locked_by = NULL,
                delivery_claim_token = NULL,
                delivery_lease_expires_at = NULL,
                updated_at = NOW()
            WHERE email_id = $1 AND status = 'claimed'
              AND delivery_locked_by = $4 AND delivery_claim_token = $5
              AND delivery_lease_expires_at > NOW()
            """,
            email_id,
            reason[:4000] if reason else None,
            delivery_audit_event_id,
            worker_id,
            claim_token,
        )
        return int(updated or 0) == 1

    async def claim_due_delivery_audits(
        self,
        *,
        limit: int,
        worker_id: str,
        lease_seconds: int,
        claim_token: str | None = None,
    ) -> list[EmailOutboxRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            WITH due AS (
                SELECT email_id
                FROM deltallm_emailoutbox
                WHERE kind = 'test'
                  AND status IN ('sent', 'failed', 'cancelled', 'delivery_unknown')
                  AND delivery_audit_event_id IS NOT NULL
                  AND (
                    (
                      delivery_audit_status IN ('pending', 'retrying')
                      AND delivery_audit_next_attempt_at <= NOW()
                    )
                    OR (
                      delivery_audit_status = 'processing'
                      AND delivery_audit_lease_expires_at <= NOW()
                    )
                  )
                ORDER BY delivery_audit_next_attempt_at ASC, created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE deltallm_emailoutbox eo
            SET delivery_audit_status = 'processing',
                delivery_audit_attempt_count = eo.delivery_audit_attempt_count + 1,
                delivery_audit_locked_by = $2,
                delivery_audit_claim_token = $3 || ':' || eo.email_id,
                delivery_audit_lease_expires_at = NOW() + make_interval(secs => $4::double precision),
                updated_at = NOW()
            FROM due
            WHERE eo.email_id = due.email_id
            RETURNING eo.*
            """,
            max(1, int(limit)),
            worker_id,
            claim_token or str(uuid4()),
            max(1, int(lease_seconds)),
        )
        return [self._row_to_record(row) for row in rows]

    async def renew_delivery_audit_claim(
        self,
        *,
        email_id: str,
        worker_id: str,
        claim_token: str,
        lease_seconds: int,
    ) -> bool:
        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET delivery_audit_lease_expires_at =
                    NOW() + make_interval(secs => $4::double precision),
                updated_at = NOW()
            WHERE email_id = $1 AND delivery_audit_status = 'processing'
              AND delivery_audit_locked_by = $2 AND delivery_audit_claim_token = $3
              AND delivery_audit_lease_expires_at > NOW()
            """,
            email_id,
            worker_id,
            claim_token,
            max(1, int(lease_seconds)),
        )
        return int(updated or 0) == 1

    async def mark_delivery_audited(
        self, *, email_id: str, worker_id: str, claim_token: str
    ) -> bool:
        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET delivery_audit_status = 'persisted',
                delivery_audited_at = NOW(),
                delivery_audit_last_error = NULL,
                delivery_audit_locked_by = NULL,
                delivery_audit_claim_token = NULL,
                delivery_audit_lease_expires_at = NULL,
                updated_at = NOW()
            WHERE email_id = $1
              AND delivery_audit_status = 'processing'
              AND delivery_audit_locked_by = $2
              AND delivery_audit_claim_token = $3
              AND delivery_audit_lease_expires_at > NOW()
            """,
            email_id,
            worker_id,
            claim_token,
        )
        return int(updated or 0) == 1

    async def mark_delivery_audit_retry(
        self,
        *,
        email_id: str,
        worker_id: str,
        claim_token: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        if self.prisma is None:
            return True
        updated = await self.prisma.execute_raw(
            """
            UPDATE deltallm_emailoutbox
            SET delivery_audit_status = CASE
                    WHEN delivery_audit_attempt_count >= delivery_audit_max_attempts
                    THEN 'blocked'
                    ELSE 'retrying'
                END,
                delivery_audit_next_attempt_at = CASE
                    WHEN delivery_audit_attempt_count >= delivery_audit_max_attempts
                    THEN NULL
                    ELSE $4::timestamptz
                END,
                delivery_audit_last_error = $3,
                delivery_audit_locked_by = NULL,
                delivery_audit_claim_token = NULL,
                delivery_audit_lease_expires_at = NULL,
                delivery_audit_blocked_at = CASE
                    WHEN delivery_audit_attempt_count >= delivery_audit_max_attempts
                    THEN NOW()
                    ELSE delivery_audit_blocked_at
                END,
                updated_at = NOW()
            WHERE email_id = $1
              AND delivery_audit_status = 'processing'
              AND delivery_audit_locked_by = $2
              AND delivery_audit_claim_token = $5
              AND delivery_audit_lease_expires_at > NOW()
            """,
            email_id,
            worker_id,
            error[:4000],
            next_attempt_at,
            claim_token,
        )
        return int(updated or 0) == 1

    async def replay_blocked_delivery_audit(self, *, email_id: str, replayed_by: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_emailoutbox
            SET delivery_audit_status = 'pending', delivery_audit_attempt_count = 0,
                delivery_audit_next_attempt_at = NOW(), delivery_audit_last_error = NULL,
                delivery_audit_locked_by = NULL, delivery_audit_claim_token = NULL,
                delivery_audit_lease_expires_at = NULL, delivery_audit_blocked_at = NULL,
                delivery_audit_replay_count = delivery_audit_replay_count + 1,
                delivery_audit_last_replayed_at = NOW(),
                delivery_audit_last_replayed_by = $2, updated_at = NOW()
            WHERE email_id = $1 AND delivery_audit_status = 'blocked'
              AND delivery_audit_event_id IS NOT NULL
            RETURNING email_id
            """,
            email_id,
            replayed_by,
        )
        return bool(rows)

    async def resolve_unknown_delivery(self, *, email_id: str, resolution: str) -> bool:
        if self.prisma is None or resolution not in {"sent", "failed"}:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_emailoutbox
            SET status = $2,
                sent_at = CASE WHEN $2 = 'sent' THEN NOW() ELSE sent_at END,
                last_error = CASE
                    WHEN $2 = 'sent' THEN NULL
                    ELSE 'operator resolved ambiguous delivery as failed'
                END,
                delivery_blocked_at = NULL,
                delivery_audit_status = CASE WHEN kind = 'test' THEN 'pending' ELSE 'not_required' END,
                delivery_audit_next_attempt_at = CASE WHEN kind = 'test' THEN NOW() ELSE NULL END,
                updated_at = NOW()
            WHERE email_id = $1 AND status = 'delivery_unknown'
            RETURNING email_id
            """,
            email_id,
            resolution,
        )
        return bool(rows)

    async def count_pending(self) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS count
            FROM deltallm_emailoutbox
            WHERE status IN ('queued', 'retrying', 'claimed', 'sending')
            """
        )
        return int((rows[0] if rows else {}).get("count") or 0)

    async def count_delivery_audits_by_status(self) -> dict[str, int]:
        if self.prisma is None:
            return {}
        rows = await self.prisma.query_raw(
            """
            SELECT delivery_audit_status AS status, COUNT(*)::int AS count
            FROM deltallm_emailoutbox
            WHERE delivery_audit_status IN ('pending', 'retrying', 'processing', 'blocked')
            GROUP BY delivery_audit_status
            ORDER BY delivery_audit_status
            """
        )
        return {str(row.get("status") or ""): int(row.get("count") or 0) for row in rows}

    async def get_by_email_id(self, email_id: str) -> EmailOutboxRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                email_id, kind, provider, to_addresses, cc_addresses, bcc_addresses,
                from_address, reply_to, template_key, payload_json, subject, text_body, html_body,
                status, attempt_count, max_attempts, next_attempt_at, last_error,
                last_provider_message_id, created_by_account_id, created_at, updated_at, sent_at,
                delivery_audit_status, delivery_audit_event_id,
                delivery_audit_attempt_count, delivery_audit_max_attempts,
                delivery_audit_next_attempt_at, delivery_audit_last_error,
                delivery_locked_by, delivery_claim_token, delivery_lease_expires_at,
                delivery_started_at, delivery_blocked_at,
                delivery_audit_locked_by, delivery_audit_claim_token,
                delivery_audit_lease_expires_at, delivery_audit_blocked_at,
                delivery_audit_replay_count, delivery_audit_last_replayed_at,
                delivery_audit_last_replayed_by,
                delivery_audited_at
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
                last_provider_message_id, created_by_account_id, created_at, updated_at, sent_at,
                delivery_audit_status, delivery_audit_event_id,
                delivery_audit_attempt_count, delivery_audit_max_attempts,
                delivery_audit_next_attempt_at, delivery_audit_last_error,
                delivery_locked_by, delivery_claim_token, delivery_lease_expires_at,
                delivery_started_at, delivery_blocked_at,
                delivery_audit_locked_by, delivery_audit_claim_token,
                delivery_audit_lease_expires_at, delivery_audit_blocked_at,
                delivery_audit_replay_count, delivery_audit_last_replayed_at,
                delivery_audit_last_replayed_by,
                delivery_audited_at
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
        return [
            EmailOutboxSummaryRecord(
                status=str(row.get("status") or ""), count=int(row.get("count") or 0)
            )
            for row in rows
        ]

    async def purge_terminal_before(self, *, before: datetime) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_emailoutbox
            WHERE status IN ('sent', 'failed', 'cancelled')
              AND delivery_audit_status IN ('not_required', 'persisted')
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
            delivery_locked_by=str(row.get("delivery_locked_by") or "") or None,
            delivery_claim_token=str(row.get("delivery_claim_token") or "") or None,
            delivery_lease_expires_at=_coerce_datetime(row.get("delivery_lease_expires_at")),
            delivery_started_at=_coerce_datetime(row.get("delivery_started_at")),
            delivery_blocked_at=_coerce_datetime(row.get("delivery_blocked_at")),
            delivery_audit_status=str(row.get("delivery_audit_status") or "not_required"),
            delivery_audit_event_id=str(row.get("delivery_audit_event_id") or "") or None,
            delivery_audit_attempt_count=int(row.get("delivery_audit_attempt_count") or 0),
            delivery_audit_max_attempts=int(row.get("delivery_audit_max_attempts") or 10),
            delivery_audit_next_attempt_at=_coerce_datetime(
                row.get("delivery_audit_next_attempt_at")
            ),
            delivery_audit_last_error=str(row.get("delivery_audit_last_error") or "") or None,
            delivery_audit_locked_by=str(row.get("delivery_audit_locked_by") or "") or None,
            delivery_audit_claim_token=str(row.get("delivery_audit_claim_token") or "") or None,
            delivery_audit_lease_expires_at=_coerce_datetime(
                row.get("delivery_audit_lease_expires_at")
            ),
            delivery_audit_blocked_at=_coerce_datetime(row.get("delivery_audit_blocked_at")),
            delivery_audit_replay_count=int(row.get("delivery_audit_replay_count") or 0),
            delivery_audit_last_replayed_at=_coerce_datetime(
                row.get("delivery_audit_last_replayed_at")
            ),
            delivery_audit_last_replayed_by=(
                str(row.get("delivery_audit_last_replayed_by") or "") or None
            ),
            delivery_audited_at=_coerce_datetime(row.get("delivery_audited_at")),
        )
