from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.api.admin.batch_access import resolve_batch_owner_scope
from src.batch.repositories.admin_visibility_repository import BatchOwnerScope
from src.api.admin.endpoints.common import AuthScope
from src.auth.roles import ORG_ROLE_PERMISSIONS, TEAM_ROLE_PERMISSIONS, Permission
from src.services.ui.ui_authorization import build_ui_access, effective_permissions_for_context

MEMBER_PERMISSIONS = ORG_ROLE_PERMISSIONS["org_member"]
VIEWER_PERMISSIONS = TEAM_ROLE_PERMISSIONS["team_viewer"]
DEVELOPER_PERMISSIONS = TEAM_ROLE_PERMISSIONS["team_developer"]


def _member_scope(
    *,
    account_id: str | None = "acct-owner",
    orgs: dict[str, set[str]] | None = None,
    teams: dict[str, set[str]] | None = None,
    operator_org_ids: list[str] | None = None,
    operator_team_ids: list[str] | None = None,
) -> AuthScope:
    return AuthScope(
        is_platform_admin=False,
        org_ids=operator_org_ids or [],
        team_ids=operator_team_ids or [],
        org_permissions_by_id=orgs if orgs is not None else {"org-1": set(MEMBER_PERMISSIONS)},
        team_permissions_by_id=teams if teams is not None else {},
        account_id=account_id,
    )


def _patch_scope(monkeypatch, scope: AuthScope) -> None:  # noqa: ANN001
    def _scope(
        request,  # noqa: ANN001
        authorization=None,  # noqa: ANN001
        x_master_key=None,  # noqa: ANN001
        required_permission=None,  # noqa: ANN001
    ) -> AuthScope:
        # Mirror get_auth_scope: operator IDs are the memberships holding the
        # required permission, so write routes never inherit read-only IDs.
        org_ids = [
            org_id
            for org_id, permissions in scope.org_permissions_by_id.items()
            if required_permission in permissions
        ]
        team_ids = [
            team_id
            for team_id, permissions in scope.team_permissions_by_id.items()
            if required_permission in permissions
        ]
        return AuthScope(
            is_platform_admin=scope.is_platform_admin,
            org_ids=org_ids,
            team_ids=team_ids,
            org_permissions_by_id=scope.org_permissions_by_id,
            team_permissions_by_id=scope.team_permissions_by_id,
            account_id=scope.account_id,
            external_workspace=scope.external_workspace,
        )

    monkeypatch.setattr("src.api.admin.endpoints.batches.get_auth_scope", _scope)


class OwnerBatchDB:
    """Minimal batch table: rows are filtered by the owner and membership params."""

    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        base = {
            "endpoint": "/v1/embeddings",
            "status": "in_progress",
            "model": "text-embedding-3-small",
            "execution_mode": "managed_internal",
            "total_items": 1,
            "completed_items": 0,
            "failed_items": 0,
            "cancelled_items": 0,
            "in_progress_items": 1,
            "created_by_api_key": "sk-owner-key-123456",
            "created_at": now,
            "started_at": now,
            "completed_at": None,
            "cancel_requested_at": None,
            "expires_at": None,
            "total_cost": 0.0,
        }
        self.batches = {
            "own-team": {
                **base,
                "batch_id": "own-team",
                "created_by_team_id": "team-1",
                "created_by_organization_id": None,
                "organization_id": "org-1",
                "team_alias": "Team One",
                "created_by_owner_account_id": "acct-owner",
            },
            "peer-team": {
                **base,
                "batch_id": "peer-team",
                "created_by_team_id": "team-1",
                "created_by_organization_id": None,
                "organization_id": "org-1",
                "team_alias": "Team One",
                "created_by_owner_account_id": "acct-peer",
            },
            "own-left-org": {
                **base,
                "batch_id": "own-left-org",
                "created_by_team_id": None,
                "created_by_organization_id": "org-9",
                "organization_id": "org-9",
                "team_alias": None,
                "created_by_owner_account_id": "acct-owner",
            },
            "legacy-unowned": {
                **base,
                "batch_id": "legacy-unowned",
                "created_by_team_id": "team-1",
                "created_by_organization_id": None,
                "organization_id": "org-1",
                "team_alias": "Team One",
                "created_by_owner_account_id": None,
            },
        }

    async def query_raw(self, query: str, *params):  # noqa: ANN002, ANN201
        self.queries.append((query, params))
        if "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1" in query:
            return [{"organization_id": "org-1"}] if params[0] == "team-1" else []
        if "COUNT(*) AS total FROM deltallm_batch_job j" in query:
            return [{"total": 0}]
        if "COUNT(*) FILTER" in query:
            return [{"total": 0}]
        if "FROM deltallm_batch_job j" in query and "WHERE j.batch_id = $1" in query:
            row = self.batches.get(str(params[0]))
            return [dict(row)] if row else []
        if "FROM deltallm_batch_job" in query and "WHERE batch_id = $1" in query:
            row = self.batches.get(str(params[0]))
            if not row:
                return []
            return [
                {
                    key: row[key]
                    for key in (
                        "batch_id",
                        "status",
                        "created_by_team_id",
                        "created_by_organization_id",
                        "created_by_owner_account_id",
                    )
                }
            ]
        if "FROM deltallm_batch_job j" in query:
            return []
        if "SUM(provider_cost)" in query:
            return [{"total_provider_cost": 0.0, "total_billed_cost": 0.0}]
        if "WHERE batch_id = $1 AND item_id = $2" in query:
            return [
                {
                    "item_id": params[1],
                    "batch_id": params[0],
                    "line_number": 1,
                    "custom_id": "req-1",
                    "status": "completed",
                    "attempts": 1,
                    "provider_cost": 0.0,
                    "billed_cost": 0.0,
                    "last_error": None,
                    "request_body": {"input": "own prompt"},
                    "response_body": None,
                    "_has_error_body": False,
                    "_error_retry_category": None,
                    "_error_code": None,
                    "usage": None,
                    "created_at": None,
                    "started_at": None,
                    "completed_at": None,
                }
            ]
        if "FROM deltallm_batch_item" in query:
            return []
        if "UPDATE deltallm_batch_job" in query:
            raise AssertionError("read-only owners must never reach a batch mutation")
        return []


