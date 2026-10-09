from __future__ import annotations

import pytest

from src.db.mcp.mcp import MCPServerRecord
from src.models.responses import UserAPIKeyAuth
from src.services.creator_mcp_access import CreatorMCPAccessService, CreatorMCPAccessSnapshot
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
    owner: str | None,
    *,
    governance_source: GovernanceSource = GovernanceSource.CREATOR,
    subject_type: AssetSubjectType | None = None,
    subject_id: str | None = None,
) -> AssetAccessPolicy:
    return AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id=asset_id,
            asset_kind=AssetKind.MCP_SERVER,
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
        assert asset_kind is AssetKind.MCP_SERVER
        assert principal.is_platform_admin
        return self.policies


class _MCPRepository:
    def __init__(self, servers: list[MCPServerRecord]) -> None:
        self.servers = servers

    async def list_by_managed_asset_ids(self, asset_ids: list[str]):  # noqa: ANN201
        return [server for server in self.servers if server.managed_asset_id in asset_ids]


@pytest.mark.asyncio
async def test_creator_mcp_snapshot_compiles_owner_team_org_and_public_visibility() -> None:
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
    servers = [
        MCPServerRecord(
            mcp_server_id=f"server-{index}",
            server_key=name,
            name=name,
            managed_asset_id=asset_id,
        )
        for index, (asset_id, name) in enumerate(
            (
                ("asset-private", "private"),
                ("asset-team", "team"),
                ("asset-org", "org"),
                ("asset-public", "public"),
                ("asset-platform", "platform"),
            )
        )
    ]
    service = CreatorMCPAccessService(
        _AccessRepository(policies),  # type: ignore[arg-type]
        _MCPRepository(servers),  # type: ignore[arg-type]
    )

    snapshot = await service.reload()

    assert snapshot.server_ids == {"server-0", "server-1", "server-2", "server-3"}
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        owner_account_id="owner-1",
        team_id="team-1",
        organization_id="org-1",
    )
    assert snapshot.visible_server_ids(auth) == {
        "server-0",
        "server-1",
        "server-2",
        "server-3",
    }
    assert snapshot.visible_server_ids(UserAPIKeyAuth(api_key="sk-outsider")) == {"server-3"}


def test_creator_mcp_fail_closed_snapshot_keeps_creator_classification() -> None:
    snapshot = CreatorMCPAccessSnapshot.create(
        server_ids={"server-1"},
        public_servers={"server-1"},
        servers_by_owner={"owner-1": {"server-1"}},
        servers_by_team={"team-1": {"server-1"}},
        servers_by_organization={"org-1": {"server-1"}},
        asset_id_by_server_id={"server-1": "asset-1"},
    )

    denied = snapshot.deny_asset("asset-1")

    assert denied.server_ids == {"server-1"}
    assert (
        denied.visible_server_ids(
            UserAPIKeyAuth(
                api_key="sk-test",
                owner_account_id="owner-1",
                team_id="team-1",
                organization_id="org-1",
            )
        )
        == set()
    )
