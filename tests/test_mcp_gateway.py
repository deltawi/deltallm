from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from prometheus_client import generate_latest

from src.cache import InMemoryBackend
from src.db.mcp import MCPApprovalRequestRecord
from src.db.mcp import MCPServerBindingRecord, MCPServerRecord, MCPToolPolicyRecord
from src.db.mcp_scope_policies import MCPScopePolicyRecord
from src.metrics import get_prometheus_registry
from src.mcp.approvals import MCPApprovalService
from src.mcp.capabilities import namespace_tools
from src.mcp.exceptions import MCPApprovalDeniedError, MCPApprovalRequiredError, MCPRateLimitError, MCPToolTimeoutError
from src.mcp.gateway import MCPGatewayService
from src.mcp.governance import MCPGovernanceService
from src.mcp.models import MCPToolCallResult, MCPToolSchema
from src.mcp.policy import MCPToolPolicyEnforcer
from src.mcp.result_cache import MCPToolResultCache
from src.models.responses import UserAPIKeyAuth
from src.services.limit_counter import LimitCounter
from src.services.runtime_scopes import annotate_auth_metadata


def _server(
    server_id: str,
    server_key: str,
    *,
    capabilities: list[MCPToolSchema],
    forwarded_headers_allowlist: list[str] | None = None,
) -> MCPServerRecord:
    return MCPServerRecord(
        mcp_server_id=server_id,
        server_key=server_key,
        name=server_key.title(),
        transport="streamable_http",
        base_url=f"https://{server_key}.example.com/mcp",
        enabled=True,
        forwarded_headers_allowlist=list(forwarded_headers_allowlist or []),
        capabilities_json={
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                    "annotations": tool.annotations,
                }
                for tool in capabilities
            ]
        },
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )


class _FakeRegistry:
    def __init__(self, servers: list[MCPServerRecord], bindings: list[MCPServerBindingRecord], policies: list[MCPToolPolicyRecord]) -> None:
        self.servers = {server.mcp_server_id: server for server in servers}
        self.bindings = list(bindings)
        self.policies = list(policies)

    async def list_servers(self, *, search=None, enabled=None, limit=100, offset=0):  # noqa: ANN001, ANN201
        items = list(self.servers.values())
        if search:
            query = str(search).lower()
            items = [item for item in items if query in item.server_key.lower() or query in item.name.lower()]
        if enabled is not None:
            items = [item for item in items if item.enabled is enabled]
        return items[offset : offset + limit], len(items)

    async def get_server(self, server_id: str):  # noqa: ANN201
        return self.servers.get(server_id)

    async def list_effective_bindings(self, *, scopes):  # noqa: ANN001, ANN201
        scope_set = {(scope_type, scope_id) for scope_type, scope_id in scopes}
        return [binding for binding in self.bindings if binding.enabled and (binding.scope_type, binding.scope_id) in scope_set]

    async def list_effective_tool_policies(self, *, scopes, server_id=None):  # noqa: ANN001, ANN201
        scope_set = {(scope_type, scope_id) for scope_type, scope_id in scopes}
        policies = [policy for policy in self.policies if (policy.scope_type, policy.scope_id) in scope_set]
        if server_id:
            policies = [policy for policy in policies if policy.mcp_server_id == server_id]
        return policies

    async def list_namespaced_tools(self, server: MCPServerRecord):  # noqa: ANN201
        schemas = [
            MCPToolSchema(
                name=str(item.get("name") or ""),
                description=str(item.get("description")) if item.get("description") is not None else None,
                input_schema=item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {},
                annotations=item.get("annotations") if isinstance(item.get("annotations"), dict) else {},
            )
            for item in (server.capabilities_json or {}).get("tools", [])
            if isinstance(item, dict)
        ]
        return namespace_tools(server.server_key, schemas)


class _FakeGovernanceRepository:
    def __init__(
        self,
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


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str] | None, dict[str, object]]] = []

    async def call_tool(self, server, *, tool_name, arguments=None, request_headers=None):  # noqa: ANN001, ANN201
        self.calls.append((server.server_key, tool_name, request_headers, arguments or {}))
        return MCPToolCallResult(
            content=[{"type": "text", "text": f"{server.server_key}:{tool_name}"}],
            structured_content={"server": server.server_key, "tool": tool_name, "arguments": arguments or {}},
            is_error=False,
        )


