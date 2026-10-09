from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from src.db.managed_assets import ManagedAssetAccessRepository
from src.db.mcp import MCPRepository
from src.models.responses import UserAPIKeyAuth
from src.services.managed_asset_access import (
    AssetKind,
    AssetPrincipal,
    AssetSubjectType,
    AuthorizationSnapshotFreshness,
    GovernanceSource,
    load_managed_asset_resources_in_batches,
)
from src.services.runtime_scopes import resolve_runtime_scope_context


@dataclass(frozen=True, slots=True)
class CreatorMCPAccessSnapshot:
    """Immutable creator-MCP visibility used by the data plane."""

    server_ids: frozenset[str]
    public_servers: frozenset[str]
    servers_by_owner: Mapping[str, frozenset[str]]
    servers_by_team: Mapping[str, frozenset[str]]
    servers_by_organization: Mapping[str, frozenset[str]]
    asset_id_by_server_id: Mapping[str, str]
    server_id_by_asset_id: Mapping[str, str]
    freshness: AuthorizationSnapshotFreshness

    @classmethod
    def empty(cls, *, max_staleness_seconds: float = 60.0) -> CreatorMCPAccessSnapshot:
        return cls.create(
            server_ids=set(),
            public_servers=set(),
            servers_by_owner={},
            servers_by_team={},
            servers_by_organization={},
            asset_id_by_server_id={},
            freshness=AuthorizationSnapshotFreshness(max_staleness_seconds),
        )

    @classmethod
    def create(
        cls,
        *,
        server_ids: set[str],
        public_servers: set[str],
        servers_by_owner: dict[str, set[str]],
        servers_by_team: dict[str, set[str]],
        servers_by_organization: dict[str, set[str]],
        asset_id_by_server_id: dict[str, str],
        freshness: AuthorizationSnapshotFreshness | None = None,
    ) -> CreatorMCPAccessSnapshot:
        return cls(
            server_ids=frozenset(server_ids),
            public_servers=frozenset(public_servers),
            servers_by_owner=MappingProxyType(
                {key: frozenset(value) for key, value in servers_by_owner.items()}
            ),
            servers_by_team=MappingProxyType(
                {key: frozenset(value) for key, value in servers_by_team.items()}
            ),
            servers_by_organization=MappingProxyType(
                {key: frozenset(value) for key, value in servers_by_organization.items()}
            ),
            asset_id_by_server_id=MappingProxyType(dict(asset_id_by_server_id)),
            server_id_by_asset_id=MappingProxyType(
                {asset_id: server_id for server_id, asset_id in asset_id_by_server_id.items()}
            ),
            freshness=freshness or AuthorizationSnapshotFreshness(),
        )

    def visible_server_ids(self, auth: UserAPIKeyAuth) -> frozenset[str]:
        scope = resolve_runtime_scope_context(auth)
        if scope.is_master_key:
            return self.server_ids
        if self.freshness.expired:
            return frozenset()
        visible = set(self.public_servers)
        if scope.owner_account_id:
            visible.update(self.servers_by_owner.get(scope.owner_account_id, ()))
        if scope.team_id:
            visible.update(self.servers_by_team.get(scope.team_id, ()))
        if scope.organization_id:
            visible.update(self.servers_by_organization.get(scope.organization_id, ()))
        return frozenset(visible)

    def deny_asset(self, asset_id: str) -> CreatorMCPAccessSnapshot:
        server_id = self.server_id_by_asset_id.get(asset_id)
        return self.deny_server_ids({server_id} if server_id else set())

    def deny_server_ids(self, server_ids: set[str]) -> CreatorMCPAccessSnapshot:
        denied = {server_id for server_id in server_ids if server_id}
        if not denied:
            return self
        return CreatorMCPAccessSnapshot.create(
            server_ids=set(self.server_ids) | denied,
            public_servers=set(self.public_servers) - denied,
            servers_by_owner={
                key: set(values) - denied for key, values in self.servers_by_owner.items()
            },
            servers_by_team={
                key: set(values) - denied for key, values in self.servers_by_team.items()
            },
            servers_by_organization={
                key: set(values) - denied for key, values in self.servers_by_organization.items()
            },
            asset_id_by_server_id=dict(self.asset_id_by_server_id),
            freshness=self.freshness,
        )


