from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from src.audit.actions import AuditAction
from src.middleware.auth import authenticate_request, require_api_key
from src.middleware.rate_limit import enforce_rate_limits
from src.mcp.capabilities import parse_namespaced_tool_name
from src.mcp import (
    MCPAccessDeniedError,
    MCPApprovalDeniedError,
    MCPApprovalRequiredError,
    MCPAuthError,
    MCPError,
    MCPGatewayService,
    MCPInvalidResponseError,
    MCPPolicyDeniedError,
    MCPRateLimitError,
    MCPToolNotFoundError,
    MCPToolTimeoutError,
    MCPTransportError,
)
from src.routers.audit_helpers import emit_audit_event

router = APIRouter(tags=["mcp"])


def _jsonrpc_success(request_id: str | int | None, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=200, content={"jsonrpc": "2.0", "id": request_id, "result": result})


def _jsonrpc_error(
    request_id: str | int | None,
    *,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return JSONResponse(
        status_code=200,
        content={"jsonrpc": "2.0", "id": request_id, "error": error},
    )


def _gateway_or_503(request: Request) -> MCPGatewayService:
    service = getattr(request.app.state, "mcp_gateway_service", None)
    if service is None:
        raise RuntimeError("MCP gateway service unavailable")
    return service


def _map_mcp_error(request_id: str | int | None, exc: Exception) -> JSONResponse:
    if isinstance(exc, MCPToolNotFoundError):
        return _jsonrpc_error(request_id, code=-32004, message=str(exc))
    if isinstance(exc, (MCPAccessDeniedError, MCPPolicyDeniedError)):
        return _jsonrpc_error(request_id, code=-32003, message=str(exc))
    if isinstance(exc, MCPApprovalDeniedError):
        return _jsonrpc_error(
            request_id,
            code=-32009,
            message=str(exc),
            data={"approval_request_id": exc.approval_request_id} if exc.approval_request_id else None,
        )
    if isinstance(exc, MCPApprovalRequiredError):
        return _jsonrpc_error(
            request_id,
            code=-32008,
            message=str(exc),
            data={"approval_request_id": exc.approval_request_id} if exc.approval_request_id else None,
        )
    if isinstance(exc, MCPRateLimitError):
        return _jsonrpc_error(request_id, code=-32029, message=str(exc))
    if isinstance(exc, MCPToolTimeoutError):
        return _jsonrpc_error(request_id, code=-32030, message=str(exc))
    if isinstance(exc, MCPAuthError):
        return _jsonrpc_error(request_id, code=-32001, message=str(exc))
    if isinstance(exc, (MCPTransportError, MCPInvalidResponseError)):
        return _jsonrpc_error(request_id, code=-32005, message=str(exc))
    if isinstance(exc, ValueError):
        return _jsonrpc_error(request_id, code=-32602, message=str(exc))
    return _jsonrpc_error(request_id, code=-32000, message="Internal MCP gateway error")


def _initialize_result() -> dict[str, Any]:
    return {
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "DeltaLLM MCP Gateway", "version": "0.1.0"},
    }