class _FakeApprovalRepository:
    def __init__(self) -> None:
        self.records_by_fingerprint: dict[str, list[MCPApprovalRequestRecord]] = {}
        self.create_calls = 0

    async def find_pending_approval_request(self, *, request_fingerprint: str):  # noqa: ANN201
        records = self.records_by_fingerprint.get(request_fingerprint, [])
        for record in reversed(records):
            if record.status == "pending":
                return record
        return None

    async def expire_stale_approval_requests(self, *, request_fingerprint: str):  # noqa: ANN201
        now = datetime.now(tz=UTC)
        updated = 0
        for record in self.records_by_fingerprint.get(request_fingerprint, []):
            if record.status in {"pending", "approved", "rejected"} and record.expires_at is not None and record.expires_at <= now:
                record.status = "expired"
                updated += 1
        return updated

    async def find_active_approval_request(self, *, request_fingerprint: str):  # noqa: ANN201
        records = self.records_by_fingerprint.get(request_fingerprint, [])
        for record in reversed(records):
            if record.status in {"pending", "approved", "rejected"}:
                return record
        return None

    async def create_approval_request(self, **kwargs):  # noqa: ANN003, ANN201
        self.create_calls += 1
        record = MCPApprovalRequestRecord(
            mcp_approval_request_id=f"approval-{self.create_calls}",
            mcp_server_id=kwargs["server_id"],
            tool_name=kwargs["tool_name"],
            scope_type=kwargs["scope_type"],
            scope_id=kwargs["scope_id"],
            status="pending",
            request_fingerprint=kwargs["request_fingerprint"],
            requested_by_api_key=kwargs.get("requested_by_api_key"),
            requested_by_user=kwargs.get("requested_by_user"),
            organization_id=kwargs.get("organization_id"),
            request_id=kwargs.get("request_id"),
            correlation_id=kwargs.get("correlation_id"),
            arguments_json=kwargs.get("arguments_json"),
            expires_at=kwargs.get("expires_at"),
            metadata=kwargs.get("metadata"),
        )
        self.records_by_fingerprint.setdefault(record.request_fingerprint, []).append(record)
        return record


class _BlockingTransport(_FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def call_tool(self, server, *, tool_name, arguments=None, request_headers=None):  # noqa: ANN001, ANN201
        self.calls.append((server.server_key, tool_name, request_headers, arguments or {}))
        self.started.set()
        await self.release.wait()
        return MCPToolCallResult(
            content=[{"type": "text", "text": f"{server.server_key}:{tool_name}"}],
            structured_content={"server": server.server_key, "tool": tool_name, "arguments": arguments or {}},
            is_error=False,
        )


class _SleepingTransport(_FakeTransport):
    def __init__(self, delay_seconds: float) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds

    async def call_tool(self, server, *, tool_name, arguments=None, request_headers=None):  # noqa: ANN001, ANN201
        self.calls.append((server.server_key, tool_name, request_headers, arguments or {}))
        await asyncio.sleep(self.delay_seconds)
        return MCPToolCallResult(
            content=[{"type": "text", "text": f"{server.server_key}:{tool_name}"}],
            structured_content={"server": server.server_key, "tool": tool_name, "arguments": arguments or {}},
            is_error=False,
        )


@pytest.mark.asyncio
async def test_mcp_gateway_initialize_and_tools_list(client, test_app):
    key_record = next(iter(test_app.state._test_repo.records.values()))
    key_record.team_id = "team-ops"
    key_record.organization_id = "org-acme"
    key_record.rpm_limit = 100
    key_record.tpm_limit = 100000
    key_record.expires = datetime.now(tz=UTC) + timedelta(hours=1)

    docs_server = _server(
        "srv-docs",
        "docs",
        capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
    )
    github_server = _server(
        "srv-github",
        "github",
        capabilities=[MCPToolSchema(name="search", description="Search code", input_schema={"type": "object"})],
    )
    hidden_server = _server(
        "srv-hidden",
        "hidden",
        capabilities=[MCPToolSchema(name="secret", description="Secret tool", input_schema={"type": "object"})],
    )
    registry = _FakeRegistry(
        servers=[docs_server, github_server, hidden_server],
        bindings=[
            MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None),
            MCPServerBindingRecord("bind-2", "srv-github", "organization", "org-acme", True, None),
            MCPServerBindingRecord("bind-3", "srv-hidden", "team", "team-other", True, None),
        ],
        policies=[],
    )
    transport = _FakeTransport()
    test_app.state.mcp_gateway_service = MCPGatewayService(registry, transport)  # type: ignore[arg-type]

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}

    initialize = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialize.status_code == 200
    assert initialize.json()["result"]["serverInfo"]["name"] == "DeltaLLM MCP Gateway"

    tool_list = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert tool_list.status_code == 200
    names = [tool["name"] for tool in tool_list.json()["result"]["tools"]]
    assert names == ["docs.search", "github.search"]


@pytest.mark.asyncio
async def test_mcp_gateway_tools_call_routes_to_namespaced_upstream_tool(client, test_app):
    key_record = next(iter(test_app.state._test_repo.records.values()))
    key_record.team_id = "team-ops"
    key_record.organization_id = "org-acme"
    key_record.rpm_limit = 100
    key_record.tpm_limit = 100000

    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, ["search"])],
        policies=[],
    )
    transport = _FakeTransport()
    test_app.state.mcp_gateway_service = MCPGatewayService(registry, transport)  # type: ignore[arg-type]

    headers = {
        "Authorization": f"Bearer {test_app.state._test_key}",
        "x-deltallm-mcp-docs-authorization": "Bearer forwarded-token",
    }

    response = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "docs.search", "arguments": {"query": "hello"}},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["server"] == "docs"
    assert transport.calls[0][0] == "docs"
    assert transport.calls[0][1] == "search"
    assert transport.calls[0][3] == {"query": "hello"}


@pytest.mark.asyncio
async def test_mcp_gateway_denies_policy_disabled_tool(client, test_app):
    key_record = next(iter(test_app.state._test_repo.records.values()))
    key_record.team_id = "team-ops"
    key_record.organization_id = "org-acme"
    key_record.rpm_limit = 100
    key_record.tpm_limit = 100000

    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-ops", False, None, None, None, None)],
    )
    transport = _FakeTransport()
    test_app.state.mcp_gateway_service = MCPGatewayService(registry, transport)  # type: ignore[arg-type]

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    tool_list = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}})
    assert tool_list.status_code == 200
    assert tool_list.json()["result"]["tools"] == []

    response = await client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "docs.search", "arguments": {}}},
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32004


@pytest.mark.asyncio
async def test_gateway_service_enforces_policy_execution_timeout() -> None:
    auth = type("Auth", (), {"api_key": "sk-test", "team_id": "team-ops", "organization_id": "org-acme", "metadata": {}})()
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, ["search"])],
        policies=[
            MCPToolPolicyRecord(
                "policy-timeout",
                "srv-docs",
                "search",
                "team",
                "team-ops",
                True,
                metadata={"max_total_mcp_execution_time_ms": 10},
            )
        ],
    )
    gateway = MCPGatewayService(registry, _SleepingTransport(0.05))  # type: ignore[arg-type]

    with pytest.raises(MCPToolTimeoutError):
        await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "delta"})


@pytest.mark.asyncio
async def test_gateway_service_jwt_auth_does_not_use_pseudo_api_key_scope() -> None:
    auth = annotate_auth_metadata(
        UserAPIKeyAuth(
            api_key="jwt:user-1",
            user_id="user-1",
            team_id="team-ops",
            organization_id="org-acme",
        ),
        auth_source="jwt",
    )
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            ),
            _server(
                "srv-team",
                "teamdocs",
                capabilities=[MCPToolSchema(name="search", description="Search team docs", input_schema={"type": "object"})],
            ),
        ],
        bindings=[
            MCPServerBindingRecord("bind-1", "srv-docs", "api_key", "jwt:user-1", True, None),
            MCPServerBindingRecord("bind-2", "srv-team", "team", "team-ops", True, None),
        ],
        policies=[],
    )

    gateway = MCPGatewayService(registry, _FakeTransport())  # type: ignore[arg-type]
    visible = await gateway.list_visible_servers(auth)

    assert [item.server.server_key for item in visible] == ["teamdocs"]


