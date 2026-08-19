from __future__ import annotations

from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from src.audit.actions import AuditAction
from src.middleware.auth import authenticate_request, require_api_key
from src.middleware.rate_limit import check_and_acquire_rate_limits_for_payload
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
from src.services.audit_service import require_audit_service

router = APIRouter(tags=["mcp"])


def _tool_audit_event_id(operation_id: str, phase: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"deltallm:mcp-tool:{operation_id}:{phase}"))


def _jsonrpc_success(request_id: str | int | None, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=200, content={"jsonrpc": "2.0", "id": request_id, "result": result}
    )


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
            data={"approval_request_id": exc.approval_request_id}
            if exc.approval_request_id
            else None,
        )
    if isinstance(exc, MCPApprovalRequiredError):
        return _jsonrpc_error(
            request_id,
            code=-32008,
            message=str(exc),
            data={"approval_request_id": exc.approval_request_id}
            if exc.approval_request_id
            else None,
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


@router.post("/mcp", dependencies=[Depends(require_api_key)])
async def mcp_gateway(request: Request):
    request_start = perf_counter()
    auth = await authenticate_request(request)
    request_id: str | int | None = None
    tool_operation_id: str | None = None

    try:
        payload = await request.json()
    except ValueError:
        return _jsonrpc_error(None, code=-32700, message="Parse error")

    if not isinstance(payload, dict):
        return _jsonrpc_error(None, code=-32600, message="Invalid request")

    method = str(payload.get("method") or "").strip()
    params = payload.get("params")
    tool_params = params if isinstance(params, dict) else {}
    request_id = payload.get("id")

    if not method:
        return _jsonrpc_error(request_id, code=-32600, message="Invalid request")

    await check_and_acquire_rate_limits_for_payload(
        request,
        model=None,
        payload=payload,
    )

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
            await emit_audit_event(
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
            tool_operation_id = str(uuid4())
            try:
                server_key = parse_namespaced_tool_name(tool_name)[0]
            except MCPToolNotFoundError:
                server_key = ""
            require_audit_service(getattr(request.app.state, "audit_service", None))
            await emit_audit_event(
                request=request,
                request_start=request_start,
                action=AuditAction.MCP_TOOL_CALL,
                status="attempted",
                actor_type="api_key",
                actor_id=auth.user_id or auth.api_key,
                organization_id=auth.organization_id,
                api_key=auth.api_key,
                resource_type="mcp_tool",
                resource_id=tool_name,
                request_payload={"name": tool_name},
                metadata={
                    "operation_id": tool_operation_id,
                    "phase": "attempt",
                    "server_key": server_key,
                    "tool_name": tool_name,
                },
                critical=True,
                event_id=_tool_audit_event_id(tool_operation_id, "attempt"),
            )
            result = await gateway.call_tool(
                auth,
                namespaced_tool_name=tool_name,
                arguments=arguments if isinstance(arguments, dict) else {},
                request_headers=dict(request.headers),
                request_id=request.headers.get("x-request-id"),
                correlation_id=request.headers.get("x-request-id"),
            )
            await emit_audit_event(
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
                    "operation_id": tool_operation_id,
                    "phase": "outcome",
                    "server_key": str(result.metadata.get("server_key") or server_key),
                    "tool_name": str(result.metadata.get("tool_name") or tool_name),
                    "scope_type": result.metadata.get("scope_type"),
                    "scope_id": result.metadata.get("scope_id"),
                },
                critical=False,
                event_id=_tool_audit_event_id(tool_operation_id, "outcome"),
            )
            return _jsonrpc_success(
                request_id,
                {
                    "content": result.content,
                    "structuredContent": result.structured_content,
                    "isError": result.is_error,
                },
            )
        return _jsonrpc_error(
            request_id, code=-32601, message=f"Method '{method}' is not supported"
        )
    except MCPError as exc:
        if method == "tools/call":
            tool_name = str(tool_params.get("name") or "")
            try:
                server_key = parse_namespaced_tool_name(tool_name)[0]
            except MCPToolNotFoundError:
                server_key = ""
            await emit_audit_event(
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
                    "operation_id": tool_operation_id,
                    "phase": "outcome",
                    "server_key": server_key,
                    "tool_name": tool_name,
                    "scope_type": None,
                    "scope_id": None,
                },
                critical=False,
                event_id=(
                    _tool_audit_event_id(tool_operation_id, "outcome")
                    if tool_operation_id is not None
                    else None
                ),
            )
        elif method == "tools/list":
            await emit_audit_event(
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
            await emit_audit_event(
                request=request,
                request_start=request_start,
                action=AuditAction.MCP_TOOL_CALL,
                status="error",
                actor_type="api_key",
                actor_id=auth.user_id or auth.api_key,
                organization_id=auth.organization_id,
                api_key=auth.api_key,
                resource_type="mcp_tool",
                resource_id=str(tool_params.get("name") or ""),
                error=exc,
                metadata={
                    "operation_id": tool_operation_id,
                    "phase": "outcome",
                },
                critical=False,
                event_id=(
                    _tool_audit_event_id(tool_operation_id, "outcome")
                    if tool_operation_id is not None
                    else None
                ),
            )
        elif method == "tools/list":
            await emit_audit_event(
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
