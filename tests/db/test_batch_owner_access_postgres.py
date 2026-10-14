from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
import json
import os
from uuid import uuid4

import pytest
from prisma import Prisma

from src.api.admin.batch_access import append_batch_scope_clause
from src.api.admin.endpoints.common import AuthScope
from src.auth.roles import ORG_ROLE_PERMISSIONS, TEAM_ROLE_PERMISSIONS, Permission

pytestmark = pytest.mark.postgres

AUTH = {"Authorization": "Bearer mk-test"}


class _RollbackFixture(Exception):
    pass


@pytest.fixture
async def batch_database() -> AsyncIterator[tuple[Prisma, str]]:
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires DATABASE_URL")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    prefix = "batch-owner-" + uuid4().hex[:12]
    try:
        async with db.tx(timeout=timedelta(seconds=30)) as tx:
            await _seed(tx, prefix)
            yield tx, prefix
            # Roll back all fixture rows, including when a test fails.
            raise _RollbackFixture()
    except _RollbackFixture:
        pass
    finally:
        await db.disconnect()


async def _seed(db: Prisma, prefix: str) -> None:
    for org in ("org-a", "org-b"):
        await db.execute_raw(
            "INSERT INTO deltallm_organizationtable (id, organization_id, updated_at) VALUES ($1, $1, NOW())",
            f"{prefix}-{org}",
        )
    for team, org in (("team-a1", "org-a"), ("team-a2", "org-a"), ("team-b1", "org-b")):
        await db.execute_raw(
            "INSERT INTO deltallm_teamtable (team_id, organization_id) VALUES ($1, $2)",
            f"{prefix}-{team}",
            f"{prefix}-{org}",
        )
    await db.execute_raw(
        "INSERT INTO deltallm_batch_file (file_id, purpose, filename, bytes, storage_key) VALUES ($1, 'batch', 'in.jsonl', 12, $1)",
        f"{prefix}-file",
    )
    jobs = (
        # (batch, team, organization, owner)
        ("own-team-a1", "team-a1", None, "me"),
        ("own-org-a", None, "org-a", "me"),
        ("peer-team-a1", "team-a1", None, "peer"),
        ("legacy-team-a1", "team-a1", None, None),
        ("own-team-b1", "team-b1", None, "me"),
    )
    for batch, team, organization, owner in jobs:
        await db.execute_raw(
            """INSERT INTO deltallm_batch_job (batch_id, endpoint, status, input_file_id,
                created_by_team_id, created_by_organization_id,
                created_by_owner_account_id, created_by_owner_snapshot_complete)
            VALUES ($1, '/v1/embeddings', 'completed', $2, $3, $4, $5, true)""",
            f"{prefix}-{batch}",
            f"{prefix}-file",
            f"{prefix}-{team}" if team else None,
            f"{prefix}-{organization}" if organization else None,
            f"{prefix}-{owner}" if owner else None,
        )
        await db.execute_raw(
            """INSERT INTO deltallm_batch_item (item_id, batch_id, line_number, custom_id, status,
                request_body) VALUES ($1, $2, 1, 'item', 'completed', '{"input":"private"}')""",
            f"{prefix}-{batch}-item",
            f"{prefix}-{batch}",
        )


@pytest.fixture
def batch_app(test_app, batch_database, monkeypatch):  # noqa: ANN001, ANN201
    db, prefix = batch_database
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    scopes: dict[str, AuthScope] = {}

    def _scope(
        request,  # noqa: ANN001
        authorization=None,  # noqa: ANN001
        x_master_key=None,  # noqa: ANN001
        required_permission=None,  # noqa: ANN001
    ) -> AuthScope:
        scope = scopes["current"]
        return AuthScope(
            org_ids=[
                key
                for key, values in scope.org_permissions_by_id.items()
                if required_permission in values
            ],
            team_ids=[
                key
                for key, values in scope.team_permissions_by_id.items()
                if required_permission in values
            ],
            org_permissions_by_id=scope.org_permissions_by_id,
            team_permissions_by_id=scope.team_permissions_by_id,
            account_id=scope.account_id,
        )

    monkeypatch.setattr("src.api.admin.endpoints.batches.get_auth_scope", _scope)

    def use(*, orgs: dict[str, str], teams: dict[str, str]) -> None:
        scopes["current"] = AuthScope(
            org_permissions_by_id={
                f"{prefix}-{org}": set(ORG_ROLE_PERMISSIONS[role]) for org, role in orgs.items()
            },
            team_permissions_by_id={
                f"{prefix}-{team}": set(TEAM_ROLE_PERMISSIONS[role]) for team, role in teams.items()
            },
            account_id=f"{prefix}-me",
        )

    return prefix, use


async def _visible(client, prefix: str) -> tuple[set[str], int, str]:  # noqa: ANN001
    listing = await client.get("/ui/api/batches", params={"limit": 50}, headers=AUTH)
    summary = await client.get("/ui/api/batches/summary", headers=AUTH)
    assert listing.status_code == summary.status_code == 200
    names = {row["batch_id"].removeprefix(f"{prefix}-") for row in listing.json()["data"]}
    assert listing.json()["pagination"]["total"] == summary.json()["total"] == len(names)
    return names, summary.json()["completed"], listing.json()["view"]


