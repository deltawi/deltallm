from __future__ import annotations

import pytest

from src.db.prompt_registry import PromptTemplateRecord
from src.models.responses import UserAPIKeyAuth
from src.services.creator_prompt_access import (
    CreatorPromptAccessService,
    CreatorPromptAccessSnapshot,
)
from src.services.managed_asset_access import (
    AssetAccessPolicy,
    AssetAccessRole,
    AssetGrant,
    AssetKind,
    AssetSubjectType,
    GovernanceSource,
    ManagedAsset,
)
from src.services.runtime_scopes import annotate_auth_metadata, resolve_runtime_scope_context


def _policy(
    asset_id: str,
    owner: str,
    *,
    subject_type: AssetSubjectType | None = None,
    subject_id: str | None = None,
) -> AssetAccessPolicy:
    return AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id=asset_id,
            asset_kind=AssetKind.PROMPT_TEMPLATE,
            governance_source=GovernanceSource.CREATOR,
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
        assert asset_kind is AssetKind.PROMPT_TEMPLATE
        assert principal.is_platform_admin
        return self.policies


class _PromptRepository:
    def __init__(self, records: list[PromptTemplateRecord]) -> None:
        self.records = records

    async def list_by_managed_asset_ids(
        self, asset_ids: list[str]
    ) -> list[PromptTemplateRecord]:
        return [record for record in self.records if record.managed_asset_id in asset_ids]


def _scope(
    *,
    owner: str | None = None,
    team: str | None = None,
    organization: str | None = None,
    master: bool = False,
):  # noqa: ANN202
    auth = annotate_auth_metadata(
        UserAPIKeyAuth(
            api_key="master_key" if master else "sk-test",
            owner_account_id=owner,
            team_id=team,
            organization_id=organization,
        ),
        auth_source="master_key" if master else "api_key",
        is_master_key=master,
    )
    return resolve_runtime_scope_context(auth)


@pytest.mark.asyncio
async def test_creator_prompt_snapshot_compiles_owner_team_org_and_public_access() -> None:
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
                asset_kind=AssetKind.PROMPT_TEMPLATE,
                governance_source=GovernanceSource.PLATFORM,
                owner_account_id=None,
            )
        ),
    ]
    records = [
        PromptTemplateRecord(
            prompt_template_id=f"prompt-{index}",
            template_key=template_key,
            name=template_key,
            managed_asset_id=asset_id,
        )
        for index, (asset_id, template_key) in enumerate(
            (
                ("asset-private", "private.prompt"),
                ("asset-team", "team.prompt"),
                ("asset-org", "org.prompt"),
                ("asset-public", "public.prompt"),
                ("asset-platform", "platform.prompt"),
            )
        )
    ]
    service = CreatorPromptAccessService(
        _AccessRepository(policies),  # type: ignore[arg-type]
        _PromptRepository(records),  # type: ignore[arg-type]
    )

    snapshot = await service.reload()

    assert snapshot.template_keys == {
        "private.prompt",
        "team.prompt",
        "org.prompt",
        "public.prompt",
    }
    combined = _scope(owner="owner-1", team="team-1", organization="org-1")
    assert all(snapshot.can_use(key, combined) for key in snapshot.template_keys)
    outsider = _scope()
    assert snapshot.can_use("public.prompt", outsider)
    assert not snapshot.can_use("private.prompt", outsider)
    assert snapshot.can_use("platform.prompt", outsider)
    assert snapshot.can_use("private.prompt", _scope(master=True))


def test_fail_closed_prompt_snapshot_keeps_creator_classification() -> None:
    snapshot = CreatorPromptAccessSnapshot.create(
        template_keys={"creator.prompt"},
        public_templates={"creator.prompt"},
        templates_by_owner={"owner-1": {"creator.prompt"}},
        templates_by_team={"team-1": {"creator.prompt"}},
        templates_by_organization={"org-1": {"creator.prompt"}},
        template_key_by_asset_id={"asset-1": "creator.prompt"},
    ).deny_asset("asset-1")

    assert snapshot.template_keys == {"creator.prompt"}
    assert not snapshot.can_use("creator.prompt", _scope(master=False))
    assert snapshot.can_use("creator.prompt", _scope(master=True))
    assert snapshot.template_key_by_asset_id == {"asset-1": "creator.prompt"}