class CreatorMCPAccessService:
    def __init__(
        self,
        access_repository: ManagedAssetAccessRepository,
        mcp_repository: MCPRepository,
        *,
        max_staleness_seconds: float = 60.0,
    ) -> None:
        self.access_repository = access_repository
        self.mcp_repository = mcp_repository
        self.max_staleness_seconds = float(max_staleness_seconds)
        self._snapshot = CreatorMCPAccessSnapshot.empty(
            max_staleness_seconds=self.max_staleness_seconds
        )

    def snapshot(self) -> CreatorMCPAccessSnapshot:
        return self._snapshot

    def replace_snapshot(self, snapshot: CreatorMCPAccessSnapshot) -> None:
        self._snapshot = snapshot

    def mark_reload_failed(self) -> None:
        self._snapshot.freshness.mark_reload_failed()

    def authorization_ready(self) -> bool:
        return self._snapshot.freshness.ready

    async def reload(self) -> CreatorMCPAccessSnapshot:
        policies = await self.access_repository.list_accessible_policies(
            AssetKind.MCP_SERVER,
            AssetPrincipal(account_id=None, is_platform_admin=True),
        )
        creator_policies = {
            policy.asset.asset_id: policy
            for policy in policies
            if policy.asset.governance_source is GovernanceSource.CREATOR
        }
        servers = await load_managed_asset_resources_in_batches(
            list(creator_policies),
            self.mcp_repository.list_by_managed_asset_ids,
        )

        server_ids: set[str] = set()
        public_servers: set[str] = set()
        servers_by_owner: dict[str, set[str]] = {}
        servers_by_team: dict[str, set[str]] = {}
        servers_by_organization: dict[str, set[str]] = {}
        asset_id_by_server_id: dict[str, str] = {}

        for server in servers:
            if not server.managed_asset_id:
                continue
            policy = creator_policies.get(server.managed_asset_id)
            if policy is None:
                continue
            server_id = server.mcp_server_id
            server_ids.add(server_id)
            asset_id_by_server_id[server_id] = policy.asset.asset_id
            if policy.asset.owner_account_id:
                servers_by_owner.setdefault(policy.asset.owner_account_id, set()).add(server_id)
            for grant in policy.grants:
                if grant.subject_type is AssetSubjectType.PUBLIC:
                    public_servers.add(server_id)
                elif grant.subject_type is AssetSubjectType.TEAM and grant.subject_id:
                    servers_by_team.setdefault(grant.subject_id, set()).add(server_id)
                elif grant.subject_type is AssetSubjectType.ORGANIZATION and grant.subject_id:
                    servers_by_organization.setdefault(grant.subject_id, set()).add(server_id)

        snapshot = CreatorMCPAccessSnapshot.create(
            server_ids=server_ids,
            public_servers=public_servers,
            servers_by_owner=servers_by_owner,
            servers_by_team=servers_by_team,
            servers_by_organization=servers_by_organization,
            asset_id_by_server_id=asset_id_by_server_id,
            freshness=AuthorizationSnapshotFreshness(self.max_staleness_seconds),
        )
        self._snapshot = snapshot
        return snapshot


async def refresh_creator_mcp_access_for_app(
    app: Any,
    *,
    fail_closed_asset_id: str | None = None,
    fail_closed_server_ids: set[str] | None = None,
) -> tuple[str, ...]:
    service = getattr(app.state, "creator_mcp_access_service", None)
    if not isinstance(service, CreatorMCPAccessService):
        return ("Creator MCP authorization refresh unavailable",)
    try:
        await service.reload()
    except Exception:
        service.mark_reload_failed()
        snapshot = service.snapshot()
        if fail_closed_asset_id:
            snapshot = snapshot.deny_asset(fail_closed_asset_id)
        if fail_closed_server_ids:
            snapshot = snapshot.deny_server_ids(fail_closed_server_ids)
        service.replace_snapshot(snapshot)
        warnings = ["Creator MCP authorization refresh failed; changed access was disabled"]
        warnings.extend(await _notify_remote_refresh(app))
        return tuple(warnings)
    return await _notify_remote_refresh(app)


async def _notify_remote_refresh(app: Any) -> tuple[str, ...]:
    invalidation = getattr(app.state, "governance_invalidation_service", None)
    if invalidation is None or getattr(invalidation, "redis", None) is None:
        return ()
    if await invalidation.notify("mcp"):
        return ()
    return ("Creator MCP authorization refresh was not published to peer instances",)
