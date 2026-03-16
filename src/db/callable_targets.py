from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


@dataclass
class CallableTargetBindingRecord:
    callable_target_binding_id: str
    callable_key: str
    scope_type: str
    scope_id: str
    enabled: bool = True
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CallableTargetBindingRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def list_bindings(
        self,
        *,
        callable_key: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[CallableTargetBindingRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if callable_key:
            params.append(callable_key)
            clauses.append(f"callable_key = ${len(params)}")
        if scope_type:
            params.append(scope_type)
            clauses.append(f"scope_type = ${len(params)}")
        if scope_id:
            params.append(scope_id)
            clauses.append(f"scope_id = ${len(params)}")

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_callabletargetbinding {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                callable_target_binding_id,
                callable_key,
                scope_type,
                scope_id,
                enabled,
                metadata,
                created_at,
                updated_at
            FROM deltallm_callabletargetbinding
            {where_sql}
            ORDER BY created_at DESC, callable_key ASC, scope_type ASC, scope_id ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [self._to_binding_record(row) for row in rows], total

    async def upsert_binding(
        self,
        *,
        callable_key: str,
        scope_type: str,
        scope_id: str,
        enabled: bool,
        metadata: dict[str, Any] | None,
    ) -> CallableTargetBindingRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_callabletargetbinding (
                callable_target_binding_id,
                callable_key,
                scope_type,
                scope_id,
                enabled,
                metadata,
                created_at,
                updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5::jsonb, NOW(), NOW())
            ON CONFLICT (callable_key, scope_type, scope_id)
            DO UPDATE SET
                enabled = EXCLUDED.enabled,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING callable_target_binding_id, callable_key, scope_type, scope_id, enabled, metadata, created_at, updated_at
            """,
            callable_key,
            scope_type,
            scope_id,
            enabled,
            json.dumps(metadata) if metadata is not None else None,
        )
        return self._to_binding_record(rows[0]) if rows else None

    async def get_binding(self, binding_id: str) -> CallableTargetBindingRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT
                callable_target_binding_id,
                callable_key,
                scope_type,
                scope_id,
                enabled,
                metadata,
                created_at,
                updated_at
            FROM deltallm_callabletargetbinding
            WHERE callable_target_binding_id = $1
            LIMIT 1
            """,
            binding_id,
        )
        return self._to_binding_record(rows[0]) if rows else None

    async def delete_binding(self, binding_id: str) -> bool:
        if self.prisma is None:
            return False

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_callabletargetbinding
            WHERE callable_target_binding_id = $1
            RETURNING callable_target_binding_id
            """,
            binding_id,
        )
        return bool(rows)

    @staticmethod
    def _to_binding_record(row: dict[str, Any]) -> CallableTargetBindingRecord:
        return CallableTargetBindingRecord(
            callable_target_binding_id=str(row.get("callable_target_binding_id") or ""),
            callable_key=str(row.get("callable_key") or ""),
            scope_type=str(row.get("scope_type") or ""),
            scope_id=str(row.get("scope_id") or ""),
            enabled=bool(row.get("enabled", True)),
            metadata=_parse_json_object(row.get("metadata")) if row.get("metadata") is not None else None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )
