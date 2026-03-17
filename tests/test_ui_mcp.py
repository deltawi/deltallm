from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from prometheus_client import generate_latest

from src.audit.actions import AuditAction
from src.auth.roles import OrganizationRole, TeamRole
from src.db.mcp import MCPApprovalRequestRecord, MCPRepository, MCPServerBindingRecord, MCPServerRecord, MCPToolPolicyRecord
from src.db.mcp_scope_policies import MCPScopePolicyRecord
from src.metrics import get_prometheus_registry
from src.mcp.capabilities import extract_tool_schemas, namespace_tools
from src.mcp.health import MCPHealthProbe
from src.mcp.models import MCPToolSchema
from src.models.platform_auth import PlatformAuthContext


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class _FakeMCPRepository(MCPRepository):
    def __init__(self) -> None:
        super().__init__(prisma_client=None)
        self.servers: dict[str, MCPServerRecord] = {}
        self.bindings: dict[str, MCPServerBindingRecord] = {}
        self.policies: dict[str, MCPToolPolicyRecord] = {}
        self.approvals: dict[str, MCPApprovalRequestRecord] = {}
        self._server_counter = 0
        self._binding_counter = 0
        self._policy_counter = 0
        self._approval_counter = 0

    async def list_servers(self, *, search=None, enabled=None, limit=100, offset=0):  # noqa: ANN001, ANN201
        items = list(self.servers.values())
        if search:
            query = str(search).lower()
            items = [item for item in items if query in item.server_key.lower() or query in item.name.lower()]
        if enabled is not None:
            items = [item for item in items if item.enabled is enabled]
        items.sort(key=lambda item: ((item.created_at or _utcnow()), item.server_key), reverse=True)
        return items[offset : offset + limit], len(items)

    async def get_server(self, server_id: str):  # noqa: ANN201
        return self.servers.get(server_id)

    async def get_server_by_key(self, server_key: str):  # noqa: ANN201
        return next((item for item in self.servers.values() if item.server_key == server_key), None)

    async def create_server(self, **kwargs):  # noqa: ANN003, ANN201
        self._server_counter += 1
        now = _utcnow()
        record = MCPServerRecord(
            mcp_server_id=f"mcp-{self._server_counter}",
            server_key=kwargs["server_key"],
            name=kwargs["name"],
            description=kwargs.get("description"),
            owner_scope_type=kwargs.get("owner_scope_type", "global"),
            owner_scope_id=kwargs.get("owner_scope_id"),
            transport=kwargs["transport"],
            base_url=kwargs["base_url"],
            enabled=kwargs["enabled"],
            auth_mode=kwargs["auth_mode"],
            auth_config=kwargs.get("auth_config"),
            forwarded_headers_allowlist=list(kwargs.get("forwarded_headers_allowlist") or []),
            request_timeout_ms=kwargs["request_timeout_ms"],
            metadata=kwargs.get("metadata"),
            created_by_account_id=kwargs.get("created_by_account_id"),
            created_at=now,
            updated_at=now,
        )
        self.servers[record.mcp_server_id] = record
        return record

    async def update_server(self, server_id: str, **kwargs):  # noqa: ANN003, ANN201
        existing = self.servers.get(server_id)
        if existing is None:
            return None
        updated = replace(
            existing,
            name=kwargs["name"],
            description=kwargs.get("description"),
            transport=kwargs["transport"],
            base_url=kwargs["base_url"],
            enabled=kwargs["enabled"],
            auth_mode=kwargs["auth_mode"],
            auth_config=kwargs.get("auth_config"),
            forwarded_headers_allowlist=list(kwargs.get("forwarded_headers_allowlist") or []),
            request_timeout_ms=kwargs["request_timeout_ms"],
            metadata=kwargs.get("metadata"),
            updated_at=_utcnow(),
        )
        self.servers[server_id] = updated
        return updated

    async def delete_server(self, server_id: str) -> bool:
        removed = self.servers.pop(server_id, None)
        if removed is None:
            return False
        self.bindings = {key: value for key, value in self.bindings.items() if value.mcp_server_id != server_id}
        self.policies = {key: value for key, value in self.policies.items() if value.mcp_server_id != server_id}
        self.approvals = {key: value for key, value in self.approvals.items() if value.mcp_server_id != server_id}
        return True

    async def update_server_capabilities(self, server_id: str, *, capabilities_json, capabilities_etag=None):  # noqa: ANN001, ANN201
        existing = self.servers.get(server_id)
        if existing is None:
            return None
        updated = replace(
            existing,
            capabilities_json=capabilities_json,
            capabilities_etag=capabilities_etag,
            capabilities_fetched_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self.servers[server_id] = updated
        return updated

    async def record_health_check(self, server_id: str, *, status, error, latency_ms):  # noqa: ANN001, ANN201
        existing = self.servers.get(server_id)
        if existing is None:
            return None
        updated = replace(
            existing,
            last_health_status=status,
            last_health_error=error,
            last_health_latency_ms=latency_ms,
            last_health_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self.servers[server_id] = updated
        return updated

    async def list_bindings(self, *, server_id=None, scope_type=None, scope_id=None, enabled=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings.values())
        if server_id:
            items = [item for item in items if item.mcp_server_id == server_id]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        if enabled is not None:
            items = [item for item in items if item.enabled is enabled]
        items.sort(key=lambda item: ((item.created_at or _utcnow()), item.mcp_binding_id), reverse=True)
        return items[offset : offset + limit], len(items)

    async def upsert_binding(self, *, server_id, scope_type, scope_id, enabled, tool_allowlist, metadata):  # noqa: ANN001, ANN201
        existing = next(
            (
                item
                for item in self.bindings.values()
                if item.mcp_server_id == server_id and item.scope_type == scope_type and item.scope_id == scope_id
            ),
            None,
        )
        if existing is not None:
            updated = replace(
                existing,
                enabled=enabled,
                tool_allowlist=list(tool_allowlist or []),
                metadata=metadata,
                updated_at=_utcnow(),
            )
            self.bindings[updated.mcp_binding_id] = updated
            return updated
        self._binding_counter += 1
        record = MCPServerBindingRecord(
            mcp_binding_id=f"binding-{self._binding_counter}",
            mcp_server_id=server_id,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=enabled,
            tool_allowlist=list(tool_allowlist or []),
            metadata=metadata,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self.bindings[record.mcp_binding_id] = record
        return record

    async def delete_binding(self, binding_id: str) -> bool:
        return self.bindings.pop(binding_id, None) is not None

    async def get_binding(self, binding_id: str):  # noqa: ANN201
        return self.bindings.get(binding_id)

    async def list_tool_policies(self, *, server_id=None, scope_type=None, scope_id=None, enabled=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.policies.values())
        if server_id:
            items = [item for item in items if item.mcp_server_id == server_id]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        if enabled is not None:
            items = [item for item in items if item.enabled is enabled]
        items.sort(key=lambda item: ((item.created_at or _utcnow()), item.mcp_tool_policy_id), reverse=True)
        return items[offset : offset + limit], len(items)

    async def upsert_tool_policy(  # noqa: ANN201
        self,
        *,
        server_id,
        tool_name,
        scope_type,
        scope_id,
        enabled,
        require_approval,
        max_rpm,
        max_concurrency,
        result_cache_ttl_seconds,
        metadata,
    ):
        existing = next(
            (
                item
                for item in self.policies.values()
                if item.mcp_server_id == server_id
                and item.tool_name == tool_name
                and item.scope_type == scope_type
                and item.scope_id == scope_id
            ),
            None,
        )
        if existing is not None:
            updated = replace(
                existing,
                enabled=enabled,
                require_approval=require_approval,
                max_rpm=max_rpm,
                max_concurrency=max_concurrency,
                result_cache_ttl_seconds=result_cache_ttl_seconds,
                metadata=metadata,
                updated_at=_utcnow(),
            )
            self.policies[updated.mcp_tool_policy_id] = updated
            return updated
        self._policy_counter += 1
        record = MCPToolPolicyRecord(
            mcp_tool_policy_id=f"policy-{self._policy_counter}",
            mcp_server_id=server_id,
            tool_name=tool_name,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=enabled,
            require_approval=require_approval,
            max_rpm=max_rpm,
            max_concurrency=max_concurrency,
            result_cache_ttl_seconds=result_cache_ttl_seconds,
            metadata=metadata,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self.policies[record.mcp_tool_policy_id] = record
        return record

    async def delete_tool_policy(self, policy_id: str) -> bool:
        return self.policies.pop(policy_id, None) is not None

    async def get_tool_policy(self, policy_id: str):  # noqa: ANN201
        return self.policies.get(policy_id)

    async def list_approval_requests(self, *, server_id=None, status=None, limit=100, offset=0):  # noqa: ANN001, ANN201
        items = list(self.approvals.values())
        if server_id:
            items = [item for item in items if item.mcp_server_id == server_id]
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: ((item.created_at or _utcnow()), item.mcp_approval_request_id), reverse=True)
        return items[offset : offset + limit], len(items)

    async def get_approval_request(self, approval_request_id: str):  # noqa: ANN201
        return self.approvals.get(approval_request_id)

    async def decide_approval_request(self, approval_request_id: str, *, status, decided_by_account_id, decision_comment, expires_at):  # noqa: ANN001, ANN201
        existing = self.approvals.get(approval_request_id)
        if existing is None or existing.status != "pending":
            return None
        updated = replace(
            existing,
            status=status,
            decided_by_account_id=decided_by_account_id,
            decision_comment=decision_comment,
            decided_at=_utcnow(),
            expires_at=expires_at,
            updated_at=_utcnow(),
        )
        self.approvals[approval_request_id] = updated
        return updated


class _FakeMCPScopePolicyRepository:
    def __init__(self) -> None:
        self.policies: list[MCPScopePolicyRecord] = []
        self._counter = 0

    async def list_policies(self, *, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.policies)
        if scope_type is not None:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id is not None:
            items = [item for item in items if item.scope_id == scope_id]
        items.sort(key=lambda item: ((item.created_at or _utcnow()), item.mcp_scope_policy_id), reverse=True)
        return items[offset : offset + limit], len(items)

    async def upsert_policy(self, *, scope_type, scope_id, mode, metadata):  # noqa: ANN001, ANN201
        existing = next(
            (item for item in self.policies if item.scope_type == scope_type and item.scope_id == scope_id),
            None,
        )
        if existing is not None:
            updated = replace(existing, mode=mode, metadata=metadata, updated_at=_utcnow())
            self.policies = [updated if item.mcp_scope_policy_id == updated.mcp_scope_policy_id else item for item in self.policies]
            return updated
        self._counter += 1
        record = MCPScopePolicyRecord(
            mcp_scope_policy_id=f"mcp-scope-policy-{self._counter}",
            scope_type=scope_type,
            scope_id=scope_id,
            mode=mode,
            metadata=metadata,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self.policies.append(record)
        return record

    async def get_policy(self, policy_id: str):  # noqa: ANN201
        return next((item for item in self.policies if item.mcp_scope_policy_id == policy_id), None)

    async def delete_policy(self, policy_id: str) -> bool:
        kept = [item for item in self.policies if item.mcp_scope_policy_id != policy_id]
        if len(kept) == len(self.policies):
            return False
        self.policies = kept
        return True


class _FakeMCPRegistryService:
    def __init__(self, repository: _FakeMCPRepository) -> None:
        self.repository = repository
        self.invalidated_server_keys: list[str] = []
        self.invalidate_all_calls = 0

    async def list_servers(self, *, search=None, enabled=None, limit=100, offset=0):  # noqa: ANN001, ANN201
        return await self.repository.list_servers(search=search, enabled=enabled, limit=limit, offset=offset)

    async def store_server_capabilities(self, server_id: str, *, capabilities, etag=None):  # noqa: ANN001, ANN201
        return await self.repository.update_server_capabilities(server_id, capabilities_json=capabilities, capabilities_etag=etag)

    async def list_namespaced_tools(self, server: MCPServerRecord):  # noqa: ANN201
        return namespace_tools(server.server_key, extract_tool_schemas(server.capabilities_json or {}))

    async def record_health(self, server_id: str, *, status, error, latency_ms):  # noqa: ANN001, ANN201
        return await self.repository.record_health_check(server_id, status=status, error=error, latency_ms=latency_ms)

    async def invalidate_server(self, server_key: str) -> None:
        self.invalidated_server_keys.append(server_key)

    async def invalidate_all(self) -> None:
        self.invalidate_all_calls += 1


class _FakeTransportClient:
    def __init__(self) -> None:
        self.initialize_calls: list[str] = []
        self.list_tools_calls: list[str] = []
        self.tools_by_server: dict[str, list[MCPToolSchema]] = {
            "docs": [
                MCPToolSchema(
                    name="search",
                    description="Search knowledge",
                    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                )
            ]
        }

    async def initialize(self, server, *, request_headers=None):  # noqa: ANN001, ANN201
        del request_headers
        self.initialize_calls.append(server.server_key)
        return {"ok": True}

    async def list_tools(self, server, *, request_headers=None):  # noqa: ANN001, ANN201
        del request_headers
        self.list_tools_calls.append(server.server_key)
        return list(self.tools_by_server.get(server.server_key, []))


class _RecordingAuditService:
    def __init__(self) -> None:
        self.sync_calls: list[tuple[object, list[object]]] = []

    async def record_event_sync(self, event, *, payloads=None):  # noqa: ANN001, ANN201
        self.sync_calls.append((event, list(payloads or [])))

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        del event, payloads, critical


class _FakeAuditQueryClient:
    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(str(query).split())
        if "GROUP BY resource_id" in normalized:
            return [
                {"tool_name": "docs.search", "total_calls": 6, "failed_calls": 1, "avg_latency_ms": 120.0},
                {"tool_name": "docs.fetch", "total_calls": 3, "failed_calls": 1, "avg_latency_ms": 187.0},
            ]
        if "status = 'error'" in normalized and "ORDER BY occurred_at DESC" in normalized:
            return [
                {
                    "event_id": "evt-1",
                    "occurred_at": _utcnow(),
                    "tool_name": "docs.fetch",
                    "error_type": "TimeoutError",
                    "error_code": None,
                    "latency_ms": 250,
                    "request_id": "req-1",
                }
            ]
        if "FROM deltallm_mcpapprovalrequest" in normalized:
            return [{"total_requests": 4, "pending_requests": 1, "approved_requests": 2, "rejected_requests": 1}]
        if "COUNT(*)::int AS total_calls" in normalized:
            return [{"total_calls": 9, "failed_calls": 2, "avg_latency_ms": 142.4}]
        raise AssertionError(f"Unexpected query: {normalized} params={params}")


class _FakeMCPScopeQueryClient:
    def __init__(self) -> None:
        self.organizations = {"org-main"}
        self.teams = {"team-ops": {"organization_id": "org-main"}}
        self.keys = {"sk-key-1": {"team_id": "team-ops", "organization_id": "org-main"}}
        self.users = {"user-1": {"team_id": "team-ops", "organization_id": "org-main"}}

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(str(query).split())
        if "FROM deltallm_organizationtable" in normalized:
            organization_id = str(params[0] or "")
            if organization_id in self.organizations:
                return [{"organization_id": organization_id}]
            return []
        if "FROM deltallm_teamtable" in normalized and "LEFT JOIN" not in normalized:
            assert "organization_id" in normalized
            team_id = str(params[0] or "")
            team = self.teams.get(team_id)
            if team is None:
                return []
            return [{"team_id": team_id, "organization_id": team["organization_id"]}]
        if "FROM deltallm_verificationtoken vt" in normalized:
            token = str(params[0] or "")
            key = self.keys.get(token)
            if key is None:
                return []
            return [{"token": token, "team_id": key["team_id"], "organization_id": key["organization_id"]}]
        if "FROM deltallm_usertable u" in normalized:
            user_id = str(params[0] or "")
            user = self.users.get(user_id)
            if user is None:
                return []
            return [{"user_id": user_id, "team_id": user["team_id"], "organization_id": user["organization_id"]}]
        raise AssertionError(f"Unexpected query: {normalized} params={params}")


class _FakeMCPMigrationQueryClient:
    def __init__(self) -> None:
        self.organizations = {"org-main": {"organization_name": "Main Org"}}
        self.teams = {"team-ops": {"organization_id": "org-main", "team_alias": "Ops"}}
        self.keys = {"sk-key-1": {"team_id": "team-ops", "organization_id": "org-main", "key_name": "Ops Key"}}
        self.users = {"user-1": {"team_id": "team-ops", "organization_id": "org-main", "user_email": "user-1@example.com"}}

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(str(query).split())
        if "SELECT organization_id, organization_name FROM deltallm_organizationtable" in normalized:
            if "WHERE organization_id = $1" in normalized:
                organization_id = str(params[0] or "")
                organization = self.organizations.get(organization_id)
                return [{"organization_id": organization_id, "organization_name": organization["organization_name"]}] if organization else []
            return [
                {"organization_id": organization_id, "organization_name": organization["organization_name"]}
                for organization_id, organization in sorted(self.organizations.items())
            ]
        if "SELECT team_id, team_alias, organization_id FROM deltallm_teamtable" in normalized:
            if "WHERE organization_id = $1" in normalized:
                organization_id = str(params[0] or "")
                return [
                    {"team_id": team_id, "team_alias": team["team_alias"], "organization_id": team["organization_id"]}
                    for team_id, team in sorted(self.teams.items())
                    if team["organization_id"] == organization_id
                ]
            return [
                {"team_id": team_id, "team_alias": team["team_alias"], "organization_id": team["organization_id"]}
                for team_id, team in sorted(self.teams.items())
            ]
        if "SELECT vt.token, vt.key_name, vt.team_id, t.organization_id FROM deltallm_verificationtoken vt" in normalized:
            if "WHERE t.organization_id = $1" in normalized:
                organization_id = str(params[0] or "")
                return [
                    {
                        "token": token,
                        "key_name": key["key_name"],
                        "team_id": key["team_id"],
                        "organization_id": key["organization_id"],
                    }
                    for token, key in sorted(self.keys.items())
                    if key["organization_id"] == organization_id
                ]
            return [
                {
                    "token": token,
                    "key_name": key["key_name"],
                    "team_id": key["team_id"],
                    "organization_id": key["organization_id"],
                }
                for token, key in sorted(self.keys.items())
            ]
        if "SELECT DISTINCT ON (u.user_id, t.organization_id)" in normalized:
            if "WHERE t.organization_id = $1" in normalized:
                organization_id = str(params[0] or "")
                return [
                    {
                        "user_id": user_id,
                        "user_email": user["user_email"],
                        "team_id": user["team_id"],
                        "organization_id": user["organization_id"],
                    }
                    for user_id, user in sorted(self.users.items())
                    if user["organization_id"] == organization_id
                ]
            return [
                {
                    "user_id": user_id,
                    "user_email": user["user_email"],
                    "team_id": user["team_id"],
                    "organization_id": user["organization_id"],
                }
                for user_id, user in sorted(self.users.items())
            ]
        raise AssertionError(f"Unexpected query: {normalized} params={params}")


class _FakeApprovalScopeQueryClient:
    def __init__(self, approvals: list[dict[str, object]]) -> None:
        self.approvals = approvals
        self.teams = {"team-ops": {"organization_id": "org-main"}}
        self.keys = {"sk-key-1": {"team_id": "team-ops", "organization_id": "org-main"}}
        self.users = {"user-1": {"team_id": "team-ops", "organization_id": "org-main"}}

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(str(query).split())
        if "SELECT COUNT(*)::int AS total FROM deltallm_mcpapprovalrequest r" in normalized:
            return [{"total": len(self.approvals)}]
        if "FROM deltallm_mcpapprovalrequest r" in normalized and "ORDER BY r.created_at DESC" in normalized:
            return list(self.approvals)
        if "FROM deltallm_teamtable" in normalized:
            team_id = str(params[0] or "")
            team = self.teams.get(team_id)
            if team is None:
                return []
            return [{"organization_id": team["organization_id"]}]
        if "FROM deltallm_verificationtoken vt" in normalized:
            token = str(params[0] or "")
            key = self.keys.get(token)
            if key is None:
                return []
            return [{"team_id": key["team_id"], "organization_id": key["organization_id"]}]
        if "FROM deltallm_usertable u" in normalized:
            user_id = str(params[0] or "")
            user = self.users.get(user_id)
            if user is None:
                return []
            return [{"team_id": user["team_id"], "organization_id": user["organization_id"]}]
        raise AssertionError(f"Unexpected query: {normalized} params={params}")


class _FakeBindingPolicyListQueryClient:
    def __init__(self, *, bindings: list[dict[str, object]] | None = None, policies: list[dict[str, object]] | None = None) -> None:
        now = _utcnow()
        self.bindings = bindings or [
            {
                "mcp_binding_id": "binding-org",
                "mcp_server_id": "mcp-1",
                "scope_type": "organization",
                "scope_id": "org-main",
                "enabled": True,
                "tool_allowlist": ["search"],
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
            {
                "mcp_binding_id": "binding-team",
                "mcp_server_id": "mcp-1",
                "scope_type": "team",
                "scope_id": "team-ops",
                "enabled": True,
                "tool_allowlist": ["search"],
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
            {
                "mcp_binding_id": "binding-key",
                "mcp_server_id": "mcp-1",
                "scope_type": "api_key",
                "scope_id": "sk-key-1",
                "enabled": True,
                "tool_allowlist": ["search"],
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
        ]
        self.policies = policies or [
            {
                "mcp_tool_policy_id": "policy-org",
                "mcp_server_id": "mcp-1",
                "tool_name": "search",
                "scope_type": "organization",
                "scope_id": "org-main",
                "enabled": True,
                "require_approval": "never",
                "max_rpm": 10,
                "max_concurrency": None,
                "result_cache_ttl_seconds": None,
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
            {
                "mcp_tool_policy_id": "policy-team",
                "mcp_server_id": "mcp-1",
                "tool_name": "search",
                "scope_type": "team",
                "scope_id": "team-ops",
                "enabled": True,
                "require_approval": "never",
                "max_rpm": 20,
                "max_concurrency": None,
                "result_cache_ttl_seconds": None,
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
            {
                "mcp_tool_policy_id": "policy-key",
                "mcp_server_id": "mcp-1",
                "tool_name": "search",
                "scope_type": "api_key",
                "scope_id": "sk-key-1",
                "enabled": True,
                "require_approval": "never",
                "max_rpm": 30,
                "max_concurrency": None,
                "result_cache_ttl_seconds": None,
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
        ]
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(str(query).split())
        self.calls.append((normalized, params))
        if "COUNT(*)::int AS total FROM deltallm_mcpbinding b" in normalized:
            return [{"total": len(self.bindings)}]
        if "FROM deltallm_mcpbinding b" in normalized and "ORDER BY b.created_at DESC" in normalized:
            return list(self.bindings)
        if "COUNT(*)::int AS total FROM deltallm_mcptoolpolicy p" in normalized:
            return [{"total": len(self.policies)}]
        if "FROM deltallm_mcptoolpolicy p" in normalized and "ORDER BY p.created_at DESC" in normalized:
            return list(self.policies)
        raise AssertionError(f"Unexpected query: {normalized} params={params}")


class _FakeScopedServerQueryClient:
    def __init__(self) -> None:
        now = _utcnow()
        self.servers = [
            {
                "mcp_server_id": "mcp-1",
                "server_key": "docs",
                "name": "Docs MCP",
                "description": None,
                "transport": "streamable_http",
                "base_url": "https://mcp.example.com",
                "enabled": True,
                "auth_mode": "none",
                "auth_config": {},
                "forwarded_headers_allowlist": [],
                "request_timeout_ms": 30000,
                "capabilities_json": {
                    "tools": [
                        {"name": "search", "inputSchema": {"type": "object"}},
                        {"name": "secret", "inputSchema": {"type": "object"}},
                    ]
                },
                "capabilities_etag": None,
                "capabilities_fetched_at": now,
                "last_health_status": "healthy",
                "last_health_error": None,
                "last_health_at": now,
                "last_health_latency_ms": 42,
                "metadata": {},
                "created_by_account_id": None,
                "created_at": now,
                "updated_at": now,
            }
        ]
        self.bindings = [
            {
                "mcp_binding_id": "binding-team",
                "mcp_server_id": "mcp-1",
                "scope_type": "team",
                "scope_id": "team-ops",
                "enabled": True,
                "tool_allowlist": ["search"],
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            }
        ]
        self.policies = [
            {
                "mcp_tool_policy_id": "policy-team",
                "mcp_server_id": "mcp-1",
                "tool_name": "search",
                "scope_type": "team",
                "scope_id": "team-ops",
                "enabled": True,
                "require_approval": "never",
                "max_rpm": 20,
                "max_concurrency": None,
                "result_cache_ttl_seconds": None,
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            }
        ]
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(str(query).split())
        self.calls.append((normalized, params))
        if "SELECT EXISTS(" in normalized and "FROM deltallm_mcpserver s" in normalized:
            return [{"visible": True}]
        if "COUNT(*)::int AS total FROM deltallm_mcpserver s" in normalized:
            return [{"total": len(self.servers)}]
        if "FROM deltallm_mcpserver s" in normalized and "ORDER BY s.created_at DESC" in normalized:
            return list(self.servers)
        if "COUNT(*)::int AS total FROM deltallm_mcpbinding b" in normalized:
            return [{"total": len(self.bindings)}]
        if "FROM deltallm_mcpbinding b" in normalized and "ORDER BY b.created_at DESC" in normalized:
            return list(self.bindings)
        if "COUNT(*)::int AS total FROM deltallm_mcptoolpolicy p" in normalized:
            return [{"total": len(self.policies)}]
        if "FROM deltallm_mcptoolpolicy p" in normalized and "ORDER BY p.created_at DESC" in normalized:
            return list(self.policies)
        if "COUNT(*)::int AS total_calls" in normalized:
            assert "metadata->>'scope_type'" in normalized
            assert "metadata->>'scope_id'" in normalized
            return [{"total_calls": 4, "failed_calls": 1, "avg_latency_ms": 110.0}]
        if "GROUP BY resource_id" in normalized:
            assert "metadata->>'scope_type'" in normalized
            assert "metadata->>'scope_id'" in normalized
            return [{"tool_name": "docs.search", "total_calls": 4, "failed_calls": 1, "avg_latency_ms": 110.0}]
        if "status = 'error'" in normalized and "ORDER BY occurred_at DESC" in normalized:
            assert "metadata->>'scope_type'" in normalized
            assert "metadata->>'scope_id'" in normalized
            return [{"event_id": "evt-1", "occurred_at": _utcnow(), "tool_name": "docs.search", "error_type": "TimeoutError", "error_code": None, "latency_ms": 180, "request_id": "req-1"}]
        if "FROM deltallm_mcpapprovalrequest" in normalized:
            return [{"total_requests": 2, "pending_requests": 1, "approved_requests": 1, "rejected_requests": 0}]
        raise AssertionError(f"Unexpected query: {normalized} params={params}")


def _set_auth_context(monkeypatch: pytest.MonkeyPatch, context: PlatformAuthContext | None) -> None:
    monkeypatch.setattr("src.middleware.platform_auth.get_platform_auth_context", lambda request: context)
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)


def _make_context(*, platform_role: str = "platform_user", org_role: str | None = None, team_role: str | None = None) -> PlatformAuthContext:
    org_memberships = [{"organization_id": "org-main", "role": org_role}] if org_role else []
    team_memberships = [{"team_id": "team-ops", "role": team_role}] if team_role else []
    return PlatformAuthContext(
        account_id="acct-1",
        email="user@example.com",
        role=platform_role,
        organization_memberships=org_memberships,
        team_memberships=team_memberships,
    )


@pytest.mark.asyncio
async def test_mcp_server_admin_crud_refresh_and_health(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    registry = _FakeMCPRegistryService(repository)
    transport = _FakeTransportClient()
    test_app.state.mcp_repository = repository
    test_app.state.mcp_registry_service = registry
    test_app.state.mcp_transport_client = transport
    test_app.state.mcp_health_probe = MCPHealthProbe(registry, transport)
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit
    headers = {"Authorization": "Bearer mk-test"}

    create = await client.post(
        "/ui/api/mcp-servers",
        headers=headers,
        json={
            "server_key": "docs",
            "name": "Docs MCP",
            "base_url": "https://mcp.example.com",
            "auth_mode": "bearer",
            "auth_config": {"token": "secret-token"},
            "forwarded_headers_allowlist": ["x-api-key"],
        },
    )
    assert create.status_code == 200
    created = create.json()
    assert created["server_key"] == "docs"
    assert created["tool_count"] == 0
    assert created["auth_credentials_present"] is True
    assert "auth_config" not in created

    list_response = await client.get("/ui/api/mcp-servers", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["data"][0]["name"] == "Docs MCP"
    assert list_response.json()["data"][0]["auth_credentials_present"] is True
    assert "auth_config" not in list_response.json()["data"][0]

    refresh = await client.post(f"/ui/api/mcp-servers/{created['mcp_server_id']}/refresh-capabilities", headers=headers)
    assert refresh.status_code == 200
    assert refresh.json()["server"]["tool_count"] == 1
    assert refresh.json()["tools"][0]["namespaced_name"] == "docs.search"

    detail = await client.get(f"/ui/api/mcp-servers/{created['mcp_server_id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["tools"][0]["original_name"] == "search"
    assert detail.json()["server"]["auth_credentials_present"] is True
    assert "auth_config" not in detail.json()["server"]

    health = await client.post(f"/ui/api/mcp-servers/{created['mcp_server_id']}/health-check", headers=headers)
    assert health.status_code == 200
    assert health.json()["health"]["status"] == "healthy"
    assert health.json()["server"]["last_health_status"] == "healthy"

    update = await client.patch(
        f"/ui/api/mcp-servers/{created['mcp_server_id']}",
        headers=headers,
        json={"name": "Updated Docs MCP", "base_url": "https://mcp.example.com/v2"},
    )
    assert update.status_code == 200
    assert update.json()["name"] == "Updated Docs MCP"
    assert update.json()["auth_credentials_present"] is True
    assert "auth_config" not in update.json()
    assert repository.servers[created["mcp_server_id"]].auth_config == {"token": "secret-token"}

    delete = await client.delete(f"/ui/api/mcp-servers/{created['mcp_server_id']}", headers=headers)
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True

    actions = [call[0].action for call in audit.sync_calls]
    assert AuditAction.ADMIN_MCP_SERVER_CREATE in actions
    assert AuditAction.ADMIN_MCP_SERVER_REFRESH_CAPABILITIES in actions
    assert AuditAction.ADMIN_MCP_SERVER_HEALTH_CHECK in actions
    assert AuditAction.ADMIN_MCP_SERVER_UPDATE in actions
    assert AuditAction.ADMIN_MCP_SERVER_DELETE in actions

    metrics_text = generate_latest(get_prometheus_registry()).decode("utf-8")
    assert 'deltallm_mcp_capability_refresh_total' in metrics_text
    assert 'deltallm_mcp_health_check_total' in metrics_text
    assert 'deltallm_mcp_server_health_status' in metrics_text
    assert 'server_key="docs"' in metrics_text


@pytest.mark.asyncio
async def test_mcp_binding_and_tool_policy_admin_lifecycle(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    registry = _FakeMCPRegistryService(repository)
    test_app.state.mcp_repository = repository
    test_app.state.mcp_registry_service = registry
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeMCPScopeQueryClient()})()
    headers = {"Authorization": "Bearer mk-test"}

    binding = await client.post(
        "/ui/api/mcp-bindings",
        headers=headers,
        json={
            "server_id": server.mcp_server_id,
            "scope_type": "team",
            "scope_id": "team-ops",
            "tool_allowlist": ["search"],
        },
    )
    assert binding.status_code == 200
    assert binding.json()["scope_type"] == "team"

    binding_list = await client.get("/ui/api/mcp-bindings?server_id=" + server.mcp_server_id, headers=headers)
    assert binding_list.status_code == 200
    assert binding_list.json()["data"][0]["scope_id"] == "team-ops"

    policy = await client.post(
        "/ui/api/mcp-tool-policies",
        headers=headers,
        json={
            "server_id": server.mcp_server_id,
            "tool_name": "search",
            "scope_type": "team",
            "scope_id": "team-ops",
            "require_approval": "manual",
            "max_rpm": 30,
            "max_total_execution_time_ms": 1500,
        },
    )
    assert policy.status_code == 200
    assert policy.json()["require_approval"] == "manual"
    assert policy.json()["max_total_execution_time_ms"] == 1500
    assert policy.json()["metadata"]["max_total_mcp_execution_time_ms"] == 1500

    policy_list = await client.get("/ui/api/mcp-tool-policies?server_id=" + server.mcp_server_id, headers=headers)
    assert policy_list.status_code == 200
    assert policy_list.json()["data"][0]["tool_name"] == "search"
    assert policy_list.json()["data"][0]["max_total_execution_time_ms"] == 1500

    delete_policy = await client.delete(f"/ui/api/mcp-tool-policies/{policy.json()['mcp_tool_policy_id']}", headers=headers)
    assert delete_policy.status_code == 200
    delete_binding = await client.delete(f"/ui/api/mcp-bindings/{binding.json()['mcp_binding_id']}", headers=headers)
    assert delete_binding.status_code == 200
    assert registry.invalidate_all_calls == 4


@pytest.mark.asyncio
async def test_mcp_binding_and_tool_policy_admin_lifecycle_accepts_user_scope(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    registry = _FakeMCPRegistryService(repository)
    test_app.state.mcp_repository = repository
    test_app.state.mcp_registry_service = registry
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeMCPScopeQueryClient()})()
    headers = {"Authorization": "Bearer mk-test"}

    binding = await client.post(
        "/ui/api/mcp-bindings",
        headers=headers,
        json={
            "server_id": server.mcp_server_id,
            "scope_type": "user",
            "scope_id": "user-1",
            "tool_allowlist": ["search"],
        },
    )
    assert binding.status_code == 200
    assert binding.json()["scope_type"] == "user"

    policy = await client.post(
        "/ui/api/mcp-tool-policies",
        headers=headers,
        json={
            "server_id": server.mcp_server_id,
            "tool_name": "search",
            "scope_type": "user",
            "scope_id": "user-1",
            "require_approval": "manual",
        },
    )
    assert policy.status_code == 200
    assert policy.json()["scope_type"] == "user"
    assert registry.invalidate_all_calls == 2


@pytest.mark.asyncio
async def test_mcp_scope_policy_admin_lifecycle_accepts_user_scope(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.mcp_scope_policy_repository = _FakeMCPScopePolicyRepository()
    test_app.state.mcp_registry_service = _FakeMCPRegistryService(_FakeMCPRepository())
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeMCPScopeQueryClient()})()
    headers = {"Authorization": "Bearer mk-test"}

    create = await client.post(
        "/ui/api/mcp-scope-policies",
        headers=headers,
        json={
            "scope_type": "user",
            "scope_id": "user-1",
            "mode": "restrict",
            "metadata": {"rollout": "pilot"},
        },
    )
    assert create.status_code == 200
    payload = create.json()
    assert payload["scope_type"] == "user"
    assert payload["mode"] == "restrict"

    listing = await client.get("/ui/api/mcp-scope-policies?scope_type=user", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["data"][0]["scope_id"] == "user-1"

    delete = await client.delete(f"/ui/api/mcp-scope-policies/{payload['mcp_scope_policy_id']}", headers=headers)
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True


@pytest.mark.asyncio
async def test_mcp_migration_report_identifies_org_bootstrap_and_scope_backfill(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    policy_repository = _FakeMCPScopePolicyRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    await repository.upsert_binding(
        server_id=server.mcp_server_id,
        scope_type="team",
        scope_id="team-ops",
        enabled=True,
        tool_allowlist=["search"],
        metadata=None,
    )
    await repository.upsert_binding(
        server_id=server.mcp_server_id,
        scope_type="user",
        scope_id="user-1",
        enabled=True,
        tool_allowlist=["search"],
        metadata=None,
    )
    test_app.state.mcp_repository = repository
    test_app.state.mcp_scope_policy_repository = policy_repository
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeMCPMigrationQueryClient()})()

    response = await client.get("/ui/api/mcp-migration/report", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["organizations_needing_bootstrap"] == 1
    assert payload["organizations"][0]["rollout_state"] == "needs_org_bootstrap"
    assert payload["organizations"][0]["missing_org_server_keys"] == ["docs"]
    assert payload["organizations"][0]["teams"][0]["rollout_state"] == "needs_scope_backfill"
    assert payload["organizations"][0]["users"][0]["rollout_state"] == "needs_scope_backfill"


@pytest.mark.asyncio
async def test_mcp_migration_backfill_bootstraps_org_ceiling_and_scope_policies(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    policy_repository = _FakeMCPScopePolicyRepository()
    registry = _FakeMCPRegistryService(repository)
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    await repository.upsert_binding(
        server_id=server.mcp_server_id,
        scope_type="team",
        scope_id="team-ops",
        enabled=True,
        tool_allowlist=["search"],
        metadata=None,
    )
    await repository.upsert_binding(
        server_id=server.mcp_server_id,
        scope_type="api_key",
        scope_id="sk-key-1",
        enabled=True,
        tool_allowlist=["search"],
        metadata=None,
    )
    test_app.state.mcp_repository = repository
    test_app.state.mcp_scope_policy_repository = policy_repository
    test_app.state.mcp_registry_service = registry
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeMCPMigrationQueryClient()})()

    response = await client.post(
        "/ui/api/mcp-migration/backfill",
        headers={"Authorization": "Bearer mk-test"},
        json={"rollout_states": ["needs_org_bootstrap", "needs_scope_backfill"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["applied"]["organizations_bootstrapped"] == 1
    assert payload["applied"]["organization_bindings_created"] == 1
    assert payload["applied"]["team_policies_created"] == 1
    assert payload["applied"]["api_key_policies_created"] == 1
    assert payload["organizations"][0]["rollout_state"] == "ready_for_enforce"
    assert payload["organizations"][0]["org_binding_server_keys"] == ["docs"]
    assert payload["organizations"][0]["teams"][0]["scope_policy_mode"] == "restrict"
    assert payload["organizations"][0]["api_keys"][0]["scope_policy_mode"] == "restrict"
    assert registry.invalidate_all_calls == 1


@pytest.mark.asyncio
async def test_mcp_binding_and_policy_validation_reject_unknown_scope_targets(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    test_app.state.mcp_repository = repository
    test_app.state.mcp_registry_service = _FakeMCPRegistryService(repository)
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeMCPScopeQueryClient()})()
    headers = {"Authorization": "Bearer mk-test"}

    missing_team = await client.post(
        "/ui/api/mcp-bindings",
        headers=headers,
        json={
            "server_id": server.mcp_server_id,
            "scope_type": "team",
            "scope_id": "team-missing",
        },
    )
    assert missing_team.status_code == 404
    assert missing_team.json()["detail"] == "Team not found"

    missing_key = await client.post(
        "/ui/api/mcp-tool-policies",
        headers=headers,
        json={
            "server_id": server.mcp_server_id,
            "tool_name": "search",
            "scope_type": "api_key",
            "scope_id": "sk-missing",
        },
    )
    assert missing_key.status_code == 404
    assert missing_key.json()["detail"] == "API key not found"

    missing_user = await client.post(
        "/ui/api/mcp-bindings",
        headers=headers,
        json={
            "server_id": server.mcp_server_id,
            "scope_type": "user",
            "scope_id": "user-missing",
        },
    )
    assert missing_user.status_code == 400
    assert missing_user.json()["detail"] == "user_id not found"

    test_app.state.mcp_scope_policy_repository = _FakeMCPScopePolicyRepository()
    missing_policy_scope = await client.post(
        "/ui/api/mcp-scope-policies",
        headers=headers,
        json={
            "scope_type": "user",
            "scope_id": "user-missing",
            "mode": "restrict",
        },
    )
    assert missing_policy_scope.status_code == 400
    assert missing_policy_scope.json()["detail"] == "user_id not found"


@pytest.mark.asyncio
async def test_mcp_server_validation_rejects_invalid_phase1_config(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    test_app.state.mcp_repository = repository
    test_app.state.mcp_registry_service = _FakeMCPRegistryService(test_app.state.mcp_repository)
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeMCPScopeQueryClient()})()
    headers = {"Authorization": "Bearer mk-test"}

    create = await client.post(
        "/ui/api/mcp-servers",
        headers=headers,
        json={
            "server_key": "docs-basic",
            "name": "Docs MCP",
            "base_url": "https://mcp.example.com",
            "auth_mode": "basic",
            "auth_config": {"username": "svc"},
        },
    )
    assert create.status_code == 400
    assert "auth_config.username and auth_config.password" in create.json()["detail"]

    policy = await client.post(
        "/ui/api/mcp-tool-policies",
        headers=headers,
        json={
            "server_id": server.mcp_server_id,
            "tool_name": "search",
            "scope_type": "team",
            "scope_id": "team-ops",
            "require_approval": "always",
        },
    )
    assert policy.status_code == 400
    assert 'require_approval must be "never" or "manual"' in policy.json()["detail"]


@pytest.mark.asyncio
async def test_mcp_server_operations_summary(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    test_app.state.mcp_repository = repository
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeAuditQueryClient()})()

    response = await client.get(
        f"/ui/api/mcp-servers/{server.mcp_server_id}/operations",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total_calls"] == 9
    assert payload["summary"]["failed_calls"] == 2
    assert payload["summary"]["pending_approvals"] == 1
    assert payload["top_tools"][0]["tool_name"] == "docs.search"
    assert payload["recent_failures"][0]["error_type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_mcp_approval_request_list_and_decision(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    approval = MCPApprovalRequestRecord(
        mcp_approval_request_id="approval-1",
        mcp_server_id=server.mcp_server_id,
        tool_name="search",
        scope_type="team",
        scope_id="team-ops",
        status="pending",
        request_fingerprint="fp-1",
        requested_by_api_key="sk-test",
        arguments_json={"query": "delta"},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    repository.approvals[approval.mcp_approval_request_id] = approval
    test_app.state.mcp_repository = repository
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit

    list_response = await client.get(
        f"/ui/api/mcp-approval-requests?server_id={server.mcp_server_id}",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert list_response.status_code == 200
    list_payload = list_response.json()["data"][0]
    assert list_payload["tool_name"] == "search"
    assert list_payload["server"]["mcp_server_id"] == server.mcp_server_id
    assert list_payload["server"]["server_key"] == server.server_key
    assert list_payload["server"]["name"] == server.name
    assert list_payload["capabilities"]["can_decide"] is True

    decision = await client.post(
        f"/ui/api/mcp-approval-requests/{approval.mcp_approval_request_id}/decision",
        headers={"Authorization": "Bearer mk-test"},
        json={"status": "approved"},
    )
    assert decision.status_code == 200
    decision_payload = decision.json()
    assert decision_payload["status"] == "approved"
    assert decision_payload["server"]["mcp_server_id"] == server.mcp_server_id
    assert decision_payload["capabilities"]["can_decide"] is False
    actions = [call[0].action for call in audit.sync_calls]
    assert AuditAction.ADMIN_MCP_APPROVAL_DECIDE in actions
    metrics_text = generate_latest(get_prometheus_registry()).decode("utf-8")
    assert "deltallm_mcp_approval_decision_total" in metrics_text


@pytest.mark.asyncio
async def test_scoped_org_admin_can_list_and_decide_visible_approval_requests(client, test_app, monkeypatch):
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    approval = MCPApprovalRequestRecord(
        mcp_approval_request_id="approval-org-1",
        mcp_server_id=server.mcp_server_id,
        tool_name="search",
        scope_type="team",
        scope_id="team-ops",
        status="pending",
        request_fingerprint="fp-org-1",
        requested_by_api_key="sk-key-1",
        organization_id="org-main",
        arguments_json={"query": "delta"},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    repository.approvals[approval.mcp_approval_request_id] = approval
    test_app.state.mcp_repository = repository
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeApprovalScopeQueryClient([approval.__dict__])})()
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    list_response = await client.get("/ui/api/mcp-approval-requests")
    assert list_response.status_code == 200
    list_payload = list_response.json()["data"][0]
    assert list_payload["mcp_approval_request_id"] == "approval-org-1"
    assert list_payload["server"]["mcp_server_id"] == server.mcp_server_id
    assert list_payload["server"]["server_key"] == server.server_key
    assert list_payload["capabilities"]["can_decide"] is True

    decision = await client.post(
        f"/ui/api/mcp-approval-requests/{approval.mcp_approval_request_id}/decision",
        json={"status": "approved"},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_scoped_org_admin_can_list_and_decide_user_scoped_approval_requests(client, test_app, monkeypatch):
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    approval = MCPApprovalRequestRecord(
        mcp_approval_request_id="approval-user-1",
        mcp_server_id=server.mcp_server_id,
        tool_name="search",
        scope_type="user",
        scope_id="user-1",
        status="pending",
        request_fingerprint="fp-user-1",
        requested_by_api_key="sk-key-1",
        requested_by_user="user-1",
        organization_id="org-main",
        arguments_json={"query": "delta"},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    repository.approvals[approval.mcp_approval_request_id] = approval
    test_app.state.mcp_repository = repository
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeApprovalScopeQueryClient([approval.__dict__])})()
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    list_response = await client.get("/ui/api/mcp-approval-requests")
    assert list_response.status_code == 200
    assert list_response.json()["data"][0]["mcp_approval_request_id"] == "approval-user-1"

    decision = await client.post(
        f"/ui/api/mcp-approval-requests/{approval.mcp_approval_request_id}/decision",
        json={"status": "approved"},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_mcp_admin_lists_enabled_rows_by_default_and_include_disabled_for_platform_admin(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    enabled_binding = await repository.upsert_binding(
        server_id=server.mcp_server_id,
        scope_type="team",
        scope_id="team-ops",
        enabled=True,
        tool_allowlist=["search"],
        metadata=None,
    )
    await repository.upsert_binding(
        server_id=server.mcp_server_id,
        scope_type="api_key",
        scope_id="sk-key-1",
        enabled=False,
        tool_allowlist=["search"],
        metadata=None,
    )
    enabled_policy = await repository.upsert_tool_policy(
        server_id=server.mcp_server_id,
        tool_name="search",
        scope_type="team",
        scope_id="team-ops",
        enabled=True,
        require_approval="never",
        max_rpm=None,
        max_concurrency=None,
        result_cache_ttl_seconds=None,
        metadata=None,
    )
    await repository.upsert_tool_policy(
        server_id=server.mcp_server_id,
        tool_name="search",
        scope_type="api_key",
        scope_id="sk-key-1",
        enabled=False,
        require_approval="manual",
        max_rpm=None,
        max_concurrency=None,
        result_cache_ttl_seconds=None,
        metadata=None,
    )
    test_app.state.mcp_repository = repository
    test_app.state.mcp_registry_service = _FakeMCPRegistryService(repository)

    bindings_default = await client.get("/ui/api/mcp-bindings", headers={"Authorization": "Bearer mk-test"})
    assert bindings_default.status_code == 200
    assert [item["mcp_binding_id"] for item in bindings_default.json()["data"]] == [enabled_binding.mcp_binding_id]

    bindings_all = await client.get(
        "/ui/api/mcp-bindings?include_disabled=true",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert bindings_all.status_code == 200
    assert len(bindings_all.json()["data"]) == 2

    policies_default = await client.get("/ui/api/mcp-tool-policies", headers={"Authorization": "Bearer mk-test"})
    assert policies_default.status_code == 200
    assert [item["mcp_tool_policy_id"] for item in policies_default.json()["data"]] == [enabled_policy.mcp_tool_policy_id]

    policies_all = await client.get(
        "/ui/api/mcp-tool-policies?include_disabled=true",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert policies_all.status_code == 200
    assert len(policies_all.json()["data"]) == 2

    server_detail = await client.get(f"/ui/api/mcp-servers/{server.mcp_server_id}", headers={"Authorization": "Bearer mk-test"})
    assert server_detail.status_code == 200
    assert len(server_detail.json()["bindings"]) == 1
    assert len(server_detail.json()["tool_policies"]) == 1

    server_detail_all = await client.get(
        f"/ui/api/mcp-servers/{server.mcp_server_id}?include_disabled=true",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert server_detail_all.status_code == 200
    assert len(server_detail_all.json()["bindings"]) == 2
    assert len(server_detail_all.json()["tool_policies"]) == 2


@pytest.mark.asyncio
async def test_scoped_team_developer_cannot_decide_approval_requests(client, test_app, monkeypatch):
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    approval = MCPApprovalRequestRecord(
        mcp_approval_request_id="approval-team-1",
        mcp_server_id=server.mcp_server_id,
        tool_name="search",
        scope_type="team",
        scope_id="team-ops",
        status="pending",
        request_fingerprint="fp-team-1",
        requested_by_api_key="sk-key-1",
        organization_id="org-main",
        arguments_json={"query": "delta"},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    repository.approvals[approval.mcp_approval_request_id] = approval
    test_app.state.mcp_repository = repository
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeApprovalScopeQueryClient([approval.__dict__])})()
    _set_auth_context(monkeypatch, _make_context(team_role=TeamRole.DEVELOPER))

    response = await client.post(
        f"/ui/api/mcp-approval-requests/{approval.mcp_approval_request_id}/decision",
        json={"status": "approved"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_scoped_org_admin_can_list_visible_bindings_and_policies(client, test_app, monkeypatch):
    test_app.state.mcp_repository = _FakeMCPRepository()
    fake_db = _FakeBindingPolicyListQueryClient()
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": fake_db})()
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    bindings_response = await client.get("/ui/api/mcp-bindings?server_id=mcp-1")
    assert bindings_response.status_code == 200
    assert len(bindings_response.json()["data"]) == 3

    policies_response = await client.get("/ui/api/mcp-tool-policies?server_id=mcp-1")
    assert policies_response.status_code == 200
    assert len(policies_response.json()["data"]) == 3

    binding_query = next(query for query, _ in fake_db.calls if "FROM deltallm_mcpbinding b" in query)
    policy_query = next(query for query, _ in fake_db.calls if "FROM deltallm_mcptoolpolicy p" in query)
    assert "deltallm_teamtable" in binding_query
    assert "deltallm_verificationtoken" in binding_query
    assert "deltallm_usertable" in binding_query
    assert "deltallm_teamtable" in policy_query
    assert "deltallm_verificationtoken" in policy_query
    assert "deltallm_usertable" in policy_query


@pytest.mark.asyncio
async def test_scoped_team_developer_reads_team_and_key_bound_rows_only(client, test_app, monkeypatch):
    now = _utcnow()
    test_app.state.mcp_repository = _FakeMCPRepository()
    fake_db = _FakeBindingPolicyListQueryClient(
        bindings=[
            {
                "mcp_binding_id": "binding-team",
                "mcp_server_id": "mcp-1",
                "scope_type": "team",
                "scope_id": "team-ops",
                "enabled": True,
                "tool_allowlist": ["search"],
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
            {
                "mcp_binding_id": "binding-key",
                "mcp_server_id": "mcp-1",
                "scope_type": "api_key",
                "scope_id": "sk-key-1",
                "enabled": True,
                "tool_allowlist": ["search"],
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
        ],
        policies=[
            {
                "mcp_tool_policy_id": "policy-team",
                "mcp_server_id": "mcp-1",
                "tool_name": "search",
                "scope_type": "team",
                "scope_id": "team-ops",
                "enabled": True,
                "require_approval": "never",
                "max_rpm": 20,
                "max_concurrency": None,
                "result_cache_ttl_seconds": None,
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
            {
                "mcp_tool_policy_id": "policy-key",
                "mcp_server_id": "mcp-1",
                "tool_name": "search",
                "scope_type": "api_key",
                "scope_id": "sk-key-1",
                "enabled": True,
                "require_approval": "never",
                "max_rpm": 30,
                "max_concurrency": None,
                "result_cache_ttl_seconds": None,
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
        ],
    )
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": fake_db})()
    _set_auth_context(monkeypatch, _make_context(team_role=TeamRole.DEVELOPER))

    bindings_response = await client.get("/ui/api/mcp-bindings?server_id=mcp-1")
    assert bindings_response.status_code == 200
    assert [row["scope_type"] for row in bindings_response.json()["data"]] == ["team", "api_key"]

    policies_response = await client.get("/ui/api/mcp-tool-policies?server_id=mcp-1")
    assert policies_response.status_code == 200
    assert [row["scope_type"] for row in policies_response.json()["data"]] == ["team", "api_key"]


@pytest.mark.asyncio
async def test_scoped_org_admin_can_access_mcp_server_read_surfaces_and_health_check(client, test_app, monkeypatch):
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    repository.servers[server.mcp_server_id] = replace(
        repository.servers[server.mcp_server_id],
        capabilities_json={"tools": [{"name": "search", "inputSchema": {"type": "object"}}]},
        capabilities_fetched_at=_utcnow(),
        last_health_status="healthy",
        last_health_latency_ms=42,
        last_health_at=_utcnow(),
    )
    registry = _FakeMCPRegistryService(repository)
    transport = _FakeTransportClient()
    test_app.state.mcp_repository = repository
    test_app.state.mcp_registry_service = registry
    test_app.state.mcp_transport_client = transport
    test_app.state.mcp_health_probe = MCPHealthProbe(registry, transport)
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeScopedServerQueryClient()})()
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    list_response = await client.get("/ui/api/mcp-servers")
    assert list_response.status_code == 200
    assert list_response.json()["data"][0]["server_key"] == "docs"
    assert list_response.json()["data"][0]["capabilities"] == {
        "can_mutate": False,
        "can_operate": True,
        "can_manage_scope_config": True,
    }
    assert list_response.json()["data"][0]["auth_credentials_present"] is False
    assert "auth_config" not in list_response.json()["data"][0]

    detail_response = await client.get(f"/ui/api/mcp-servers/{server.mcp_server_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["server"]["server_key"] == "docs"
    assert detail_response.json()["server"]["capabilities"] == {
        "can_mutate": False,
        "can_operate": True,
        "can_manage_scope_config": True,
    }
    assert detail_response.json()["server"]["auth_credentials_present"] is False
    assert "auth_config" not in detail_response.json()["server"]
    assert detail_response.json()["bindings"][0]["scope_id"] == "team-ops"
    assert [tool["original_name"] for tool in detail_response.json()["tools"]] == ["search"]

    operations_response = await client.get(f"/ui/api/mcp-servers/{server.mcp_server_id}/operations")
    assert operations_response.status_code == 200
    assert operations_response.json()["summary"]["total_calls"] == 4

    health_response = await client.post(f"/ui/api/mcp-servers/{server.mcp_server_id}/health-check")
    assert health_response.status_code == 200
    assert health_response.json()["health"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_scoped_org_admin_can_create_org_owned_server_but_not_global_server(client, test_app, monkeypatch):
    repository = _FakeMCPRepository()
    test_app.state.mcp_repository = repository
    test_app.state.mcp_registry_service = _FakeMCPRegistryService(repository)
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeMCPScopeQueryClient()})()
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    create_owned = await client.post(
        "/ui/api/mcp-servers",
        json={
            "server_key": "org-docs",
            "name": "Org Docs MCP",
            "base_url": "https://mcp.example.com",
        },
    )
    assert create_owned.status_code == 200
    assert create_owned.json()["owner_scope_type"] == "organization"
    assert create_owned.json()["owner_scope_id"] == "org-main"

    create_global = await client.post(
        "/ui/api/mcp-servers",
        json={
            "server_key": "global-docs",
            "name": "Global Docs MCP",
            "base_url": "https://mcp.example.com",
            "owner_scope_type": "global",
        },
    )
    assert create_global.status_code == 403


@pytest.mark.asyncio
async def test_scoped_org_admin_can_update_owned_server_and_scope_global_server_bindings(client, test_app, monkeypatch):
    repository = _FakeMCPRepository()
    owned_server = await repository.create_server(
        server_key="org-docs",
        name="Org Docs MCP",
        description=None,
        owner_scope_type="organization",
        owner_scope_id="org-main",
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    global_server = await repository.create_server(
        server_key="global-docs",
        name="Global Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    test_app.state.mcp_repository = repository
    registry = _FakeMCPRegistryService(repository)
    test_app.state.mcp_registry_service = registry
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeMCPScopeQueryClient()})()
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    update_owned = await client.patch(
        f"/ui/api/mcp-servers/{owned_server.mcp_server_id}",
        json={"name": "Updated Org Docs MCP"},
    )
    assert update_owned.status_code == 200
    assert update_owned.json()["name"] == "Updated Org Docs MCP"
    assert update_owned.json()["capabilities"] == {
        "can_mutate": True,
        "can_operate": True,
        "can_manage_scope_config": True,
    }

    update_global = await client.patch(
        f"/ui/api/mcp-servers/{global_server.mcp_server_id}",
        json={"name": "Updated Global Docs MCP"},
    )
    assert update_global.status_code == 403

    create_binding = await client.post(
        "/ui/api/mcp-bindings",
        json={
            "server_id": global_server.mcp_server_id,
            "scope_type": "team",
            "scope_id": "team-ops",
            "tool_allowlist": ["search"],
        },
    )
    assert create_binding.status_code == 200

    create_policy = await client.post(
        "/ui/api/mcp-tool-policies",
        json={
            "server_id": global_server.mcp_server_id,
            "tool_name": "search",
            "scope_type": "api_key",
            "scope_id": "sk-key-1",
            "max_rpm": 30,
        },
    )
    assert create_policy.status_code == 200
    assert registry.invalidate_all_calls == 2


@pytest.mark.asyncio
async def test_scoped_team_developer_cannot_run_mcp_health_check(client, test_app, monkeypatch):
    repository = _FakeMCPRepository()
    server = await repository.create_server(
        server_key="docs",
        name="Docs MCP",
        description=None,
        owner_scope_type="global",
        owner_scope_id=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=30000,
        metadata=None,
        created_by_account_id=None,
    )
    test_app.state.mcp_repository = repository
    test_app.state.mcp_registry_service = _FakeMCPRegistryService(repository)
    test_app.state.mcp_transport_client = _FakeTransportClient()
    test_app.state.mcp_health_probe = MCPHealthProbe(test_app.state.mcp_registry_service, test_app.state.mcp_transport_client)
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": _FakeScopedServerQueryClient()})()
    _set_auth_context(monkeypatch, _make_context(team_role=TeamRole.DEVELOPER))

    response = await client.post(f"/ui/api/mcp-servers/{server.mcp_server_id}/health-check")
    assert response.status_code == 403
