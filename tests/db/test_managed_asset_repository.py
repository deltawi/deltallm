from __future__ import annotations

from typing import Any

import pytest

from src.db.managed_assets import (
    ManagedAssetAccessRepository,
    ManagedAssetAudienceNotFoundError,
    ManagedAssetSnapshotLimitError,
)
from src.services.managed_asset_access import (
    AssetAccessRole,
    AssetAccessPolicy,
    AssetGrant,
    AssetKind,
    AssetPrincipal,
    AssetSubjectType,
    GovernanceSource,
    ManagedAsset,
)


def test_policy_rows_are_grouped_into_multiple_audience_grants() -> None:
    base_row = {
        "asset_id": "asset-1",
        "asset_kind": "model",
        "governance_source": "creator",
        "owner_account_id": "account-1",
        "policy_version": 2,
        "state": "active",
    }

    policy = ManagedAssetAccessRepository._policy_from_rows(
        [
            {
                **base_row,
                "subject_type": "team",
                "team_id": "team-1",
                "organization_id": None,
                "access_role": "reader",
            },
            {
                **base_row,
                "subject_type": "organization",
                "team_id": None,
                "organization_id": "org-1",
                "access_role": "editor",
            },
        ]
    )

    assert [(grant.subject_type, grant.subject_id, grant.access_role) for grant in policy.grants] == [
        (AssetSubjectType.TEAM, "team-1", AssetAccessRole.READER),
        (AssetSubjectType.ORGANIZATION, "org-1", AssetAccessRole.EDITOR),
    ]


class _CapturePrisma:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        return []


async def test_resource_policy_lookup_applies_principal_scope_in_sql() -> None:
    prisma = _CapturePrisma()
    repository = ManagedAssetAccessRepository(prisma)

    result = await repository.get_policy_for_resource(
        AssetKind.NAMED_CREDENTIAL,
        "credential-1",
        principal=AssetPrincipal(
            account_id="account-1",
            team_ids=frozenset({"team-2", "team-1"}),
            organization_ids=frozenset({"org-1"}),
        ),
    )

    assert result is None
    query, params = prisma.calls[0]
    assert "asset.owner_account_id = $3" in query
    assert "LEFT JOIN deltallm_assetgrant AS asset_grant" in query
    assert " AS grant" not in query
    assert "access_grant.team_id IN ($4, $5)" in query
    assert "access_grant.organization_id IN ($6)" in query
    assert params == (
        "credential-1",
        "named_credential",
        "account-1",
        "team-1",
        "team-2",
        "org-1",
    )


async def test_platform_admin_resource_policy_lookup_has_no_tenant_parameters() -> None:
    prisma = _CapturePrisma()
    repository = ManagedAssetAccessRepository(prisma)

    await repository.get_policy_for_resource(
        AssetKind.MODEL,
        "model-1",
        principal=AssetPrincipal(account_id="admin", is_platform_admin=True),
    )

    query, params = prisma.calls[0]
    assert "AND (TRUE)" in query
    assert params == ("model-1", "model")


async def test_audience_search_is_bounded_and_scoped_to_principal_memberships() -> None:
    class _AudiencePrisma(_CapturePrisma):
        async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            return [
                {"id": "team-pinned", "label": "Pinned", "pinned": True},
                {"id": "team-1", "label": "Engineering", "pinned": False},
            ]

    prisma = _AudiencePrisma()
    repository = ManagedAssetAccessRepository(prisma)

    options = await repository.search_audience_options(
        AssetSubjectType.TEAM,
        AssetPrincipal(
            account_id="account-1",
            team_ids=frozenset({"team-1", "team-pinned"}),
        ),
        search=" eng ",
        selected_ids=("team-pinned",),
        limit=999,
    )

    assert options == [
        {"id": "team-pinned", "label": "Pinned"},
        {"id": "team-1", "label": "Engineering"},
    ]
    query, params = prisma.calls[0]
    assert "FROM deltallm_teamtable" in query
    assert "LIMIT $3" in query
    assert params == (
        ["team-pinned"],
        "eng",
        20,
        False,
        ["team-1", "team-pinned"],
    )


async def test_owned_resource_ids_requires_active_resource_ownership() -> None:
    class _OwnedResourcePrisma(_CapturePrisma):
        async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            return [{"resource_id": "credential-2"}]

    prisma = _OwnedResourcePrisma()
    repository = ManagedAssetAccessRepository(prisma)

    result = await repository.owned_resource_ids(
        AssetKind.NAMED_CREDENTIAL,
        {"credential-2", "credential-1"},
        "account-1",
    )

    assert result == {"credential-2"}
    query, params = prisma.calls[0]
    assert "resource.credential_id = ANY($1::text[])" in query
    assert "asset.state = 'active'" in query
    assert "asset.owner_account_id = $3" in query
    assert params == (
        ["credential-1", "credential-2"],
        "named_credential",
        "account-1",
    )


async def test_model_access_index_is_bounded_to_runtime_candidates() -> None:
    class _ModelIndexPrisma(_CapturePrisma):
        async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            return [
                {
                    "model_name": "creator-visible",
                    "asset_id": "asset-visible",
                    "asset_kind": "model",
                    "governance_source": "creator",
                    "owner_account_id": "account-1",
                    "policy_version": 1,
                    "state": "active",
                    "subject_type": None,
                    "team_id": None,
                    "organization_id": None,
                    "access_role": None,
                    "is_visible": True,
                },
                {
                    "model_name": "creator-hidden",
                    "asset_id": "asset-hidden",
                    "asset_kind": "model",
                    "governance_source": "creator",
                    "owner_account_id": "account-2",
                    "policy_version": 1,
                    "state": "active",
                    "subject_type": None,
                    "team_id": None,
                    "organization_id": None,
                    "access_role": None,
                    "is_visible": False,
                },
            ]

    prisma = _ModelIndexPrisma()
    repository = ManagedAssetAccessRepository(prisma)

    visible, creator_names = await repository.model_access_index(
        {"creator-hidden", "creator-visible"},
        AssetPrincipal(account_id="account-1", team_ids=frozenset({"team-1"})),
    )

    query, params = prisma.calls[0]
    assert "model.model_name = ANY($1::text[])" in query
    assert "asset.governance_source = 'platform' AND FALSE" in query
    assert "asset.governance_source = 'creator'" in query
    assert "asset.owner_account_id = $2" in query
    assert "access_grant.team_id IN ($3)" in query
    assert params == (["creator-hidden", "creator-visible"], "account-1", "team-1")
    assert set(visible) == {"creator-visible"}
    assert creator_names == {"creator-hidden", "creator-visible"}


async def test_policy_create_rejects_missing_audience_before_writing_asset() -> None:
    class _MissingAudiencePrisma(_CapturePrisma):
        async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            if "FROM unnest($1::text[])" in query:
                return [{"subject_type": "team", "subject_id": "missing-team"}]
            raise AssertionError("policy write must not start after audience validation fails")

    prisma = _MissingAudiencePrisma()
    repository = ManagedAssetAccessRepository(prisma, use_transactions=False)
    policy = AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id="asset-1",
            asset_kind=AssetKind.PROMPT_TEMPLATE,
            governance_source=GovernanceSource.CREATOR,
            owner_account_id="account-1",
        ),
        grants=(
            AssetGrant(
                managed_asset_id="asset-1",
                subject_type=AssetSubjectType.TEAM,
                subject_id="missing-team",
                access_role=AssetAccessRole.READER,
            ),
        ),
    )

    with pytest.raises(ManagedAssetAudienceNotFoundError, match="missing-team"):
        await repository.create_policy(policy, created_by_account_id="account-1")

    assert prisma.calls[0][1] == (["missing-team"], [])


async def test_policy_replace_rejects_missing_organization_before_mutating_policy() -> None:
    current = AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id="asset-1",
            asset_kind=AssetKind.PROMPT_TEMPLATE,
            governance_source=GovernanceSource.CREATOR,
            owner_account_id="account-1",
            policy_version=4,
        )
    )

    class _MissingOrganizationPrisma(_CapturePrisma):
        async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            if "FROM unnest($1::text[])" in query:
                return [{"subject_type": "organization", "subject_id": "missing-org"}]
            raise AssertionError("policy mutation must not start after audience validation fails")

    class _ExistingPolicyRepository(ManagedAssetAccessRepository):
        async def get_policy(
            self, asset_id: str, *, for_update: bool = False
        ) -> AssetAccessPolicy | None:
            assert asset_id == "asset-1"
            assert for_update is True
            return current

    prisma = _MissingOrganizationPrisma()
    repository = _ExistingPolicyRepository(prisma, use_transactions=False)
    replacement = AssetAccessPolicy(
        asset=current.asset,
        grants=(
            AssetGrant(
                managed_asset_id="asset-1",
                subject_type=AssetSubjectType.ORGANIZATION,
                subject_id="missing-org",
                access_role=AssetAccessRole.EDITOR,
            ),
        ),
    )

    with pytest.raises(ManagedAssetAudienceNotFoundError, match="missing-org"):
        await repository.replace_grants(
            replacement,
            expected_policy_version=4,
            changed_by_account_id="account-1",
        )

    assert len(prisma.calls) == 1
    assert prisma.calls[0][1] == ([], ["missing-org"])


async def test_grant_insert_translates_race_time_foreign_key_failure() -> None:
    class _DeletedAudiencePrisma(_CapturePrisma):
        async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            raise RuntimeError("foreign key violation on deltallm_assetgrant_team_id_fkey")

    repository = ManagedAssetAccessRepository(
        _DeletedAudiencePrisma(),
        use_transactions=False,
    )
    grant = AssetGrant(
        managed_asset_id="asset-1",
        subject_type=AssetSubjectType.TEAM,
        subject_id="deleted-team",
        access_role=AssetAccessRole.READER,
    )

    with pytest.raises(ManagedAssetAudienceNotFoundError, match="no longer exists"):
        await repository._insert_grant(grant, created_by_account_id="account-1")


async def test_asset_id_lookup_applies_visibility_before_returning_policy() -> None:
    prisma = _CapturePrisma()
    repository = ManagedAssetAccessRepository(prisma)

    await repository.get_accessible_policy(
        "asset-1",
        AssetPrincipal(account_id="account-1", team_ids=frozenset({"team-1"})),
    )

    query, params = prisma.calls[0]
    assert "asset.asset_id = $1" in query
    assert "asset.owner_account_id = $2" in query
    assert "access_grant.team_id IN ($3)" in query
    assert params == ("asset-1", "account-1", "team-1")


async def test_link_health_maps_missing_mismatched_and_orphaned_counts() -> None:
    class _HealthPrisma(_CapturePrisma):
        async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            return [
                {
                    "missing_named_credentials": 1,
                    "missing_models": 2,
                    "missing_model_deployments": 3,
                    "missing_route_groups": 4,
                    "missing_mcp_servers": 5,
                    "missing_prompt_templates": 6,
                    "kind_mismatches": 7,
                    "orphaned_policies": 8,
                }
            ]

    repository = ManagedAssetAccessRepository(_HealthPrisma())

    health = await repository.get_link_health()

    assert health.missing_links == 21
    assert health.kind_mismatches == 7
    assert health.orphaned_policies == 8
    assert health.ready is False


async def test_reconciliation_platform_adopts_each_legacy_resource_kind() -> None:
    class _ReconcilePrisma(_CapturePrisma):
        async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            if "UPDATE deltallm_routeruntimestate" in query:
                return [{"revision": 2}]
            return [{"repaired": True}]

    prisma = _ReconcilePrisma()
    repository = ManagedAssetAccessRepository(prisma, use_transactions=False)

    result = await repository.reconcile_missing_links(batch_size=25)

    assert result.total_changes == 6
    assert result.model_deployments_linked == 1
    direct_calls = [call for call in prisma.calls if "inserted_assets" in call[0]]
    assert [call[1] for call in direct_calls] == [
        (25, "named_credential"),
        (25, "model"),
        (25, "route_group"),
        (25, "mcp_server"),
        (25, "prompt_template"),
    ]
    assert any("FOR UPDATE OF resource SKIP LOCKED" in call[0] for call in direct_calls)
    assert any("UPDATE deltallm_routeruntimestate" in call[0] for call in prisma.calls)


async def test_runtime_asset_policy_create_and_delete_bump_durable_revision() -> None:
    class _RuntimeRevisionPrisma(_CapturePrisma):
        async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            if "UPDATE deltallm_routeruntimestate" in query:
                return [{"revision": 2}]
            if "DELETE FROM deltallm_managedasset" in query:
                return [{"asset_id": params[0], "asset_kind": "mcp_server"}]
            return []

    prisma = _RuntimeRevisionPrisma()
    repository = ManagedAssetAccessRepository(prisma, use_transactions=False)
    await repository.create_policy(
        AssetAccessPolicy(
            asset=ManagedAsset(
                asset_id="prompt-asset",
                asset_kind=AssetKind.PROMPT_TEMPLATE,
                governance_source=GovernanceSource.CREATOR,
                owner_account_id="account-1",
            )
        ),
        created_by_account_id="account-1",
    )
    await repository.delete_policy("mcp-asset")

    revision_calls = [
        call for call in prisma.calls if "UPDATE deltallm_routeruntimestate" in call[0]
    ]
    assert len(revision_calls) == 2


async def test_named_credential_policy_create_does_not_reload_runtime_snapshots() -> None:
    prisma = _CapturePrisma()
    repository = ManagedAssetAccessRepository(prisma, use_transactions=False)
    await repository.create_policy(
        AssetAccessPolicy(
            asset=ManagedAsset(
                asset_id="credential-asset",
                asset_kind=AssetKind.NAMED_CREDENTIAL,
                governance_source=GovernanceSource.CREATOR,
                owner_account_id="account-1",
            )
        ),
        created_by_account_id="account-1",
    )

    assert not any("UPDATE deltallm_routeruntimestate" in query for query, _ in prisma.calls)


async def test_snapshot_policy_limit_is_enforced_before_materializing_a_generation() -> None:
    class _OversizedSnapshotPrisma(_CapturePrisma):
        async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            return [
                {
                    "asset_id": f"asset-{index}",
                    "asset_kind": "model",
                    "governance_source": "creator",
                    "owner_account_id": "account-1",
                    "policy_version": 1,
                    "state": "active",
                    "subject_type": None,
                    "team_id": None,
                    "organization_id": None,
                    "access_role": None,
                }
                for index in range(3)
            ]

    prisma = _OversizedSnapshotPrisma()
    repository = ManagedAssetAccessRepository(prisma, max_snapshot_policies=2)

    with pytest.raises(ManagedAssetSnapshotLimitError):
        await repository.list_accessible_policies(
            AssetKind.MODEL,
            AssetPrincipal(account_id="admin", is_platform_admin=True),
        )

    query, params = prisma.calls[0]
    assert "LIMIT $2" in query
    assert params == ("model", 3)