class _UntouchableRepository:
    def __getattr__(self, name: str):  # noqa: ANN204
        raise AssertionError(f"read-only owners must never reach batch repository {name}")


@pytest.fixture
def owner_db(test_app) -> OwnerBatchDB:  # noqa: ANN001
    db = OwnerBatchDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    test_app.state.batch_repository = _UntouchableRepository()
    setattr(test_app.state.settings, "master_key", "mk-test")
    return db


AUTH = {"Authorization": "Bearer mk-test"}


def test_owner_scope_covers_only_own_batches_in_current_memberships() -> None:
    owner = BatchOwnerScope(account_id="acct-1", organization_ids=("org-1",), team_ids=("team-1",))

    assert owner.covers(owner_account_id="acct-1", team_id="team-1", organization_id=None)
    assert owner.covers(owner_account_id="acct-1", team_id=None, organization_id="org-1")
    assert not owner.covers(owner_account_id="acct-2", team_id="team-1", organization_id="org-1")
    assert not owner.covers(owner_account_id=None, team_id="team-1", organization_id="org-1")
    assert not owner.covers(owner_account_id="acct-1", team_id="team-9", organization_id="org-9")


def test_owner_scope_is_absent_for_admins_customers_and_fully_covered_operators() -> None:
    assert resolve_batch_owner_scope(AuthScope(is_platform_admin=True, account_id="a")) is None
    assert resolve_batch_owner_scope(_member_scope(account_id=None)) is None
    assert resolve_batch_owner_scope(_member_scope(orgs={}, teams={})) is None
    external = _member_scope()
    external.external_workspace = object()  # type: ignore[assignment]
    assert resolve_batch_owner_scope(external) is None
    developer = _member_scope(
        orgs={},
        teams={"team-1": set(DEVELOPER_PERMISSIONS)},
        operator_team_ids=["team-1"],
    )
    assert resolve_batch_owner_scope(developer) is None


def test_owner_scope_only_adds_memberships_without_operator_coverage() -> None:
    mixed = _member_scope(
        orgs={"org-2": set(MEMBER_PERMISSIONS)},
        teams={"team-1": set(DEVELOPER_PERMISSIONS), "team-2": set(VIEWER_PERMISSIONS)},
        operator_team_ids=["team-1"],
    )

    assert resolve_batch_owner_scope(mixed) == BatchOwnerScope(
        account_id="acct-owner",
        organization_ids=("org-2",),
        team_ids=("team-2",),
    )


@pytest.mark.parametrize(
    ("organization_memberships", "team_memberships", "expected"),
    [
        ([{"organization_id": "org-1", "role": "org_member"}], [], True),
        ([], [{"team_id": "team-1", "role": "team_viewer"}], True),
        ([], [], False),
    ],
)
def test_ui_access_shows_batches_to_any_member(
    organization_memberships, team_memberships, expected
) -> None:  # noqa: ANN001
    context = type(
        "Context",
        (),
        {
            "role": "org_user",
            "external_workspace": None,
            "organization_memberships": organization_memberships,
            "team_memberships": team_memberships,
        },
    )()
    permissions = effective_permissions_for_context(context)

    access = build_ui_access(authenticated=True, effective_permissions=permissions)

    assert access["batches"] is expected
    assert access["keys"] is False


