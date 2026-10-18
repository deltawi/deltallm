"""Admin routes for MCP servers with the OAuth 2.0 (per user) auth mode."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from src.api.admin.auth_scope import scope_for_permissions
from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.api.admin.endpoints.mcp.dependencies import (
    _reload_runtime_governance,
    _registry_or_503,
    _repository_or_503,
)
from src.api.admin.endpoints.mcp.loaders import _load_server_or_404
from src.api.admin.endpoints.mcp.oauth_support import oauth_service_or_503
from src.api.admin.endpoints.mcp.routes.servers import (
    _access_repository,
    _authorize_server,
    _view_capabilities,
)
from src.api.admin.endpoints.mcp.serializers import _serialize_server
from src.api.admin.endpoints.mcp.validators import _validate_url
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.mcp.oauth import (
    OAUTH2_AUTH_MODE,
    MCPOAuthConfigurationError,
    MCPOAuthError,
    OAuth2ServerSettings,
)
from src.middleware.admin import require_authenticated

router = APIRouter(tags=["Admin MCP"])


@router.get("/ui/api/mcp-oauth/settings", dependencies=[Depends(require_authenticated)])
async def get_mcp_oauth_settings(request: Request) -> dict[str, Any]:
    """Gateway values the server form shows for OAuth 2.0 (per user)."""
    service = oauth_service_or_503(request)
    return {
        "redirect_url": service.redirect_uri(),
        "public_base_url_configured": service.public_base_url() is not None,
        "encryption_configured": service.encryption_configured,
    }


@router.post("/ui/api/mcp-servers/oauth/discover", dependencies=[Depends(require_authenticated)])
async def preview_mcp_oauth_discovery(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Run discovery for a base URL before the server exists. Nothing is stored."""
    # The same callers that can create an MCP server can preview its discovery.
    scope = get_auth_scope(request, authorization, x_master_key)
    if _access_repository(request) is None:
        get_auth_scope(
            request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE
        )
    elif not scope.is_platform_admin and not scope.account_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated account is required"
        )
    service = oauth_service_or_503(request)
    base_url = _validate_url(payload.get("base_url"))
    try:
        result = await service.preview_discovery(base_url)
    except MCPOAuthError as exc:
        return {"discovery": None, "discovery_error": str(exc)}
    return {"discovery": result.to_public_json(), "discovery_error": None}


@router.post(
    "/ui/api/mcp-servers/{server_id}/oauth/discovery",
    dependencies=[Depends(require_authenticated)],
)
async def run_mcp_oauth_discovery(
    request: Request,
    server_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Run discovery again and register a client when the server has none."""
    request_start = perf_counter()
    repository = _repository_or_503(request)
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key)
    legacy_scope = scope_for_permissions(scope, [Permission.ORG_UPDATE])
    server = await _load_server_or_404(request, server_id)
    principal, policy, managed_authority = await _authorize_server(
        request, server, legacy_scope, write=True
    )
    if server.auth_mode != OAUTH2_AUTH_MODE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This MCP server does not use OAuth 2.0 (per user)",
        )
    service = oauth_service_or_503(request)
    try:
        settings = await service.run_discovery(
            base_url=server.base_url,
            settings=OAuth2ServerSettings.from_auth_config(server.auth_config),
        )
    except MCPOAuthConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    stored = await service.repository.store_server_oauth_config(
        server_id=server_id, auth_config=settings.to_auth_config()
    )
    updated = await repository.get_server(server_id) if stored else None
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    await registry.invalidate_server(updated.server_key)
    await _reload_runtime_governance(request, invalidate_registry=False)
    response = _serialize_server(
        updated,
        capabilities=_view_capabilities(
            updated,
            scope=legacy_scope,
            is_visible=True,
            policy=policy,
            principal=principal,
            managed_authority=managed_authority,
        ),
        policy=policy,
        principal=principal,
    )
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_OAUTH_DISCOVERY,
        scope=scope,
        resource_type="mcp_server",
        resource_id=server_id,
        response_payload={"oauth": response.get("oauth")},
        status="success" if settings.discovery_error is None else "error",
    )
    return response


__all__ = ["router"]
