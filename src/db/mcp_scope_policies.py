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
class MCPScopePolicyRecord:
    mcp_scope_policy_id: str
    scope_type: str
    scope_id: str
    mode: str = "inherit"
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MCPScopePolicyRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def list_policies(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[MCPScopePolicyRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if scope_type:
            params.append(scope_type)
            clauses.append(f"scope_type = ${len(params)}")
        if scope_id:
            params.append(scope_id)
            clauses.append(f"scope_id = ${len(params)}")

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_mcpscopepolicy {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                mcp_scope_policy_id,
                scope_type,
                scope_id,
                mode,
                metadata,
                created_at,
                updated_at
            FROM deltallm_mcpscopepolicy
            {where_sql}
            ORDER BY created_at DESC, scope_type ASC, scope_id ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [self._to_policy_record(row) for row in rows], total

    async def upsert_policy(
        self,
        *,
        scope_type: str,
        scope_id: str,
        mode: str,
        metadata: dict[str, Any] | None,
    ) -> MCPScopePolicyRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_mcpscopepolicy (
                mcp_scope_policy_id,
                scope_type,
                scope_id,
                mode,
                metadata,
                created_at,
                updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4::jsonb, NOW(), NOW())
            ON CONFLICT (scope_type, scope_id)
            DO UPDATE SET
                mode = EXCLUDED.mode,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING mcp_scope_policy_id, scope_type, scope_id, mode, metadata, created_at, updated_at
            """,
            scope_type,
            scope_id,
            mode,
            json.dumps(metadata) if metadata is not None else None,
        )
        return self._to_policy_record(rows[0]) if rows else None

    async def get_policy(self, policy_id: str) -> MCPScopePolicyRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT
                mcp_scope_policy_id,
                scope_type,
                scope_id,
                mode,
                metadata,
                created_at,
                updated_at
            FROM deltallm_mcpscopepolicy
            WHERE mcp_scope_policy_id = $1
            LIMIT 1
            """,
            policy_id,
        )
        return self._to_policy_record(rows[0]) if rows else None

    async def delete_policy(self, policy_id: str) -> bool:
        if self.prisma is None:
            return False

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_mcpscopepolicy
            WHERE mcp_scope_policy_id = $1
            RETURNING mcp_scope_policy_id
            """,
            policy_id,
        )
        return bool(rows)

    @staticmethod
    def _to_policy_record(row: dict[str, Any]) -> MCPScopePolicyRecord:
        return MCPScopePolicyRecord(
            mcp_scope_policy_id=str(row.get("mcp_scope_policy_id") or ""),
            scope_type=str(row.get("scope_type") or ""),
            scope_id=str(row.get("scope_id") or ""),
            mode=str(row.get("mode") or "inherit"),
            metadata=_parse_json_object(row.get("metadata")) if row.get("metadata") is not None else None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )
