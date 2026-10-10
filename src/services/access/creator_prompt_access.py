from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from src.db.catalog.managed_assets import ManagedAssetAccessRepository
from src.db.catalog.prompt_registry import PromptRegistryRepository
from src.services.access.managed_asset_access import (
    AssetKind,
    AssetPrincipal,
    AssetSubjectType,
    AuthorizationSnapshotFreshness,
    GovernanceSource,
    load_managed_asset_resources_in_batches,
)
from src.services.access.runtime_scopes import RuntimeScopeContext


@dataclass(frozen=True, slots=True)
class CreatorPromptAccessSnapshot:
    """Immutable creator-prompt authorization input pinned to a routing generation."""

    template_keys: frozenset[str]
    public_templates: frozenset[str]
    templates_by_owner: Mapping[str, frozenset[str]]
    templates_by_team: Mapping[str, frozenset[str]]
    templates_by_organization: Mapping[str, frozenset[str]]
    template_key_by_asset_id: Mapping[str, str]
    freshness: AuthorizationSnapshotFreshness

    @classmethod
    def empty(cls, *, max_staleness_seconds: float = 60.0) -> CreatorPromptAccessSnapshot:
        return cls.create(
            template_keys=set(),
            public_templates=set(),
            templates_by_owner={},
            templates_by_team={},
            templates_by_organization={},
            template_key_by_asset_id={},
            freshness=AuthorizationSnapshotFreshness(max_staleness_seconds),
        )

    @classmethod
    def create(
        cls,
        *,
        template_keys: set[str],
        public_templates: set[str],
        templates_by_owner: dict[str, set[str]],
        templates_by_team: dict[str, set[str]],
        templates_by_organization: dict[str, set[str]],
        template_key_by_asset_id: dict[str, str],
        freshness: AuthorizationSnapshotFreshness | None = None,
    ) -> CreatorPromptAccessSnapshot:
        return cls(
            template_keys=frozenset(template_keys),
            public_templates=frozenset(public_templates),
            templates_by_owner=MappingProxyType(
                {key: frozenset(value) for key, value in templates_by_owner.items()}
            ),
            templates_by_team=MappingProxyType(
                {key: frozenset(value) for key, value in templates_by_team.items()}
            ),
            templates_by_organization=MappingProxyType(
                {key: frozenset(value) for key, value in templates_by_organization.items()}
            ),
            template_key_by_asset_id=MappingProxyType(dict(template_key_by_asset_id)),
            freshness=freshness or AuthorizationSnapshotFreshness(),
        )

    def can_use(self, template_key: str, scope: RuntimeScopeContext | None) -> bool:
        if template_key not in self.template_keys:
            return True
        if scope is None:
            return False
        if scope.is_master_key:
            return True
        if self.freshness.expired:
            return False
        if template_key in self.public_templates:
            return True
        if scope.owner_account_id and template_key in self.templates_by_owner.get(
            scope.owner_account_id, ()
        ):
            return True
        if scope.team_id and template_key in self.templates_by_team.get(scope.team_id, ()):
            return True
        return bool(
            scope.organization_id
            and template_key in self.templates_by_organization.get(scope.organization_id, ())
        )

    def deny_asset(self, asset_id: str) -> CreatorPromptAccessSnapshot:
        template_key = self.template_key_by_asset_id.get(asset_id)
        return self.deny_template_keys({template_key} if template_key else set())

    def deny_template_keys(self, template_keys: set[str]) -> CreatorPromptAccessSnapshot:
        denied = {key for key in template_keys if key}
        if not denied:
            return self
        return CreatorPromptAccessSnapshot.create(
            # Retain classification so a failed refresh cannot make a creator prompt
            # fall through to the unrestricted platform-prompt path.
            template_keys=set(self.template_keys) | denied,
            public_templates=set(self.public_templates) - denied,
            templates_by_owner={
                key: set(values) - denied for key, values in self.templates_by_owner.items()
            },
            templates_by_team={
                key: set(values) - denied for key, values in self.templates_by_team.items()
            },
            templates_by_organization={
                key: set(values) - denied for key, values in self.templates_by_organization.items()
            },
            template_key_by_asset_id=dict(self.template_key_by_asset_id),
            freshness=self.freshness,
        )