@router.post("/mcp", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def mcp_gateway(request: Request):
    request_start = perf_counter()
    auth = await authenticate_request(request)
    request_id: str | int | None = None

    try:
        payload = await request.json()
    except ValueError:
        return _jsonrpc_error(None, code=-32700, message="Parse error")

    if not isinstance(payload, dict):
        return _jsonrpc_error(None, code=-32600, message="Invalid request")

    method = str(payload.get("method") or "").strip()
    params = payload.get("params")
    request_id = payload.get("id")

    if not method:
        return _jsonrpc_error(request_id, code=-32600, message="Invalid request")

    if method.startswith("notifications/"):
        return Response(status_code=202)

    gateway = _gateway_or_503(request)

    try:
        if method == "initialize":
            return _jsonrpc_success(request_id, _initialize_result())
        if method == "ping":
            return _jsonrpc_success(request_id, {})
        if method == "tools/list":
            tools = await gateway.list_visible_tools(auth)
            response = {
                "tools": [
                    {
                        "name": tool.namespaced_name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                    }
                    for tool in tools
                ]
            }
            emit_audit_event(
                request=request,
                request_start=request_start,
                action=AuditAction.MCP_TOOLS_LIST,
                status="success",
                actor_type="api_key",
                actor_id=auth.user_id or auth.api_key,
                organization_id=auth.organization_id,
                api_key=auth.api_key,
                resource_type="mcp_gateway",
                resource_id="tools/list",
                response_payload={"tool_count": len(response["tools"])},
                critical=False,
            )
            return _jsonrpc_success(request_id, response)
        if method == "tools/call":
            if not isinstance(params, dict):
                raise ValueError("tools/call params must be an object")
            tool_name = str(params.get("name") or "").strip()
            if not tool_name:
                raise ValueError("tools/call params.name is required")
            arguments = params.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                raise ValueError("tools/call params.arguments must be an object")
            result = await gateway.call_tool(
                auth,
                namespaced_tool_name=tool_name,
                arguments=arguments if isinstance(arguments, dict) else {},
                request_headers=dict(request.headers),
                request_id=request.headers.get("x-request-id"),
                correlation_id=request.headers.get("x-request-id"),
            )
            emit_audit_event(
                request=request,
                request_start=request_start,
                action=AuditAction.MCP_TOOL_CALL,
                status="success" if not result.is_error else "error",
                actor_type="api_key",
                actor_id=auth.user_id or auth.api_key,
                organization_id=auth.organization_id,
                api_key=auth.api_key,
                resource_type="mcp_tool",
                resource_id=tool_name,
                request_payload={"name": tool_name},
                response_payload={"is_error": result.is_error},
                metadata={
                    "server_key": str(result.metadata.get("server_key") or parse_namespaced_tool_name(tool_name)[0]),
                    "tool_name": str(result.metadata.get("tool_name") or tool_name),
                    "scope_type": result.metadata.get("scope_type"),
                    "scope_id": result.metadata.get("scope_id"),
                },
                critical=True,
            )
            return _jsonrpc_success(
                request_id,
                {
                    "content": result.content,
                    "structuredContent": result.structured_content,
                    "isError": result.is_error,
                },
            )
        return _jsonrpc_error(request_id, code=-32601, message=f"Method '{method}' is not supported")
    except MCPError as exc:
        if method == "tools/call":
            tool_name = str((params or {}).get("name") or "")
            scope = await gateway.resolve_tool_scope(auth, namespaced_tool_name=tool_name)
            try:
                server_key = parse_namespaced_tool_name(tool_name)[0]
            except MCPToolNotFoundError:
                server_key = ""
            emit_audit_event(
                request=request,
                request_start=request_start,
                action=AuditAction.MCP_TOOL_CALL,
                status="error",
                actor_type="api_key",
                actor_id=auth.user_id or auth.api_key,
                organization_id=auth.organization_id,
                api_key=auth.api_key,
                resource_type="mcp_tool",
                resource_id=tool_name,
                error=exc,
                metadata={
                    "server_key": server_key,
                    "tool_name": tool_name,
                    "scope_type": scope.scope_type if scope is not None else None,
                    "scope_id": scope.scope_id if scope is not None else None,
                },
                critical=True,
            )
        elif method == "tools/list":
            emit_audit_event(
                request=request,
                request_start=request_start,
                action=AuditAction.MCP_TOOLS_LIST,
                status="error",
                actor_type="api_key",
                actor_id=auth.user_id or auth.api_key,
                organization_id=auth.organization_id,
                api_key=auth.api_key,
                resource_type="mcp_gateway",
                resource_id="tools/list",
                error=exc,
                critical=False,
            )
        return _map_mcp_error(request_id, exc)
    except Exception as exc:
        if method == "tools/call":
            emit_audit_event(
                request=request,
                request_start=request_start,
                action=AuditAction.MCP_TOOL_CALL,
                status="error",
                actor_type="api_key",
                actor_id=auth.user_id or auth.api_key,
                organization_id=auth.organization_id,
                api_key=auth.api_key,
                resource_type="mcp_tool",
                resource_id=str((params or {}).get("name") or ""),
                error=exc,
                critical=True,
            )
        elif method == "tools/list":
            emit_audit_event(
                request=request,
                request_start=request_start,
                action=AuditAction.MCP_TOOLS_LIST,
                status="error",
                actor_type="api_key",
                actor_id=auth.user_id or auth.api_key,
                organization_id=auth.organization_id,
                api_key=auth.api_key,
                resource_type="mcp_gateway",
                resource_id="tools/list",
                error=exc,
                critical=False,
            )
        return _map_mcp_error(request_id, exc)
