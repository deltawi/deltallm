from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from src.db.managed_assets import ManagedAssetAccessRepository
from src.db.route_groups import RouteGroupRepository
from src.models.responses import UserAPIKeyAuth
from src.services.managed_asset_access import (
    MANAGED_ASSET_RESOURCE_BATCH_SIZE,
    AssetKind,
    AssetPrincipal,
    AssetSubjectType,
    AuthorizationSnapshotFreshness,
    GovernanceSource,
    load_managed_asset_resources_in_batches,
)
from src.services.runtime_scopes import resolve_runtime_scope_context


@dataclass(frozen=True, slots=True)
class CreatorRouteGroupAccessSnapshot:
    """Creator route-group access and enabled-member dependencies for one generation."""

    group_keys: frozenset[str]
    public_groups: frozenset[str]
    groups_by_owner: Mapping[str, frozenset[str]]
    groups_by_team: Mapping[str, frozenset[str]]
    groups_by_organization: Mapping[str, frozenset[str]]
    member_model_names_by_group: Mapping[str, frozenset[str]]
    group_key_by_asset_id: Mapping[str, str]
    freshness: AuthorizationSnapshotFreshness

    @classmethod
    def empty(cls, *, max_staleness_seconds: float = 60.0) -> CreatorRouteGroupAccessSnapshot:
        return cls.create(
            group_keys=set(),
            public_groups=set(),
            groups_by_owner={},
            groups_by_team={},
            groups_by_organization={},
            member_model_names_by_group={},
            group_key_by_asset_id={},
            freshness=AuthorizationSnapshotFreshness(max_staleness_seconds),
        )

    @classmethod
    def create(
        cls,
        *,
        group_keys: set[str],
        public_groups: set[str],
        groups_by_owner: dict[str, set[str]],
        groups_by_team: dict[str, set[str]],
        groups_by_organization: dict[str, set[str]],
        member_model_names_by_group: dict[str, set[str]],
        group_key_by_asset_id: dict[str, str],
        freshness: AuthorizationSnapshotFreshness | None = None,
    ) -> CreatorRouteGroupAccessSnapshot:
        return cls(
            group_keys=frozenset(group_keys),
            public_groups=frozenset(public_groups),
            groups_by_owner=MappingProxyType(
                {key: frozenset(value) for key, value in groups_by_owner.items()}
            ),
            groups_by_team=MappingProxyType(
                {key: frozenset(value) for key, value in groups_by_team.items()}
            ),
            groups_by_organization=MappingProxyType(
                {key: frozenset(value) for key, value in groups_by_organization.items()}
            ),
            member_model_names_by_group=MappingProxyType(
                {key: frozenset(value) for key, value in member_model_names_by_group.items()}
            ),
            group_key_by_asset_id=MappingProxyType(dict(group_key_by_asset_id)),
            freshness=freshness or AuthorizationSnapshotFreshness(),
        )

    def visible_group_keys(self, auth: UserAPIKeyAuth) -> frozenset[str]:
        scope = resolve_runtime_scope_context(auth)
        if scope.is_master_key:
            return self.group_keys
        if self.freshness.expired:
            return frozenset()
        visible = set(self.public_groups)
        if scope.owner_account_id:
            visible.update(self.groups_by_owner.get(scope.owner_account_id, ()))
        if scope.team_id:
            visible.update(self.groups_by_team.get(scope.team_id, ()))
        if scope.organization_id:
            visible.update(self.groups_by_organization.get(scope.organization_id, ()))
        return frozenset(visible)

    def deny_asset(self, asset_id: str) -> CreatorRouteGroupAccessSnapshot:
        group_key = self.group_key_by_asset_id.get(asset_id)
        return self.deny_group_keys({group_key} if group_key else set())

    def deny_group_keys(self, group_keys: set[str]) -> CreatorRouteGroupAccessSnapshot:
        denied = {key for key in group_keys if key}
        if not denied:
            return self
        return CreatorRouteGroupAccessSnapshot.create(
            group_keys=set(self.group_keys) | denied,
            public_groups=set(self.public_groups) - denied,
            groups_by_owner={
                key: set(values) - denied for key, values in self.groups_by_owner.items()
            },
            groups_by_team={
                key: set(values) - denied for key, values in self.groups_by_team.items()
            },
            groups_by_organization={
                key: set(values) - denied for key, values in self.groups_by_organization.items()
            },
            member_model_names_by_group={
                key: set(values) for key, values in self.member_model_names_by_group.items()
            },
            group_key_by_asset_id=dict(self.group_key_by_asset_id),
            freshness=self.freshness,
        )