@pytest.mark.asyncio
async def test_gateway_service_user_scope_affects_mcp_visibility_and_policy_resolution() -> None:
    auth = annotate_auth_metadata(
        UserAPIKeyAuth(
            api_key="hashed-key",
            user_id="user-1",
            team_id="team-ops",
            organization_id="org-acme",
        ),
        auth_source="api_key",
        api_key_scope_id="hashed-key",
    )
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-user",
                "userdocs",
                capabilities=[MCPToolSchema(name="search", description="Search user docs", input_schema={"type": "object"})],
            ),
            _server(
                "srv-team",
                "teamdocs",
                capabilities=[MCPToolSchema(name="search", description="Search team docs", input_schema={"type": "object"})],
            ),
        ],
        bindings=[
            MCPServerBindingRecord("bind-user", "srv-user", "user", "user-1", True, None),
            MCPServerBindingRecord("bind-team", "srv-team", "team", "team-ops", True, None),
        ],
        policies=[
            MCPToolPolicyRecord("policy-team", "srv-team", "search", "team", "team-ops", True, "never", None, None, None, None),
            MCPToolPolicyRecord("policy-user", "srv-team", "search", "user", "user-1", False, "never", None, None, None, None),
        ],
    )

    gateway = MCPGatewayService(registry, _FakeTransport())  # type: ignore[arg-type]
    visible = await gateway.list_visible_servers(auth)

    assert [item.server.server_key for item in visible] == ["userdocs"]


@pytest.mark.asyncio
async def test_gateway_service_uses_governance_snapshot_instead_of_registry_binding_queries() -> None:
    auth = annotate_auth_metadata(
        UserAPIKeyAuth(
            api_key="hashed-key",
            team_id="team-ops",
            organization_id="org-acme",
        ),
        auth_source="api_key",
        api_key_scope_id="hashed-key",
    )
    servers = [
        _server(
            "srv-docs",
            "docs",
            capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
        )
    ]
    bindings = [
        MCPServerBindingRecord("bind-org", "srv-docs", "organization", "org-acme", True, None),
        MCPServerBindingRecord("bind-team", "srv-docs", "team", "team-ops", True, ["search"]),
    ]
    policies = [MCPToolPolicyRecord("policy-team", "srv-docs", "search", "team", "team-ops", True, None, 1, None, None, None)]
    governance = MCPGovernanceService(_FakeGovernanceRepository(servers, bindings, policies))  # type: ignore[arg-type]
    await governance.reload()

    class _RegistryWithoutBindingReads(_FakeRegistry):
        async def list_effective_bindings(self, *, scopes):  # noqa: ANN001, ANN201
            raise AssertionError("gateway should use governance snapshot bindings")

        async def list_effective_tool_policies(self, *, scopes, server_id=None):  # noqa: ANN001, ANN201
            raise AssertionError("gateway should use governance snapshot policies")

        async def get_server(self, server_id: str):  # noqa: ANN201
            raise AssertionError("gateway should use governance snapshot servers")

    gateway = MCPGatewayService(
        _RegistryWithoutBindingReads(servers=servers, bindings=bindings, policies=policies),
        _FakeTransport(),
        governance_service=governance,
        policy_enforcer=MCPToolPolicyEnforcer(LimitCounter(redis_client=None)),
    )  # type: ignore[arg-type]

    visible = await gateway.list_visible_servers(auth)
    assert [item.server.server_key for item in visible] == ["docs"]

    await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})
    with pytest.raises(MCPRateLimitError):
        await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "again"})


