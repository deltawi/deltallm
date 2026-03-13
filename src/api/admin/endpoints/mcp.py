from __future__ import annotations

from dataclasses import asdict
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import AuthScope, emit_admin_mutation_audit, get_auth_scope, optional_int, to_json_value
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.db.mcp import MCPApprovalRequestRecord, MCPRepository, MCPServerBindingRecord, MCPServerRecord, MCPToolPolicyRecord
from src.mcp.capabilities import extract_tool_schemas
from src.mcp.exceptions import MCPError
from src.mcp.health import MCPHealthProbe
from src.mcp.metrics import record_mcp_approval_decision, record_mcp_capability_refresh
from src.mcp.registry import MCPRegistryService, server_record_to_config
from src.mcp.transport_http import StreamableHTTPMCPClient
from src.middleware.admin import require_admin_permission
from src.middleware.platform_auth import get_platform_auth_context

router = APIRouter(tags=["Admin MCP"])

_ALLOWED_AUTH_MODES = {"none", "bearer", "basic", "header_map"}
_ALLOWED_SCOPE_TYPES = {"organization", "team", "api_key"}
_ALLOWED_TRANSPORTS = {"streamable_http"}
_SERVER_KEY_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


def _repository_or_503(request: Request) -> MCPRepository:
    repository = getattr(request.app.state, "mcp_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP repository unavailable")
    return repository


def _registry_or_503(request: Request) -> MCPRegistryService:
    service = getattr(request.app.state, "mcp_registry_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP registry service unavailable")
    return service


def _transport_or_503(request: Request) -> StreamableHTTPMCPClient:
    client = getattr(request.app.state, "mcp_transport_client", None)
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP transport client unavailable")
    return client


def _health_probe_or_503(request: Request) -> MCPHealthProbe:
    probe = getattr(request.app.state, "mcp_health_probe", None)
    if probe is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP health probe unavailable")
    return probe


def _db_or_503(request: Request) -> Any:
    client = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")
    return client


def _normalize_server_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="server_key is required")
    if any(ch not in _SERVER_KEY_ALLOWED for ch in key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="server_key may only contain lowercase letters, numbers, hyphens, and underscores",
        )
    return key


def _normalize_transport(value: Any) -> str:
    transport = str(value or "streamable_http").strip().lower()
    if transport not in _ALLOWED_TRANSPORTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="transport must be streamable_http")
    return transport


def _normalize_auth_mode(value: Any) -> str:
    mode = str(value or "none").strip().lower()
    if mode not in _ALLOWED_AUTH_MODES:
        allowed = ", ".join(sorted(_ALLOWED_AUTH_MODES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"auth_mode must be one of: {allowed}")
    return mode


def _normalize_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be an object")
    return dict(value)


def _normalize_allowlist(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="forwarded_headers_allowlist must be an array")
    out: list[str] = []
    for item in value:
        header = str(item or "").strip().lower()
        if header and header not in out:
            out.append(header)
    return out


def _validate_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="base_url is required")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="base_url must be a valid http or https URL")
    return raw.rstrip("/")


def _validate_auth_config(auth_mode: str, value: Any) -> dict[str, Any]:
    if value is None:
        config: dict[str, Any] = {}
    elif isinstance(value, dict):
        config = dict(value)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth_config must be an object")

    if auth_mode == "none":
        return {}
    if auth_mode == "bearer":
        token = str(config.get("token") or "").strip()
        if not token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth_config.token is required for bearer auth")
        return {"token": token}
    if auth_mode == "basic":
        username = str(config.get("username") or "")
        password = str(config.get("password") or "")
        if not username or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="auth_config.username and auth_config.password are required for basic auth",
            )
        return {"username": username, "password": password}
    headers = config.get("headers")
    if not isinstance(headers, dict) or not headers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth_config.headers is required for header_map auth")
    normalized_headers = {
        str(key).strip(): str(item)
        for key, item in headers.items()
        if str(key).strip() and item is not None
    }
    if not normalized_headers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth_config.headers must include at least one header")
    return {"headers": normalized_headers}


def _validate_scope_type(value: Any) -> str:
    scope_type = str(value or "").strip().lower()
    if scope_type not in _ALLOWED_SCOPE_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_SCOPE_TYPES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"scope_type must be one of: {allowed}")
    return scope_type


