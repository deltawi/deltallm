"""Resolve MCP bundles for one caller.

Bundle data comes from the in-memory governance snapshot, and tool lists come
from the registry cache that ``list_visible_tools`` also uses. Only requests
that name a bundle run this code, so other requests do no new SQL or Redis
work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.models.responses import UserAPIKeyAuth

from .bundles import (
    BundleTool,
    BundleToolTarget,
    MCPBundle,
    MCPBundleMember,
    build_bundle_catalog,
    bundle_visible_to_caller,
    visible_bundle_tools,
)
from .gateway import MCPGatewayService
from .models import NamespacedTool


class BundleToolAccess(StrEnum):
    AVAILABLE = "available"
    NEEDS_APPROVAL = "needs_approval"
    DISABLED_BY_POLICY = "disabled_by_policy"
    NO_ACCESS = "no_access"


@dataclass(frozen=True, slots=True)
class BundleToolAccessResult:
    target: BundleToolTarget
    access: BundleToolAccess


def resolve_visible_bundle(
    gateway: MCPGatewayService, auth: UserAPIKeyAuth, bundle_key: str
) -> MCPBundle | None:
    """Return the bundle when the caller can see it. Else ``None``, the same as an unknown key."""

    governance = gateway.governance_service
    if governance is None:
        return None
    bundle = governance.get_bundle(bundle_key)
    if bundle is None or not bundle_visible_to_caller(bundle, auth):
        return None
    return bundle


async def runtime_bundle_catalog(
    gateway: MCPGatewayService, members: tuple[MCPBundleMember, ...]
) -> dict[str, BundleToolTarget]:
    governance = gateway.governance_service
    server_keys: dict[str, str] = {}
    server_tools: dict[str, list[str]] = {}
    if governance is None:
        return {}
    for member in members:
        # The snapshot holds enabled servers only. A disabled server shows no tools.
        server = governance.get_server(member.server_id)
        if server is None:
            continue
        tools = await gateway.registry.list_namespaced_tools(server)
        server_keys[member.server_id] = server.server_key
        server_tools[member.server_id] = [tool.original_name for tool in tools]
    return build_bundle_catalog(
        members, server_keys=server_keys, server_tools=server_tools, strict=False
    )


async def list_bundle_tools(
    gateway: MCPGatewayService,
    auth: UserAPIKeyAuth,
    bundle: MCPBundle,
    *,
    visible_tools: list[NamespacedTool] | None = None,
) -> list[BundleTool]:
    """The bundle tools that this caller can already see through bindings and policies."""

    catalog = await runtime_bundle_catalog(gateway, bundle.members)
    if not catalog:
        return []
    visible = visible_tools if visible_tools is not None else await gateway.list_visible_tools(auth)
    return visible_bundle_tools(catalog, visible)


async def explain_bundle_access(
    gateway: MCPGatewayService,
    auth: UserAPIKeyAuth,
    catalog: dict[str, BundleToolTarget],
) -> list[BundleToolAccessResult]:
    """Tell for each bundle tool if this caller can use it, and why not."""

    visible_servers = {
        visible.server.mcp_server_id: visible
        for visible in await gateway.list_visible_servers(auth)
    }
    results: list[BundleToolAccessResult] = []
    for target in catalog.values():
        visible = visible_servers.get(target.server_id)
        policy = await gateway._effective_tool_policy(auth, target.server_id, target.tool_name)
        if visible is not None and target.tool_name in visible.tool_names:
            access = (
                BundleToolAccess.NEEDS_APPROVAL
                if policy is not None and policy.require_approval == "manual"
                else BundleToolAccess.AVAILABLE
            )
        elif policy is not None and not policy.enabled:
            access = BundleToolAccess.DISABLED_BY_POLICY
        else:
            access = BundleToolAccess.NO_ACCESS
        results.append(BundleToolAccessResult(target=target, access=access))
    return results


__all__ = [
    "BundleToolAccess",
    "BundleToolAccessResult",
    "explain_bundle_access",
    "list_bundle_tools",
    "resolve_visible_bundle",
    "runtime_bundle_catalog",
]
