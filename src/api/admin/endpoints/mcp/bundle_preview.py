"""Check which bundle tools one API key can use, and why not.

The check uses the same gateway rules as a real request: bindings, tool
policies and approvals. It builds the caller from the key record directly, so
the admin check does not use the data-plane authentication path.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from src.api.admin.endpoints.common import AuthScope
from src.api.admin.endpoints.mcp.bundle_admin import BundleOwner
from src.api.admin.endpoints.mcp.dependencies import _db_or_503
from src.api.admin.key_mutations import _get_key_scope_row, _require_key_access
from src.auth.roles import Permission
from src.db.identity.key_access import KeyAccessRepository
from src.mcp.bundle_runtime import BundleToolAccess, explain_bundle_access
from src.mcp.bundles import BundleToolTarget, MCPBundle, bundle_visible_to_caller
from src.mcp.gateway import MCPGatewayService
from src.models.responses import UserAPIKeyAuth
from src.services.access.runtime_scopes import annotate_auth_metadata

_OUTSIDE_OWNER_SCOPE = "The bundle owner scope does not include this key."


def _gateway_or_503(request: Request) -> MCPGatewayService:
    gateway = getattr(request.app.state, "mcp_gateway_service", None)
    if not isinstance(gateway, MCPGatewayService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP gateway unavailable"
        )
    return gateway


async def _auth_for_key(request: Request, scope: AuthScope, token_hash: str) -> UserAPIKeyAuth:
    db = _db_or_503(request)
    await _require_key_access(scope, db, token_hash, admin_permission=Permission.KEY_READ)
    row = await _get_key_scope_row(db, token_hash)
    owner_account_id = await KeyAccessRepository(db).owner(token_hash)
    auth = UserAPIKeyAuth(
        api_key=token_hash,
        user_id=str(row.get("user_id") or "") or None,
        team_id=str(row.get("team_id") or "") or None,
        organization_id=str(row.get("organization_id") or "") or None,
        owner_account_id=owner_account_id or None,
    )
    return annotate_auth_metadata(auth, auth_source="api_key", api_key_scope_id=token_hash)


async def preview_key_access(
    request: Request,
    *,
    scope: AuthScope,
    token_hash: str,
    owner: BundleOwner,
    catalog: dict[str, BundleToolTarget],
) -> dict[str, Any]:
    auth = await _auth_for_key(request, scope, token_hash)
    probe = MCPBundle(
        bundle_id="preview",
        bundle_key="preview",
        name="preview",
        owner_scope_type=owner.scope_type,
        owner_scope_id=owner.scope_id,
        enabled=True,
        members=(),
    )
    sees_bundle = bundle_visible_to_caller(probe, auth)
    if sees_bundle:
        results = await explain_bundle_access(_gateway_or_503(request), auth, catalog)
        tools = [
            {"name": item.target.exposed_name, "access": item.access.value} for item in results
        ]
    else:
        tools = [{"name": name, "access": BundleToolAccess.NO_ACCESS.value} for name in catalog]
    usable = {BundleToolAccess.AVAILABLE.value, BundleToolAccess.NEEDS_APPROVAL.value}
    return {
        "api_key": token_hash,
        "team_id": auth.team_id,
        "organization_id": auth.organization_id,
        "sees_bundle": sees_bundle,
        "reason": None if sees_bundle else _OUTSIDE_OWNER_SCOPE,
        "usable_count": sum(1 for tool in tools if tool["access"] in usable),
        "tools": tools,
    }


__all__ = ["preview_key_access"]