@pytest.mark.asyncio
async def test_gateway_service_governance_respects_org_ceiling_and_team_restrict() -> None:
    auth = annotate_auth_metadata(
        UserAPIKeyAuth(
            api_key="hashed-key",
            team_id="team-ops",
            organization_id="org-acme",
        ),
        auth_source="api_key",
        api_key_scope_id="hashed-key",
    )
    servers = [
        _server(
            "srv-docs",
            "docs",
            capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
        ),
        _server(
            "srv-github",
            "github",
            capabilities=[MCPToolSchema(name="search", description="Search code", input_schema={"type": "object"})],
        ),
    ]
    bindings = [
        MCPServerBindingRecord("bind-org-docs", "srv-docs", "organization", "org-acme", True, None),
        MCPServerBindingRecord("bind-org-github", "srv-github", "organization", "org-acme", True, None),
        MCPServerBindingRecord("bind-team-docs", "srv-docs", "team", "team-ops", True, ["search"]),
    ]
    scope_policies = [
        MCPScopePolicyRecord(
            mcp_scope_policy_id="mcp-scope-team",
            scope_type="team",
            scope_id="team-ops",
            mode="restrict",
        )
    ]
    governance_repository = _FakeGovernanceRepository(servers, bindings, [], scope_policies)
    governance = MCPGovernanceService(governance_repository, policy_repository=governance_repository)  # type: ignore[arg-type]
    await governance.reload()

    gateway = MCPGatewayService(
        _FakeRegistry(servers=servers, bindings=bindings, policies=[]),
        _FakeTransport(),
        governance_service=governance,
    )  # type: ignore[arg-type]

    visible = await gateway.list_visible_servers(auth)

    assert [(item.server.server_key, item.binding.scope_type, item.tool_names) for item in visible] == [
        ("docs", "team", ("search",)),
    ]


@pytest.mark.asyncio
async def test_mcp_gateway_emits_metrics(client, test_app):
    key_record = next(iter(test_app.state._test_repo.records.values()))
    key_record.team_id = "team-ops"
    key_record.organization_id = "org-acme"
    key_record.rpm_limit = 100
    key_record.tpm_limit = 100000

    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[],
    )
    transport = _FakeTransport()
    test_app.state.mcp_gateway_service = MCPGatewayService(registry, transport)  # type: ignore[arg-type]

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    list_response = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}})
    assert list_response.status_code == 200

    call_response = await client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "docs.search", "arguments": {"query": "hello"}}},
    )
    assert call_response.status_code == 200

    metrics_text = generate_latest(get_prometheus_registry()).decode("utf-8")
    assert 'deltallm_mcp_tools_list_total' in metrics_text
    assert 'deltallm_mcp_tool_call_total' in metrics_text
    assert 'server_key="docs"' in metrics_text


@pytest.mark.asyncio
async def test_mcp_gateway_enforces_tool_rpm_limit() -> None:
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-ops", True, None, 1, None, None, None)],
    )
    gateway = MCPGatewayService(
        registry,
        _FakeTransport(),
        MCPToolPolicyEnforcer(LimitCounter(redis_client=None)),
    )  # type: ignore[arg-type]
    auth = type("Auth", (), {"api_key": "sk-test", "team_id": "team-ops", "organization_id": None, "metadata": None})()

    first = await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})
    assert first.structured_content["tool"] == "search"

    with pytest.raises(MCPRateLimitError, match="Rate limit exceeded"):
        await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})


@pytest.mark.asyncio
async def test_mcp_gateway_enforces_tool_concurrency_limit() -> None:
    transport = _BlockingTransport()
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[
            MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-ops", True, None, None, 1, None, None)
        ],
    )
    gateway = MCPGatewayService(
        registry,
        transport,
        MCPToolPolicyEnforcer(LimitCounter(redis_client=None)),
    )  # type: ignore[arg-type]
    auth = type("Auth", (), {"api_key": "sk-test", "team_id": "team-ops", "organization_id": None, "metadata": None})()

    first_task = asyncio.create_task(gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "a"}))
    await transport.started.wait()

    with pytest.raises(MCPRateLimitError, match="Parallel request limit exceeded"):
        await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "b"})

    transport.release.set()
    result = await first_task
    assert result.structured_content["tool"] == "search"


