from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.governance.access_groups import normalize_access_group_key


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
class CallableTargetAccessGroupBindingRecord:
    callable_target_access_group_binding_id: str
    group_key: str
    scope_type: str
    scope_id: str
    enabled: bool = True
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class CallableTargetAccessGroupBindingCount:
    group_key: str
    binding_count: int


class CallableTargetAccessGroupBindingRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def list_bindings(
        self,
        *,
        group_key: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[CallableTargetAccessGroupBindingRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if group_key:
            normalized_group_key = normalize_access_group_key(group_key)
            if normalized_group_key is None:
                return [], 0
            params.append(normalized_group_key)
            clauses.append(f"group_key = ${len(params)}")
        if scope_type:
            params.append(str(scope_type).strip().lower())
            clauses.append(f"scope_type = ${len(params)}")
        if scope_id:
            params.append(str(scope_id).strip())
            clauses.append(f"scope_id = ${len(params)}")

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_callabletargetaccessgroupbinding {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                callable_target_access_group_binding_id,
                group_key,
                scope_type,
                scope_id,
                enabled,
                metadata,
                created_at,
                updated_at
            FROM deltallm_callabletargetaccessgroupbinding
            {where_sql}
            ORDER BY created_at DESC, group_key ASC, scope_type ASC, scope_id ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [self._to_binding_record(row) for row in rows], total

    async def list_group_binding_counts(
        self,
        *,
        search: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[CallableTargetAccessGroupBindingCount], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        normalized_search = str(search or "").strip()
        if normalized_search:
            params.append(f"%{normalized_search}%")
            clauses.append(f"group_key ILIKE ${len(params)}")

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"""
            SELECT COUNT(*)::int AS total
            FROM (
                SELECT group_key
                FROM deltallm_callabletargetaccessgroupbinding
                {where_sql}
                GROUP BY group_key
            ) grouped
            """,
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT group_key, COUNT(*)::int AS binding_count
            FROM deltallm_callabletargetaccessgroupbinding
            {where_sql}
            GROUP BY group_key
            ORDER BY group_key ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [
            CallableTargetAccessGroupBindingCount(
                group_key=str(row.get("group_key") or ""),
                binding_count=int(row.get("binding_count") or 0),
            )
            for row in rows
        ], total

    async def upsert_binding(
        self,
        *,
        group_key: str,
        scope_type: str,
        scope_id: str,
        enabled: bool,
        metadata: dict[str, Any] | None,
    ) -> CallableTargetAccessGroupBindingRecord | None:
        if self.prisma is None:
            return None

        normalized_group_key = normalize_access_group_key(group_key, strict=True)
        if normalized_group_key is None:
            raise ValueError("group_key is required")
        normalized_scope_type = str(scope_type or "").strip().lower()
        normalized_scope_id = str(scope_id or "").strip()
        if not normalized_scope_type:
            raise ValueError("scope_type is required")
        if not normalized_scope_id:
            raise ValueError("scope_id is required")

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_callabletargetaccessgroupbinding (
                callable_target_access_group_binding_id,
                group_key,
                scope_type,
                scope_id,
                enabled,
                metadata,
                created_at,
                updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5::jsonb, NOW(), NOW())
            ON CONFLICT (group_key, scope_type, scope_id)
            DO UPDATE SET
                enabled = EXCLUDED.enabled,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING
                callable_target_access_group_binding_id,
                group_key,
                scope_type,
                scope_id,
                enabled,
                metadata,
                created_at,
                updated_at
            """,
            normalized_group_key,
            normalized_scope_type,
            normalized_scope_id,
            enabled,
            json.dumps(metadata) if metadata is not None else None,
        )
        return self._to_binding_record(rows[0]) if rows else None

    async def get_binding(
        self,
        binding_id: str,
    ) -> CallableTargetAccessGroupBindingRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT
                callable_target_access_group_binding_id,
                group_key,
                scope_type,
                scope_id,
                enabled,
                metadata,
                created_at,
                updated_at
            FROM deltallm_callabletargetaccessgroupbinding
            WHERE callable_target_access_group_binding_id = $1
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
            DELETE FROM deltallm_callabletargetaccessgroupbinding
            WHERE callable_target_access_group_binding_id = $1
            RETURNING callable_target_access_group_binding_id
            """,
            binding_id,
        )
        return bool(rows)

    @staticmethod
    def _to_binding_record(row: dict[str, Any]) -> CallableTargetAccessGroupBindingRecord:
        return CallableTargetAccessGroupBindingRecord(
            callable_target_access_group_binding_id=str(
                row.get("callable_target_access_group_binding_id") or ""
            ),
            group_key=str(row.get("group_key") or ""),
            scope_type=str(row.get("scope_type") or ""),
            scope_id=str(row.get("scope_id") or ""),
            enabled=bool(row.get("enabled", True)),
            metadata=_parse_json_object(row.get("metadata")) if row.get("metadata") is not None else None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )
