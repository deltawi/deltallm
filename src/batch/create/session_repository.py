from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from src.batch.create.models import (
    BATCH_CREATE_SESSION_STATUSES,
    BatchCreateSessionCreate,
    BatchCreateSessionRecord,
    BatchCreateSessionStatus,
    normalize_batch_create_session_status,
    normalize_idempotency_pair,
)
from src.batch.repositories.mappers import parse_datetime, parse_json_dict

_UNSET = object()


def _session_from_row(row: dict[str, Any]) -> BatchCreateSessionRecord:
    created_at = parse_datetime(row.get("created_at"))
    if created_at is None:
        raise ValueError("batch create session row is missing created_at")
    return BatchCreateSessionRecord(
        session_id=str(row.get("session_id") or ""),
        target_batch_id=str(row.get("target_batch_id") or ""),
        status=normalize_batch_create_session_status(str(row.get("status") or "")),
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
        normalized_status = normalize_batch_create_session_status(session.status)
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
            normalized_status,
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

    async def get_session_for_update(self, session_id: str) -> BatchCreateSessionRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_create_session
            WHERE session_id = $1
            FOR UPDATE
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

    async def mark_session_completed(
        self,
        session_id: str,
        *,
        completed_at: datetime,
        expires_at: datetime | None,
        increment_promotion_attempt_count: bool = False,
        from_statuses: tuple[str, ...] | None = None,
    ) -> BatchCreateSessionRecord | None:
        return await self._update_session(
            session_id,
            status=BatchCreateSessionStatus.COMPLETED,
            completed_at=completed_at,
            last_attempt_at=completed_at,
            expires_at=expires_at,
            last_error_code=None,
            last_error_message=None,
            increment_promotion_attempt_count=increment_promotion_attempt_count,
            from_statuses=from_statuses,
        )

    async def mark_session_failed_retryable(
        self,
        session_id: str,
        *,
        error_code: str | None,
        error_message: str | None,
        attempted_at: datetime,
        expires_at: datetime | None,
        increment_promotion_attempt_count: bool = False,
        from_statuses: tuple[str, ...] | None = None,
    ) -> BatchCreateSessionRecord | None:
        return await self._update_session(
            session_id,
            status=BatchCreateSessionStatus.FAILED_RETRYABLE,
            last_attempt_at=attempted_at,
            expires_at=expires_at,
            last_error_code=error_code,
            last_error_message=error_message,
            increment_promotion_attempt_count=increment_promotion_attempt_count,
            from_statuses=from_statuses,
        )

    async def mark_session_failed_permanent(
        self,
        session_id: str,
        *,
        error_code: str | None,
        error_message: str | None,
        attempted_at: datetime,
        expires_at: datetime | None,
        increment_promotion_attempt_count: bool = False,
        from_statuses: tuple[str, ...] | None = None,
    ) -> BatchCreateSessionRecord | None:
        return await self._update_session(
            session_id,
            status=BatchCreateSessionStatus.FAILED_PERMANENT,
            last_attempt_at=attempted_at,
            expires_at=expires_at,
            last_error_code=error_code,
            last_error_message=error_message,
            increment_promotion_attempt_count=increment_promotion_attempt_count,
            from_statuses=from_statuses,
        )

    async def mark_session_expired(
        self,
        session_id: str,
        *,
        expired_at: datetime,
    ) -> BatchCreateSessionRecord | None:
        return await self._update_session(
            session_id,
            status=BatchCreateSessionStatus.EXPIRED,
            expires_at=expired_at,
        )

    async def list_cleanup_candidates(
        self,
        *,
        now: datetime,
        completed_before: datetime,
        retryable_before: datetime,
        failed_before: datetime,
        limit: int = 100,
    ) -> list[BatchCreateSessionRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_create_session
            WHERE (
                    expires_at IS NOT NULL
                AND expires_at < $1::timestamp
            )
               OR (
                    expires_at IS NULL
                AND (
                        (status = 'completed' AND COALESCE(completed_at, created_at) < $2::timestamp)
                     OR (status = 'failed_retryable' AND COALESCE(last_attempt_at, created_at) < $3::timestamp)
                     OR (status = 'failed_permanent' AND COALESCE(last_attempt_at, created_at) < $4::timestamp)
                )
            )
            ORDER BY COALESCE(expires_at, completed_at, last_attempt_at, created_at) ASC
            LIMIT $5
            """,
            now,
            completed_before,
            retryable_before,
            failed_before,
            max(1, min(limit, 1000)),
        )
        return [_session_from_row(row) for row in rows]

    async def summarize_statuses(self) -> dict[str, int]:
        if self.prisma is None:
            return {status: 0 for status in BATCH_CREATE_SESSION_STATUSES}
        rows = await self.prisma.query_raw(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'staged') AS staged,
                COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                COUNT(*) FILTER (WHERE status = 'failed_retryable') AS failed_retryable,
                COUNT(*) FILTER (WHERE status = 'failed_permanent') AS failed_permanent,
                COUNT(*) FILTER (WHERE status = 'expired') AS expired
            FROM deltallm_batch_create_session
            """
        )
        row = dict(rows[0]) if rows else {}
        return {
            status: int(row.get(status) or 0)
            for status in BATCH_CREATE_SESSION_STATUSES
        }

    async def is_stage_artifact_referenced(
        self,
        *,
        storage_backend: str,
        storage_key: str,
    ) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            SELECT 1
            FROM deltallm_batch_create_session
            WHERE staged_storage_backend = $1
              AND staged_storage_key = $2
            LIMIT 1
            """,
            str(storage_backend or "").strip(),
            str(storage_key or "").strip(),
        )
        return bool(rows)

    async def delete_cleanup_candidate(self, session: BatchCreateSessionRecord) -> BatchCreateSessionRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_batch_create_session
            WHERE session_id = $1
              AND status = $2
              AND staged_storage_backend = $3
              AND staged_storage_key = $4
              AND expires_at IS NOT DISTINCT FROM $5::timestamp
              AND completed_at IS NOT DISTINCT FROM $6::timestamp
              AND last_attempt_at IS NOT DISTINCT FROM $7::timestamp
            RETURNING *
            """,
            session.session_id,
            normalize_batch_create_session_status(session.status),
            session.staged_storage_backend,
            session.staged_storage_key,
            session.expires_at,
            session.completed_at,
            session.last_attempt_at,
        )
        if not rows:
            return None
        return _session_from_row(rows[0])

    async def delete_session(self, session_id: str) -> bool:
        if self.prisma is None:
            return False
        deleted = await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_batch_create_session
            WHERE session_id = $1
            """,
            session_id,
        )
        return bool(deleted)

    async def _update_session(
        self,
        session_id: str,
        *,
        status: str,
        completed_at: datetime | None | object = _UNSET,
        last_attempt_at: datetime | None | object = _UNSET,
        expires_at: datetime | None | object = _UNSET,
        last_error_code: str | None | object = _UNSET,
        last_error_message: str | None | object = _UNSET,
        increment_promotion_attempt_count: bool = False,
        from_statuses: tuple[str, ...] | None = None,
    ) -> BatchCreateSessionRecord | None:
        if self.prisma is None:
            return None
        normalized_status = normalize_batch_create_session_status(status)
        params: list[Any] = [session_id, normalized_status]
        set_clauses = ["status = $2"]
        where_clauses = ["session_id = $1"]

        if increment_promotion_attempt_count:
            set_clauses.append("promotion_attempt_count = promotion_attempt_count + 1")

        def _add_clause(column: str, value: Any, cast: str | None = None) -> None:
            if value is _UNSET:
                return
            params.append(value)
            placeholder = f"${len(params)}"
            if cast is not None:
                placeholder = f"{placeholder}::{cast}"
            set_clauses.append(f"{column} = {placeholder}")

        _add_clause("completed_at", completed_at, "timestamp")
        _add_clause("last_attempt_at", last_attempt_at, "timestamp")
        _add_clause("expires_at", expires_at, "timestamp")
        _add_clause("last_error_code", last_error_code)
        _add_clause("last_error_message", last_error_message)

        if from_statuses:
            placeholders: list[str] = []
            for value in from_statuses:
                params.append(normalize_batch_create_session_status(value))
                placeholders.append(f"${len(params)}")
            where_clauses.append(f"status IN ({', '.join(placeholders)})")

        rows = await self.prisma.query_raw(
            f"""
            UPDATE deltallm_batch_create_session
            SET {", ".join(set_clauses)}
            WHERE {" AND ".join(where_clauses)}
            RETURNING *
            """,
            *params,
        )
        if not rows:
            return None
        return _session_from_row(rows[0])
