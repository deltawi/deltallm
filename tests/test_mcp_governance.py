from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.mcp import MCPServerBindingRecord, MCPServerRecord, MCPToolPolicyRecord
from src.db.mcp_scope_policies import MCPScopePolicyRecord
from src.mcp.governance import MCPGovernanceService


def _server(server_id: str, server_key: str, *, enabled: bool = True) -> MCPServerRecord:
    return MCPServerRecord(
        mcp_server_id=server_id,
        server_key=server_key,
        name=server_key,
        transport="streamable_http",
        base_url=f"https://{server_key}.example.com/mcp",
        enabled=enabled,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )


class _FakeMCPRepository:
    def __init__(
        self,
        *,
        servers: list[MCPServerRecord],
        bindings: list[MCPServerBindingRecord],
        policies: list[MCPToolPolicyRecord],
        scope_policies: list[MCPScopePolicyRecord] | None = None,
    ) -> None:
        self.servers = list(servers)
        self.bindings = list(bindings)
        self.policies = list(policies)
        self.scope_policies = list(scope_policies or [])

    async def list_servers(self, *, search=None, enabled=None, limit=100, offset=0):  # noqa: ANN001, ANN201
        items = list(self.servers)
        if enabled is not None:
            items = [item for item in items if item.enabled is enabled]
        return items[offset : offset + limit], len(items)

    async def list_bindings(self, *, server_id=None, scope_type=None, scope_id=None, enabled=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if server_id is not None:
            items = [item for item in items if item.mcp_server_id == server_id]
        if scope_type is not None:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id is not None:
            items = [item for item in items if item.scope_id == scope_id]
        if enabled is not None:
            items = [item for item in items if item.enabled is enabled]
        return items[offset : offset + limit], len(items)

    async def list_tool_policies(self, *, server_id=None, scope_type=None, scope_id=None, enabled=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.policies)
        if server_id is not None:
            items = [item for item in items if item.mcp_server_id == server_id]
        if scope_type is not None:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id is not None:
            items = [item for item in items if item.scope_id == scope_id]
        if enabled is not None:
            items = [item for item in items if item.enabled is enabled]
        return items[offset : offset + limit], len(items)

    async def list_policies(self, *, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.scope_policies)
        if scope_type is not None:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id is not None:
            items = [item for item in items if item.scope_id == scope_id]
        return items[offset : offset + limit], len(items)


@pytest.mark.asyncio
async def test_mcp_governance_service_filters_disabled_servers_and_bindings() -> None:
    service = MCPGovernanceService(
        _FakeMCPRepository(
            servers=[
                _server("srv-enabled", "docs", enabled=True),
                _server("srv-disabled", "hidden", enabled=False),
            ],
            bindings=[
                MCPServerBindingRecord("bind-enabled", "srv-enabled", "team", "team-1", True, ["search"]),
                MCPServerBindingRecord("bind-disabled", "srv-enabled", "team", "team-2", False, ["search"]),
                MCPServerBindingRecord("bind-hidden", "srv-disabled", "team", "team-1", True, ["secret"]),
            ],
            policies=[
                MCPToolPolicyRecord("policy-enabled", "srv-enabled", "search", "team", "team-1", False, None, None, None, None),
                MCPToolPolicyRecord("policy-hidden", "srv-disabled", "secret", "team", "team-1", True, None, None, None, None),
            ],
        )
    )

    await service.reload()

    assert [server.server_key for server in service.list_enabled_servers()] == ["docs"]
    assert service.get_server("srv-enabled") is not None
    assert service.get_server("srv-disabled") is None
    assert [binding.mcp_binding_id for binding in service.list_effective_bindings(scopes=[("team", "team-1")])] == ["bind-enabled"]
    assert service.list_effective_bindings(scopes=[("team", "team-2")]) == []
    assert [policy.mcp_tool_policy_id for policy in service.list_effective_tool_policies(scopes=[("team", "team-1")])] == ["policy-enabled"]


@pytest.mark.asyncio
async def test_mcp_governance_service_filters_tool_policies_by_server() -> None:
    service = MCPGovernanceService(
        _FakeMCPRepository(
            servers=[
                _server("srv-docs", "docs", enabled=True),
                _server("srv-github", "github", enabled=True),
            ],
            bindings=[],
            policies=[
                MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-1", True, None, None, None, None),
                MCPToolPolicyRecord("policy-2", "srv-github", "search", "team", "team-1", True, None, None, None, None),
            ],
        )
    )

    await service.reload()

    assert [policy.mcp_tool_policy_id for policy in service.list_effective_tool_policies(scopes=[("team", "team-1")], server_id="srv-github")] == ["policy-2"]


@pytest.mark.asyncio
async def test_mcp_governance_service_resolves_org_ceiling_with_team_restrict() -> None:
    service = MCPGovernanceService(
        _FakeMCPRepository(
            servers=[
                _server("srv-docs", "docs", enabled=True),
                _server("srv-github", "github", enabled=True),
            ],
            bindings=[
                MCPServerBindingRecord("bind-org-docs", "srv-docs", "organization", "org-1", True, None),
                MCPServerBindingRecord("bind-org-github", "srv-github", "organization", "org-1", True, None),
                MCPServerBindingRecord("bind-team-docs", "srv-docs", "team", "team-1", True, ["search"]),
            ],
            policies=[],
            scope_policies=[
                MCPScopePolicyRecord(
                    mcp_scope_policy_id="mcp-policy-team-1",
                    scope_type="team",
                    scope_id="team-1",
                    mode="restrict",
                )
            ],
        ),
        policy_repository=_FakeMCPRepository(servers=[], bindings=[], policies=[], scope_policies=[
            MCPScopePolicyRecord(
                mcp_scope_policy_id="mcp-policy-team-1",
                scope_type="team",
                scope_id="team-1",
                mode="restrict",
            )
        ]),
    )

    await service.reload()

    from src.models.responses import UserAPIKeyAuth
    from src.services.runtime_scopes import annotate_auth_metadata

    auth = annotate_auth_metadata(
        UserAPIKeyAuth(api_key="key-1", team_id="team-1", organization_id="org-1"),
        auth_source="api_key",
        api_key_scope_id="key-1",
    )
    resolutions = service.resolve_binding_resolutions(auth)

    assert [(item.server_id, item.scope_type, item.allowed_tool_names) for item in resolutions] == [
        ("srv-docs", "team", ("search",)),
    ]
