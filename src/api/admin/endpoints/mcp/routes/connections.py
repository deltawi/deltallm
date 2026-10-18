"""User routes for per-user MCP OAuth connections ("Connected accounts").

Every route acts for the signed-in platform account only. A client cannot name
another account.
"""

from __future__ import annotations

import logging
from datetime import datetime
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from src.api.admin.endpoints.common import (
    AuthScope,
    emit_admin_mutation_audit,
    get_auth_scope,
    to_json_value,
)
from src.api.admin.endpoints.managed_assets import asset_principal_for_request
from src.api.admin.endpoints.mcp.dependencies import _db_or_503, _repository_or_503
from src.api.admin.endpoints.mcp.oauth_support import oauth_service, oauth_service_or_503
from src.api.admin.endpoints.mcp.sql_visibility import _server_visibility_exists_clause
from src.api.admin.endpoints.mcp.validators import _normalize_server_key
from src.audit.actions import AuditAction
from src.db.mcp.mcp import MCPServerRecord
from src.db.mcp.mcp_oauth import MCPOAuthConnectionRecord
from src.mcp.oauth import (
    OAUTH2_AUTH_MODE,
    MCPOAuthCallbackError,
    MCPOAuthError,
    OAuth2ServerSettings,
)
from src.mcp.oauth.service import CONNECT_PATH_PREFIX
from src.middleware.admin import require_authenticated
from src.middleware.platform_auth import get_platform_auth_context, requires_mfa_verification
from src.services.access.managed_asset_access import AssetKind

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin MCP"])

_MAX_OAUTH_SERVERS = 500
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


def _account_id_or_403(scope: AuthScope) -> str:
    account_id = str(scope.account_id or "").strip()
    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sign in with a user account to connect MCP servers. The master key has no account.",
        )
    return account_id


async def _visible_oauth_servers(
    request: Request, scope: AuthScope, connected_server_ids: set[str]
) -> list[MCPServerRecord]:
    """Enabled oauth2 servers the user can reach, plus servers the user already connected."""
    repository = _repository_or_503(request)
    servers, _ = await repository.list_servers(enabled=True, limit=_MAX_OAUTH_SERVERS, offset=0)
    candidates = [server for server in servers if server.auth_mode == OAUTH2_AUTH_MODE]
    if scope.is_platform_admin or not candidates:
        return candidates
    visible_ids = set(connected_server_ids)
    access_repository = getattr(request.app.state, "managed_asset_access_repository", None)
    if access_repository is not None:
        policies = await access_repository.list_accessible_policies(
            AssetKind.MCP_SERVER, asset_principal_for_request(request)
        )
        asset_ids = {policy.asset.asset_id for policy in policies}
        visible_ids.update(
            server.mcp_server_id
            for server in candidates
            if server.managed_asset_id and str(server.managed_asset_id) in asset_ids
        )
    remaining = [
        server.mcp_server_id for server in candidates if server.mcp_server_id not in visible_ids
    ]
    if remaining and (scope.org_ids or scope.team_ids):
        db = _db_or_503(request)
        params: list[Any] = []
        placeholders = []
        for server_id in remaining:
            params.append(server_id)
            placeholders.append(f"${len(params)}")
        rows = await db.query_raw(
            f"""
            SELECT s.mcp_server_id
            FROM deltallm_mcpserver s
            LEFT JOIN deltallm_managedasset asset ON asset.asset_id = s.managed_asset_id
            WHERE s.mcp_server_id IN ({", ".join(placeholders)})
              AND (asset.governance_source IS NULL OR asset.governance_source <> 'creator')
              AND ({_server_visibility_exists_clause("s", scope, params)})
            """,
            *params,
        )
        visible_ids.update(str(row.get("mcp_server_id")) for row in rows)
    return [server for server in candidates if server.mcp_server_id in visible_ids]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _connection_row(
    server: MCPServerRecord, connection: MCPOAuthConnectionRecord | None
) -> dict[str, Any]:
    settings = OAuth2ServerSettings.from_auth_config(server.auth_config)
    status_value = "not_connected" if connection is None else connection.status
    return {
        "server_id": server.mcp_server_id,
        "server_key": server.server_key,
        "server_name": server.name,
        "status": status_value,
        "status_reason": connection.status_reason if connection is not None else None,
        "account_label": connection.account_label if connection is not None else None,
        "scopes": list(connection.scopes) if connection is not None else list(settings.scopes),
        "expires_at": _iso(connection.expires_at) if connection is not None else None,
        "renews_automatically": bool(connection and connection.refresh_token_ciphertext),
        "connected_at": _iso(connection.connected_at) if connection is not None else None,
        "connect_available": settings.discovery is not None and bool(settings.client_id),
    }


