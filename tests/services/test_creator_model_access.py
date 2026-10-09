from __future__ import annotations

import pytest

from src.db.logical_models import LogicalModelRecord
from src.models.responses import UserAPIKeyAuth
from src.services.creator_model_access import CreatorModelAccessService, CreatorModelAccessSnapshot
from src.services.managed_asset_access import (
    AssetAccessPolicy,
    AssetAccessRole,
    AssetGrant,
    AssetKind,
    AssetSubjectType,
    GovernanceSource,
    ManagedAsset,
)


def _policy(
    asset_id: str,
    model_owner: str,
    *,
    subject_type: AssetSubjectType | None = None,
    subject_id: str | None = None,
) -> AssetAccessPolicy:
    return AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id=asset_id,
            asset_kind=AssetKind.MODEL,
            governance_source=GovernanceSource.CREATOR,
            owner_account_id=model_owner,
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
        assert asset_kind is AssetKind.MODEL
        assert principal.is_platform_admin
        return self.policies


class _LogicalRepository:
    def __init__(self, records: list[LogicalModelRecord]) -> None:
        self.records = records

    async def list_by_managed_asset_ids(self, asset_ids: list[str]) -> list[LogicalModelRecord]:
        return [record for record in self.records if record.managed_asset_id in asset_ids]


@pytest.mark.asyncio
async def test_creator_model_snapshot_compiles_owner_team_org_and_public_access() -> None:
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
        AssetAccessPolicy(
            asset=ManagedAsset(
                asset_id="asset-platform",
                asset_kind=AssetKind.MODEL,
                governance_source=GovernanceSource.PLATFORM,
                owner_account_id=None,
            ),
            grant=AssetGrant(
                managed_asset_id="asset-platform",
                subject_type=AssetSubjectType.PUBLIC,
                subject_id=None,
                access_role=AssetAccessRole.READER,
            ),
        ),
    ]
    records = [
        LogicalModelRecord(
            model_id=f"model-{index}",
            model_name=name,
            managed_asset_id=asset_id,
        )
        for index, (asset_id, name) in enumerate(
            (
                ("asset-private", "private-model"),
                ("asset-team", "team-model"),
                ("asset-org", "org-model"),
                ("asset-public", "public-model"),
                ("asset-platform", "platform-model"),
            )
        )
    ]
    service = CreatorModelAccessService(
        _AccessRepository(policies),  # type: ignore[arg-type]
        _LogicalRepository(records),  # type: ignore[arg-type]
    )

    snapshot = await service.reload()

    assert snapshot.model_names == {
        "private-model",
        "team-model",
        "org-model",
        "public-model",
    }
    assert snapshot.visible_models(
        UserAPIKeyAuth(
            api_key="sk-test",
            owner_account_id="owner-1",
            team_id="team-1",
            organization_id="org-1",
        )
    ) == {"private-model", "team-model", "org-model", "public-model"}
    assert snapshot.visible_models(UserAPIKeyAuth(api_key="sk-outsider")) == {"public-model"}


def test_fail_closed_snapshot_keeps_creator_classification_but_erases_access() -> None:
    snapshot = CreatorModelAccessSnapshot.create(
        model_names={"creator-model"},
        public_models={"creator-model"},
        models_by_owner={"owner-1": {"creator-model"}},
        models_by_team={"team-1": {"creator-model"}},
        models_by_organization={"org-1": {"creator-model"}},
        model_name_by_asset_id={"asset-1": "creator-model"},
    ).deny_asset("asset-1")

    assert snapshot.model_names == {"creator-model"}
    assert snapshot.public_models == frozenset()
    assert snapshot.models_by_owner["owner-1"] == frozenset()
    assert snapshot.models_by_team["team-1"] == frozenset()
    assert snapshot.models_by_organization["org-1"] == frozenset()
    assert snapshot.model_name_by_asset_id == {"asset-1": "creator-model"}


def test_expired_snapshot_denies_creator_models_but_keeps_master_key_override() -> None:
    snapshot = CreatorModelAccessSnapshot.create(
        model_names={"creator-model"},
        public_models={"creator-model"},
        models_by_owner={},
        models_by_team={},
        models_by_organization={},
        model_name_by_asset_id={"asset-1": "creator-model"},
    )
    snapshot.freshness.max_staleness_seconds = 0
    snapshot.freshness.mark_reload_failed()

    assert snapshot.visible_models(UserAPIKeyAuth(api_key="sk-user")) == frozenset()
    assert snapshot.visible_models(UserAPIKeyAuth(api_key="master_key")) == {"creator-model"}
