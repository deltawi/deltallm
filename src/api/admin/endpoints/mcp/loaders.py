"""Data loaders for the admin MCP endpoints.

Thin wrappers around :class:`MCPRepository` plus scope-aware raw SQL for
listing bindings / tool policies when the caller is not a platform admin.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from src.api.admin.endpoints.common import AuthScope
from src.db.mcp import (
    MCPApprovalRequestRecord,
    MCPRepository,
    MCPServerBindingRecord,
    MCPServerRecord,
    MCPToolPolicyRecord,
)

from src.api.admin.endpoints.mcp.dependencies import _db_or_503, _repository_or_503
from src.api.admin.endpoints.mcp.sql_visibility import (
    _scoped_entity_visibility_clause,
    _server_visibility_exists_clause,
)


async def _load_server_or_404(request: Request, server_id: str) -> MCPServerRecord:
    repository = _repository_or_503(request)
    server = await repository.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    return server


async def _server_visible_to_scope(request: Request, scope: AuthScope, server_id: str) -> bool:
    if scope.is_platform_admin:
        return True
    if not scope.org_ids and not scope.team_ids:
        return False
    db = _db_or_503(request)
    params: list[Any] = [server_id]
    exists_rows = await db.query_raw(
        f"""
        SELECT EXISTS(
            SELECT 1
            FROM deltallm_mcpserver s
            WHERE s.mcp_server_id = $1
              AND {_server_visibility_exists_clause('s', scope, params)}
        ) AS visible
        """,
        *params,
    )
    return bool((exists_rows[0] if exists_rows else {}).get("visible"))


async def _load_approval_request_or_404(request: Request, approval_request_id: str) -> MCPApprovalRequestRecord:
    repository = _repository_or_503(request)
    approval = await repository.get_approval_request(approval_request_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP approval request not found")
    return approval


async def _load_server_summary_map(request: Request, server_ids: list[str]) -> dict[str, MCPServerRecord]:
    if not server_ids:
        return {}
    repository = _repository_or_503(request)
    prisma = getattr(repository, "prisma", None)
    unique_ids = list(dict.fromkeys(server_ids))
    if prisma is None:
        out: dict[str, MCPServerRecord] = {}
        for server_id in unique_ids:
            server = await repository.get_server(server_id)
            if server is not None:
                out[server_id] = server
        return out

    placeholders: list[str] = []
    params: list[Any] = []
    for server_id in unique_ids:
        params.append(server_id)
        placeholders.append(f"${len(params)}")
    rows = await prisma.query_raw(
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
        WHERE s.mcp_server_id IN ({", ".join(placeholders)})
        """,
        *params,
    )
    return {
        record.mcp_server_id: record
        for record in (MCPRepository._to_server_record(row) for row in rows)
    }


async def _list_scoped_bindings(
    request: Request,
    *,
    scope: AuthScope,
    server_id: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    enabled_only: bool = True,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[MCPServerBindingRecord], int]:
    repository = _repository_or_503(request)
    if scope.is_platform_admin:
        return await repository.list_bindings(
            server_id=server_id,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=True if enabled_only else None,
            limit=limit,
            offset=offset,
        )
    if not scope.org_ids and not scope.team_ids:
        return [], 0
    db = _db_or_503(request)
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
    if enabled_only:
        clauses.append("b.enabled = true")
    clauses.append(f"({_scoped_entity_visibility_clause('b', scope, params)})")
    where_sql = f" WHERE {' AND '.join(clauses)}"
    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcpbinding b {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)
    page_params = [*params, limit, offset]
    rows = await db.query_raw(
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
    return [MCPRepository._to_binding_record(row) for row in rows], total


async def _list_scoped_tool_policies(
    request: Request,
    *,
    scope: AuthScope,
    server_id: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    enabled_only: bool = True,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[MCPToolPolicyRecord], int]:
    repository = _repository_or_503(request)
    if scope.is_platform_admin:
        return await repository.list_tool_policies(
            server_id=server_id,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=True if enabled_only else None,
            limit=limit,
            offset=offset,
        )
    if not scope.org_ids and not scope.team_ids:
        return [], 0
    db = _db_or_503(request)
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
    if enabled_only:
        clauses.append("p.enabled = true")
    clauses.append(f"({_scoped_entity_visibility_clause('p', scope, params)})")
    where_sql = f" WHERE {' AND '.join(clauses)}"
    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcptoolpolicy p {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)
    page_params = [*params, limit, offset]
    rows = await db.query_raw(
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
        ORDER BY p.created_at DESC, p.tool_name ASC, p.scope_type ASC, p.scope_id ASC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )
    return [MCPRepository._to_tool_policy_record(row) for row in rows], total


__all__ = [
    "_load_server_or_404",
    "_server_visible_to_scope",
    "_load_approval_request_or_404",
    "_load_server_summary_map",
    "_list_scoped_bindings",
    "_list_scoped_tool_policies",
]