@router.get("/ui/api/mcp-connections", dependencies=[Depends(require_authenticated)])
async def list_mcp_connections(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key)
    service = oauth_service(request)
    account_id = str(scope.account_id or "").strip()
    if service is None or not account_id:
        return {"data": [], "account_connectable": bool(account_id)}
    connections = {item.server_id: item for item in await service.list_connections(account_id)}
    servers = await _visible_oauth_servers(request, scope, set(connections))
    rows = [_connection_row(server, connections.get(server.mcp_server_id)) for server in servers]
    rows.sort(key=lambda row: (row["server_name"].lower(), row["server_key"]))
    return {"data": rows, "account_connectable": True}


async def _load_user_server(
    request: Request, scope: AuthScope, server_key: str, account_id: str
) -> MCPServerRecord:
    key = _normalize_server_key(server_key)
    repository = _repository_or_503(request)
    server = await repository.get_server_by_key(key)
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    if server is None or server.auth_mode != OAUTH2_AUTH_MODE:
        raise not_found
    service = oauth_service_or_503(request)
    connection = await service.repository.get_connection(
        server_id=server.mcp_server_id, account_id=account_id
    )
    connected = {server.mcp_server_id} if connection is not None else set()
    visible = await _visible_oauth_servers(request, scope, connected)
    if server.mcp_server_id not in {item.mcp_server_id for item in visible}:
        raise not_found
    return server


@router.post(
    "/ui/api/mcp-connections/{server_key}/connect",
    dependencies=[Depends(require_authenticated)],
)
async def start_mcp_connection(
    request: Request,
    server_key: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Start the authorization code flow. The browser opens the returned URL."""
    scope = get_auth_scope(request, authorization, x_master_key)
    account_id = _account_id_or_403(scope)
    server = await _load_user_server(request, scope, server_key, account_id)
    service = oauth_service_or_503(request)
    try:
        authorization_url = await service.start_authorization(server, account_id=account_id)
    except MCPOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"authorization_url": authorization_url}


@router.delete(
    "/ui/api/mcp-connections/{server_key}",
    dependencies=[Depends(require_authenticated)],
)
async def delete_mcp_connection(
    request: Request,
    server_key: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Revoke the tokens at the server (RFC 7009) when it can, then delete them."""
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key)
    account_id = _account_id_or_403(scope)
    key = _normalize_server_key(server_key)
    server = await _repository_or_503(request).get_server_by_key(key)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    service = oauth_service_or_503(request)
    deleted, revoked = await service.disconnect(server, account_id=account_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    response = {"deleted": True, "revoked": revoked, "server_key": server.server_key}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.MCP_OAUTH_DISCONNECT,
        scope=scope,
        resource_type="mcp_server",
        resource_id=server.mcp_server_id,
        response_payload=to_json_value(response),
        metadata={"server_key": server.server_key, "account_id": account_id},
    )
    return response


def _callback_redirect(
    request: Request, *, server_key: str | None, result: str, reason: str | None = None
) -> RedirectResponse:
    service = oauth_service(request)
    base = (service.public_base_url() if service is not None else None) or ""
    query = {"result": result, **({"reason": reason} if reason else {})}
    # server_key comes from the database and uses a safe character set; the
    # target is always this gateway, so the callback cannot redirect elsewhere.
    path = f"{CONNECT_PATH_PREFIX}{server_key}" if server_key else "/connected-accounts"
    return RedirectResponse(
        f"{base}{path}?{urlencode(query)}",
        status_code=status.HTTP_303_SEE_OTHER,
        headers=_NO_STORE_HEADERS,
    )


@router.get("/mcp/oauth/callback", include_in_schema=False)
async def mcp_oauth_callback(
    request: Request,
    state: str | None = Query(default=None, max_length=512),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=256),
) -> RedirectResponse:
    """OAuth redirect target. The state must belong to the signed-in account."""
    request_start = perf_counter()
    service = oauth_service(request)
    context = get_platform_auth_context(request)
    if service is None:
        return _callback_redirect(request, server_key=None, result="error", reason="configuration")
    if context is None or requires_mfa_verification(context):
        return _callback_redirect(
            request, server_key=None, result="error", reason="sign_in_required"
        )
    try:
        server = await service.complete_authorization(
            state=state or "", code=code, error=error, account_id=context.account_id
        )
    except MCPOAuthCallbackError as exc:
        return _callback_redirect(
            request, server_key=exc.server_key, result="error", reason=exc.reason
        )
    except Exception:
        logger.exception("mcp oauth callback failed")
        return _callback_redirect(
            request, server_key=None, result="error", reason="token_exchange_failed"
        )
    try:
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.MCP_OAUTH_CONNECT,
            resource_type="mcp_server",
            resource_id=server.mcp_server_id,
            metadata={"server_key": server.server_key, "account_id": context.account_id},
            critical=False,
        )
    except Exception:
        logger.warning("mcp oauth connect audit failed", exc_info=True)
    return _callback_redirect(request, server_key=server.server_key, result="connected")


__all__ = ["router"]
