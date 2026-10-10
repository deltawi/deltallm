from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from src.db.catalog.logical_models import LogicalModelRepository
from src.db.catalog.managed_assets import ManagedAssetAccessRepository
from src.models.responses import UserAPIKeyAuth
from src.services.access.managed_asset_access import (
    AssetKind,
    AssetPrincipal,
    AssetSubjectType,
    AuthorizationSnapshotFreshness,
    GovernanceSource,
    load_managed_asset_resources_in_batches,
)
from src.services.access.runtime_scopes import resolve_runtime_scope_context


@dataclass(frozen=True, slots=True)
class CreatorModelAccessSnapshot:
    """Immutable creator-model authorization input pinned to one routing generation."""

    model_names: frozenset[str]
    public_models: frozenset[str]
    models_by_owner: Mapping[str, frozenset[str]]
    models_by_team: Mapping[str, frozenset[str]]
    models_by_organization: Mapping[str, frozenset[str]]
    model_name_by_asset_id: Mapping[str, str]
    freshness: AuthorizationSnapshotFreshness

    @classmethod
    def empty(cls, *, max_staleness_seconds: float = 60.0) -> CreatorModelAccessSnapshot:
        return cls(
            model_names=frozenset(),
            public_models=frozenset(),
            models_by_owner=MappingProxyType({}),
            models_by_team=MappingProxyType({}),
            models_by_organization=MappingProxyType({}),
            model_name_by_asset_id=MappingProxyType({}),
            freshness=AuthorizationSnapshotFreshness(max_staleness_seconds),
        )

    @classmethod
    def create(
        cls,
        *,
        model_names: set[str],
        public_models: set[str],
        models_by_owner: dict[str, set[str]],
        models_by_team: dict[str, set[str]],
        models_by_organization: dict[str, set[str]],
        model_name_by_asset_id: dict[str, str],
        freshness: AuthorizationSnapshotFreshness | None = None,
    ) -> CreatorModelAccessSnapshot:
        return cls(
            model_names=frozenset(model_names),
            public_models=frozenset(public_models),
            models_by_owner=MappingProxyType(
                {key: frozenset(value) for key, value in models_by_owner.items()}
            ),
            models_by_team=MappingProxyType(
                {key: frozenset(value) for key, value in models_by_team.items()}
            ),
            models_by_organization=MappingProxyType(
                {key: frozenset(value) for key, value in models_by_organization.items()}
            ),
            model_name_by_asset_id=MappingProxyType(dict(model_name_by_asset_id)),
            freshness=freshness or AuthorizationSnapshotFreshness(),
        )

    def visible_models(self, auth: UserAPIKeyAuth) -> frozenset[str]:
        scope = resolve_runtime_scope_context(auth)
        if scope.is_master_key:
            return self.model_names
        if self.freshness.expired:
            return frozenset()
        visible = set(self.public_models)
        if scope.owner_account_id:
            visible.update(self.models_by_owner.get(scope.owner_account_id, ()))
        if scope.team_id:
            visible.update(self.models_by_team.get(scope.team_id, ()))
        if scope.organization_id:
            visible.update(self.models_by_organization.get(scope.organization_id, ()))
        return frozenset(visible)

    def deny_asset(self, asset_id: str) -> CreatorModelAccessSnapshot:
        model_name = self.model_name_by_asset_id.get(asset_id)
        return self.deny_model_names({model_name} if model_name else set())

    def deny_model_names(self, model_names: set[str]) -> CreatorModelAccessSnapshot:
        denied = {name for name in model_names if name}
        if not denied:
            return self
        return CreatorModelAccessSnapshot.create(
            # Keep denied names classified as creator-governed so they cannot fall
            # through to the independent platform/tier branch.
            model_names=set(self.model_names) | denied,
            public_models=set(self.public_models) - denied,
            models_by_owner={
                key: set(values) - denied for key, values in self.models_by_owner.items()
            },
            models_by_team={
                key: set(values) - denied for key, values in self.models_by_team.items()
            },
            models_by_organization={
                key: set(values) - denied for key, values in self.models_by_organization.items()
            },
            model_name_by_asset_id=dict(self.model_name_by_asset_id),
            freshness=self.freshness,
        )


