"""Admin-side helpers for MCP servers with the ``oauth2`` (per user) auth mode."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import HTTPException, Request, status

from src.api.admin.endpoints.common import AuthScope
from src.db.mcp.mcp import MCPServerRecord
from src.mcp.models import MCPServerConfig
from src.mcp.oauth import (
    OAUTH2_AUTH_MODE,
    MCPConnectionRequiredError,
    MCPOAuthConfigurationError,
    MCPOAuthError,
    MCPOAuthService,
)
from src.mcp.registry import server_record_to_config


def oauth_service(request: Request) -> MCPOAuthService | None:
    return getattr(request.app.state, "mcp_oauth_service", None)


def oauth_service_or_503(request: Request) -> MCPOAuthService:
    service = oauth_service(request)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP OAuth service unavailable",
        )
    return service


async def build_oauth2_auth_config(
    request: Request,
    *,
    base_url: str,
    requested: Any,
    existing: MCPServerRecord | None,
) -> dict[str, Any]:
    """Validate the admin input, encrypt the client secret, run discovery and registration."""
    service = oauth_service_or_503(request)
    keep_existing = existing is not None and existing.auth_mode == OAUTH2_AUTH_MODE
    try:
        return await service.prepare_server_auth_config(
            base_url=base_url,
            requested=requested,
            existing=dict(existing.auth_config or {}) if keep_existing and existing else None,
            existing_base_url=existing.base_url if keep_existing and existing else None,
        )
    except MCPOAuthConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def drop_oauth_connections_if_target_changed(
    request: Request, before: MCPServerRecord, after: MCPServerRecord
) -> None:
    """Delete user tokens when an oauth2 server gets a new URL or leaves the mode.

    A token is issued for one resource. The gateway must not send it to another
    server, so users connect again after such a change.
    """
    if before.auth_mode != OAUTH2_AUTH_MODE:
        return
    same_target = after.auth_mode == OAUTH2_AUTH_MODE and before.base_url.rstrip(
        "/"
    ) == after.base_url.rstrip("/")
    if same_target:
        return
    service = oauth_service(request)
    if service is not None:
        await service.repository.delete_connections_for_server(before.mcp_server_id)


async def acting_user_server_config(
    request: Request, server: MCPServerRecord, scope: AuthScope
) -> MCPServerConfig | None:
    """Config for an admin action (tool refresh, health check) on an oauth2 server.

    The gateway uses the connection of the signed-in admin. Returns ``None`` for
    other auth modes, which keep their stored credentials.
    """
    if server.auth_mode != OAUTH2_AUTH_MODE:
        return None
    service = oauth_service_or_503(request)
    connect_hint = (
        f"Connect your {server.name} account first: open Connected accounts in the user menu, "
        "connect the server, then try again."
    )
    account_id = str(scope.account_id or "").strip()
    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This server uses OAuth 2.0 (per user). Sign in with a user account, not the "
                f"master key. {connect_hint}"
            ),
        )
    try:
        token = await service.access_token_for(
            server_id=server.mcp_server_id,
            server_key=server.server_key,
            auth_config=dict(server.auth_config or {}),
            account_id=account_id,
        )
    except MCPConnectionRequiredError as exc:
        prefix = (
            f"Your {server.name} connection needs to be renewed. "
            if exc.reason == "needs_reconnect"
            else ""
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"{prefix}{connect_hint}"
        ) from None
    except MCPOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return replace(server_record_to_config(server), oauth_access_token=token.value)
