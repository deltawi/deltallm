"""Admin MCP bundle routes.

A bundle shows the tools of several MCP servers at one endpoint. It does not
give access: bindings, tool policies and approvals stay the only access rules.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.admin.auth_scope import scope_for_permissions
from src.api.admin.endpoints.common import AuthScope, emit_admin_mutation_audit, get_auth_scope
from src.api.admin.endpoints.mcp.bundle_admin import (
    AdminServerTools,
    BundleOwner,
    admin_server_tools,
    bad_request,
    bundle_not_found,
    bundle_repository_or_503,
    catalog_for,
    check_server_fits_owner,
    default_owner_request,
    ensure_key_free,
    load_member_servers,
    owner_filter_for_scope,
    owner_of_record,
    owner_readable,
    owner_writable,
    record_tool_count,
    resolve_owner,
    serialize_bundle,
    serialize_catalog,
    serialize_server_tools,
    strict_catalog,
)
from src.api.admin.endpoints.mcp.bundle_preview import preview_key_access
from src.api.admin.endpoints.mcp.dependencies import _reload_runtime_governance
from src.api.admin.endpoints.mcp.routes.servers import _authorize_server
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.db.mcp.mcp_bundles import MCPBundleRecord
from src.mcp.bundles import (
    MCPBundleMember,
    MCPBundleValidationError,
    normalize_bundle_key,
    parse_bundle_members,
)
from src.middleware.admin import require_authenticated

router = APIRouter(tags=["Admin MCP"])

_MAX_NAME_LENGTH = 200
_MAX_DESCRIPTION_LENGTH = 2000


def _scopes(
    request: Request, authorization: str | None, x_master_key: str | None
) -> tuple[AuthScope, AuthScope, AuthScope]:
    scope = get_auth_scope(request, authorization, x_master_key)
    return (
        scope,
        scope_for_permissions(scope, [Permission.KEY_READ]),
        scope_for_permissions(scope, [Permission.ORG_UPDATE]),
    )


def _text(payload: dict[str, Any], field: str, *, required: bool, limit: int) -> str | None:
    raw = payload.get(field)
    value = str(raw).strip() if raw is not None else ""
    if required and not value:
        raise bad_request(f"{field} is required")
    if len(value) > limit:
        raise bad_request(f"{field} must be at most {limit} characters")
    return value or None


def _members(payload: dict[str, Any]) -> tuple[MCPBundleMember, ...]:
    try:
        return parse_bundle_members(payload.get("members"))
    except MCPBundleValidationError as exc:
        raise bad_request(str(exc)) from exc


async def _member_tools(
    request: Request,
    members: tuple[MCPBundleMember, ...],
    *,
    read_scope: AuthScope,
    owner: BundleOwner,
) -> dict[str, AdminServerTools]:
    """Check each member server against the admin and the owner, and load its tools."""

    servers = await load_member_servers(request, members)
    out: dict[str, AdminServerTools] = {}
    for member in members:
        server = servers[member.server_id]
        try:
            await _authorize_server(request, server, read_scope)
        except HTTPException as exc:
            # Do not tell apart a hidden server and a missing one.
            raise bad_request(f"MCP server '{member.server_id}' not found") from exc
        check_server_fits_owner(server, owner)
        out[member.server_id] = await admin_server_tools(
            request, server, read_scope=read_scope, owner=owner
        )
    return out


async def _load_readable(
    request: Request, bundle_id: str, read_scope: AuthScope
) -> tuple[MCPBundleRecord, BundleOwner]:
    record = await bundle_repository_or_503(request).get_bundle(bundle_id)
    if record is None:
        raise bundle_not_found()
    owner = await owner_of_record(request, record)
    if not owner_readable(owner, read_scope):
        raise bundle_not_found()
    return record, owner


@router.get("/ui/api/mcp-bundles", dependencies=[Depends(require_authenticated)])
async def list_mcp_bundles(
    request: Request,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    _, read_scope, manage_scope = _scopes(request, authorization, x_master_key)
    owner_filter = owner_filter_for_scope(read_scope)
    if owner_filter is not None and not (owner_filter.organization_ids or owner_filter.team_ids):
        records, total = [], 0
    else:
        records, total = await bundle_repository_or_503(request).list_bundles(
            search=search, owner_filter=owner_filter, limit=limit, offset=offset
        )
    data = []
    for record in records:
        owner = await owner_of_record(request, record)
        data.append(
            serialize_bundle(
                record,
                can_mutate=owner_writable(owner, manage_scope),
                tool_count=record_tool_count(record),
            )
        )
    return {
        "data": data,
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@router.post("/ui/api/mcp-bundles", dependencies=[Depends(require_authenticated)])
async def create_mcp_bundle(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope, read_scope, manage_scope = _scopes(request, authorization, x_master_key)
    repository = bundle_repository_or_503(request)
    try:
        bundle_key = normalize_bundle_key(payload.get("bundle_key"))
    except MCPBundleValidationError as exc:
        raise bad_request(str(exc)) from exc
    name = _text(payload, "name", required=True, limit=_MAX_NAME_LENGTH)
    description = _text(payload, "description", required=False, limit=_MAX_DESCRIPTION_LENGTH)
    owner_type, owner_id = default_owner_request(payload, manage_scope)
    owner = await resolve_owner(request, scope_type=owner_type, scope_id=owner_id)
    if not owner_writable(owner, manage_scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )
    members = _members(payload)
    tools = await _member_tools(request, members, read_scope=read_scope, owner=owner)
    strict_catalog(members, tools)
    await ensure_key_free(request, bundle_key)
    created = await repository.create_bundle(
        bundle_key=bundle_key,
        name=str(name),
        description=description,
        owner_scope_type=owner.scope_type,
        owner_scope_id=owner.scope_id,
        enabled=bool(payload.get("enabled", True)),
        members=members,
        metadata=None,
        created_by_account_id=scope.account_id,
    )
    if created is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
        )
    await _reload_runtime_governance(request, invalidate_registry=False)
    response = serialize_bundle(created, can_mutate=True, tool_count=record_tool_count(created))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_BUNDLE_CREATE,
        scope=scope,
        resource_type="mcp_bundle",
        resource_id=created.mcp_bundle_id,
        organization_id=owner.organization_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.post("/ui/api/mcp-bundles/preview", dependencies=[Depends(require_authenticated)])
async def preview_mcp_bundle(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Show the tools of a saved or draft bundle, and what one API key can use."""

    scope, read_scope, _ = _scopes(request, authorization, x_master_key)
    bundle_id = str(payload.get("mcp_bundle_id") or "").strip()
    if bundle_id:
        record, owner = await _load_readable(request, bundle_id, read_scope)
        members = (
            _members(payload)
            if "members" in payload
            else tuple(member.to_member() for member in record.members)
        )
    else:
        owner_type, owner_id = default_owner_request(payload, read_scope)
        owner = await resolve_owner(request, scope_type=owner_type, scope_id=owner_id)
        if not owner_readable(owner, read_scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        members = _members(payload)
    tools = await _member_tools(request, members, read_scope=read_scope, owner=owner)
    errors: list[str] = []
    try:
        catalog_for(members, tools, strict=True)
    except MCPBundleValidationError as exc:
        errors.append(str(exc))
    catalog = catalog_for(members, tools, strict=False)
    key_result = None
    api_key = str(payload.get("api_key") or "").strip()
    if api_key:
        key_result = await preview_key_access(
            request, scope=scope, token_hash=api_key, owner=owner, catalog=catalog
        )
    return {
        "servers": [serialize_server_tools(tools[member.server_id]) for member in members],
        "tools": serialize_catalog(catalog, tools),
        "errors": errors,
        "key": key_result,
    }


@router.get("/ui/api/mcp-bundles/{bundle_id}", dependencies=[Depends(require_authenticated)])
async def get_mcp_bundle(
    request: Request,
    bundle_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    _, read_scope, manage_scope = _scopes(request, authorization, x_master_key)
    record, owner = await _load_readable(request, bundle_id, read_scope)
    return serialize_bundle(
        record, can_mutate=owner_writable(owner, manage_scope), tool_count=record_tool_count(record)
    )


@router.patch("/ui/api/mcp-bundles/{bundle_id}", dependencies=[Depends(require_authenticated)])
async def update_mcp_bundle(
    request: Request,
    bundle_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope, read_scope, manage_scope = _scopes(request, authorization, x_master_key)
    existing, owner = await _load_readable(request, bundle_id, read_scope)
    if not owner_writable(owner, manage_scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )
    if "bundle_key" in payload and str(payload.get("bundle_key") or "").strip().lower() != (
        existing.bundle_key
    ):
        raise bad_request("bundle_key cannot be changed")
    for field, current in (
        ("owner_scope_type", existing.owner_scope_type),
        ("owner_scope_id", existing.owner_scope_id),
    ):
        if field in payload and (payload.get(field) or None) != current:
            raise bad_request("MCP bundle ownership cannot be changed")
    members = (
        _members(payload)
        if "members" in payload
        else tuple(member.to_member() for member in existing.members)
    )
    tools = await _member_tools(request, members, read_scope=read_scope, owner=owner)
    strict_catalog(members, tools)
    name = (
        _text(payload, "name", required=True, limit=_MAX_NAME_LENGTH)
        if "name" in payload
        else existing.name
    )
    description = (
        _text(payload, "description", required=False, limit=_MAX_DESCRIPTION_LENGTH)
        if "description" in payload
        else existing.description
    )
    updated = await bundle_repository_or_503(request).update_bundle(
        bundle_id,
        name=str(name),
        description=description,
        enabled=bool(payload.get("enabled", existing.enabled)),
        members=members,
        metadata=existing.metadata,
    )
    if updated is None:
        raise bundle_not_found()
    await _reload_runtime_governance(request, invalidate_registry=False)
    response = serialize_bundle(updated, can_mutate=True, tool_count=record_tool_count(updated))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_BUNDLE_UPDATE,
        scope=scope,
        resource_type="mcp_bundle",
        resource_id=updated.mcp_bundle_id,
        organization_id=owner.organization_id,
        request_payload=payload,
        response_payload=response,
        before=serialize_bundle(existing, can_mutate=True),
        after=response,
    )
    return response


@router.delete("/ui/api/mcp-bundles/{bundle_id}", dependencies=[Depends(require_authenticated)])
async def delete_mcp_bundle(
    request: Request,
    bundle_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope, read_scope, manage_scope = _scopes(request, authorization, x_master_key)
    existing, owner = await _load_readable(request, bundle_id, read_scope)
    if not owner_writable(owner, manage_scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )
    if not await bundle_repository_or_503(request).delete_bundle(bundle_id):
        raise bundle_not_found()
    await _reload_runtime_governance(request, invalidate_registry=False)
    response = {"deleted": True, "mcp_bundle_id": bundle_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_BUNDLE_DELETE,
        scope=scope,
        resource_type="mcp_bundle",
        resource_id=bundle_id,
        organization_id=owner.organization_id,
        response_payload=response,
        before=serialize_bundle(existing, can_mutate=True),
    )
    return response


__all__ = ["router"]