class CreatorModelAccessService:
    def __init__(
        self,
        access_repository: ManagedAssetAccessRepository,
        logical_model_repository: LogicalModelRepository,
        *,
        max_staleness_seconds: float = 60.0,
    ) -> None:
        self.access_repository = access_repository
        self.logical_model_repository = logical_model_repository
        self.max_staleness_seconds = float(max_staleness_seconds)
        self._snapshot = CreatorModelAccessSnapshot.empty(
            max_staleness_seconds=self.max_staleness_seconds
        )

    def snapshot(self) -> CreatorModelAccessSnapshot:
        return self._snapshot

    def replace_snapshot(self, snapshot: CreatorModelAccessSnapshot) -> None:
        self._snapshot = snapshot

    def mark_reload_failed(self) -> None:
        self._snapshot.freshness.mark_reload_failed()

    def authorization_ready(self) -> bool:
        return self._snapshot.freshness.ready

    async def reload(self) -> CreatorModelAccessSnapshot:
        policies = await self.access_repository.list_accessible_policies(
            AssetKind.MODEL,
            AssetPrincipal(account_id=None, is_platform_admin=True),
        )
        creator_policies = {
            policy.asset.asset_id: policy
            for policy in policies
            if policy.asset.governance_source is GovernanceSource.CREATOR
        }
        logical_models = await load_managed_asset_resources_in_batches(
            list(creator_policies),
            self.logical_model_repository.list_by_managed_asset_ids,
        )

        model_names: set[str] = set()
        public_models: set[str] = set()
        models_by_owner: dict[str, set[str]] = {}
        models_by_team: dict[str, set[str]] = {}
        models_by_organization: dict[str, set[str]] = {}
        model_name_by_asset_id: dict[str, str] = {}

        for logical_model in logical_models:
            policy = creator_policies.get(logical_model.managed_asset_id)
            if policy is None:
                continue
            model_name = logical_model.model_name
            model_names.add(model_name)
            model_name_by_asset_id[policy.asset.asset_id] = model_name
            if policy.asset.owner_account_id:
                models_by_owner.setdefault(policy.asset.owner_account_id, set()).add(model_name)
            for grant in policy.grants:
                if grant.subject_type is AssetSubjectType.PUBLIC:
                    public_models.add(model_name)
                elif grant.subject_type is AssetSubjectType.TEAM and grant.subject_id:
                    models_by_team.setdefault(grant.subject_id, set()).add(model_name)
                elif grant.subject_type is AssetSubjectType.ORGANIZATION and grant.subject_id:
                    models_by_organization.setdefault(grant.subject_id, set()).add(model_name)

        snapshot = CreatorModelAccessSnapshot.create(
            model_names=model_names,
            public_models=public_models,
            models_by_owner=models_by_owner,
            models_by_team=models_by_team,
            models_by_organization=models_by_organization,
            model_name_by_asset_id=model_name_by_asset_id,
            freshness=AuthorizationSnapshotFreshness(self.max_staleness_seconds),
        )
        self._snapshot = snapshot
        return snapshot


async def refresh_creator_model_access_for_app(
    app: Any,
    *,
    fail_closed_asset_id: str | None = None,
    fail_closed_model_names: set[str] | None = None,
) -> tuple[str, ...]:
    """Reload and atomically publish creator access, removing changed assets on failure."""

    service = getattr(app.state, "creator_model_access_service", None)
    if not isinstance(service, CreatorModelAccessService):
        return ("Creator-model authorization refresh unavailable",)
    try:
        snapshot = await service.reload()
    except Exception:
        service.mark_reload_failed()
        snapshot = service.snapshot()
        if fail_closed_asset_id:
            snapshot = snapshot.deny_asset(fail_closed_asset_id)
        if fail_closed_model_names:
            snapshot = snapshot.deny_model_names(fail_closed_model_names)
        service.replace_snapshot(snapshot)
        _publish_snapshot(app, snapshot)
        warnings = ["Creator-model authorization refresh failed; changed access was disabled"]
        warnings.extend(await _notify_remote_refresh(app))
        return tuple(warnings)
    _publish_snapshot(app, snapshot)
    return await _notify_remote_refresh(app)


def _publish_snapshot(app: Any, snapshot: CreatorModelAccessSnapshot) -> None:
    from src.router.runtime_generation import (
        RoutingRuntimeGenerationStore,
        with_creator_model_access_snapshot,
    )

    store = getattr(app.state, "routing_runtime_generation_store", None)
    if not isinstance(store, RoutingRuntimeGenerationStore):
        return
    current = store.snapshot()
    if current is not None:
        store.replace(with_creator_model_access_snapshot(current, snapshot))


async def _notify_remote_refresh(app: Any) -> tuple[str, ...]:
    invalidation = getattr(app.state, "governance_invalidation_service", None)
    if invalidation is None or getattr(invalidation, "redis", None) is None:
        return ()
    if await invalidation.notify("creator_model"):
        return ()
    return ("Creator-model authorization refresh was not published to peer instances",)