def test_ui_access_still_hides_batches_from_external_customers() -> None:
    access = build_ui_access(
        authenticated=True,
        effective_permissions=[Permission.KEY_READ, Permission.TEAM_READ],
        external_customer=True,
    )

    assert access["batches"] is False


@pytest.mark.asyncio
async def test_member_list_and_summary_are_limited_to_owned_batches(
    client, owner_db, monkeypatch
) -> None:  # noqa: ANN001
    _patch_scope(
        monkeypatch,
        _member_scope(teams={"team-1": set(VIEWER_PERMISSIONS)}),
    )

    listing = await client.get("/ui/api/batches", headers=AUTH)
    summary = await client.get("/ui/api/batches/summary", headers=AUTH)

    assert listing.status_code == 200
    assert listing.json()["view"] == "own"
    assert summary.status_code == 200
    count_query, count_params = next(
        (query, params)
        for query, params in owner_db.queries
        if "COUNT(*) AS total FROM deltallm_batch_job j" in query
    )
    assert "j.created_by_owner_account_id = $1" in count_query
    assert count_params == ("acct-owner", "team-1", "org-1")
    summary_query, summary_params = next(
        (query, params) for query, params in owner_db.queries if "COUNT(*) FILTER" in query
    )
    assert "created_by_owner_account_id = $1" in summary_query
    assert summary_params == ("acct-owner", "team-1", "org-1")


@pytest.mark.asyncio
async def test_developer_batch_list_query_is_unchanged(client, owner_db, monkeypatch) -> None:  # noqa: ANN001
    _patch_scope(
        monkeypatch,
        _member_scope(orgs={}, teams={"team-1": set(DEVELOPER_PERMISSIONS)}),
    )

    listing = await client.get("/ui/api/batches", headers=AUTH)

    assert listing.status_code == 200
    assert listing.json()["view"] == "operator"
    count_query, count_params = next(
        (query, params)
        for query, params in owner_db.queries
        if "COUNT(*) AS total FROM deltallm_batch_job j" in query
    )
    assert "created_by_owner_account_id" not in count_query
    assert count_params == ("team-1",)


@pytest.mark.asyncio
async def test_member_reads_own_batch_detail_items_costs_and_webhooks(
    client, owner_db, test_app, monkeypatch
) -> None:  # noqa: ANN001
    class Repository:
        async def list_webhook_outbox_by_batch_id(self, *, batch_id: str):  # noqa: ANN201
            return []

    test_app.state.batch_repository = Repository()
    _patch_scope(monkeypatch, _member_scope())

    detail = await client.get("/ui/api/batches/own-team", headers=AUTH)
    item = await client.get("/ui/api/batches/own-team/items/item-1", headers=AUTH)
    costs = await client.get("/ui/api/batches/own-team/costs", headers=AUTH)
    webhooks = await client.get("/ui/api/batches/own-team/webhook-deliveries", headers=AUTH)

    assert [detail.status_code, item.status_code, costs.status_code, webhooks.status_code] == [
        200,
        200,
        200,
        200,
    ]
    assert detail.json()["capabilities"] == {
        "view": True,
        "cancel": False,
        "retry_finalization": False,
        "requeue_stale": False,
        "mark_failed": False,
        "replay_webhook": False,
    }
    assert "created_by_owner_account_id" not in detail.json()
    assert webhooks.json()["capabilities"]["replay_webhook"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_id", ["peer-team", "own-left-org", "legacy-unowned"])
async def test_member_cannot_read_batches_they_do_not_own_in_current_memberships(
    client, owner_db, monkeypatch, batch_id
) -> None:  # noqa: ANN001
    _patch_scope(monkeypatch, _member_scope())

    for path in (
        f"/ui/api/batches/{batch_id}",
        f"/ui/api/batches/{batch_id}/items/item-1",
        f"/ui/api/batches/{batch_id}/costs",
    ):
        response = await client.get(path, headers=AUTH)
        assert response.status_code == 403, path
        assert "own prompt" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/ui/api/batches/own-team/cancel"),
        ("POST", "/ui/api/batches/own-team/mark-failed"),
        ("POST", "/ui/api/batches/own-team/requeue-stale"),
        ("POST", "/ui/api/batches/own-team/retry-finalization"),
    ],
)
async def test_member_cannot_change_their_own_batch(
    client, owner_db, monkeypatch, method, path
) -> None:  # noqa: ANN001
    _patch_scope(monkeypatch, _member_scope())

    response = await client.request(method, path, headers=AUTH, json={})

    assert response.status_code == 403
    assert not any("UPDATE deltallm_batch_job" in query for query, _ in owner_db.queries)