@pytest.mark.asyncio
async def test_mcp_gateway_uses_most_specific_policy() -> None:
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[
            MCPToolPolicyRecord("policy-org", "srv-docs", "search", "organization", "org-acme", True, None, 100, None, None, None),
            MCPToolPolicyRecord("policy-team", "srv-docs", "search", "team", "team-ops", True, None, 1, None, None, None),
        ],
    )
    gateway = MCPGatewayService(
        registry,
        _FakeTransport(),
        MCPToolPolicyEnforcer(LimitCounter(redis_client=None)),
    )  # type: ignore[arg-type]
    auth = type("Auth", (), {"api_key": "sk-test", "team_id": "team-ops", "organization_id": "org-acme", "metadata": None})()

    await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})
    with pytest.raises(MCPRateLimitError):
        await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "again"})


@pytest.mark.asyncio
async def test_mcp_gateway_caches_tool_result_when_policy_ttl_is_set() -> None:
    transport = _FakeTransport()
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[
            MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-ops", True, None, None, None, 60, None)
        ],
    )
    gateway = MCPGatewayService(
        registry,
        transport,
        MCPToolPolicyEnforcer(LimitCounter(redis_client=None)),
        MCPToolResultCache(InMemoryBackend()),
    )  # type: ignore[arg-type]
    auth = type("Auth", (), {"api_key": "sk-test", "team_id": "team-ops", "organization_id": None, "metadata": None})()

    first = await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})
    second = await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})

    assert first.structured_content["tool"] == "search"
    assert second.structured_content["tool"] == "search"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_mcp_gateway_cache_key_includes_forwarded_headers() -> None:
    transport = _FakeTransport()
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
                forwarded_headers_allowlist=["authorization"],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[
            MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-ops", True, None, None, None, 60, None)
        ],
    )
    gateway = MCPGatewayService(
        registry,
        transport,
        MCPToolPolicyEnforcer(LimitCounter(redis_client=None)),
        MCPToolResultCache(InMemoryBackend()),
    )  # type: ignore[arg-type]
    auth = type("Auth", (), {"api_key": "sk-test", "team_id": "team-ops", "organization_id": None, "metadata": None})()

    await gateway.call_tool(
        auth,
        namespaced_tool_name="docs.search",
        arguments={"query": "hello"},
        request_headers={"x-deltallm-mcp-docs-authorization": "Bearer one"},
    )
    await gateway.call_tool(
        auth,
        namespaced_tool_name="docs.search",
        arguments={"query": "hello"},
        request_headers={"x-deltallm-mcp-docs-authorization": "Bearer two"},
    )

    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_mcp_gateway_manual_approval_creates_pending_request() -> None:
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[
            MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-ops", True, "manual", None, None, None, None)
        ],
    )
    approval_repo = _FakeApprovalRepository()
    gateway = MCPGatewayService(
        registry,
        _FakeTransport(),
        MCPToolPolicyEnforcer(LimitCounter(redis_client=None)),
        approval_service=MCPApprovalService(approval_repo),  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    auth = type("Auth", (), {"api_key": "sk-test", "team_id": "team-ops", "organization_id": None, "metadata": None})()

    with pytest.raises(MCPApprovalRequiredError) as exc:
        await gateway.call_tool(
            auth,
            namespaced_tool_name="docs.search",
            arguments={"query": "hello"},
            request_id="req-approval-1",
            correlation_id="req-approval-1",
        )

    assert exc.value.approval_request_id == "approval-1"
    assert approval_repo.create_calls == 1
    metrics_text = generate_latest(get_prometheus_registry()).decode("utf-8")
    assert "deltallm_mcp_approval_request_total" in metrics_text


@pytest.mark.asyncio
async def test_mcp_gateway_manual_approval_approved_retry_executes() -> None:
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[
            MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-ops", True, "manual", None, None, None, None)
        ],
    )
    approval_repo = _FakeApprovalRepository()
    transport = _FakeTransport()
    approval_service = MCPApprovalService(approval_repo)
    gateway = MCPGatewayService(
        registry,
        transport,
        MCPToolPolicyEnforcer(LimitCounter(redis_client=None)),
        approval_service=approval_service,  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    auth = type("Auth", (), {"api_key": "sk-test", "team_id": "team-ops", "organization_id": "org-acme", "metadata": None})()

    with pytest.raises(MCPApprovalRequiredError):
        await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})

    fingerprint = next(iter(approval_repo.records_by_fingerprint))
    approval_repo.records_by_fingerprint[fingerprint][-1].status = "approved"
    approval_repo.records_by_fingerprint[fingerprint][-1].expires_at = datetime.now(tz=UTC) + timedelta(minutes=5)

    result = await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})

    assert result.structured_content["tool"] == "search"
    assert transport.calls == [("docs", "search", None, {"query": "hello"})]