def _normalize_scope_id(value: Any, *, field_name: str = "scope_id") -> str:
    scope_id = str(value or "").strip()
    if not scope_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} is required")
    return scope_id


async def _validate_scope_target(request: Request, *, scope_type: str, scope_id: str) -> None:
    db = _db_or_503(request)
    if scope_type == "organization":
        rows = await db.query_raw(
            """
            SELECT organization_id
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            scope_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        return

    if scope_type == "team":
        rows = await db.query_raw(
            """
            SELECT team_id
            FROM deltallm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            scope_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        return

    if scope_type == "api_key":
        rows = await db.query_raw(
            """
            SELECT vt.token, vt.team_id, t.organization_id
            FROM deltallm_verificationtoken vt
            LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
            WHERE vt.token = $1
            LIMIT 1
            """,
            scope_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
        row = rows[0]
        if not str(row.get("team_id") or "").strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API key must belong to a team")
        return


def _normalize_tool_name(value: Any) -> str:
    tool_name = str(value or "").strip()
    if not tool_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tool_name is required")
    return tool_name


def _append_param_list(params: list[Any], values: list[str]) -> str:
    start = len(params) + 1
    params.extend(values)
    return ", ".join(f"${start + index}" for index in range(len(values)))


def _scoped_entity_visibility_clause(alias: str, scope: AuthScope, params: list[Any]) -> str:
    clauses: list[str] = []
    if scope.org_ids:
        org_placeholders = _append_param_list(params, scope.org_ids)
        clauses.append(f"({alias}.scope_type = 'organization' AND {alias}.scope_id IN ({org_placeholders}))")
        clauses.append(
            f"""({alias}.scope_type = 'team' AND EXISTS (
                    SELECT 1 FROM deltallm_teamtable t
                    WHERE t.team_id = {alias}.scope_id
                      AND t.organization_id IN ({org_placeholders})
                ))"""
        )
        clauses.append(
            f"""({alias}.scope_type = 'api_key' AND EXISTS (
                    SELECT 1 FROM deltallm_verificationtoken vt
                    LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
                    WHERE vt.token = {alias}.scope_id
                      AND t.organization_id IN ({org_placeholders})
                ))"""
        )
    if scope.team_ids:
        team_placeholders = _append_param_list(params, scope.team_ids)
        clauses.append(f"({alias}.scope_type = 'team' AND {alias}.scope_id IN ({team_placeholders}))")
        clauses.append(
            f"""({alias}.scope_type = 'api_key' AND EXISTS (
                    SELECT 1 FROM deltallm_verificationtoken vt
                    WHERE vt.token = {alias}.scope_id
                      AND vt.team_id IN ({team_placeholders})
                ))"""
        )
    return " OR ".join(clauses) if clauses else "FALSE"


def _server_visibility_exists_clause(server_alias: str, scope: AuthScope, params: list[Any]) -> str:
    return (
        f"""EXISTS (
                SELECT 1
                FROM deltallm_mcpserverbinding b
                WHERE b.mcp_server_id = {server_alias}.mcp_server_id
                  AND ({_scoped_entity_visibility_clause('b', scope, params)})
            )"""
    )


def _audit_scope_visibility_clause(scope: AuthScope, params: list[Any]) -> str:
    clauses: list[str] = []
    if scope.org_ids:
        org_placeholders = _append_param_list(params, scope.org_ids)
        clauses.append(f"(metadata->>'scope_type' = 'organization' AND metadata->>'scope_id' IN ({org_placeholders}))")
        clauses.append(
            f"""(metadata->>'scope_type' = 'team' AND EXISTS (
                    SELECT 1 FROM deltallm_teamtable t
                    WHERE t.team_id = metadata->>'scope_id'
                      AND t.organization_id IN ({org_placeholders})
                ))"""
        )
        clauses.append(
            f"""(metadata->>'scope_type' = 'api_key' AND EXISTS (
                    SELECT 1 FROM deltallm_verificationtoken vt
                    LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
                    WHERE vt.token = metadata->>'scope_id'
                      AND t.organization_id IN ({org_placeholders})
                ))"""
        )
    if scope.team_ids:
        team_placeholders = _append_param_list(params, scope.team_ids)
        clauses.append(f"(metadata->>'scope_type' = 'team' AND metadata->>'scope_id' IN ({team_placeholders}))")
        clauses.append(
            f"""(metadata->>'scope_type' = 'api_key' AND EXISTS (
                    SELECT 1 FROM deltallm_verificationtoken vt
                    WHERE vt.token = metadata->>'scope_id'
                      AND vt.team_id IN ({team_placeholders})
                ))"""
        )
    return " OR ".join(clauses) if clauses else "FALSE"


def _approval_visibility_clause(scope: AuthScope, params: list[Any]) -> str:
    return _scoped_entity_visibility_clause("r", scope, params)


async def _approval_visible_to_scope(request: Request, scope: AuthScope, approval: MCPApprovalRequestRecord) -> bool:
    if scope.is_platform_admin:
        return True
    if approval.scope_type == "organization":
        return approval.scope_id in scope.org_ids
    if approval.scope_type == "team":
        if approval.scope_id in scope.team_ids:
            return True
        if not scope.org_ids:
            return False
        db = _db_or_503(request)
        rows = await db.query_raw(
            """
            SELECT organization_id
            FROM deltallm_teamtable
            WHERE team_id = $1
            LIMIT 1
            """,
            approval.scope_id,
        )
        organization_id = str((rows[0] if rows else {}).get("organization_id") or "")
        return bool(organization_id and organization_id in scope.org_ids)
    if approval.scope_type == "api_key":
        db = _db_or_503(request)
        rows = await db.query_raw(
            """
            SELECT vt.team_id, t.organization_id
            FROM deltallm_verificationtoken vt
            LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
            WHERE vt.token = $1
            LIMIT 1
            """,
            approval.scope_id,
        )
        row = rows[0] if rows else {}
        team_id = str(row.get("team_id") or "")
        organization_id = str(row.get("organization_id") or "")
        return bool((team_id and team_id in scope.team_ids) or (organization_id and organization_id in scope.org_ids))
    return False


async def _load_approval_request_or_404(request: Request, approval_request_id: str) -> MCPApprovalRequestRecord:
    repository = _repository_or_503(request)
    approval = await repository.get_approval_request(approval_request_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP approval request not found")
    return approval


def _validate_require_approval(value: Any) -> str | None:
    if value is None:
        return None
    approval = str(value).strip().lower()
    if approval not in {"never", "manual"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='require_approval must be "never" or "manual"')
    return approval


def _serialize_server(server: MCPServerRecord) -> dict[str, Any]:
    payload = to_json_value(asdict(server))
    payload["tool_count"] = len(extract_tool_schemas(server.capabilities_json or {}))
    return payload


def _serialize_binding(binding: MCPServerBindingRecord) -> dict[str, Any]:
    return to_json_value(asdict(binding))


def _serialize_policy(policy: MCPToolPolicyRecord) -> dict[str, Any]:
    return to_json_value(asdict(policy))


def _serialize_approval_request(record: MCPApprovalRequestRecord) -> dict[str, Any]:
    return to_json_value(asdict(record))


def _filter_server_tools_for_scope(
    tools: list[Any],
    *,
    bindings: list[MCPServerBindingRecord],
    policies: list[MCPToolPolicyRecord],
    include_all: bool,
) -> list[Any]:
    if include_all:
        return tools
    if not bindings:
        return []

    allowed_names: set[str] = set()
    all_allowed = False
    for binding in bindings:
        if not binding.enabled:
            continue
        allowlist = tuple(binding.tool_allowlist or [])
        if not allowlist:
            all_allowed = True
            break
        allowed_names.update(str(name) for name in allowlist)

    filtered = [tool for tool in tools if all_allowed or str(getattr(tool, "original_name", "")) in allowed_names]
    precedence = {"api_key": 0, "team": 1, "organization": 2}
    effective_policy_by_tool: dict[str, MCPToolPolicyRecord] = {}
    for policy in sorted(policies, key=lambda item: precedence.get(item.scope_type, 999)):
        effective_policy_by_tool.setdefault(policy.tool_name, policy)
    visible_tools: list[Any] = []
    for tool in filtered:
        policy = effective_policy_by_tool.get(str(getattr(tool, "original_name", "")))
        if policy is not None and not policy.enabled:
            continue
        visible_tools.append(tool)
    return visible_tools


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


async def _list_scoped_bindings(
    request: Request,
    *,
    scope: AuthScope,
    server_id: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[MCPServerBindingRecord], int]:
    repository = _repository_or_503(request)
    if scope.is_platform_admin:
        return await repository.list_bindings(
            server_id=server_id,
            scope_type=scope_type,
            scope_id=scope_id,
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
    clauses.append(f"({_scoped_entity_visibility_clause('b', scope, params)})")
    where_sql = f" WHERE {' AND '.join(clauses)}"
    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcpserverbinding b {where_sql}",
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
        FROM deltallm_mcpserverbinding b
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
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[MCPToolPolicyRecord], int]:
    repository = _repository_or_503(request)
    if scope.is_platform_admin:
        return await repository.list_tool_policies(
            server_id=server_id,
            scope_type=scope_type,
            scope_id=scope_id,
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


async def _capability_refresh(
    request: Request,
    server: MCPServerRecord,
) -> tuple[MCPServerRecord, list[dict[str, Any]]]:
    started = perf_counter()
    registry = _registry_or_503(request)
    transport = _transport_or_503(request)
    config = server_record_to_config(server)
    try:
        await transport.initialize(config)
        tools = await transport.list_tools(config)
    except Exception:
        record_mcp_capability_refresh(
            server_key=server.server_key,
            success=False,
            latency_ms=int((perf_counter() - started) * 1000),
        )
        raise
    capabilities = {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "annotations": tool.annotations,
            }
            for tool in tools
        ]
    }
    updated = await registry.store_server_capabilities(server.mcp_server_id, capabilities=capabilities)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    record_mcp_capability_refresh(
        server_key=server.server_key,
        success=True,
        latency_ms=int((perf_counter() - started) * 1000),
    )
    namespaced = await registry.list_namespaced_tools(updated)
    return updated, [to_json_value(asdict(item)) for item in namespaced]


def _request_timeout_ms(payload: dict[str, Any], *, default: int) -> int:
    timeout_ms = optional_int(payload.get("request_timeout_ms"), "request_timeout_ms")
    value = timeout_ms if timeout_ms is not None else default
    if value <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="request_timeout_ms must be greater than 0")
    return value


@router.get("/ui/api/mcp-servers", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def list_mcp_servers(
    request: Request,
    search: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    if scope.is_platform_admin:
        servers, total = await registry.list_servers(search=search, enabled=enabled, limit=limit, offset=offset)
        return {
            "data": [_serialize_server(server) for server in servers],
            "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
        }

    if not scope.org_ids and not scope.team_ids:
        return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    db = _db_or_503(request)
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
    clauses.append(_server_visibility_exists_clause("s", scope, params))
    where_sql = f" WHERE {' AND '.join(clauses)}"

    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcpserver s {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    page_params = [*params, limit, offset]
    rows = await db.query_raw(
        f"""
        SELECT
            s.mcp_server_id,
            s.server_key,
            s.name,
            s.description,
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
    servers = [MCPRepository._to_server_record(row) for row in rows]
    return {
        "data": [_serialize_server(server) for server in servers],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/mcp-servers", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def create_mcp_server(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key)

    server_key = _normalize_server_key(payload.get("server_key"))
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    auth_mode = _normalize_auth_mode(payload.get("auth_mode"))
    existing = await repository.get_server_by_key(server_key)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An MCP server with this server_key already exists")

    created_by_account_id = getattr(get_platform_auth_context(request), "account_id", None)
    created = await repository.create_server(
        server_key=server_key,
        name=name,
        description=str(payload.get("description")).strip() if payload.get("description") is not None else None,
        transport=_normalize_transport(payload.get("transport")),
        base_url=_validate_url(payload.get("base_url")),
        enabled=bool(payload.get("enabled", True)),
        auth_mode=auth_mode,
        auth_config=_validate_auth_config(auth_mode, payload.get("auth_config")),
        forwarded_headers_allowlist=_normalize_allowlist(payload.get("forwarded_headers_allowlist")),
        request_timeout_ms=_request_timeout_ms(payload, default=30000),
        metadata=_normalize_metadata(payload.get("metadata")),
        created_by_account_id=created_by_account_id,
    )
    response = _serialize_server(created)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_CREATE,
        scope=scope,
        resource_type="mcp_server",
        resource_id=created.mcp_server_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.get("/ui/api/mcp-servers/{server_id}", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def get_mcp_server(
    request: Request,
    server_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    server = await _load_server_or_404(request, server_id)
    if not await _server_visible_to_scope(request, scope, server_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    bindings, _ = await _list_scoped_bindings(request, scope=scope, server_id=server_id, limit=200, offset=0)
    policies, _ = await _list_scoped_tool_policies(request, scope=scope, server_id=server_id, limit=200, offset=0)
    tools = _filter_server_tools_for_scope(
        await registry.list_namespaced_tools(server),
        bindings=bindings,
        policies=policies,
        include_all=scope.is_platform_admin,
    )
    return {
        "server": _serialize_server(server),
        "tools": [to_json_value(asdict(item)) for item in tools],
        "bindings": [_serialize_binding(binding) for binding in bindings],
        "tool_policies": [_serialize_policy(policy) for policy in policies],
    }


@router.get("/ui/api/mcp-servers/{server_id}/operations", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def get_mcp_server_operations(
    request: Request,
    server_id: str,
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    top_tools_limit: int = Query(default=5, ge=1, le=20),
    failures_limit: int = Query(default=5, ge=1, le=20),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    db = _db_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    server = await _load_server_or_404(request, server_id)
    if not await _server_visible_to_scope(request, scope, server_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    params: list[Any] = [AuditAction.MCP_TOOL_CALL.value, server.server_key, window_hours]
    scope_sql = ""
    approval_scope_sql = ""
    approval_params: list[Any] = [server.mcp_server_id, window_hours]
    if not scope.is_platform_admin:
        scope_sql = f" AND ({_audit_scope_visibility_clause(scope, params)})"
        approval_scope_sql = f" AND ({_approval_visibility_clause(scope, approval_params)})"

    summary_rows = await db.query_raw(
        f"""
        SELECT
            COUNT(*)::int AS total_calls,
            COUNT(*) FILTER (WHERE status = 'error')::int AS failed_calls,
            COALESCE(AVG(latency_ms), 0)::float8 AS avg_latency_ms
        FROM deltallm_auditevent
        WHERE action = $1
          AND resource_type = 'mcp_tool'
          AND metadata->>'server_key' = $2
          AND occurred_at >= NOW() - ($3::int * INTERVAL '1 hour')
          {scope_sql}
        """,
        *params,
    )
    summary_row = summary_rows[0] if summary_rows else {}
    total_calls = int(summary_row.get("total_calls") or 0)
    failed_calls = int(summary_row.get("failed_calls") or 0)
    avg_latency_ms = float(summary_row.get("avg_latency_ms") or 0.0)

    tool_params = [*params, top_tools_limit]
    tool_rows = await db.query_raw(
        f"""
        SELECT
            resource_id AS tool_name,
            COUNT(*)::int AS total_calls,
            COUNT(*) FILTER (WHERE status = 'error')::int AS failed_calls,
            COALESCE(AVG(latency_ms), 0)::float8 AS avg_latency_ms
        FROM deltallm_auditevent
        WHERE action = $1
          AND resource_type = 'mcp_tool'
          AND metadata->>'server_key' = $2
          AND occurred_at >= NOW() - ($3::int * INTERVAL '1 hour')
          {scope_sql}
        GROUP BY resource_id
        ORDER BY total_calls DESC, resource_id ASC
        LIMIT ${len(tool_params)}
        """,
        *tool_params,
    )

    failure_params = [*params, failures_limit]
    failure_rows = await db.query_raw(
        f"""
        SELECT
            event_id,
            occurred_at,
            resource_id AS tool_name,
            error_type,
            error_code,
            latency_ms,
            request_id
        FROM deltallm_auditevent
        WHERE action = $1
          AND resource_type = 'mcp_tool'
          AND metadata->>'server_key' = $2
          AND status = 'error'
          AND occurred_at >= NOW() - ($3::int * INTERVAL '1 hour')
          {scope_sql}
        ORDER BY occurred_at DESC
        LIMIT ${len(failure_params)}
        """,
        *failure_params,
    )

    approval_rows = await db.query_raw(
        f"""
        SELECT
            COUNT(*)::int AS total_requests,
            COUNT(*) FILTER (WHERE status = 'pending')::int AS pending_requests,
            COUNT(*) FILTER (WHERE status = 'approved')::int AS approved_requests,
            COUNT(*) FILTER (WHERE status = 'rejected')::int AS rejected_requests
        FROM deltallm_mcpapprovalrequest
        WHERE mcp_server_id = $1
          AND created_at >= NOW() - ($2::int * INTERVAL '1 hour')
          {approval_scope_sql}
        """,
        *approval_params,
    )
    approval_row = approval_rows[0] if approval_rows else {}

    return {
        "window_hours": window_hours,
        "summary": {
            "total_calls": total_calls,
            "failed_calls": failed_calls,
            "success_calls": max(total_calls - failed_calls, 0),
            "failure_rate": (failed_calls / total_calls) if total_calls else 0.0,
            "avg_latency_ms": avg_latency_ms,
            "approval_requests": int(approval_row.get("total_requests") or 0),
            "pending_approvals": int(approval_row.get("pending_requests") or 0),
            "approved_approvals": int(approval_row.get("approved_requests") or 0),
            "rejected_approvals": int(approval_row.get("rejected_requests") or 0),
        },
        "top_tools": [to_json_value(dict(row)) for row in tool_rows],
        "recent_failures": [to_json_value(dict(row)) for row in failure_rows],
    }


@router.patch("/ui/api/mcp-servers/{server_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def update_mcp_server(
    request: Request,
    server_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key)

    existing = await _load_server_or_404(request, server_id)
    auth_mode = _normalize_auth_mode(payload.get("auth_mode", existing.auth_mode))
    updated = await repository.update_server(
        server_id,
        name=str(payload.get("name", existing.name) or "").strip() or existing.name,
        description=str(payload.get("description")).strip() if payload.get("description") is not None else existing.description,
        transport=_normalize_transport(payload.get("transport", existing.transport)),
        base_url=_validate_url(payload.get("base_url", existing.base_url)),
        enabled=bool(payload.get("enabled", existing.enabled)),
        auth_mode=auth_mode,
        auth_config=_validate_auth_config(auth_mode, payload.get("auth_config", existing.auth_config or {})),
        forwarded_headers_allowlist=_normalize_allowlist(
            payload.get("forwarded_headers_allowlist", existing.forwarded_headers_allowlist or [])
        ),
        request_timeout_ms=_request_timeout_ms(payload, default=existing.request_timeout_ms),
        metadata=_normalize_metadata(payload.get("metadata", existing.metadata)) or None,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    await registry.invalidate_server(updated.server_key)
    response = _serialize_server(updated)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_UPDATE,
        scope=scope,
        resource_type="mcp_server",
        resource_id=updated.mcp_server_id,
        request_payload=payload,
        response_payload=response,
        before=_serialize_server(existing),
        after=response,
    )
    return response


@router.delete("/ui/api/mcp-servers/{server_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def delete_mcp_server(
    request: Request,
    server_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key)

    server = await _load_server_or_404(request, server_id)
    deleted = await repository.delete_server(server_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    await registry.invalidate_server(server.server_key)
    response = {"deleted": True, "mcp_server_id": server_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_DELETE,
        scope=scope,
        resource_type="mcp_server",
        resource_id=server_id,
        response_payload=response,
        before=_serialize_server(server),
    )
    return response


@router.post("/ui/api/mcp-servers/{server_id}/refresh-capabilities", dependencies=[Depends(require_admin_permission(Permission.KEY_UPDATE))])
async def refresh_mcp_server_capabilities(
    request: Request,
    server_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    server = await _load_server_or_404(request, server_id)
    if not await _server_visible_to_scope(request, scope, server_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    try:
        updated, tools = await _capability_refresh(request, server)
    except MCPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    response = {"server": _serialize_server(updated), "tools": tools}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_REFRESH_CAPABILITIES,
        scope=scope,
        resource_type="mcp_server",
        resource_id=server_id,
        response_payload=response,
    )
    return response


@router.post("/ui/api/mcp-servers/{server_id}/health-check", dependencies=[Depends(require_admin_permission(Permission.KEY_UPDATE))])
async def health_check_mcp_server(
    request: Request,
    server_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    server = await _load_server_or_404(request, server_id)
    if not await _server_visible_to_scope(request, scope, server_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    probe = _health_probe_or_503(request)
    try:
        result = await probe.check_server(server)
    except MCPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    refreshed = await _load_server_or_404(request, server_id)
    response = {
        "server": _serialize_server(refreshed),
        "health": {"status": result.status, "latency_ms": result.latency_ms, "error": result.error},
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_HEALTH_CHECK,
        scope=scope,
        resource_type="mcp_server",
        resource_id=server_id,
        response_payload=response,
    )
    return response


@router.get("/ui/api/mcp-bindings", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def list_mcp_bindings(
    request: Request,
    server_id: str | None = Query(default=None),
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    normalized_scope_type = _validate_scope_type(scope_type) if scope_type is not None else None
    if scope.is_platform_admin:
        bindings, total = await repository.list_bindings(
            server_id=server_id,
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            limit=limit,
            offset=offset,
        )
        return {
            "data": [_serialize_binding(binding) for binding in bindings],
            "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
        }

    if not scope.org_ids and not scope.team_ids:
        return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    db = _db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []
    if server_id:
        params.append(server_id)
        clauses.append(f"b.mcp_server_id = ${len(params)}")
    if normalized_scope_type:
        params.append(normalized_scope_type)
        clauses.append(f"b.scope_type = ${len(params)}")
    if scope_id:
        params.append(scope_id)
        clauses.append(f"b.scope_id = ${len(params)}")
    clauses.append(f"({_scoped_entity_visibility_clause('b', scope, params)})")
    where_sql = f" WHERE {' AND '.join(clauses)}"

    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcpserverbinding b {where_sql}",
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
        FROM deltallm_mcpserverbinding b
        {where_sql}
        ORDER BY b.created_at DESC, b.scope_type ASC, b.scope_id ASC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )
    bindings = [MCPRepository._to_binding_record(row) for row in rows]
    return {
        "data": [_serialize_binding(binding) for binding in bindings],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/mcp-bindings", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def upsert_mcp_binding(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key)
    server = await _load_server_or_404(request, _normalize_scope_id(payload.get("server_id"), field_name="server_id"))
    scope_type = _validate_scope_type(payload.get("scope_type"))
    scope_id = _normalize_scope_id(payload.get("scope_id"))
    await _validate_scope_target(request, scope_type=scope_type, scope_id=scope_id)
    binding = await repository.upsert_binding(
        server_id=server.mcp_server_id,
        scope_type=scope_type,
        scope_id=scope_id,
        enabled=bool(payload.get("enabled", True)),
        tool_allowlist=_normalize_allowlist(payload.get("tool_allowlist")),
        metadata=_normalize_metadata(payload.get("metadata")),
    )
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    await registry.invalidate_all()
    response = _serialize_binding(binding)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_BINDING_UPSERT,
        scope=scope,
        resource_type="mcp_binding",
        resource_id=binding.mcp_binding_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/mcp-bindings/{binding_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def delete_mcp_binding(
    request: Request,
    binding_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key)
    deleted = await repository.delete_binding(binding_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP binding not found")
    await registry.invalidate_all()
    response = {"deleted": True, "mcp_binding_id": binding_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_BINDING_DELETE,
        scope=scope,
        resource_type="mcp_binding",
        resource_id=binding_id,
        response_payload=response,
    )
    return response


@router.get("/ui/api/mcp-tool-policies", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def list_mcp_tool_policies(
    request: Request,
    server_id: str | None = Query(default=None),
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    normalized_scope_type = _validate_scope_type(scope_type) if scope_type is not None else None
    if scope.is_platform_admin:
        policies, total = await repository.list_tool_policies(
            server_id=server_id,
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            limit=limit,
            offset=offset,
        )
        return {
            "data": [_serialize_policy(policy) for policy in policies],
            "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
        }

    if not scope.org_ids and not scope.team_ids:
        return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    db = _db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []
    if server_id:
        params.append(server_id)
        clauses.append(f"p.mcp_server_id = ${len(params)}")
    if normalized_scope_type:
        params.append(normalized_scope_type)
        clauses.append(f"p.scope_type = ${len(params)}")
    if scope_id:
        params.append(scope_id)
        clauses.append(f"p.scope_id = ${len(params)}")
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
    policies = [MCPRepository._to_tool_policy_record(row) for row in rows]
    return {
        "data": [_serialize_policy(policy) for policy in policies],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.get("/ui/api/mcp-approval-requests", dependencies=[Depends(require_admin_permission(Permission.KEY_UPDATE))])
async def list_mcp_approval_requests(
    request: Request,
    server_id: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    if status_value is not None and status_value not in {"pending", "approved", "rejected"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status must be pending, approved, or rejected")
    if scope.is_platform_admin:
        approvals, total = await repository.list_approval_requests(
            server_id=server_id,
            status=status_value,
            limit=limit,
            offset=offset,
        )
        return {
            "data": [_serialize_approval_request(item) for item in approvals],
            "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
        }

    if not scope.org_ids and not scope.team_ids:
        return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    db = _db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []
    if server_id:
        params.append(server_id)
        clauses.append(f"r.mcp_server_id = ${len(params)}")
    if status_value:
        params.append(status_value)
        clauses.append(f"r.status = ${len(params)}")
    clauses.append(f"({_approval_visibility_clause(scope, params)})")
    where_sql = f" WHERE {' AND '.join(clauses)}"

    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcpapprovalrequest r {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    page_params = [*params, limit, offset]
    rows = await db.query_raw(
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
    approvals = [MCPRepository._to_approval_request_record(row) for row in rows]
    return {
        "data": [_serialize_approval_request(item) for item in approvals],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/mcp-tool-policies", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def upsert_mcp_tool_policy(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key)
    server = await _load_server_or_404(request, _normalize_scope_id(payload.get("server_id"), field_name="server_id"))
    scope_type = _validate_scope_type(payload.get("scope_type"))
    scope_id = _normalize_scope_id(payload.get("scope_id"))
    await _validate_scope_target(request, scope_type=scope_type, scope_id=scope_id)
    policy = await repository.upsert_tool_policy(
        server_id=server.mcp_server_id,
        tool_name=_normalize_tool_name(payload.get("tool_name")),
        scope_type=scope_type,
        scope_id=scope_id,
        enabled=bool(payload.get("enabled", True)),
        require_approval=_validate_require_approval(payload.get("require_approval")),
        max_rpm=optional_int(payload.get("max_rpm"), "max_rpm"),
        max_concurrency=optional_int(payload.get("max_concurrency"), "max_concurrency"),
        result_cache_ttl_seconds=optional_int(payload.get("result_cache_ttl_seconds"), "result_cache_ttl_seconds"),
        metadata=_normalize_metadata(payload.get("metadata")),
    )
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    await registry.invalidate_all()
    response = _serialize_policy(policy)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_TOOL_POLICY_UPSERT,
        scope=scope,
        resource_type="mcp_tool_policy",
        resource_id=policy.mcp_tool_policy_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.post("/ui/api/mcp-approval-requests/{approval_request_id}/decision", dependencies=[Depends(require_admin_permission(Permission.KEY_UPDATE))])
async def decide_mcp_approval_request(
    request: Request,
    approval_request_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    decision = str(payload.get("status") or "").strip().lower()
    if decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status must be approved or rejected")
    existing = await _load_approval_request_or_404(request, approval_request_id)
    if not await _approval_visible_to_scope(request, scope, existing):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    context = get_platform_auth_context(request)
    decided = await repository.decide_approval_request(
        approval_request_id,
        status=decision,
        decided_by_account_id=getattr(context, "account_id", None),
        decision_comment=str(payload.get("decision_comment")).strip() if payload.get("decision_comment") is not None else None,
    )
    if decided is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending MCP approval request not found")
    record_mcp_approval_decision(status=decision)
    response = _serialize_approval_request(decided)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_APPROVAL_DECIDE,
        scope=scope,
        resource_type="mcp_approval_request",
        resource_id=approval_request_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/mcp-tool-policies/{policy_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def delete_mcp_tool_policy(
    request: Request,
    policy_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key)
    deleted = await repository.delete_tool_policy(policy_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP tool policy not found")
    await registry.invalidate_all()
    response = {"deleted": True, "mcp_tool_policy_id": policy_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_TOOL_POLICY_DELETE,
        scope=scope,
        resource_type="mcp_tool_policy",
        resource_id=policy_id,
        response_payload=response,
    )
    return response


__all__ = ["router"]