async def test_org_member_sees_only_own_batches_in_current_organizations(client, batch_app):  # noqa: ANN001
    prefix, use = batch_app
    use(orgs={"org-a": "org_member"}, teams={})

    names, completed, view = await _visible(client, prefix)

    assert names == {"own-team-a1", "own-org-a"}
    assert completed == 2
    assert view == "own"
    own = await client.get(f"/ui/api/batches/{prefix}-own-team-a1", headers=AUTH)
    own_item = await client.get(
        f"/ui/api/batches/{prefix}-own-team-a1/items/{prefix}-own-team-a1-item", headers=AUTH
    )
    assert own.status_code == own_item.status_code == 200
    assert own.json()["capabilities"]["cancel"] is False
    for batch in ("peer-team-a1", "legacy-team-a1", "own-team-b1"):
        detail = await client.get(f"/ui/api/batches/{prefix}-{batch}", headers=AUTH)
        item = await client.get(
            f"/ui/api/batches/{prefix}-{batch}/items/{prefix}-{batch}-item", headers=AUTH
        )
        assert detail.status_code == item.status_code == 403, batch
        assert "private" not in item.text
    cancel = await client.post(f"/ui/api/batches/{prefix}-own-team-a1/cancel", headers=AUTH)
    assert cancel.status_code == 403


async def test_team_viewer_sees_own_batches_in_that_team(client, batch_app):  # noqa: ANN001
    prefix, use = batch_app
    use(orgs={}, teams={"team-b1": "team_viewer"})

    names, _, view = await _visible(client, prefix)

    assert names == {"own-team-b1"}
    assert view == "own"


async def test_developer_keeps_team_wide_view_and_gains_own_org_batches(client, batch_app):  # noqa: ANN001
    prefix, use = batch_app
    use(orgs={"org-a": "org_member"}, teams={"team-a1": "team_developer"})

    names, _, view = await _visible(client, prefix)

    assert names == {"own-team-a1", "peer-team-a1", "legacy-team-a1", "own-org-a"}
    assert view == "operator"
    peer = await client.get(f"/ui/api/batches/{prefix}-peer-team-a1", headers=AUTH)
    assert peer.status_code == 200


async def test_org_owner_view_is_unchanged(client, batch_app):  # noqa: ANN001
    prefix, use = batch_app
    use(orgs={"org-a": "org_owner"}, teams={})

    names, _, view = await _visible(client, prefix)

    assert names == {"own-team-a1", "own-org-a", "peer-team-a1", "legacy-team-a1"}
    assert view == "operator"
    assert Permission.KEY_READ in ORG_ROLE_PERMISSIONS["org_owner"]


async def test_member_batch_query_uses_scope_indexes_not_a_full_scan(batch_database):  # noqa: ANN001
    db, prefix = batch_database
    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationtable (id, organization_id, updated_at)
        SELECT $1 || '-bulk-org-' || n, $1 || '-bulk-org-' || n, NOW()
        FROM generate_series(1, 50) n
        """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_teamtable (team_id, organization_id)
        SELECT $1 || '-bulk-team-' || n, $1 || '-bulk-org-' || ((n % 50) + 1)
        FROM generate_series(1, 500) n
        """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_batch_job (batch_id, endpoint, status, input_file_id,
            created_by_team_id, created_by_owner_account_id, created_by_owner_snapshot_complete)
        SELECT $1 || '-bulk-job-' || n, '/v1/embeddings', 'completed', $1 || '-file',
               $1 || '-bulk-team-' || ((n % 500) + 1), $1 || '-bulk-acct-' || (n % 2000), true
        FROM generate_series(1, 50000) n
        """,
        prefix,
    )
    await db.execute_raw("ANALYZE deltallm_batch_job")
    await db.execute_raw("ANALYZE deltallm_teamtable")
    scope = AuthScope(
        org_permissions_by_id={f"{prefix}-bulk-org-7": set(ORG_ROLE_PERMISSIONS["org_member"])},
        team_permissions_by_id={f"{prefix}-bulk-team-9": set(TEAM_ROLE_PERMISSIONS["team_viewer"])},
        account_id=f"{prefix}-bulk-acct-7",
    )
    clauses: list[str] = []
    params: list[object] = []
    assert append_batch_scope_clause(clauses=clauses, params=params, scope=scope, job_alias="j.")

    rows = await db.query_raw(
        f"EXPLAIN (FORMAT JSON) SELECT COUNT(*) FROM deltallm_batch_job j WHERE {clauses[0]}",
        *params,
    )

    plan = json.dumps(rows[0])
    assert "Seq Scan" not in plan, plan
    assert "deltallm_batch_job_created_by_team_id_created_at_idx" in plan, plan