class CreatorRouteGroupAccessService:
    def __init__(
        self,
        access_repository: ManagedAssetAccessRepository,
        route_group_repository: RouteGroupRepository,
        *,
        max_staleness_seconds: float = 60.0,
    ) -> None:
        self.access_repository = access_repository
        self.route_group_repository = route_group_repository
        self.max_staleness_seconds = float(max_staleness_seconds)
        self._snapshot = CreatorRouteGroupAccessSnapshot.empty(
            max_staleness_seconds=self.max_staleness_seconds
        )

    def snapshot(self) -> CreatorRouteGroupAccessSnapshot:
        return self._snapshot

    def replace_snapshot(self, snapshot: CreatorRouteGroupAccessSnapshot) -> None:
        self._snapshot = snapshot

    def mark_reload_failed(self) -> None:
        self._snapshot.freshness.mark_reload_failed()

    def authorization_ready(self) -> bool:
        return self._snapshot.freshness.ready

    async def reload(self) -> CreatorRouteGroupAccessSnapshot:
        policies = await self.access_repository.list_accessible_policies(
            AssetKind.ROUTE_GROUP,
            AssetPrincipal(account_id=None, is_platform_admin=True),
        )
        creator_policies = {
            policy.asset.asset_id: policy
            for policy in policies
            if policy.asset.governance_source is GovernanceSource.CREATOR
        }
        groups = await load_managed_asset_resources_in_batches(
            list(creator_policies),
            self.route_group_repository.list_by_managed_asset_ids,
        )
        member_names_by_id: dict[str, frozenset[str]] = {}
        group_ids = [group.route_group_id for group in groups]
        for start in range(0, len(group_ids), MANAGED_ASSET_RESOURCE_BATCH_SIZE):
            member_names_by_id.update(
                await self.route_group_repository.list_member_model_names_by_group_ids(
                    group_ids[start : start + MANAGED_ASSET_RESOURCE_BATCH_SIZE]
                )
            )

        group_keys: set[str] = set()
        public_groups: set[str] = set()
        groups_by_owner: dict[str, set[str]] = {}
        groups_by_team: dict[str, set[str]] = {}
        groups_by_organization: dict[str, set[str]] = {}
        member_model_names_by_group: dict[str, set[str]] = {}
        group_key_by_asset_id: dict[str, str] = {}

        for group in groups:
            if not group.managed_asset_id:
                continue
            policy = creator_policies.get(group.managed_asset_id)
            if policy is None:
                continue
            group_key = group.group_key
            group_keys.add(group_key)
            group_key_by_asset_id[policy.asset.asset_id] = group_key
            member_model_names_by_group[group_key] = set(
                member_names_by_id.get(group.route_group_id, ())
            )
            if policy.asset.owner_account_id:
                groups_by_owner.setdefault(policy.asset.owner_account_id, set()).add(group_key)
            for grant in policy.grants:
                if grant.subject_type is AssetSubjectType.PUBLIC:
                    public_groups.add(group_key)
                elif grant.subject_type is AssetSubjectType.TEAM and grant.subject_id:
                    groups_by_team.setdefault(grant.subject_id, set()).add(group_key)
                elif grant.subject_type is AssetSubjectType.ORGANIZATION and grant.subject_id:
                    groups_by_organization.setdefault(grant.subject_id, set()).add(group_key)

        snapshot = CreatorRouteGroupAccessSnapshot.create(
            group_keys=group_keys,
            public_groups=public_groups,
            groups_by_owner=groups_by_owner,
            groups_by_team=groups_by_team,
            groups_by_organization=groups_by_organization,
            member_model_names_by_group=member_model_names_by_group,
            group_key_by_asset_id=group_key_by_asset_id,
            freshness=AuthorizationSnapshotFreshness(self.max_staleness_seconds),
        )
        self._snapshot = snapshot
        return snapshot


async def refresh_creator_route_group_access_for_app(
    app: Any,
    *,
    fail_closed_asset_id: str | None = None,
    fail_closed_group_keys: set[str] | None = None,
) -> tuple[str, ...]:
    service = getattr(app.state, "creator_route_group_access_service", None)
    if not isinstance(service, CreatorRouteGroupAccessService):
        return ("Creator route-group authorization refresh unavailable",)
    try:
        snapshot = await service.reload()
    except Exception:
        service.mark_reload_failed()
        snapshot = service.snapshot()
        if fail_closed_asset_id:
            snapshot = snapshot.deny_asset(fail_closed_asset_id)
        if fail_closed_group_keys:
            snapshot = snapshot.deny_group_keys(fail_closed_group_keys)
        service.replace_snapshot(snapshot)
        _publish_snapshot(app, snapshot)
        warnings = ["Creator route-group authorization refresh failed; changed access was disabled"]
        warnings.extend(await _notify_remote_refresh(app))
        return tuple(warnings)
    _publish_snapshot(app, snapshot)
    return await _notify_remote_refresh(app)


def _publish_snapshot(app: Any, snapshot: CreatorRouteGroupAccessSnapshot) -> None:
    from src.router.runtime_generation import (
        RoutingRuntimeGenerationStore,
        with_creator_route_group_access_snapshot,
    )

    store = getattr(app.state, "routing_runtime_generation_store", None)
    if not isinstance(store, RoutingRuntimeGenerationStore):
        return
    current = store.snapshot()
    if current is not None:
        store.replace(with_creator_route_group_access_snapshot(current, snapshot))


async def _notify_remote_refresh(app: Any) -> tuple[str, ...]:
    invalidation = getattr(app.state, "governance_invalidation_service", None)
    if invalidation is None or getattr(invalidation, "redis", None) is None:
        return ()
    if await invalidation.notify("route_groups"):
        return ()
    return ("Creator route-group authorization refresh was not published to peer instances",)
