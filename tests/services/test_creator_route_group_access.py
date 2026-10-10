from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.db.routing.route_groups import RouteGroupRecord
from src.models.errors import PermissionDeniedError
from src.models.responses import UserAPIKeyAuth
from src.services.access.creator_model_access import CreatorModelAccessSnapshot
from src.services.access.creator_route_group_access import (
    CreatorRouteGroupAccessService,
    CreatorRouteGroupAccessSnapshot,
)
from src.services.access.managed_asset_access import (
    AssetAccessPolicy,
    AssetAccessRole,
    AssetGrant,
    AssetKind,
    AssetSubjectType,
    GovernanceSource,
    ManagedAsset,
)
from src.services.access.model_visibility import ensure_model_allowed


def _policy(
    asset_id: str,
    owner: str | None,
    *,
    governance_source: GovernanceSource = GovernanceSource.CREATOR,
    subject_type: AssetSubjectType | None = None,
    subject_id: str | None = None,
) -> AssetAccessPolicy:
    return AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id=asset_id,
            asset_kind=AssetKind.ROUTE_GROUP,
            governance_source=governance_source,
            owner_account_id=owner,
        ),
        grant=(
            AssetGrant(
                managed_asset_id=asset_id,
                subject_type=subject_type,
                subject_id=subject_id,
                access_role=AssetAccessRole.READER,
            )
            if subject_type is not None
            else None
        ),
    )


class _AccessRepository:
    def __init__(self, policies: list[AssetAccessPolicy]) -> None:
        self.policies = policies

    async def list_accessible_policies(self, asset_kind, principal):  # noqa: ANN001, ANN201
        assert asset_kind is AssetKind.ROUTE_GROUP
        assert principal.is_platform_admin
        return self.policies


class _RouteGroupRepository:
    def __init__(
        self,
        groups: list[RouteGroupRecord],
        member_names: dict[str, frozenset[str]],
    ) -> None:
        self.groups = groups
        self.member_names = member_names
        self.member_lookup_batches: list[list[str]] = []

    async def list_by_managed_asset_ids(  # noqa: ANN201
        self,
        asset_ids: list[str],
    ):
        return [group for group in self.groups if group.managed_asset_id in asset_ids]

    async def list_member_model_names_by_group_ids(  # noqa: ANN201
        self,
        group_ids: list[str],
    ):
        self.member_lookup_batches.append(group_ids)
        return {group_id: self.member_names.get(group_id, frozenset()) for group_id in group_ids}


class _GrantService:
    def __init__(self, direct: frozenset[str] | None = None) -> None:
        self.direct = direct

    def resolve_policy_allowlist(self, auth, *, snapshot=None):  # noqa: ANN001, ANN201
        del auth, snapshot
        return SimpleNamespace(
            allowlist=frozenset({"platform-model"}),
            authoritative=True,
            fallback_reason=None,
        )

    def resolve_direct_restrict_allowlist(self, auth, *, snapshot=None):  # noqa: ANN001, ANN201
        del auth, snapshot
        return self.direct


class _TierService:
    mode = "enforce"
    snapshot_stale = False

    def __init__(self, allowed: set[str]) -> None:
        self.allowed = frozenset(allowed)

    def resolve_org_allowed_callable_keys(self, organization_id: str) -> frozenset[str]:
        assert organization_id == "org-1"
        return self.allowed


@pytest.mark.asyncio
async def test_creator_route_group_snapshot_compiles_access_and_enabled_members() -> None:
    policies = [
        _policy("asset-private", "owner-1"),
        _policy(
            "asset-team",
            "owner-2",
            subject_type=AssetSubjectType.TEAM,
            subject_id="team-1",
        ),
        _policy(
            "asset-org",
            "owner-3",
            subject_type=AssetSubjectType.ORGANIZATION,
            subject_id="org-1",
        ),
        _policy(
            "asset-public",
            "owner-4",
            subject_type=AssetSubjectType.PUBLIC,
        ),
        _policy(
            "asset-platform",
            None,
            governance_source=GovernanceSource.PLATFORM,
        ),
    ]
    groups = [
        RouteGroupRecord(
            route_group_id=f"group-{index}",
            group_key=name,
            managed_asset_id=asset_id,
        )
        for index, (asset_id, name) in enumerate(
            (
                ("asset-private", "private-group"),
                ("asset-team", "team-group"),
                ("asset-org", "org-group"),
                ("asset-public", "public-group"),
                ("asset-platform", "platform-group"),
            )
        )
    ]
    service = CreatorRouteGroupAccessService(
        _AccessRepository(policies),  # type: ignore[arg-type]
        _RouteGroupRepository(
            groups,
            {"group-1": frozenset({"creator-model", "platform-model"})},
        ),  # type: ignore[arg-type]
    )

    snapshot = await service.reload()

    assert snapshot.group_keys == {
        "private-group",
        "team-group",
        "org-group",
        "public-group",
    }
    assert snapshot.member_model_names_by_group["team-group"] == {
        "creator-model",
        "platform-model",
    }
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        owner_account_id="owner-1",
        team_id="team-1",
        organization_id="org-1",
    )
    assert snapshot.visible_group_keys(auth) == {
        "private-group",
        "team-group",
        "org-group",
        "public-group",
    }
    assert snapshot.visible_group_keys(UserAPIKeyAuth(api_key="sk-outsider")) == {"public-group"}


@pytest.mark.asyncio
async def test_creator_route_group_snapshot_batches_member_lookups() -> None:
    policies = [_policy(f"asset-{index}", "owner-1") for index in range(501)]
    groups = [
        RouteGroupRecord(
            route_group_id=f"group-{index}",
            group_key=f"key-{index}",
            managed_asset_id=f"asset-{index}",
        )
        for index in range(501)
    ]
    repository = _RouteGroupRepository(groups, {})
    service = CreatorRouteGroupAccessService(
        _AccessRepository(policies),  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
    )

    await service.reload()

    assert [len(batch) for batch in repository.member_lookup_batches] == [500, 1]


def test_creator_route_group_runtime_requires_group_and_every_member() -> None:
    group_snapshot = CreatorRouteGroupAccessSnapshot.create(
        group_keys={"creator-group"},
        public_groups=set(),
        groups_by_owner={},
        groups_by_team={"team-1": {"creator-group"}},
        groups_by_organization={},
        member_model_names_by_group={"creator-group": {"creator-model", "platform-model"}},
        group_key_by_asset_id={"asset-group": "creator-group"},
    )
    model_snapshot = CreatorModelAccessSnapshot.create(
        model_names={"creator-model"},
        public_models=set(),
        models_by_owner={},
        models_by_team={"team-1": {"creator-model"}},
        models_by_organization={},
        model_name_by_asset_id={"asset-model": "creator-model"},
    )
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        team_id="team-1",
        organization_id="org-1",
    )

    ensure_model_allowed(
        auth,
        "creator-group",
        creator_model_access_snapshot=model_snapshot,
        creator_route_group_access_snapshot=group_snapshot,
        callable_target_grant_service=_GrantService(),  # type: ignore[arg-type]
        tier_policy_service=_TierService({"platform-model"}),  # type: ignore[arg-type]
        tier_policy_mode="enforce",
    )

    with pytest.raises(PermissionDeniedError):
        ensure_model_allowed(
            auth,
            "creator-group",
            creator_model_access_snapshot=model_snapshot,
            creator_route_group_access_snapshot=group_snapshot,
            callable_target_grant_service=_GrantService(),  # type: ignore[arg-type]
            tier_policy_service=_TierService(set()),  # type: ignore[arg-type]
            tier_policy_mode="enforce",
        )

    with pytest.raises(PermissionDeniedError):
        ensure_model_allowed(
            UserAPIKeyAuth(api_key="sk-outsider", organization_id="org-1"),
            "creator-group",
            creator_model_access_snapshot=model_snapshot,
            creator_route_group_access_snapshot=group_snapshot,
            callable_target_grant_service=_GrantService(),  # type: ignore[arg-type]
            tier_policy_service=_TierService({"platform-model"}),  # type: ignore[arg-type]
            tier_policy_mode="enforce",
        )


def test_fail_closed_route_group_snapshot_retains_creator_classification() -> None:
    snapshot = CreatorRouteGroupAccessSnapshot.create(
        group_keys={"creator-group"},
        public_groups={"creator-group"},
        groups_by_owner={"owner-1": {"creator-group"}},
        groups_by_team={"team-1": {"creator-group"}},
        groups_by_organization={"org-1": {"creator-group"}},
        member_model_names_by_group={"creator-group": {"model-1"}},
        group_key_by_asset_id={"asset-1": "creator-group"},
    ).deny_asset("asset-1")

    assert snapshot.group_keys == {"creator-group"}
    assert snapshot.public_groups == frozenset()
    assert snapshot.groups_by_owner["owner-1"] == frozenset()
    assert snapshot.groups_by_team["team-1"] == frozenset()
    assert snapshot.groups_by_organization["org-1"] == frozenset()
    assert snapshot.member_model_names_by_group["creator-group"] == {"model-1"}
