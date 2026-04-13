from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from src.batch.create.models import (
    BatchCreateSessionCreate,
    BatchCreateSessionRecord,
    normalize_idempotency_pair,
)
from src.batch.repositories.mappers import parse_datetime, parse_json_dict


def _session_from_row(row: dict[str, Any]) -> BatchCreateSessionRecord:
    created_at = parse_datetime(row.get("created_at"))
    if created_at is None:
        raise ValueError("batch create session row is missing created_at")
    return BatchCreateSessionRecord(
        session_id=str(row.get("session_id") or ""),
        target_batch_id=str(row.get("target_batch_id") or ""),
        status=str(row.get("status") or ""),
        endpoint=str(row.get("endpoint") or ""),
        input_file_id=str(row.get("input_file_id") or ""),
        staged_storage_backend=str(row.get("staged_storage_backend") or ""),
        staged_storage_key=str(row.get("staged_storage_key") or ""),
        staged_checksum=row.get("staged_checksum"),
        staged_bytes=int(row.get("staged_bytes") or 0),
        expected_item_count=int(row.get("expected_item_count") or 0),
        inferred_model=row.get("inferred_model"),
        metadata=parse_json_dict(row.get("metadata")),
        requested_service_tier=row.get("requested_service_tier"),
        effective_service_tier=row.get("effective_service_tier"),
        service_tier_source=row.get("service_tier_source"),
        scheduling_scope_key=row.get("scheduling_scope_key"),
        priority_quota_scope_key=row.get("priority_quota_scope_key"),
        idempotency_scope_key=row.get("idempotency_scope_key"),
        idempotency_key=row.get("idempotency_key"),
        last_error_code=row.get("last_error_code"),
        last_error_message=row.get("last_error_message"),
        promotion_attempt_count=int(row.get("promotion_attempt_count") or 0),
        created_by_api_key=row.get("created_by_api_key"),
        created_by_user_id=row.get("created_by_user_id"),
        created_by_team_id=row.get("created_by_team_id"),
        created_by_organization_id=row.get("created_by_organization_id"),
        created_at=created_at,
        completed_at=parse_datetime(row.get("completed_at")),
        last_attempt_at=parse_datetime(row.get("last_attempt_at")),
        expires_at=parse_datetime(row.get("expires_at")),
    )


class BatchCreateSessionRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def create_session(self, session: BatchCreateSessionCreate) -> BatchCreateSessionRecord | None:
        if self.prisma is None:
            return None
        idempotency_scope_key, idempotency_key = normalize_idempotency_pair(
            session.idempotency_scope_key,
            session.idempotency_key,
        )
        session_id = str(uuid4())
        metadata_json = json.dumps(session.metadata) if session.metadata is not None else None
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_batch_create_session (
                session_id, target_batch_id, status, endpoint, input_file_id,
                staged_storage_backend, staged_storage_key, staged_checksum, staged_bytes,
                expected_item_count, inferred_model, metadata, requested_service_tier,
                effective_service_tier, service_tier_source, scheduling_scope_key,
                priority_quota_scope_key, idempotency_scope_key, idempotency_key,
                last_error_code, last_error_message, promotion_attempt_count,
                created_by_api_key, created_by_user_id, created_by_team_id,
                created_by_organization_id, completed_at, last_attempt_at, expires_at
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9,
                $10, $11, $12::jsonb, $13,
                $14, $15, $16,
                $17, $18, $19,
                $20, $21, $22,
                $23, $24, $25,
                $26, $27::timestamp, $28::timestamp, $29::timestamp
            )
            RETURNING *
            """,
            session_id,
            session.target_batch_id,
            session.status,
            session.endpoint,
            session.input_file_id,
            session.staged_storage_backend,
            session.staged_storage_key,
            session.staged_checksum,
            session.staged_bytes,
            session.expected_item_count,
            session.inferred_model,
            metadata_json,
            session.requested_service_tier,
            session.effective_service_tier,
            session.service_tier_source,
            session.scheduling_scope_key,
            session.priority_quota_scope_key,
            idempotency_scope_key,
            idempotency_key,
            session.last_error_code,
            session.last_error_message,
            session.promotion_attempt_count,
            session.created_by_api_key,
            session.created_by_user_id,
            session.created_by_team_id,
            session.created_by_organization_id,
            session.completed_at,
            session.last_attempt_at,
            session.expires_at,
        )
        if not rows:
            return None
        return _session_from_row(rows[0])

    async def get_session(self, session_id: str) -> BatchCreateSessionRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_create_session
            WHERE session_id = $1
            LIMIT 1
            """,
            session_id,
        )
        if not rows:
            return None
        return _session_from_row(rows[0])

    async def get_session_by_target_batch_id(self, target_batch_id: str) -> BatchCreateSessionRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_create_session
            WHERE target_batch_id = $1
            LIMIT 1
            """,
            target_batch_id,
        )
        if not rows:
            return None
        return _session_from_row(rows[0])

    async def get_session_by_idempotency_key(
        self,
        *,
        idempotency_scope_key: str,
        idempotency_key: str,
    ) -> BatchCreateSessionRecord | None:
        if self.prisma is None:
            return None
        try:
            normalized_scope_key, normalized_key = normalize_idempotency_pair(
                idempotency_scope_key,
                idempotency_key,
            )
        except ValueError as exc:
            raise ValueError("idempotency_scope_key and idempotency_key are required for lookup") from exc
        if normalized_scope_key is None or normalized_key is None:
            raise ValueError("idempotency_scope_key and idempotency_key are required for lookup")
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_create_session
            WHERE idempotency_scope_key = $1
              AND idempotency_key = $2
            LIMIT 1
            """,
            normalized_scope_key,
            normalized_key,
        )
        if not rows:
            return None
        return _session_from_row(rows[0])

    async def list_expired_sessions(self, *, now: datetime, limit: int = 100) -> list[BatchCreateSessionRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_create_session
            WHERE expires_at IS NOT NULL
              AND expires_at < $1::timestamp
            ORDER BY expires_at ASC
            LIMIT $2
            """,
            now,
            max(1, min(limit, 1000)),
        )
        return [_session_from_row(row) for row in rows]

    async def delete_session(self, session_id: str) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_batch_create_session
            WHERE session_id = $1
            """,
            session_id,
        )
