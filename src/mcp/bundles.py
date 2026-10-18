"""MCP bundles: one endpoint that shows the tools of several MCP servers.

A bundle is a view. It never gives access to a tool. Bindings, tool policies
and approvals stay the only access rules: a caller sees a bundle tool only when
the same caller can already see the real tool on its server.

The exposed name of a tool is ``<prefix>_<tool>`` when the member has a name
prefix, else the tool's own name. The name map is built from the full bundle
catalog, not from the tools of one caller, so one exposed name always points
to the same real tool for every caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from src.models.responses import UserAPIKeyAuth
from src.services.access.runtime_scopes import resolve_runtime_scope_context

from .models import NamespacedTool

BUNDLE_OWNER_SCOPE_TYPES = frozenset({"global", "organization", "team"})
BUNDLE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
NAME_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
MAX_BUNDLE_MEMBERS = 50
MAX_MEMBER_TOOLS = 500
# The chat tool search uses this name. A bundle cannot expose it.
RESERVED_TOOL_NAMES = frozenset({"mcp_tool_search"})


@dataclass(frozen=True, slots=True)
class MCPBundleMember:
    server_id: str
    # None means all tools of the server, also tools that it adds later.
    tool_names: tuple[str, ...] | None
    name_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class MCPBundle:
    bundle_id: str
    bundle_key: str
    name: str
    owner_scope_type: str
    owner_scope_id: str | None
    enabled: bool
    members: tuple[MCPBundleMember, ...]
    description: str | None = None


@dataclass(frozen=True, slots=True)
class BundleToolTarget:
    exposed_name: str
    server_id: str
    server_key: str
    tool_name: str

    @property
    def namespaced_name(self) -> str:
        return f"{self.server_key}.{self.tool_name}"


@dataclass(frozen=True, slots=True)
class BundleTool:
    """A bundle tool that one caller can see."""

    exposed_name: str
    tool: NamespacedTool


class MCPBundleValidationError(ValueError):
    pass


def exposed_tool_name(name_prefix: str | None, tool_name: str) -> str:
    return f"{name_prefix}_{tool_name}" if name_prefix else tool_name


def build_bundle_catalog(
    members: Sequence[MCPBundleMember],
    *,
    server_keys: Mapping[str, str],
    server_tools: Mapping[str, Iterable[str]],
    strict: bool,
) -> dict[str, BundleToolTarget]:
    """Map each exposed name of the bundle to its real server and tool.

    ``server_keys`` and ``server_tools`` cover the member servers that exist and
    are enabled. A member whose server is missing contributes no tools.

    With ``strict``, an unknown tool name or a duplicate exposed name raises
    :class:`MCPBundleValidationError`. Without it (at runtime), the first
    member keeps a duplicate name and the later member does not show it. A
    server can add a tool after the bundle was saved, so a runtime conflict is
    possible for a member that shows all tools.
    """

    catalog: dict[str, BundleToolTarget] = {}
    for member in members:
        server_key = server_keys.get(member.server_id)
        if server_key is None:
            if strict:
                raise MCPBundleValidationError(f"MCP server '{member.server_id}' not found")
            continue
        available = list(dict.fromkeys(server_tools.get(member.server_id, ())))
        if member.tool_names is None:
            names = available
        else:
            known = set(available)
            unknown = [name for name in member.tool_names if name not in known]
            if unknown and strict:
                raise MCPBundleValidationError(
                    f"MCP server '{server_key}' has no tool named '{unknown[0]}'"
                )
            names = [name for name in dict.fromkeys(member.tool_names) if name in known]
        for tool_name in names:
            exposed = exposed_tool_name(member.name_prefix, tool_name)
            existing = catalog.get(exposed)
            if existing is not None:
                if strict:
                    raise MCPBundleValidationError(
                        f"Tool name '{exposed}' is used by '{existing.namespaced_name}' and "
                        f"'{server_key}.{tool_name}'. Set a name prefix to keep names unique."
                    )
                continue
            if exposed in RESERVED_TOOL_NAMES:
                if strict:
                    raise MCPBundleValidationError(
                        f"Tool name '{exposed}' is reserved. Set another name prefix."
                    )
                continue
            catalog[exposed] = BundleToolTarget(
                exposed_name=exposed,
                server_id=member.server_id,
                server_key=server_key,
                tool_name=tool_name,
            )
    return catalog


def visible_bundle_tools(
    catalog: Mapping[str, BundleToolTarget],
    visible_tools: Iterable[NamespacedTool],
) -> list[BundleTool]:
    """Keep the bundle tools that the caller can already see, in bundle order."""

    by_namespaced_name = {tool.namespaced_name: tool for tool in visible_tools}
    out: list[BundleTool] = []
    for target in catalog.values():
        tool = by_namespaced_name.get(target.namespaced_name)
        if tool is not None:
            out.append(BundleTool(exposed_name=target.exposed_name, tool=tool))
    return out


def bundle_visible_to_caller(bundle: MCPBundle, auth: UserAPIKeyAuth) -> bool:
    """A caller sees an enabled bundle when its owner scope contains the caller."""

    if not bundle.enabled:
        return False
    context = resolve_runtime_scope_context(auth)
    if context.is_master_key or bundle.owner_scope_type == "global":
        return True
    if bundle.owner_scope_type == "organization":
        return bool(bundle.owner_scope_id) and context.organization_id == bundle.owner_scope_id
    if bundle.owner_scope_type == "team":
        return bool(bundle.owner_scope_id) and context.team_id == bundle.owner_scope_id
    return False


def normalize_bundle_key(value: object) -> str:
    key = str(value or "").strip().lower()
    if not key:
        raise MCPBundleValidationError("bundle_key is required")
    if not BUNDLE_KEY_PATTERN.fullmatch(key):
        raise MCPBundleValidationError(
            "bundle_key must start with a letter or number and contain only lowercase "
            "letters, numbers, hyphens, and underscores (maximum 63 characters)"
        )
    return key


def normalize_name_prefix(value: object) -> str | None:
    if value is None:
        return None
    prefix = str(value).strip()
    if not prefix:
        return None
    if not NAME_PREFIX_PATTERN.fullmatch(prefix):
        raise MCPBundleValidationError(
            "name_prefix must start with a letter or number and contain only letters, "
            "numbers, hyphens, and underscores (maximum 32 characters)"
        )
    return prefix


def parse_bundle_members(value: object) -> tuple[MCPBundleMember, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise MCPBundleValidationError("members must be an array")
    if len(value) > MAX_BUNDLE_MEMBERS:
        raise MCPBundleValidationError(f"A bundle can have at most {MAX_BUNDLE_MEMBERS} servers")
    members: list[MCPBundleMember] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise MCPBundleValidationError("each member must be an object")
        server_id = str(item.get("mcp_server_id") or "").strip()
        if not server_id:
            raise MCPBundleValidationError("member mcp_server_id is required")
        if server_id in seen:
            raise MCPBundleValidationError("a server can be in a bundle one time only")
        seen.add(server_id)
        members.append(
            MCPBundleMember(
                server_id=server_id,
                tool_names=_parse_tool_names(item),
                name_prefix=normalize_name_prefix(item.get("name_prefix")),
            )
        )
    return tuple(members)


def _parse_tool_names(item: Mapping[str, object]) -> tuple[str, ...] | None:
    if item.get("all_tools") is True:
        return None
    raw = item.get("tool_names")
    if not isinstance(raw, list):
        raise MCPBundleValidationError("member tool_names must be an array, or set all_tools")
    names = tuple(dict.fromkeys(str(name).strip() for name in raw if str(name or "").strip()))
    if not names:
        raise MCPBundleValidationError("select at least one tool for each server, or set all_tools")
    if len(names) > MAX_MEMBER_TOOLS:
        raise MCPBundleValidationError(f"a member can list at most {MAX_MEMBER_TOOLS} tools")
    return names


__all__ = [
    "BUNDLE_OWNER_SCOPE_TYPES",
    "BundleTool",
    "BundleToolTarget",
    "MCPBundle",
    "MCPBundleMember",
    "MCPBundleValidationError",
    "build_bundle_catalog",
    "bundle_visible_to_caller",
    "exposed_tool_name",
    "normalize_bundle_key",
    "normalize_name_prefix",
    "parse_bundle_members",
    "visible_bundle_tools",
]
