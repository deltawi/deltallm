from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.db.mcp import MCPServerBindingRecord, MCPServerRecord, MCPToolPolicyRecord
from src.models.responses import UserAPIKeyAuth
from src.mcp.models import MCPBindingResolution
from src.services.runtime_scopes import resolve_runtime_scope_context

if TYPE_CHECKING:
    from src.db.mcp import MCPRepository
    from src.db.mcp_scope_policies import MCPScopePolicyRepository


@dataclass(frozen=True, slots=True)
class MCPGovernanceSnapshot:
    enabled_servers: tuple[MCPServerRecord, ...]
    servers_by_id: dict[str, MCPServerRecord]
    bindings_by_scope: dict[tuple[str, str], tuple[MCPServerBindingRecord, ...]]
    binding_counts_by_scope: dict[tuple[str, str], int]
    scope_modes_by_scope: dict[tuple[str, str], str]
    policies_by_scope: dict[tuple[str, str], tuple[MCPToolPolicyRecord, ...]]


class MCPGovernanceService:
    def __init__(
        self,
        repository: MCPRepository | None,
        *,
        policy_repository: MCPScopePolicyRepository | None = None,
    ) -> None:
        self.repository = repository
        self.policy_repository = policy_repository
        self._reload_lock = asyncio.Lock()
        self._snapshot = MCPGovernanceSnapshot(
            enabled_servers=(),
            servers_by_id={},
            bindings_by_scope={},
            binding_counts_by_scope={},
            scope_modes_by_scope={},
            policies_by_scope={},
        )

    async def reload(self) -> None:
        if self.repository is None and self.policy_repository is None:
            self._snapshot = MCPGovernanceSnapshot(
                enabled_servers=(),
                servers_by_id={},
                bindings_by_scope={},
                binding_counts_by_scope={},
                scope_modes_by_scope={},
                policies_by_scope={},
            )
            return

        async with self._reload_lock:
            enabled_servers: list[MCPServerRecord] = []
            servers_by_id: dict[str, MCPServerRecord] = {}
            offset = 0
            limit = 500
            while True:
                page, total = await self.repository.list_servers(enabled=True, limit=limit, offset=offset)
                enabled_servers.extend(page)
                offset += len(page)
                if not page or offset >= total:
                    break
            servers_by_id = {server.mcp_server_id: server for server in enabled_servers}

            bindings_by_scope: dict[tuple[str, str], list[MCPServerBindingRecord]] = defaultdict(list)
            binding_counts_by_scope: dict[tuple[str, str], int] = defaultdict(int)
            if self.repository is not None:
                offset = 0
                limit = 1000
                while True:
                    page, total = await self.repository.list_bindings(limit=limit, offset=offset)
                    for binding in page:
                        if binding.mcp_server_id not in servers_by_id:
                            continue
                        scope = (binding.scope_type, binding.scope_id)
                        binding_counts_by_scope[scope] += 1
                        if not binding.enabled:
                            continue
                        bindings_by_scope[scope].append(binding)
                    offset += len(page)
                    if not page or offset >= total:
                        break

            scope_modes_by_scope: dict[tuple[str, str], str] = {}
            if self.policy_repository is not None:
                offset = 0
                limit = 1000
                while True:
                    page, total = await self.policy_repository.list_policies(limit=limit, offset=offset)
                    for policy in page:
                        scope_modes_by_scope[(policy.scope_type, policy.scope_id)] = policy.mode
                    offset += len(page)
                    if not page or offset >= total:
                        break

            policies_by_scope: dict[tuple[str, str], list[MCPToolPolicyRecord]] = defaultdict(list)
            if self.repository is not None:
                offset = 0
                limit = 1000
                while True:
                    page, total = await self.repository.list_tool_policies(limit=limit, offset=offset)
                    for policy in page:
                        if policy.mcp_server_id not in servers_by_id:
                            continue
                        policies_by_scope[(policy.scope_type, policy.scope_id)].append(policy)
                    offset += len(page)
                    if not page or offset >= total:
                        break

            self._snapshot = MCPGovernanceSnapshot(
                enabled_servers=tuple(enabled_servers),
                servers_by_id=servers_by_id,
                bindings_by_scope={scope: tuple(items) for scope, items in bindings_by_scope.items()},
                binding_counts_by_scope=dict(binding_counts_by_scope),
                scope_modes_by_scope=scope_modes_by_scope,
                policies_by_scope={scope: tuple(items) for scope, items in policies_by_scope.items()},
            )

    async def invalidate_all(self) -> None:
        await self.reload()

    def list_enabled_servers(self) -> list[MCPServerRecord]:
        return list(self._snapshot.enabled_servers)

    def get_server(self, server_id: str) -> MCPServerRecord | None:
        return self._snapshot.servers_by_id.get(str(server_id or "").strip())

    def get_scope_mode(self, scope_type: str, scope_id: str) -> str | None:
        normalized_scope_type = str(scope_type or "").strip()
        normalized_scope_id = str(scope_id or "").strip()
        if not normalized_scope_type or not normalized_scope_id:
            return None
        return self._snapshot.scope_modes_by_scope.get((normalized_scope_type, normalized_scope_id))

    def list_effective_bindings(self, *, scopes: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> list[MCPServerBindingRecord]:
        snapshot = self._snapshot
        bindings: list[MCPServerBindingRecord] = []
        for scope in scopes:
            bindings.extend(snapshot.bindings_by_scope.get(scope, ()))
        return bindings

    def list_effective_tool_policies(
        self,
        *,
        scopes: tuple[tuple[str, str], ...] | list[tuple[str, str]],
        server_id: str | None = None,
    ) -> list[MCPToolPolicyRecord]:
        snapshot = self._snapshot
        policies: list[MCPToolPolicyRecord] = []
        for scope in scopes:
            policies.extend(snapshot.policies_by_scope.get(scope, ()))
        if server_id is None:
            return policies
        normalized_server_id = str(server_id or "").strip()
        return [policy for policy in policies if policy.mcp_server_id == normalized_server_id]

    def resolve_binding_resolutions(self, auth: UserAPIKeyAuth) -> list[MCPBindingResolution]:
        scope_context = resolve_runtime_scope_context(auth)
        if scope_context.is_master_key:
            return []

        snapshot = self._snapshot
        if scope_context.organization_id is not None and self._has_bindings(snapshot, "organization", scope_context.organization_id):
            effective = self._bindings_for_scope(snapshot, "organization", scope_context.organization_id)
            if scope_context.team_id is not None and self._should_restrict_scope(snapshot, "team", scope_context.team_id):
                effective = self._intersect_bindings(effective, self._bindings_for_scope(snapshot, "team", scope_context.team_id))
            if scope_context.api_key_scope_id is not None and self._should_restrict_scope(snapshot, "api_key", scope_context.api_key_scope_id):
                effective = self._intersect_bindings(effective, self._bindings_for_scope(snapshot, "api_key", scope_context.api_key_scope_id))
            if scope_context.user_id is not None and self._should_restrict_scope(snapshot, "user", scope_context.user_id):
                effective = self._intersect_bindings(effective, self._bindings_for_scope(snapshot, "user", scope_context.user_id))
            return list(effective.values())

        # Compatibility path for legacy MCP scope setups with no org-level ceiling yet.
        scoped_bindings = self.list_effective_bindings(scopes=scope_context.scope_chain)
        grouped: dict[str, list[MCPServerBindingRecord]] = defaultdict(list)
        for binding in scoped_bindings:
            grouped[binding.mcp_server_id].append(binding)
        resolutions: list[MCPBindingResolution] = []
        for server_bindings in grouped.values():
            selected = _select_record_for_scope_order(server_bindings, scope_order=scope_context.scope_chain)
            resolutions.append(
                MCPBindingResolution(
                    server_id=selected.mcp_server_id,
                    server_key="",
                    scope_type=selected.scope_type,
                    scope_id=selected.scope_id,
                    allowed_tool_names=tuple(selected.tool_allowlist or []) or None,
                )
            )
        return resolutions

    @staticmethod
    def _has_bindings(snapshot: MCPGovernanceSnapshot, scope_type: str, scope_id: str) -> bool:
        normalized_scope = (str(scope_type or "").strip(), str(scope_id or "").strip())
        if not normalized_scope[0] or not normalized_scope[1]:
            return False
        return snapshot.binding_counts_by_scope.get(normalized_scope, 0) > 0

    @staticmethod
    def _bindings_for_scope(
        snapshot: MCPGovernanceSnapshot,
        scope_type: str,
        scope_id: str,
    ) -> dict[str, MCPBindingResolution]:
        normalized_scope = (str(scope_type or "").strip(), str(scope_id or "").strip())
        if not normalized_scope[0] or not normalized_scope[1]:
            return {}
        out: dict[str, MCPBindingResolution] = {}
        for binding in snapshot.bindings_by_scope.get(normalized_scope, ()):
            out[binding.mcp_server_id] = MCPBindingResolution(
                server_id=binding.mcp_server_id,
                server_key="",
                scope_type=binding.scope_type,
                scope_id=binding.scope_id,
                allowed_tool_names=tuple(binding.tool_allowlist or []) or None,
            )
        return out

    @staticmethod
    def _intersect_bindings(
        current: dict[str, MCPBindingResolution],
        narrowed: dict[str, MCPBindingResolution],
    ) -> dict[str, MCPBindingResolution]:
        resolved: dict[str, MCPBindingResolution] = {}
        for server_id, current_binding in current.items():
            next_binding = narrowed.get(server_id)
            if next_binding is None:
                continue
            allowed_tools = _intersect_allowed_tool_names(
                current_binding.allowed_tool_names,
                next_binding.allowed_tool_names,
            )
            if allowed_tools == ():
                continue
            resolved[server_id] = MCPBindingResolution(
                server_id=server_id,
                server_key="",
                scope_type=next_binding.scope_type,
                scope_id=next_binding.scope_id,
                allowed_tool_names=allowed_tools,
            )
        return resolved

    @staticmethod
    def _should_restrict_scope(
        snapshot: MCPGovernanceSnapshot,
        scope_type: str,
        scope_id: str,
    ) -> bool:
        normalized_scope = (str(scope_type or "").strip(), str(scope_id or "").strip())
        if not normalized_scope[0] or not normalized_scope[1]:
            return False
        mode = snapshot.scope_modes_by_scope.get(normalized_scope)
        if mode == "restrict":
            return True
        if mode == "inherit":
            return False
        return snapshot.binding_counts_by_scope.get(normalized_scope, 0) > 0


def _intersect_allowed_tool_names(
    left: tuple[str, ...] | None,
    right: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    if left is None and right is None:
        return None
    if left is None:
        return right
    if right is None:
        return left
    intersection = tuple(sorted(set(left).intersection(right)))
    return intersection


def _select_record_for_scope_order(
    records: list[MCPServerBindingRecord],
    *,
    scope_order: tuple[tuple[str, str], ...],
) -> MCPServerBindingRecord:
    scoped_records = {(record.scope_type, record.scope_id): record for record in records}
    for scope in scope_order:
        match = scoped_records.get(scope)
        if match is not None:
            return match
    return records[0]
