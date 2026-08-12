from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


CACHE_INVALIDATION_SCOPE_TYPES = {"organization", "team", "user", "key_hash"}
CACHE_INVALIDATION_STATUSES = {"pending", "processing", "completed", "failed", "superseded"}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


def _parse_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _normalize_scope_type(scope_type: str) -> str:
    normalized = str(scope_type or "").strip().lower()
    if normalized not in CACHE_INVALIDATION_SCOPE_TYPES:
        allowed = ", ".join(sorted(CACHE_INVALIDATION_SCOPE_TYPES))
        raise ValueError(f"scope_type must be one of: {allowed}")
    return normalized


def _normalize_scope_id(scope_id: str) -> str:
    normalized = str(scope_id or "").strip()
    if not normalized:
        raise ValueError("scope_id is required")
    return normalized


def _normalize_reason(reason: str) -> str:
    normalized = str(reason or "").strip()
    if not normalized:
        raise ValueError("reason is required")
    return normalized


def _record_from_row(row: dict[str, Any]) -> CacheInvalidationOutboxRecord:
    return CacheInvalidationOutboxRecord(
        invalidation_id=str(row.get("invalidation_id") or ""),
        scope_type=str(row.get("scope_type") or ""),
        scope_id=str(row.get("scope_id") or ""),
        reason=str(row.get("reason") or ""),
        metadata=_parse_metadata(row.get("metadata")),
        status=str(row.get("status") or ""),
        attempt_count=int(row.get("attempt_count") or 0),
        max_attempts=int(row.get("max_attempts") or 0),
        next_attempt_at=_parse_datetime(row.get("next_attempt_at")),
        last_error=str(row.get("last_error")) if row.get("last_error") is not None else None,
        locked_by=str(row.get("locked_by")) if row.get("locked_by") is not None else None,
        lease_expires_at=_parse_datetime(row.get("lease_expires_at")),
        created_at=_parse_datetime(row.get("created_at")),
        updated_at=_parse_datetime(row.get("updated_at")),
        processed_at=_parse_datetime(row.get("processed_at")),
    )


_RETURNING_COLUMNS = """
    invalidation_id,
    scope_type,
    scope_id,
    reason,
    metadata,
    status,
    attempt_count,
    max_attempts,
    next_attempt_at,
    last_error,
    locked_by,
    lease_expires_at,
    created_at,
    updated_at,
    processed_at
"""

_RETURNING_COLUMNS_FROM_OUTBOX_ALIAS = """
    o.invalidation_id,
    o.scope_type,
    o.scope_id,
    o.reason,
    o.metadata,
    o.status,
    o.attempt_count,
    o.max_attempts,
    o.next_attempt_at,
    o.last_error,
    o.locked_by,
    o.lease_expires_at,
    o.created_at,
    o.updated_at,
    o.processed_at
"""


@dataclass(frozen=True)
class CacheInvalidationOutboxRecord:
    invalidation_id: str
    scope_type: str
    scope_id: str
    reason: str
    metadata: dict[str, Any] | None = None
    status: str = "pending"
    attempt_count: int = 0
    max_attempts: int = 10
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    locked_by: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    processed_at: datetime | None = None


class CacheInvalidationOutboxRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def enqueue(
        self,
        *,
        scope_type: str,
        scope_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
        max_attempts: int = 10,
        next_attempt_at: datetime | None = None,
    ) -> CacheInvalidationOutboxRecord | None:
        if self.prisma is None:
            return None

        normalized_scope_type = _normalize_scope_type(scope_type)
        normalized_scope_id = _normalize_scope_id(scope_id)
        normalized_reason = _normalize_reason(reason)
        normalized_max_attempts = max(1, int(max_attempts))
        invalidation_id = str(uuid4())
        rows = await self.prisma.query_raw(
            f"""
            INSERT INTO deltallm_cacheinvalidationoutbox (
                invalidation_id,
                scope_type,
                scope_id,
                reason,
                metadata,
                status,
                attempt_count,
                max_attempts,
                next_attempt_at,
                created_at,
                updated_at
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5::jsonb,
                'pending',
                0,
                $6,
                COALESCE($7::timestamp, NOW()),
                NOW(),
                NOW()
            )
            ON CONFLICT (scope_type, scope_id, reason)
            WHERE status = 'pending'
            DO UPDATE SET
                metadata = COALESCE(EXCLUDED.metadata, deltallm_cacheinvalidationoutbox.metadata),
                max_attempts = GREATEST(
                    deltallm_cacheinvalidationoutbox.max_attempts,
                    EXCLUDED.max_attempts
                ),
                next_attempt_at = LEAST(
                    deltallm_cacheinvalidationoutbox.next_attempt_at,
                    EXCLUDED.next_attempt_at
                ),
                updated_at = NOW()
            RETURNING {_RETURNING_COLUMNS}
            """,
            invalidation_id,
            normalized_scope_type,
            normalized_scope_id,
            normalized_reason,
            json.dumps(metadata) if metadata is not None else None,
            normalized_max_attempts,
            next_attempt_at,
        )
        return _record_from_row(dict(rows[0])) if rows else None

    async def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int = 25,
    ) -> list[CacheInvalidationOutboxRecord]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            f"""
            WITH due AS (
                SELECT invalidation_id
                FROM deltallm_cacheinvalidationoutbox
                WHERE (
                        status = 'pending'
                    AND next_attempt_at <= NOW()
                ) OR (
                        status = 'processing'
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at < NOW()
                )
                ORDER BY created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE deltallm_cacheinvalidationoutbox o
            SET status = 'processing',
                attempt_count = o.attempt_count + 1,
                locked_by = $2,
                lease_expires_at = NOW() + ($3 || ' seconds')::interval,
                updated_at = NOW()
            FROM due
            WHERE o.invalidation_id = due.invalidation_id
            RETURNING {_RETURNING_COLUMNS_FROM_OUTBOX_ALIAS}
            """,
            max(1, min(limit, 500)),
            str(worker_id or "").strip() or "cache-invalidation-worker",
            max(1, int(lease_seconds)),
        )
        return [_record_from_row(dict(row)) for row in rows]

    async def mark_completed(self, invalidation_id: str, *, worker_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            f"""
            UPDATE deltallm_cacheinvalidationoutbox
            SET status = 'completed',
                last_error = NULL,
                locked_by = NULL,
                lease_expires_at = NULL,
                processed_at = NOW(),
                updated_at = NOW()
            WHERE invalidation_id = $1
              AND status = 'processing'
              AND locked_by = $2
            RETURNING {_RETURNING_COLUMNS}
            """,
            invalidation_id,
            worker_id,
        )
        return bool(rows)

    async def mark_retry(
        self,
        invalidation_id: str,
        *,
        worker_id: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            WITH current AS (
                UPDATE deltallm_cacheinvalidationoutbox
                SET status = 'superseded',
                    last_error = $2,
                    locked_by = NULL,
                    lease_expires_at = NULL,
                    processed_at = NOW(),
                    updated_at = NOW()
                WHERE invalidation_id = $1
                  AND status = 'processing'
                  AND locked_by = $4
                RETURNING
                    scope_type,
                    scope_id,
                    reason,
                    metadata,
                    attempt_count,
                    max_attempts
            ),
            retry AS (
                INSERT INTO deltallm_cacheinvalidationoutbox (
                    invalidation_id,
                    scope_type,
                    scope_id,
                    reason,
                    metadata,
                    status,
                    attempt_count,
                    max_attempts,
                    next_attempt_at,
                    last_error,
                    created_at,
                    updated_at
                )
                SELECT
                    gen_random_uuid()::text,
                    scope_type,
                    scope_id,
                    reason,
                    metadata,
                    'pending',
                    attempt_count,
                    max_attempts,
                    $3::timestamp,
                    $2,
                    NOW(),
                    NOW()
                FROM current
                ON CONFLICT (scope_type, scope_id, reason)
                WHERE status = 'pending'
                DO NOTHING
                RETURNING invalidation_id
            )
            SELECT
                (SELECT COUNT(*)::int FROM current) AS updated_count,
                (SELECT COUNT(*)::int FROM retry) AS retry_count
            """,
            invalidation_id,
            error[:4000],
            next_attempt_at,
            worker_id,
        )
        return int((rows[0] if rows else {}).get("updated_count") or 0) > 0

    async def mark_failed(self, invalidation_id: str, *, worker_id: str, error: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            f"""
            UPDATE deltallm_cacheinvalidationoutbox
            SET status = 'failed',
                last_error = $2,
                locked_by = NULL,
                lease_expires_at = NULL,
                processed_at = NOW(),
                updated_at = NOW()
            WHERE invalidation_id = $1
              AND status = 'processing'
              AND locked_by = $3
            RETURNING {_RETURNING_COLUMNS}
            """,
            invalidation_id,
            error[:4000],
            worker_id,
        )
        return bool(rows)

    async def count_pending(self) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS count
            FROM deltallm_cacheinvalidationoutbox
            WHERE status = 'pending'
            """
        )
        return int((rows[0] if rows else {}).get("count") or 0)


__all__ = [
    "CACHE_INVALIDATION_SCOPE_TYPES",
    "CACHE_INVALIDATION_STATUSES",
    "CacheInvalidationOutboxRecord",
    "CacheInvalidationOutboxRepository",
]