class CreatorPromptAccessService:
    def __init__(
        self,
        access_repository: ManagedAssetAccessRepository,
        prompt_repository: PromptRegistryRepository,
        *,
        max_staleness_seconds: float = 60.0,
    ) -> None:
        self.access_repository = access_repository
        self.prompt_repository = prompt_repository
        self.max_staleness_seconds = float(max_staleness_seconds)
        self._snapshot = CreatorPromptAccessSnapshot.empty(
            max_staleness_seconds=self.max_staleness_seconds
        )

    def snapshot(self) -> CreatorPromptAccessSnapshot:
        return self._snapshot

    def replace_snapshot(self, snapshot: CreatorPromptAccessSnapshot) -> None:
        self._snapshot = snapshot

    def mark_reload_failed(self) -> None:
        self._snapshot.freshness.mark_reload_failed()

    def authorization_ready(self) -> bool:
        return self._snapshot.freshness.ready

    async def reload(self) -> CreatorPromptAccessSnapshot:
        policies = await self.access_repository.list_accessible_policies(
            AssetKind.PROMPT_TEMPLATE,
            AssetPrincipal(account_id=None, is_platform_admin=True),
        )
        creator_policies = {
            policy.asset.asset_id: policy
            for policy in policies
            if policy.asset.governance_source is GovernanceSource.CREATOR
        }
        templates = await load_managed_asset_resources_in_batches(
            list(creator_policies),
            self.prompt_repository.list_by_managed_asset_ids,
        )

        template_keys: set[str] = set()
        public_templates: set[str] = set()
        templates_by_owner: dict[str, set[str]] = {}
        templates_by_team: dict[str, set[str]] = {}
        templates_by_organization: dict[str, set[str]] = {}
        template_key_by_asset_id: dict[str, str] = {}

        for template in templates:
            if not template.managed_asset_id:
                continue
            policy = creator_policies.get(template.managed_asset_id)
            if policy is None:
                continue
            template_key = template.template_key
            template_keys.add(template_key)
            template_key_by_asset_id[policy.asset.asset_id] = template_key
            if policy.asset.owner_account_id:
                templates_by_owner.setdefault(policy.asset.owner_account_id, set()).add(
                    template_key
                )
            for grant in policy.grants:
                if grant.subject_type is AssetSubjectType.PUBLIC:
                    public_templates.add(template_key)
                elif grant.subject_type is AssetSubjectType.TEAM and grant.subject_id:
                    templates_by_team.setdefault(grant.subject_id, set()).add(template_key)
                elif grant.subject_type is AssetSubjectType.ORGANIZATION and grant.subject_id:
                    templates_by_organization.setdefault(grant.subject_id, set()).add(template_key)

        snapshot = CreatorPromptAccessSnapshot.create(
            template_keys=template_keys,
            public_templates=public_templates,
            templates_by_owner=templates_by_owner,
            templates_by_team=templates_by_team,
            templates_by_organization=templates_by_organization,
            template_key_by_asset_id=template_key_by_asset_id,
            freshness=AuthorizationSnapshotFreshness(self.max_staleness_seconds),
        )
        self._snapshot = snapshot
        return snapshot


async def refresh_creator_prompt_access_for_app(
    app: Any,
    *,
    fail_closed_asset_id: str | None = None,
    fail_closed_template_keys: set[str] | None = None,
) -> tuple[str, ...]:
    """Reload and publish prompt access, denying changed templates if reload fails."""

    service = getattr(app.state, "creator_prompt_access_service", None)
    if not isinstance(service, CreatorPromptAccessService):
        return ("Creator-prompt authorization refresh unavailable",)
    try:
        snapshot = await service.reload()
    except Exception:
        service.mark_reload_failed()
        snapshot = service.snapshot()
        if fail_closed_asset_id:
            snapshot = snapshot.deny_asset(fail_closed_asset_id)
        if fail_closed_template_keys:
            snapshot = snapshot.deny_template_keys(fail_closed_template_keys)
        service.replace_snapshot(snapshot)
        _publish_snapshot(app, snapshot)
        warnings = ["Creator-prompt authorization refresh failed; changed access was disabled"]
        warnings.extend(await _notify_remote_refresh(app))
        return tuple(warnings)
    _publish_snapshot(app, snapshot)
    return await _notify_remote_refresh(app)


def _publish_snapshot(app: Any, snapshot: CreatorPromptAccessSnapshot) -> None:
    from src.router.runtime_generation import (
        RoutingRuntimeGenerationStore,
        with_creator_prompt_access_snapshot,
    )

    store = getattr(app.state, "routing_runtime_generation_store", None)
    if not isinstance(store, RoutingRuntimeGenerationStore):
        return
    current = store.snapshot()
    if current is not None:
        store.replace(with_creator_prompt_access_snapshot(current, snapshot))


async def _notify_remote_refresh(app: Any) -> tuple[str, ...]:
    invalidation = getattr(app.state, "governance_invalidation_service", None)
    if invalidation is None or getattr(invalidation, "redis", None) is None:
        return ()
    if await invalidation.notify("prompt"):
        return ()
    return ("Creator-prompt authorization refresh was not published to peer instances",)
