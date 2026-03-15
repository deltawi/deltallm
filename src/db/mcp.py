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


def _parse_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, tuple):
        return [str(item) for item in value if item is not None]
    return []


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
class MCPServerRecord:
    mcp_server_id: str
    server_key: str
    name: str
    description: str | None = None
    owner_scope_type: str = "global"
    owner_scope_id: str | None = None
    transport: str = "streamable_http"
    base_url: str = ""
    enabled: bool = True
    auth_mode: str = "none"
    auth_config: dict[str, Any] | None = None
    forwarded_headers_allowlist: list[str] | None = None
    request_timeout_ms: int = 30000
    capabilities_json: dict[str, Any] | None = None
    capabilities_etag: str | None = None
    capabilities_fetched_at: datetime | None = None
    last_health_status: str | None = None
    last_health_error: str | None = None
    last_health_at: datetime | None = None
    last_health_latency_ms: int | None = None
    metadata: dict[str, Any] | None = None
    created_by_account_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class MCPServerBindingRecord:
    mcp_binding_id: str
    mcp_server_id: str
    scope_type: str
    scope_id: str
    enabled: bool = True
    tool_allowlist: list[str] | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class MCPToolPolicyRecord:
    mcp_tool_policy_id: str
    mcp_server_id: str
    tool_name: str
    scope_type: str
    scope_id: str
    enabled: bool = True
    require_approval: str | None = None
    max_rpm: int | None = None
    max_concurrency: int | None = None
    result_cache_ttl_seconds: int | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class MCPApprovalRequestRecord:
    mcp_approval_request_id: str
    mcp_server_id: str
    tool_name: str
    scope_type: str
    scope_id: str
    status: str = "pending"
    request_fingerprint: str = ""
    requested_by_api_key: str | None = None
    requested_by_user: str | None = None
    organization_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    arguments_json: dict[str, Any] | None = None
    decision_comment: str | None = None
    decided_by_account_id: str | None = None
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MCPRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def list_servers(
        self,
        *,
        search: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MCPServerRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if search:
            params.append(f"%{search.strip()}%")
            clauses.append(
                f"(s.server_key ILIKE ${len(params)} OR s.name ILIKE ${len(params)} OR COALESCE(s.description, '') ILIKE ${len(params)})"
            )
        if enabled is not None:
            params.append(enabled)
            clauses.append(f"s.enabled = ${len(params)}")

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_mcpserver s {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                s.mcp_server_id,
                s.server_key,
                s.name,
                s.description,
                s.owner_scope_type,
                s.owner_scope_id,
                s.transport,
                s.base_url,
                s.enabled,
                s.auth_mode,
                s.auth_config,
                s.forwarded_headers_allowlist,
                s.request_timeout_ms,
                s.capabilities_json,
                s.capabilities_etag,
                s.capabilities_fetched_at,
                s.last_health_status,
                s.last_health_error,
                s.last_health_at,
                s.last_health_latency_ms,
                s.metadata,
                s.created_by_account_id,
                s.created_at,
                s.updated_at
            FROM deltallm_mcpserver s
            {where_sql}
            ORDER BY s.created_at DESC, s.server_key ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [self._to_server_record(row) for row in rows], total

    async def get_server(self, server_id: str) -> MCPServerRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                mcp_server_id,
                server_key,
                name,
                description,
                owner_scope_type,
                owner_scope_id,
                transport,
                base_url,
                enabled,
                auth_mode,
                auth_config,
                forwarded_headers_allowlist,
                request_timeout_ms,
                capabilities_json,
                capabilities_etag,
                capabilities_fetched_at,
                last_health_status,
                last_health_error,
                last_health_at,
                last_health_latency_ms,
                metadata,
                created_by_account_id,
                created_at,
                updated_at
            FROM deltallm_mcpserver
            WHERE mcp_server_id = $1
            LIMIT 1
            """,
            server_id,
        )
        return self._to_server_record(rows[0]) if rows else None

    async def get_server_by_key(self, server_key: str) -> MCPServerRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                mcp_server_id,
                server_key,
                name,
                description,
                owner_scope_type,
                owner_scope_id,
                transport,
                base_url,
                enabled,
                auth_mode,
                auth_config,
                forwarded_headers_allowlist,
                request_timeout_ms,
                capabilities_json,
                capabilities_etag,
                capabilities_fetched_at,
                last_health_status,
                last_health_error,
                last_health_at,
                last_health_latency_ms,
                metadata,
                created_by_account_id,
                created_at,
                updated_at
            FROM deltallm_mcpserver
            WHERE server_key = $1
            LIMIT 1
            """,
            server_key,
        )
        return self._to_server_record(rows[0]) if rows else None

    async def create_server(
        self,
        *,
        server_key: str,
        name: str,
        description: str | None,
        owner_scope_type: str,
        owner_scope_id: str | None,
        transport: str,
        base_url: str,
        enabled: bool,
        auth_mode: str,
        auth_config: dict[str, Any] | None,
        forwarded_headers_allowlist: list[str] | None,
        request_timeout_ms: int,
        metadata: dict[str, Any] | None,
        created_by_account_id: str | None,
    ) -> MCPServerRecord:
        if self.prisma is None:
            return MCPServerRecord(
                mcp_server_id="",
                server_key=server_key,
                name=name,
                description=description,
                owner_scope_type=owner_scope_type,
                owner_scope_id=owner_scope_id,
                transport=transport,
                base_url=base_url,
                enabled=enabled,
                auth_mode=auth_mode,
                auth_config=auth_config,
                forwarded_headers_allowlist=forwarded_headers_allowlist or [],
                request_timeout_ms=request_timeout_ms,
                metadata=metadata,
                created_by_account_id=created_by_account_id,
            )
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_mcpserver (
                mcp_server_id, server_key, name, description, owner_scope_type, owner_scope_id,
                transport, base_url, enabled, auth_mode, auth_config, forwarded_headers_allowlist,
                request_timeout_ms, metadata, created_by_account_id, created_at, updated_at
            )
            VALUES (
                gen_random_uuid()::text, $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10::jsonb, $11::text[],
                $12, $13::jsonb, $14, NOW(), NOW()
            )
            RETURNING
                mcp_server_id, server_key, name, description, owner_scope_type, owner_scope_id,
                transport, base_url, enabled, auth_mode, auth_config, forwarded_headers_allowlist,
                request_timeout_ms, capabilities_json, capabilities_etag, capabilities_fetched_at,
                last_health_status, last_health_error, last_health_at, last_health_latency_ms,
                metadata, created_by_account_id, created_at, updated_at
            """,
            server_key,
            name,
            description,
            owner_scope_type,
            owner_scope_id,
            transport,
            base_url,
            enabled,
            auth_mode,
            auth_config or {},
            forwarded_headers_allowlist or [],
            request_timeout_ms,
            metadata or {},
            created_by_account_id,
        )
        return self._to_server_record(rows[0])

    async def update_server(
        self,
        server_id: str,
        *,
        name: str,
        description: str | None,
        transport: str,
        base_url: str,
        enabled: bool,
        auth_mode: str,
        auth_config: dict[str, Any] | None,
        forwarded_headers_allowlist: list[str] | None,
        request_timeout_ms: int,
        metadata: dict[str, Any] | None,
    ) -> MCPServerRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_mcpserver
            SET
                name = $2,
                description = $3,
                transport = $4,
                base_url = $5,
                enabled = $6,
                auth_mode = $7,
                auth_config = $8::jsonb,
                forwarded_headers_allowlist = $9::text[],
                request_timeout_ms = $10,
                metadata = $11::jsonb,
                updated_at = NOW()
            WHERE mcp_server_id = $1
            RETURNING
                mcp_server_id, server_key, name, description, owner_scope_type, owner_scope_id,
                transport, base_url, enabled, auth_mode, auth_config, forwarded_headers_allowlist,
                request_timeout_ms, capabilities_json, capabilities_etag, capabilities_fetched_at,
                last_health_status, last_health_error, last_health_at, last_health_latency_ms,
                metadata, created_by_account_id, created_at, updated_at
            """,
            server_id,
            name,
            description,
            transport,
            base_url,
            enabled,
            auth_mode,
            auth_config or {},
            forwarded_headers_allowlist or [],
            request_timeout_ms,
            metadata or {},
        )
        return self._to_server_record(rows[0]) if rows else None

    async def delete_server(self, server_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_mcpserver
            WHERE mcp_server_id = $1
            RETURNING mcp_server_id
            """,
            server_id,
        )
        return bool(rows)

    async def update_server_capabilities(
        self,
        server_id: str,
        *,
        capabilities_json: dict[str, Any] | None,
        capabilities_etag: str | None = None,
    ) -> MCPServerRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_mcpserver
            SET
                capabilities_json = $2::jsonb,
                capabilities_etag = $3,
                capabilities_fetched_at = NOW(),
                updated_at = NOW()
            WHERE mcp_server_id = $1
            RETURNING
                mcp_server_id, server_key, name, description, owner_scope_type, owner_scope_id,
                transport, base_url, enabled, auth_mode, auth_config, forwarded_headers_allowlist,
                request_timeout_ms, capabilities_json, capabilities_etag, capabilities_fetched_at,
                last_health_status, last_health_error, last_health_at, last_health_latency_ms,
                metadata, created_by_account_id, created_at, updated_at
            """,
            server_id,
            capabilities_json or {},
            capabilities_etag,
        )
        return self._to_server_record(rows[0]) if rows else None

    async def record_health_check(
        self,
        server_id: str,
        *,
        status: str,
        error: str | None,
        latency_ms: int | None,
    ) -> MCPServerRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_mcpserver
            SET
                last_health_status = $2,
                last_health_error = $3,
                last_health_latency_ms = $4,
                last_health_at = NOW(),
                updated_at = NOW()
            WHERE mcp_server_id = $1
            RETURNING
                mcp_server_id, server_key, name, description, owner_scope_type, owner_scope_id,
                transport, base_url, enabled, auth_mode, auth_config, forwarded_headers_allowlist,
                request_timeout_ms, capabilities_json, capabilities_etag, capabilities_fetched_at,
                last_health_status, last_health_error, last_health_at, last_health_latency_ms,
                metadata, created_by_account_id, created_at, updated_at
            """,
            server_id,
            status,
            error,
            latency_ms,
        )
        return self._to_server_record(rows[0]) if rows else None

    async def list_bindings(
        self,
        *,
        server_id: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[MCPServerBindingRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if server_id:
            params.append(server_id)
            clauses.append(f"b.mcp_server_id = ${len(params)}")
        if scope_type:
            params.append(scope_type)
            clauses.append(f"b.scope_type = ${len(params)}")
        if scope_id:
            params.append(scope_id)
            clauses.append(f"b.scope_id = ${len(params)}")

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_mcpbinding b {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                b.mcp_binding_id,
                b.mcp_server_id,
                b.scope_type,
                b.scope_id,
                b.enabled,
                b.tool_allowlist,
                b.metadata,
                b.created_at,
                b.updated_at
            FROM deltallm_mcpbinding b
            {where_sql}
            ORDER BY b.created_at DESC, b.scope_type ASC, b.scope_id ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [self._to_binding_record(row) for row in rows], total

    async def upsert_binding(
        self,
        *,
        server_id: str,
        scope_type: str,
        scope_id: str,
        enabled: bool,
        tool_allowlist: list[str] | None,
        metadata: dict[str, Any] | None,
    ) -> MCPServerBindingRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_mcpbinding (
                mcp_binding_id, mcp_server_id, scope_type, scope_id, enabled, tool_allowlist, metadata, created_at, updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5::text[], $6::jsonb, NOW(), NOW())
            ON CONFLICT (mcp_server_id, scope_type, scope_id)
            DO UPDATE SET
                enabled = EXCLUDED.enabled,
                tool_allowlist = EXCLUDED.tool_allowlist,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING mcp_binding_id, mcp_server_id, scope_type, scope_id, enabled, tool_allowlist, metadata, created_at, updated_at
            """,
            server_id,
            scope_type,
            scope_id,
            enabled,
            tool_allowlist or [],
            metadata or {},
        )
        return self._to_binding_record(rows[0]) if rows else None

    async def delete_binding(self, binding_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_mcpbinding
            WHERE mcp_binding_id = $1
            RETURNING mcp_binding_id
            """,
            binding_id,
        )
        return bool(rows)

    async def get_binding(self, binding_id: str) -> MCPServerBindingRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                mcp_binding_id,
                mcp_server_id,
                scope_type,
                scope_id,
                enabled,
                tool_allowlist,
                metadata,
                created_at,
                updated_at
            FROM deltallm_mcpbinding
            WHERE mcp_binding_id = $1
            LIMIT 1
            """,
            binding_id,
        )
        return self._to_binding_record(rows[0]) if rows else None

    async def list_effective_bindings(self, *, scopes: list[tuple[str, str]]) -> list[MCPServerBindingRecord]:
        if self.prisma is None or not scopes:
            return []
        params: list[Any] = []
        scope_clauses: list[str] = []
        for scope_type, scope_id in scopes:
            params.extend([scope_type, scope_id])
            base = len(params) - 1
            scope_clauses.append(f"(b.scope_type = ${base} AND b.scope_id = ${base + 1})")
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                b.mcp_binding_id,
                b.mcp_server_id,
                b.scope_type,
                b.scope_id,
                b.enabled,
                b.tool_allowlist,
                b.metadata,
                b.created_at,
                b.updated_at
            FROM deltallm_mcpbinding b
            JOIN deltallm_mcpserver s ON s.mcp_server_id = b.mcp_server_id
            WHERE b.enabled = true
              AND s.enabled = true
              AND ({' OR '.join(scope_clauses)})
            ORDER BY b.created_at ASC
            """,
            *params,
        )
        return [self._to_binding_record(row) for row in rows]

    async def list_tool_policies(
        self,
        *,
        server_id: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[MCPToolPolicyRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if server_id:
            params.append(server_id)
            clauses.append(f"p.mcp_server_id = ${len(params)}")
        if scope_type:
            params.append(scope_type)
            clauses.append(f"p.scope_type = ${len(params)}")
        if scope_id:
            params.append(scope_id)
            clauses.append(f"p.scope_id = ${len(params)}")
        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_mcptoolpolicy p {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                p.mcp_tool_policy_id,
                p.mcp_server_id,
                p.tool_name,
                p.scope_type,
                p.scope_id,
                p.enabled,
                p.require_approval,
                p.max_rpm,
                p.max_concurrency,
                p.result_cache_ttl_seconds,
                p.metadata,
                p.created_at,
                p.updated_at
            FROM deltallm_mcptoolpolicy p
            {where_sql}
            ORDER BY p.created_at DESC, p.tool_name ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [self._to_tool_policy_record(row) for row in rows], total

    async def upsert_tool_policy(
        self,
        *,
        server_id: str,
        tool_name: str,
        scope_type: str,
        scope_id: str,
        enabled: bool,
        require_approval: str | None,
        max_rpm: int | None,
        max_concurrency: int | None,
        result_cache_ttl_seconds: int | None,
        metadata: dict[str, Any] | None,
    ) -> MCPToolPolicyRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_mcptoolpolicy (
                mcp_tool_policy_id, mcp_server_id, tool_name, scope_type, scope_id, enabled, require_approval,
                max_rpm, max_concurrency, result_cache_ttl_seconds, metadata, created_at, updated_at
            )
            VALUES (
                gen_random_uuid()::text, $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10::jsonb, NOW(), NOW()
            )
            ON CONFLICT (mcp_server_id, tool_name, scope_type, scope_id)
            DO UPDATE SET
                enabled = EXCLUDED.enabled,
                require_approval = EXCLUDED.require_approval,
                max_rpm = EXCLUDED.max_rpm,
                max_concurrency = EXCLUDED.max_concurrency,
                result_cache_ttl_seconds = EXCLUDED.result_cache_ttl_seconds,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING
                mcp_tool_policy_id, mcp_server_id, tool_name, scope_type, scope_id, enabled,
                require_approval, max_rpm, max_concurrency, result_cache_ttl_seconds, metadata, created_at, updated_at
            """,
            server_id,
            tool_name,
            scope_type,
            scope_id,
            enabled,
            require_approval,
            max_rpm,
            max_concurrency,
            result_cache_ttl_seconds,
            metadata or {},
        )
        return self._to_tool_policy_record(rows[0]) if rows else None

    async def delete_tool_policy(self, policy_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_mcptoolpolicy
            WHERE mcp_tool_policy_id = $1
            RETURNING mcp_tool_policy_id
            """,
            policy_id,
        )
        return bool(rows)

    async def get_tool_policy(self, policy_id: str) -> MCPToolPolicyRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                mcp_tool_policy_id,
                mcp_server_id,
                tool_name,
                scope_type,
                scope_id,
                enabled,
                require_approval,
                max_rpm,
                max_concurrency,
                result_cache_ttl_seconds,
                metadata,
                created_at,
                updated_at
            FROM deltallm_mcptoolpolicy
            WHERE mcp_tool_policy_id = $1
            LIMIT 1
            """,
            policy_id,
        )
        return self._to_tool_policy_record(rows[0]) if rows else None

    async def list_effective_tool_policies(
        self,
        *,
        scopes: list[tuple[str, str]],
        server_id: str | None = None,
    ) -> list[MCPToolPolicyRecord]:
        if self.prisma is None or not scopes:
            return []
        params: list[Any] = []
        scope_clauses: list[str] = []
        for scope_type, scope_id in scopes:
            params.extend([scope_type, scope_id])
            base = len(params) - 1
            scope_clauses.append(f"(p.scope_type = ${base} AND p.scope_id = ${base + 1})")
        server_sql = ""
        if server_id:
            params.append(server_id)
            server_sql = f" AND p.mcp_server_id = ${len(params)}"
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                p.mcp_tool_policy_id,
                p.mcp_server_id,
                p.tool_name,
                p.scope_type,
                p.scope_id,
                p.enabled,
                p.require_approval,
                p.max_rpm,
                p.max_concurrency,
                p.result_cache_ttl_seconds,
                p.metadata,
                p.created_at,
                p.updated_at
            FROM deltallm_mcptoolpolicy p
            JOIN deltallm_mcpserver s ON s.mcp_server_id = p.mcp_server_id
            WHERE s.enabled = true
              AND ({' OR '.join(scope_clauses)})
              {server_sql}
            ORDER BY p.created_at ASC
            """,
            *params,
        )
        return [self._to_tool_policy_record(row) for row in rows]

    async def find_pending_approval_request(self, *, request_fingerprint: str) -> MCPApprovalRequestRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                r.mcp_approval_request_id,
                r.mcp_server_id,
                r.tool_name,
                r.scope_type,
                r.scope_id,
                r.status,
                r.request_fingerprint,
                r.requested_by_api_key,
                r.requested_by_user,
                r.organization_id,
                r.request_id,
                r.correlation_id,
                r.arguments_json,
                r.decision_comment,
                r.decided_by_account_id,
                r.decided_at,
                r.expires_at,
                r.metadata,
                r.created_at,
                r.updated_at
            FROM deltallm_mcpapprovalrequest r
            WHERE r.request_fingerprint = $1
              AND r.status = 'pending'
              AND (r.expires_at IS NULL OR r.expires_at > NOW())
            ORDER BY r.created_at DESC
            LIMIT 1
            """,
            request_fingerprint,
        )
        return self._to_approval_request_record(rows[0]) if rows else None

    async def expire_stale_approval_requests(self, *, request_fingerprint: str) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_mcpapprovalrequest
            SET status = 'expired',
                updated_at = NOW()
            WHERE request_fingerprint = $1
              AND status IN ('pending', 'approved', 'rejected')
              AND expires_at IS NOT NULL
              AND expires_at <= NOW()
            RETURNING mcp_approval_request_id
            """,
            request_fingerprint,
        )
        return len(rows)

    async def find_active_approval_request(self, *, request_fingerprint: str) -> MCPApprovalRequestRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                r.mcp_approval_request_id,
                r.mcp_server_id,
                r.tool_name,
                r.scope_type,
                r.scope_id,
                r.status,
                r.request_fingerprint,
                r.requested_by_api_key,
                r.requested_by_user,
                r.organization_id,
                r.request_id,
                r.correlation_id,
                r.arguments_json,
                r.decision_comment,
                r.decided_by_account_id,
                r.decided_at,
                r.expires_at,
                r.metadata,
                r.created_at,
                r.updated_at
            FROM deltallm_mcpapprovalrequest r
            WHERE r.request_fingerprint = $1
              AND r.status IN ('pending', 'approved', 'rejected')
              AND (r.expires_at IS NULL OR r.expires_at > NOW())
            ORDER BY COALESCE(r.decided_at, r.created_at) DESC, r.updated_at DESC
            LIMIT 1
            """,
            request_fingerprint,
        )
        return self._to_approval_request_record(rows[0]) if rows else None

    async def get_approval_request(self, approval_request_id: str) -> MCPApprovalRequestRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT
                r.mcp_approval_request_id,
                r.mcp_server_id,
                r.tool_name,
                r.scope_type,
                r.scope_id,
                r.status,
                r.request_fingerprint,
                r.requested_by_api_key,
                r.requested_by_user,
                r.organization_id,
                r.request_id,
                r.correlation_id,
                r.arguments_json,
                r.decision_comment,
                r.decided_by_account_id,
                r.decided_at,
                r.expires_at,
                r.metadata,
                r.created_at,
                r.updated_at
            FROM deltallm_mcpapprovalrequest r
            WHERE r.mcp_approval_request_id = $1
            LIMIT 1
            """,
            approval_request_id,
        )
        return self._to_approval_request_record(rows[0]) if rows else None

    async def create_approval_request(
        self,
        *,
        server_id: str,
        tool_name: str,
        scope_type: str,
        scope_id: str,
        request_fingerprint: str,
        requested_by_api_key: str | None,
        requested_by_user: str | None,
        organization_id: str | None,
        request_id: str | None,
        correlation_id: str | None,
        arguments_json: dict[str, Any],
        expires_at: datetime | None,
        metadata: dict[str, Any] | None,
    ) -> MCPApprovalRequestRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_mcpapprovalrequest (
                mcp_approval_request_id, mcp_server_id, tool_name, scope_type, scope_id, status,
                request_fingerprint, requested_by_api_key, requested_by_user, organization_id,
                request_id, correlation_id, arguments_json, expires_at, metadata, created_at, updated_at
            )
            VALUES (
                gen_random_uuid()::text, $1, $2, $3, $4, 'pending',
                $5, $6, $7, $8,
                $9, $10, $11::jsonb, $12, $13::jsonb, NOW(), NOW()
            )
            ON CONFLICT (request_fingerprint) WHERE status = 'pending'
            DO UPDATE SET updated_at = deltallm_mcpapprovalrequest.updated_at
            RETURNING
                mcp_approval_request_id, mcp_server_id, tool_name, scope_type, scope_id, status,
                request_fingerprint, requested_by_api_key, requested_by_user, organization_id,
                request_id, correlation_id, arguments_json, decision_comment, decided_by_account_id,
                decided_at, expires_at, metadata, created_at, updated_at
            """,
            server_id,
            tool_name,
            scope_type,
            scope_id,
            request_fingerprint,
            requested_by_api_key,
            requested_by_user,
            organization_id,
            request_id,
            correlation_id,
            arguments_json,
            expires_at,
            metadata or {},
        )
        return self._to_approval_request_record(rows[0]) if rows else None

    async def list_approval_requests(
        self,
        *,
        server_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MCPApprovalRequestRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if server_id:
            params.append(server_id)
            clauses.append(f"r.mcp_server_id = ${len(params)}")
        if status:
            params.append(status)
            clauses.append(f"r.status = ${len(params)}")
        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_mcpapprovalrequest r {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                r.mcp_approval_request_id,
                r.mcp_server_id,
                r.tool_name,
                r.scope_type,
                r.scope_id,
                r.status,
                r.request_fingerprint,
                r.requested_by_api_key,
                r.requested_by_user,
                r.organization_id,
                r.request_id,
                r.correlation_id,
                r.arguments_json,
                r.decision_comment,
                r.decided_by_account_id,
                r.decided_at,
                r.expires_at,
                r.metadata,
                r.created_at,
                r.updated_at
            FROM deltallm_mcpapprovalrequest r
            {where_sql}
            ORDER BY r.created_at DESC, r.mcp_approval_request_id ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [self._to_approval_request_record(row) for row in rows], total

    async def decide_approval_request(
        self,
        approval_request_id: str,
        *,
        status: str,
        decided_by_account_id: str | None,
        decision_comment: str | None,
        expires_at: datetime | None,
    ) -> MCPApprovalRequestRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_mcpapprovalrequest
            SET status = $2,
                decided_by_account_id = $3,
                decision_comment = $4,
                expires_at = $5,
                decided_at = NOW(),
                updated_at = NOW()
            WHERE mcp_approval_request_id = $1
              AND status = 'pending'
            RETURNING
                mcp_approval_request_id, mcp_server_id, tool_name, scope_type, scope_id, status,
                request_fingerprint, requested_by_api_key, requested_by_user, organization_id,
                request_id, correlation_id, arguments_json, decision_comment, decided_by_account_id,
                decided_at, expires_at, metadata, created_at, updated_at
            """,
            approval_request_id,
            status,
            decided_by_account_id,
            decision_comment,
            expires_at,
        )
        return self._to_approval_request_record(rows[0]) if rows else None

    @staticmethod
    def _to_server_record(row: dict[str, Any]) -> MCPServerRecord:
        return MCPServerRecord(
            mcp_server_id=str(row.get("mcp_server_id") or ""),
            server_key=str(row.get("server_key") or ""),
            name=str(row.get("name") or ""),
            description=str(row.get("description")) if row.get("description") is not None else None,
            owner_scope_type=str(row.get("owner_scope_type") or "global"),
            owner_scope_id=str(row.get("owner_scope_id")) if row.get("owner_scope_id") is not None else None,
            transport=str(row.get("transport") or "streamable_http"),
            base_url=str(row.get("base_url") or ""),
            enabled=bool(row.get("enabled", True)),
            auth_mode=str(row.get("auth_mode") or "none"),
            auth_config=_parse_json_object(row.get("auth_config")) or None,
            forwarded_headers_allowlist=_parse_text_list(row.get("forwarded_headers_allowlist")),
            request_timeout_ms=int(row.get("request_timeout_ms") or 30000),
            capabilities_json=_parse_json_object(row.get("capabilities_json")) or None,
            capabilities_etag=str(row.get("capabilities_etag")) if row.get("capabilities_etag") is not None else None,
            capabilities_fetched_at=_parse_datetime(row.get("capabilities_fetched_at")),
            last_health_status=str(row.get("last_health_status")) if row.get("last_health_status") is not None else None,
            last_health_error=str(row.get("last_health_error")) if row.get("last_health_error") is not None else None,
            last_health_at=_parse_datetime(row.get("last_health_at")),
            last_health_latency_ms=int(row.get("last_health_latency_ms")) if row.get("last_health_latency_ms") is not None else None,
            metadata=_parse_json_object(row.get("metadata")) or None,
            created_by_account_id=str(row.get("created_by_account_id")) if row.get("created_by_account_id") is not None else None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )

    @staticmethod
    def _to_binding_record(row: dict[str, Any]) -> MCPServerBindingRecord:
        return MCPServerBindingRecord(
            mcp_binding_id=str(row.get("mcp_binding_id") or ""),
            mcp_server_id=str(row.get("mcp_server_id") or ""),
            scope_type=str(row.get("scope_type") or ""),
            scope_id=str(row.get("scope_id") or ""),
            enabled=bool(row.get("enabled", True)),
            tool_allowlist=_parse_text_list(row.get("tool_allowlist")),
            metadata=_parse_json_object(row.get("metadata")) or None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )

    @staticmethod
    def _to_tool_policy_record(row: dict[str, Any]) -> MCPToolPolicyRecord:
        return MCPToolPolicyRecord(
            mcp_tool_policy_id=str(row.get("mcp_tool_policy_id") or ""),
            mcp_server_id=str(row.get("mcp_server_id") or ""),
            tool_name=str(row.get("tool_name") or ""),
            scope_type=str(row.get("scope_type") or ""),
            scope_id=str(row.get("scope_id") or ""),
            enabled=bool(row.get("enabled", True)),
            require_approval=str(row.get("require_approval")) if row.get("require_approval") is not None else None,
            max_rpm=int(row.get("max_rpm")) if row.get("max_rpm") is not None else None,
            max_concurrency=int(row.get("max_concurrency")) if row.get("max_concurrency") is not None else None,
            result_cache_ttl_seconds=int(row.get("result_cache_ttl_seconds")) if row.get("result_cache_ttl_seconds") is not None else None,
            metadata=_parse_json_object(row.get("metadata")) or None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )

    @staticmethod
    def _to_approval_request_record(row: dict[str, Any]) -> MCPApprovalRequestRecord:
        return MCPApprovalRequestRecord(
            mcp_approval_request_id=str(row.get("mcp_approval_request_id") or ""),
            mcp_server_id=str(row.get("mcp_server_id") or ""),
            tool_name=str(row.get("tool_name") or ""),
            scope_type=str(row.get("scope_type") or ""),
            scope_id=str(row.get("scope_id") or ""),
            status=str(row.get("status") or "pending"),
            request_fingerprint=str(row.get("request_fingerprint") or ""),
            requested_by_api_key=str(row.get("requested_by_api_key")) if row.get("requested_by_api_key") is not None else None,
            requested_by_user=str(row.get("requested_by_user")) if row.get("requested_by_user") is not None else None,
            organization_id=str(row.get("organization_id")) if row.get("organization_id") is not None else None,
            request_id=str(row.get("request_id")) if row.get("request_id") is not None else None,
            correlation_id=str(row.get("correlation_id")) if row.get("correlation_id") is not None else None,
            arguments_json=_parse_json_object(row.get("arguments_json")) or None,
            decision_comment=str(row.get("decision_comment")) if row.get("decision_comment") is not None else None,
            decided_by_account_id=str(row.get("decided_by_account_id")) if row.get("decided_by_account_id") is not None else None,
            decided_at=_parse_datetime(row.get("decided_at")),
            expires_at=_parse_datetime(row.get("expires_at")),
            metadata=_parse_json_object(row.get("metadata")) or None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )
