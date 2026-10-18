"""MCP bundle repository against real PostgreSQL."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from src.db.mcp.mcp_bundles import MCPBundleOwnerFilter, MCPBundleRepository
from src.mcp.bundles import MCPBundleMember
from src.mcp.governance import MCPGovernanceService
from tests.db.tier_migration_helpers import connect_prisma, seed_organization

pytestmark = pytest.mark.postgres


async def _seed_server(db, server_id: str, key: str, tools: list[str]) -> None:  # noqa: ANN001
    await db.execute_raw(
        """
        INSERT INTO deltallm_mcpserver (
            mcp_server_id, server_key, name, base_url, forwarded_headers_allowlist,
            capabilities_json, updated_at
        )
        VALUES ($1, $2, $3, 'https://mcp.example.com', ARRAY[]::text[], $4::jsonb, NOW())
        """,
        server_id,
        key,
        key.title(),
        json.dumps({"tools": [{"name": name} for name in tools]}),
    )


async def _seed_team(db, team_id: str, organization_id: str) -> None:  # noqa: ANN001
    await db.execute_raw(
        """
        INSERT INTO deltallm_teamtable (team_id, team_alias, organization_id, created_at, updated_at)
        VALUES ($1, $1, $2, NOW(), NOW())
        """,
        team_id,
        organization_id,
    )


async def _cleanup(db, run: str) -> None:  # noqa: ANN001
    await db.execute_raw("DELETE FROM deltallm_mcpbundle WHERE bundle_key LIKE $1", f"{run}%")
    await db.execute_raw("DELETE FROM deltallm_mcpserver WHERE server_key LIKE $1", f"{run}%")
    await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id LIKE $1", f"{run}%")
    await db.execute_raw(
        "DELETE FROM deltallm_organizationtable WHERE organization_id LIKE $1", f"{run}%"
    )
    await db.disconnect()


async def test_bundle_round_trip_member_order_and_cascades() -> None:
    db = await connect_prisma()
    run = f"b{uuid4().hex[:10]}"
    repository = MCPBundleRepository(db)
    try:
        await _seed_server(db, f"{run}-s1", f"{run}-github", ["create_issue", "search"])
        await _seed_server(db, f"{run}-s2", f"{run}-jira", ["search"])
        members = (
            MCPBundleMember(f"{run}-s2", ("search",), "jira"),
            MCPBundleMember(f"{run}-s1", None, None),
        )
        created = await repository.create_bundle(
            bundle_key=f"{run}-eng",
            name="Eng",
            description="Code review",
            owner_scope_type="global",
            owner_scope_id=None,
            enabled=True,
            members=members,
            metadata=None,
            created_by_account_id="acct-1",
        )
        assert created is not None
        assert [member.mcp_server_id for member in created.members] == [f"{run}-s2", f"{run}-s1"]
        assert created.members[0].tool_allowlist == ["search"]
        assert created.members[0].name_prefix == "jira"
        assert created.members[1].all_tools is True
        assert created.members[1].server_tool_names == ["create_issue", "search"]
        assert created.to_bundle().members == members

        by_key = await repository.get_bundle_by_key(f"{run}-eng")
        assert by_key is not None and by_key.mcp_bundle_id == created.mcp_bundle_id

        updated = await repository.update_bundle(
            created.mcp_bundle_id,
            name="Engineering",
            description=None,
            enabled=False,
            members=(MCPBundleMember(f"{run}-s1", ("create_issue",), "gh"),),
            metadata=None,
        )
        assert updated is not None and updated.name == "Engineering" and updated.enabled is False
        assert [(m.mcp_server_id, m.tool_allowlist) for m in updated.members] == [
            (f"{run}-s1", ["create_issue"])
        ]

        # Deleting a server removes it from every bundle.
        await db.execute_raw("DELETE FROM deltallm_mcpserver WHERE mcp_server_id = $1", f"{run}-s1")
        reloaded = await repository.get_bundle(created.mcp_bundle_id)
        assert reloaded is not None and reloaded.members == []

        assert await repository.delete_bundle(created.mcp_bundle_id) is True
        assert await repository.get_bundle(created.mcp_bundle_id) is None
        assert await repository.delete_bundle(created.mcp_bundle_id) is False
    finally:
        await _cleanup(db, run)


async def test_constraints_reject_duplicate_keys_members_and_bad_owner() -> None:
    db = await connect_prisma()
    run = f"b{uuid4().hex[:10]}"
    repository = MCPBundleRepository(db)
    try:
        await _seed_server(db, f"{run}-s1", f"{run}-github", ["search"])
        common = {
            "name": "Eng",
            "description": None,
            "enabled": True,
            "metadata": None,
            "created_by_account_id": None,
        }
        await repository.create_bundle(
            bundle_key=f"{run}-eng",
            owner_scope_type="global",
            owner_scope_id=None,
            members=(),
            **common,
        )
        with pytest.raises(Exception, match="(?i)unique|already exists"):
            await repository.create_bundle(
                bundle_key=f"{run}-eng",
                owner_scope_type="global",
                owner_scope_id=None,
                members=(),
                **common,
            )
        with pytest.raises(Exception, match="(?i)check|owner_scope"):
            await repository.create_bundle(
                bundle_key=f"{run}-bad",
                owner_scope_type="organization",
                owner_scope_id=None,
                members=(),
                **common,
            )
        member = MCPBundleMember(f"{run}-s1", None, None)
        with pytest.raises(Exception, match="(?i)unique|already exists"):
            await repository.create_bundle(
                bundle_key=f"{run}-twice",
                owner_scope_type="global",
                owner_scope_id=None,
                members=(member, member),
                **common,
            )
        # The failed statement leaves no half-created bundle.
        assert await repository.get_bundle_by_key(f"{run}-twice") is None
        assert await repository.get_bundle_by_key(f"{run}-bad") is None
    finally:
        await _cleanup(db, run)


async def test_owner_filter_and_governance_snapshot() -> None:
    db = await connect_prisma()
    run = f"b{uuid4().hex[:10]}"
    repository = MCPBundleRepository(db)
    try:
        org, other = f"{run}-org", f"{run}-other"
        await seed_organization(db, organization_id=org)
        await seed_organization(db, organization_id=other)
        await _seed_team(db, f"{run}-team", org)
        await _seed_team(db, f"{run}-xteam", other)
        await _seed_server(db, f"{run}-s1", f"{run}-docs", ["search"])
        member = (MCPBundleMember(f"{run}-s1", None, None),)
        for key, owner_type, owner_id, enabled in (
            ("global", "global", None, True),
            ("org", "organization", org, True),
            ("team", "team", f"{run}-team", True),
            ("xteam", "team", f"{run}-xteam", True),
            ("off", "organization", org, False),
        ):
            await repository.create_bundle(
                bundle_key=f"{run}-{key}",
                name=key,
                description=None,
                owner_scope_type=owner_type,
                owner_scope_id=owner_id,
                enabled=enabled,
                members=member,
                metadata=None,
                created_by_account_id=None,
            )

        def keys(records):  # noqa: ANN001, ANN202
            return sorted(record.bundle_key.removeprefix(f"{run}-") for record in records)

        org_admin, total = await repository.list_bundles(
            search=run, owner_filter=MCPBundleOwnerFilter(organization_ids=(org,))
        )
        assert keys(org_admin) == ["off", "org", "team"] and total == 3
        team_member, _ = await repository.list_bundles(
            search=run, owner_filter=MCPBundleOwnerFilter(team_ids=(f"{run}-team",))
        )
        assert keys(team_member) == ["team"]
        nobody, _ = await repository.list_bundles(search=run, owner_filter=MCPBundleOwnerFilter())
        assert nobody == []
        page, total = await repository.list_bundles(search=run, limit=2, offset=0)
        assert len(page) == 2 and total == 5

        governance = MCPGovernanceService(None, bundle_repository=repository)
        governance.repository = _NoServers()  # type: ignore[assignment]
        await governance.reload()
        assert governance.get_bundle(f"{run}-org") is not None
        assert governance.get_bundle(f"{run}-off") is None
    finally:
        await _cleanup(db, run)


class _NoServers:
    async def list_servers(self, **kwargs):  # noqa: ANN003, ANN201
        return [], 0

    async def list_bindings(self, **kwargs):  # noqa: ANN003, ANN201
        return [], 0

    async def list_tool_policies(self, **kwargs):  # noqa: ANN003, ANN201
        return [], 0
