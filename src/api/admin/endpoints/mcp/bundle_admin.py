"""Scope rules, validation and preview for admin MCP bundle endpoints.

A bundle is a view over MCP servers. These rules make sure that an admin can
only build a bundle from servers and tools that the admin can already see, and
that a bundle stays inside one tenant: an organization-owned server can only
be a member of a bundle of the same organization (or of one of its teams).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from src.api.admin.endpoints.common import AuthScope, to_json_value
from src.api.admin.endpoints.mcp.dependencies import _repository_or_503
from src.api.admin.endpoints.mcp.loaders import (
    _list_scoped_bindings,
    _list_scoped_tool_policies,
    _load_server_summary_map,
)
from src.api.admin.endpoints.mcp.operations import _filter_server_tools_for_scope
from src.api.admin.endpoints.mcp.scope_visibility import (
    _server_owned_by_scope,
    _validate_scope_target,
)
from src.db.mcp.mcp import MCPServerRecord, MCPToolPolicyRecord
from src.db.mcp.mcp_bundles import MCPBundleOwnerFilter, MCPBundleRecord, MCPBundleRepository
from src.mcp.bundles import (
    BUNDLE_OWNER_SCOPE_TYPES,
    BundleToolTarget,
    MCPBundleMember,
    MCPBundleValidationError,
    build_bundle_catalog,
)
from src.mcp.capabilities import extract_tool_schemas, namespace_tools


@dataclass(frozen=True, slots=True)
class BundleOwner:
    scope_type: str
    scope_id: str | None
    # The organization of the owner. None for a global bundle.
    organization_id: str | None


@dataclass(frozen=True, slots=True)
class AdminServerTools:
    server: MCPServerRecord
    tools: list[Any]
    policies: list[MCPToolPolicyRecord]


def bundle_repository_or_503(request: Request) -> MCPBundleRepository:
    repository = getattr(request.app.state, "mcp_bundle_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP bundle repository unavailable",
        )
    return repository


def bundle_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP bundle not found")


def bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def owner_filter_for_scope(scope: AuthScope) -> MCPBundleOwnerFilter | None:
    """``None`` means all bundles. Global bundles are for platform admins only."""

    if scope.is_platform_admin:
        return None
    return MCPBundleOwnerFilter(
        organization_ids=tuple(scope.org_ids), team_ids=tuple(scope.team_ids)
    )


async def resolve_owner(request: Request, *, scope_type: str, scope_id: str | None) -> BundleOwner:
    normalized_type = str(scope_type or "").strip().lower()
    if normalized_type not in BUNDLE_OWNER_SCOPE_TYPES:
        raise bad_request("owner_scope_type must be one of: global, organization, team")
    normalized_id = str(scope_id or "").strip() or None
    if normalized_type == "global":
        if normalized_id:
            raise bad_request("owner_scope_id must be omitted for global bundles")
        return BundleOwner("global", None, None)
    if not normalized_id:
        raise bad_request(f"owner_scope_id is required for {normalized_type} bundles")
    target = await _validate_scope_target(
        request, scope_type=normalized_type, scope_id=normalized_id
    )
    return BundleOwner(normalized_type, normalized_id, target.organization_id)


async def owner_of_record(request: Request, record: MCPBundleRecord) -> BundleOwner:
    if record.owner_scope_type == "global":
        return BundleOwner("global", None, None)
    if record.owner_scope_type == "organization":
        return BundleOwner("organization", record.owner_scope_id, record.owner_scope_id)
    try:
        target = await _validate_scope_target(
            request, scope_type="team", scope_id=str(record.owner_scope_id or "")
        )
    except HTTPException:
        # The team is gone. Only a platform admin can manage the bundle now.
        return BundleOwner("team", record.owner_scope_id, None)
    return BundleOwner("team", record.owner_scope_id, target.organization_id)


def owner_readable(owner: BundleOwner, scope: AuthScope) -> bool:
    if scope.is_platform_admin:
        return True
    if owner.scope_type == "team" and owner.scope_id in scope.team_ids:
        return True
    return owner.scope_type != "global" and bool(
        owner.organization_id and owner.organization_id in scope.org_ids
    )


def owner_writable(owner: BundleOwner, scope: AuthScope) -> bool:
    """The same rule as MCP servers: write access needs org.update in the owner organization."""

    if scope.is_platform_admin:
        return True
    return owner.scope_type != "global" and bool(
        owner.organization_id and owner.organization_id in scope.org_ids
    )


def default_owner_request(payload: dict[str, Any], scope: AuthScope) -> tuple[str, str | None]:
    scope_type = payload.get("owner_scope_type")
    scope_id = payload.get("owner_scope_id")
    if scope_type is None:
        if scope.is_platform_admin:
            return "global", None
        scope_type = "organization"
        if scope_id is None and len(scope.org_ids) == 1:
            scope_id = scope.org_ids[0]
    return str(scope_type), str(scope_id) if scope_id is not None else None


def check_server_fits_owner(server: MCPServerRecord, owner: BundleOwner) -> None:
    """Keep a bundle inside one tenant."""

    if server.owner_scope_type != "organization":
        return
    if owner.organization_id is None or owner.organization_id != server.owner_scope_id:
        raise bad_request(
            f"MCP server '{server.server_key}' belongs to another organization. "
            "Only bundles of that organization or its teams can use it."
        )


async def admin_server_tools(
    request: Request,
    server: MCPServerRecord,
    *,
    read_scope: AuthScope,
    owner: BundleOwner,
) -> AdminServerTools:
    """The tools of one server that this admin can see, with the owner scope policies."""

    registry_tools = namespace_tools(
        server.server_key, extract_tool_schemas(server.capabilities_json)
    )
    include_all = read_scope.is_platform_admin or _server_owned_by_scope(server, read_scope)
    bindings = []
    if not include_all:
        bindings, _ = await _list_scoped_bindings(
            request, scope=read_scope, server_id=server.mcp_server_id, limit=200, offset=0
        )
    tools = _filter_server_tools_for_scope(
        registry_tools, bindings=bindings, policies=[], include_all=include_all
    )
    policies = await _owner_policies(request, server, read_scope=read_scope, owner=owner)
    return AdminServerTools(server=server, tools=tools, policies=policies)


async def _owner_policies(
    request: Request,
    server: MCPServerRecord,
    *,
    read_scope: AuthScope,
    owner: BundleOwner,
) -> list[MCPToolPolicyRecord]:
    scopes: list[tuple[str, str]] = []
    if owner.scope_type == "team" and owner.scope_id:
        scopes.append(("team", owner.scope_id))
    if owner.organization_id:
        scopes.append(("organization", owner.organization_id))
    if not scopes:
        return []
    policies, _ = await _list_scoped_tool_policies(
        request,
        scope=read_scope,
        server_id=server.mcp_server_id,
        enabled_only=False,
        limit=200,
        offset=0,
    )
    order = {scope: index for index, scope in enumerate(scopes)}
    selected: dict[str, MCPToolPolicyRecord] = {}
    for policy in sorted(
        (item for item in policies if (item.scope_type, item.scope_id) in order),
        key=lambda item: order[(item.scope_type, item.scope_id)],
    ):
        selected.setdefault(policy.tool_name, policy)
    return list(selected.values())


async def load_member_servers(
    request: Request, members: tuple[MCPBundleMember, ...]
) -> dict[str, MCPServerRecord]:
    servers = await _load_server_summary_map(request, [member.server_id for member in members])
    for member in members:
        if member.server_id not in servers:
            raise bad_request(f"MCP server '{member.server_id}' not found")
    return servers


async def ensure_key_free(request: Request, bundle_key: str) -> None:
    """A bundle key and an MCP server key share one name space."""

    if await _repository_or_503(request).get_server_by_key(bundle_key) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The key '{bundle_key}' is already used by an MCP server",
        )
    if await bundle_repository_or_503(request).get_bundle_by_key(bundle_key) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An MCP bundle with the key '{bundle_key}' already exists",
        )


def catalog_for(
    members: tuple[MCPBundleMember, ...],
    server_tools: dict[str, AdminServerTools],
    *,
    strict: bool,
) -> dict[str, BundleToolTarget]:
    return build_bundle_catalog(
        members,
        server_keys={sid: item.server.server_key for sid, item in server_tools.items()},
        server_tools={
            sid: [str(tool.original_name) for tool in item.tools]
            for sid, item in server_tools.items()
        },
        strict=strict,
    )


def strict_catalog(
    members: tuple[MCPBundleMember, ...], server_tools: dict[str, AdminServerTools]
) -> dict[str, BundleToolTarget]:
    try:
        return catalog_for(members, server_tools, strict=True)
    except MCPBundleValidationError as exc:
        raise bad_request(str(exc)) from exc


def serialize_bundle(
    record: MCPBundleRecord, *, can_mutate: bool, tool_count: int | None = None
) -> dict[str, Any]:
    members = [
        {
            "mcp_server_id": member.mcp_server_id,
            "server_key": member.server_key,
            "server_name": member.server_name,
            "server_enabled": member.server_enabled,
            "all_tools": member.all_tools,
            "tool_names": None if member.all_tools else list(member.tool_allowlist),
            "name_prefix": member.name_prefix,
            "tool_count": len(member.server_tool_names)
            if member.all_tools
            else len(set(member.tool_allowlist) & set(member.server_tool_names)),
            "server_tool_count": len(member.server_tool_names),
        }
        for member in record.members
    ]
    return {
        "mcp_bundle_id": record.mcp_bundle_id,
        "bundle_key": record.bundle_key,
        "name": record.name,
        "description": record.description,
        "owner_scope_type": record.owner_scope_type,
        "owner_scope_id": record.owner_scope_id,
        "enabled": record.enabled,
        "members": members,
        "tool_count": tool_count,
        "created_at": to_json_value(record.created_at),
        "updated_at": to_json_value(record.updated_at),
        "capabilities": {"can_mutate": can_mutate},
    }


def record_tool_count(record: MCPBundleRecord) -> int:
    """Count the exposed tools from stored capabilities, as the runtime does."""

    catalog = build_bundle_catalog(
        [member.to_member() for member in record.members if member.server_enabled],
        server_keys={member.mcp_server_id: member.server_key for member in record.members},
        server_tools={member.mcp_server_id: member.server_tool_names for member in record.members},
        strict=False,
    )
    return len(catalog)


def serialize_server_tools(item: AdminServerTools) -> dict[str, Any]:
    policy_by_tool = {policy.tool_name: policy for policy in item.policies}
    tools = []
    for tool in item.tools:
        policy = policy_by_tool.get(str(tool.original_name))
        tools.append(
            {
                "name": tool.original_name,
                "description": tool.description,
                "needs_approval": bool(
                    policy is not None and policy.enabled and policy.require_approval == "manual"
                ),
                "disabled_by_policy": bool(policy is not None and not policy.enabled),
            }
        )
    return {
        "mcp_server_id": item.server.mcp_server_id,
        "server_key": item.server.server_key,
        "name": item.server.name,
        "enabled": item.server.enabled,
        "tools": tools,
    }


def serialize_catalog(
    catalog: dict[str, BundleToolTarget], server_tools: dict[str, AdminServerTools]
) -> list[dict[str, Any]]:
    descriptions = {
        (sid, str(tool.original_name)): tool.description
        for sid, item in server_tools.items()
        for tool in item.tools
    }
    return [
        {
            **to_json_value(asdict(target)),
            "description": descriptions.get((target.server_id, target.tool_name)),
        }
        for target in catalog.values()
    ]


__all__ = [
    "AdminServerTools",
    "BundleOwner",
    "admin_server_tools",
    "bad_request",
    "bundle_not_found",
    "bundle_repository_or_503",
    "catalog_for",
    "check_server_fits_owner",
    "default_owner_request",
    "ensure_key_free",
    "load_member_servers",
    "owner_filter_for_scope",
    "owner_of_record",
    "owner_readable",
    "owner_writable",
    "record_tool_count",
    "resolve_owner",
    "serialize_bundle",
    "serialize_catalog",
    "serialize_server_tools",
    "strict_catalog",
]