@pytest.mark.asyncio
async def test_mcp_gateway_manual_approval_rejected_retry_denies() -> None:
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[
            MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-ops", True, "manual", None, None, None, None)
        ],
    )
    approval_repo = _FakeApprovalRepository()
    gateway = MCPGatewayService(
        registry,
        _FakeTransport(),
        MCPToolPolicyEnforcer(LimitCounter(redis_client=None)),
        approval_service=MCPApprovalService(approval_repo),  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    auth = type("Auth", (), {"api_key": "sk-test", "team_id": "team-ops", "organization_id": "org-acme", "metadata": None})()

    with pytest.raises(MCPApprovalRequiredError):
        await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})

    fingerprint = next(iter(approval_repo.records_by_fingerprint))
    approval_repo.records_by_fingerprint[fingerprint][-1].status = "rejected"
    approval_repo.records_by_fingerprint[fingerprint][-1].expires_at = datetime.now(tz=UTC) + timedelta(minutes=5)

    with pytest.raises(MCPApprovalDeniedError):
        await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})


@pytest.mark.asyncio
async def test_mcp_gateway_manual_approval_expired_creates_new_request() -> None:
    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[
            MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-ops", True, "manual", None, None, None, None)
        ],
    )
    approval_repo = _FakeApprovalRepository()
    approval_service = MCPApprovalService(approval_repo, pending_ttl=timedelta(minutes=1))
    gateway = MCPGatewayService(
        registry,
        _FakeTransport(),
        MCPToolPolicyEnforcer(LimitCounter(redis_client=None)),
        approval_service=approval_service,  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    auth = type("Auth", (), {"api_key": "sk-test", "team_id": "team-ops", "organization_id": "org-acme", "metadata": None})()

    with pytest.raises(MCPApprovalRequiredError):
        await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})

    fingerprint = next(iter(approval_repo.records_by_fingerprint))
    approval_repo.records_by_fingerprint[fingerprint][-1].status = "expired"
    approval_repo.records_by_fingerprint[fingerprint][-1].expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)

    with pytest.raises(MCPApprovalRequiredError) as exc:
        await gateway.call_tool(auth, namespaced_tool_name="docs.search", arguments={"query": "hello"})

    assert exc.value.approval_request_id == "approval-2"
    assert approval_repo.create_calls == 2


@pytest.mark.asyncio
async def test_mcp_jsonrpc_manual_approval_returns_request_id(client, test_app):
    key_record = next(iter(test_app.state._test_repo.records.values()))
    key_record.team_id = "team-ops"
    key_record.organization_id = "org-acme"
    key_record.rpm_limit = 100
    key_record.tpm_limit = 100000

    registry = _FakeRegistry(
        servers=[
            _server(
                "srv-docs",
                "docs",
                capabilities=[MCPToolSchema(name="search", description="Search docs", input_schema={"type": "object"})],
            )
        ],
        bindings=[MCPServerBindingRecord("bind-1", "srv-docs", "team", "team-ops", True, None)],
        policies=[
            MCPToolPolicyRecord("policy-1", "srv-docs", "search", "team", "team-ops", True, "manual", None, None, None, None)
        ],
    )
    test_app.state.mcp_gateway_service = MCPGatewayService(
        registry,
        _FakeTransport(),
        approval_service=MCPApprovalService(_FakeApprovalRepository()),  # type: ignore[arg-type]
    )  # type: ignore[arg-type]

    response = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "docs.search", "arguments": {"query": "hello"}},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["code"] == -32008
    assert payload["error"]["data"]["approval_request_id"] == "approval-1"
