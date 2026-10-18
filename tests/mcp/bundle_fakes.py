"""In-memory MCP bundle fixtures shared by the bundle tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from src.db.mcp.mcp import MCPServerBindingRecord, MCPServerRecord, MCPToolPolicyRecord
from src.db.mcp.mcp_bundles import (
    MCPBundleMemberRecord,
    MCPBundleOwnerFilter,
    MCPBundleRecord,
)
from src.mcp.bundles import MCPBundleMember
from src.mcp.capabilities import extract_tool_schemas
from src.mcp.gateway import MCPGatewayService
from src.mcp.governance import MCPGovernanceService
from src.mcp.models import MCPToolSchema
from src.models.responses import UserAPIKeyAuth
from src.services.access.runtime_scopes import annotate_auth_metadata
from tests.test_mcp_gateway import (
    _FakeGovernanceRepository,
    _FakeRegistry,
    _FakeTransport,
    _server,
)


def server(server_id: str, key: str, tools: list[str], **changes: Any) -> MCPServerRecord:
    record = _server(
        server_id,
        key,
        capabilities=[
            MCPToolSchema(
                name=name, description=f"{name} on {key}", input_schema={"type": "object"}
            )
            for name in tools
        ],
    )
    return replace(record, **changes) if changes else record


def binding(server_id: str, scope: tuple[str, str], tools: list[str] | None = None):  # noqa: ANN201
    return MCPServerBindingRecord(
        f"bind-{server_id}-{scope[1]}", server_id, scope[0], scope[1], True, tools
    )


def policy(  # noqa: ANN201
    server_id: str,
    tool: str,
    scope: tuple[str, str],
    *,
    enabled: bool = True,
    approval: str | None = None,
):
    return MCPToolPolicyRecord(
        f"pol-{server_id}-{tool}-{scope[1]}",
        server_id,
        tool,
        scope[0],
        scope[1],
        enabled,
        approval,
        None,
        None,
        None,
        None,
    )


def key_auth(
    *, team_id: str | None = "team-default", organization_id: str | None = "org-default"
) -> UserAPIKeyAuth:
    return annotate_auth_metadata(
        UserAPIKeyAuth(api_key="hashed-key", team_id=team_id, organization_id=organization_id),
        auth_source="api_key",
        api_key_scope_id="hashed-key",
    )


class FakeBundleRepository:
    """Stores bundles in memory and joins member servers like the SQL repository."""

    def __init__(self, servers: dict[str, MCPServerRecord] | None = None) -> None:
        self.servers = servers if servers is not None else {}
        self.bundles: dict[str, MCPBundleRecord] = {}
        self.team_orgs: dict[str, str] = {}
        self._counter = 0

    def add(
        self,
        key: str,
        members: list[tuple[str, list[str] | None, str | None]],
        *,
        owner: tuple[str, str | None] = ("global", None),
        enabled: bool = True,
    ) -> MCPBundleRecord:
        record = MCPBundleRecord(
            mcp_bundle_id=f"bundle-{key}",
            bundle_key=key,
            name=key.title(),
            owner_scope_type=owner[0],
            owner_scope_id=owner[1],
            enabled=enabled,
            members=[],
        )
        self.bundles[record.mcp_bundle_id] = record
        self._set_members(
            record,
            tuple(
                MCPBundleMember(
                    server_id=sid, tool_names=tuple(tools) if tools else None, name_prefix=prefix
                )
                for sid, tools, prefix in members
            ),
        )
        return record

    def _set_members(self, record: MCPBundleRecord, members: tuple[MCPBundleMember, ...]) -> None:
        out: list[MCPBundleMemberRecord] = []
        for position, member in enumerate(members):
            srv = self.servers.get(member.server_id)
            out.append(
                MCPBundleMemberRecord(
                    mcp_server_id=member.server_id,
                    position=position,
                    all_tools=member.tool_names is None,
                    tool_allowlist=list(member.tool_names or ()),
                    name_prefix=member.name_prefix,
                    server_key=srv.server_key if srv else "",
                    server_name=srv.name if srv else "",
                    server_owner_scope_type=srv.owner_scope_type if srv else "global",
                    server_owner_scope_id=srv.owner_scope_id if srv else None,
                    server_enabled=srv.enabled if srv else False,
                    server_tool_names=[
                        tool.name for tool in extract_tool_schemas(srv.capabilities_json)
                    ]
                    if srv
                    else [],
                )
            )
        record.members = out

    def _visible(self, record: MCPBundleRecord, owner_filter: MCPBundleOwnerFilter | None) -> bool:
        if owner_filter is None:
            return True
        if record.owner_scope_type == "organization":
            return record.owner_scope_id in owner_filter.organization_ids
        if record.owner_scope_type == "team":
            return record.owner_scope_id in owner_filter.team_ids or (
                self.team_orgs.get(str(record.owner_scope_id)) in owner_filter.organization_ids
            )
        return False

    async def list_bundles(self, *, search=None, owner_filter=None, limit=100, offset=0):  # noqa: ANN001, ANN201
        items = sorted(self.bundles.values(), key=lambda item: item.bundle_key)
        if search:
            items = [
                item for item in items if search.lower() in item.bundle_key + item.name.lower()
            ]
        items = [item for item in items if self._visible(item, owner_filter)]
        return items[offset : offset + limit], len(items)

    async def get_bundle(self, bundle_id: str):  # noqa: ANN201
        return self.bundles.get(bundle_id)

    async def get_bundle_by_key(self, bundle_key: str):  # noqa: ANN201
        return next((item for item in self.bundles.values() if item.bundle_key == bundle_key), None)

    async def create_bundle(self, *, members, **kwargs):  # noqa: ANN001, ANN003, ANN201
        self._counter += 1
        now = datetime.now(tz=UTC)
        record = MCPBundleRecord(
            mcp_bundle_id=f"created-{self._counter}",
            bundle_key=kwargs["bundle_key"],
            name=kwargs["name"],
            description=kwargs["description"],
            owner_scope_type=kwargs["owner_scope_type"],
            owner_scope_id=kwargs["owner_scope_id"],
            enabled=kwargs["enabled"],
            created_by_account_id=kwargs["created_by_account_id"],
            created_at=now,
            updated_at=now,
        )
        self._set_members(record, members)
        self.bundles[record.mcp_bundle_id] = record
        return record

    async def update_bundle(self, bundle_id, *, members, name, description, enabled, metadata):  # noqa: ANN001, ANN201
        existing = self.bundles.get(bundle_id)
        if existing is None:
            return None
        record = replace(existing, name=name, description=description, enabled=enabled)
        self._set_members(record, members)
        self.bundles[bundle_id] = record
        return record

    async def delete_bundle(self, bundle_id: str) -> bool:
        return self.bundles.pop(bundle_id, None) is not None


async def bundle_gateway(
    servers: list[MCPServerRecord],
    bindings: list[MCPServerBindingRecord],
    policies: list[MCPToolPolicyRecord],
    bundles: FakeBundleRepository,
) -> tuple[MCPGatewayService, _FakeTransport]:
    governance = MCPGovernanceService(
        _FakeGovernanceRepository(servers, bindings, policies),  # type: ignore[arg-type]
        bundle_repository=bundles,  # type: ignore[arg-type]
    )
    await governance.reload()
    transport = _FakeTransport()
    gateway = MCPGatewayService(
        _FakeRegistry(servers, bindings, policies),  # type: ignore[arg-type]
        transport,  # type: ignore[arg-type]
        governance_service=governance,
    )
    return gateway, transport


ORG = ("organization", "org-default")

GITHUB = server(
    "srv-github",
    "github",
    ["create_issue", "list_pull_requests", "delete_branch", "merge_pull_request"],
)
JIRA = server("srv-jira", "jira", ["search", "create_issue"])
DOCS = server("srv-docs", "docs", ["search"])


async def engineering_gateway(
    *, owner: tuple[str, str | None] = ("global", None)
) -> tuple[MCPGatewayService, _FakeTransport, FakeBundleRepository]:
    """github + jira in the `eng` bundle. The key cannot use two github tools."""

    servers = [GITHUB, JIRA, DOCS]
    bundles = FakeBundleRepository({item.mcp_server_id: item for item in servers})
    bundles.add(
        "eng",
        [("srv-github", None, "gh"), ("srv-jira", ["search", "create_issue"], "jira")],
        owner=owner,
    )
    gateway, transport = await bundle_gateway(
        servers,
        [
            # merge_pull_request is not in the binding.
            binding("srv-github", ORG, ["create_issue", "list_pull_requests", "delete_branch"]),
            binding("srv-jira", ORG),
            binding("srv-docs", ORG),
        ],
        [
            policy("srv-github", "delete_branch", ORG, enabled=False),
            policy("srv-jira", "create_issue", ORG, approval="manual"),
        ],
        bundles,
    )
    return gateway, transport, bundles
