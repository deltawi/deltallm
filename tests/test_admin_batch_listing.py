"""Batch jobs list filters, status counts, filter options, item filters and error groups."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.api.admin.endpoints.common import AuthScope
from src.auth.roles import Permission
from src.batch.repositories.admin_items_repository import (
    ERROR_GROUP_LIMIT,
    BatchItemErrorGroup,
    BatchItemPageQuery,
    fetch_batch_item_rows,
    group_batch_item_errors,
)
from src.batch.repositories.admin_listing_repository import (
    BatchListFilters,
    append_batch_list_filters,
    count_batch_statuses,
)

AUTH = {"Authorization": "Bearer mk-test"}


class RecordingDB:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.queries: list[tuple[str, tuple]] = []

    async def query_raw(self, query: str, *params):  # noqa: ANN002, ANN201
        self.queries.append((query, params))
        return self.rows


def test_list_filters_bind_every_value_and_narrow_the_scope() -> None:
    clauses = ["(j.created_by_team_id IN ($1))"]
    params: list = ["team-1"]
    after = datetime(2026, 10, 1, tzinfo=UTC)

    append_batch_list_filters(
        clauses=clauses,
        params=params,
        filters=BatchListFilters(
            search="abc",
            statuses=("in_progress", "finalizing"),
            model="gpt-4o-mini",
            endpoint="/v1/chat/completions",
            team_id="team-1",
            created_after=after,
        ),
        job_alias="j.",
    )

    assert clauses[0] == "(j.created_by_team_id IN ($1))"
    assert clauses[1:] == [
        "(j.batch_id ILIKE $2 OR j.model ILIKE $2)",
        'j.status IN ($3::"DeltaLLM_BatchJobStatus", $4::"DeltaLLM_BatchJobStatus")',
        "j.model = $5",
        "j.endpoint = $6",
        "j.created_by_team_id = $7",
        "j.created_at >= $8::timestamp",
    ]
    assert params == [
        "team-1",
        "%abc%",
        "in_progress",
        "finalizing",
        "gpt-4o-mini",
        "/v1/chat/completions",
        "team-1",
        datetime(2026, 10, 1),
    ]


def test_summary_filters_skip_status() -> None:
    clauses: list[str] = []
    params: list = []

    append_batch_list_filters(
        clauses=clauses,
        params=params,
        filters=BatchListFilters(statuses=("failed",), model="bge-m3"),
        include_status=False,
    )

    assert clauses == ["model = $1"]
    assert params == ["bge-m3"]


@pytest.mark.asyncio
async def test_status_counts_split_finalizing_and_expired() -> None:
    db = RecordingDB(
        [
            {
                "total": 9,
                "queued": 1,
                "in_progress": 3,
                "finalizing": 1,
                "completed": 2,
                "failed": 1,
                "cancelled": 1,
                "expired": 1,
            }
        ]
    )

    counts = await count_batch_statuses(db, clauses=["model = $1"], params=["bge-m3"])

    assert counts.to_response() == {
        "total": 9,
        "queued": 1,
        "in_progress": 3,
        "finalizing": 1,
        "completed": 2,
        "failed": 1,
        "cancelled": 1,
        "expired": 1,
    }
    query, params = db.queries[0]
    assert "WHERE model = $1" in query
    assert params == ("bge-m3",)


@pytest.mark.asyncio
async def test_item_page_filters_by_status_and_custom_id() -> None:
    db = RecordingDB()

    await fetch_batch_item_rows(
        db,
        BatchItemPageQuery(
            batch_id="batch-1",
            limit=50,
            after_line_number=100,
            status="failed",
            custom_id="doc-7",
        ),
    )

    query, params = db.queries[0]
    assert "WHERE batch_id = $1 AND status = $2 AND custom_id = $3 AND line_number > $4" in query
    assert "LIMIT $5" in query and "OFFSET" not in query
    assert params == ("batch-1", "failed", "doc-7", 100, 51)


@pytest.mark.asyncio
async def test_error_groups_read_only_allowlisted_fields() -> None:
    db = RecordingDB([{"retry_category": "timeout", "code": None, "count": 20}])

    groups = await group_batch_item_errors(db, "batch-1")

    assert [(g.retry_category, g.code, g.count) for g in groups] == [("timeout", None, 20)]
    query, params = db.queries[0]
    assert "last_error" not in query
    assert "response_body" not in query
    assert params == ("batch-1", "failed")


class ListingDB:
    """Fake database for the HTTP routes. It records every query."""

    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.queries: list[tuple[str, tuple]] = []
        self.job = {
            "batch_id": "batch-1",
            "endpoint": "/v1/embeddings",
            "status": "in_progress",
            "model": "bge-m3",
            "execution_mode": "managed_internal",
            "input_file_id": "file-in",
            "output_file_id": None,
            "error_file_id": None,
            "total_items": 10,
            "completed_items": 6,
            "failed_items": 2,
            "cancelled_items": 0,
            "in_progress_items": 1,
            "created_by_api_key": "sk-test-1234567890",
            "created_by_team_id": "team-1",
            "created_by_organization_id": None,
            "created_by_owner_account_id": None,
            "organization_id": "org-1",
            "team_alias": "Search",
            "created_at": now,
            "started_at": now,
            "completed_at": None,
            "cancel_requested_at": None,
            "expires_at": None,
        }

    async def query_raw(self, query: str, *params):  # noqa: ANN002, ANN201
        self.queries.append((query, params))
        if "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1" in query:
            return [{"organization_id": "org-1"}]
        if "GROUP BY 1, 2" in query:
            return [
                {"retry_category": "timeout", "code": None, "count": 2},
                {"retry_category": "provider said: secret", "code": "not-public", "count": 1},
                {"retry_category": "other junk", "code": None, "count": 4},
            ]
        if "GROUP BY j.model" in query:
            return [{"value": "bge-m3", "count": 3}]
        if "GROUP BY j.created_by_team_id" in query:
            return [{"value": "team-1", "label": "Search", "count": 3}]
        if "COUNT(*) FILTER" in query:
            return [{"total": 1, "in_progress": 1}]
        if "COUNT(*) AS total FROM deltallm_batch_job j" in query:
            return [{"total": 1}]
        if "FROM deltallm_batch_job j" in query and "WHERE j.batch_id = $1" in query:
            return [dict(self.job)] if params[0] == "batch-1" else []
        if "FROM deltallm_batch_job" in query and "WHERE batch_id = $1" in query:
            return [
                {
                    "batch_id": "batch-1",
                    "status": "in_progress",
                    "created_by_team_id": "team-1",
                    "created_by_organization_id": None,
                    "created_by_owner_account_id": None,
                }
            ]
        if "FROM deltallm_batch_job j" in query:
            return [dict(self.job, total_cost=0.0)]
        if "FROM deltallm_batch_item" in query:
            return []
        return []


@pytest.fixture
def listing_db(test_app, monkeypatch):  # noqa: ANN001, ANN201
    db = ListingDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    scope = AuthScope(
        is_platform_admin=False,
        org_ids=[],
        team_ids=["team-1"],
        team_permissions_by_id={"team-1": {Permission.TEAM_READ, Permission.KEY_READ}},
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: scope,
    )
    return db


def _query_with(db: ListingDB, marker: str) -> tuple[str, tuple]:
    return next((query, params) for query, params in db.queries if marker in query)


@pytest.mark.asyncio
async def test_list_route_applies_filters_after_scope(client, listing_db) -> None:  # noqa: ANN001
    response = await client.get(
        "/ui/api/batches",
        params={
            "status": "in_progress,finalizing,unknown",
            "model": "bge-m3",
            "endpoint": "/v1/embeddings",
            "team_id": "team-1",
            "created_after": "2026-10-01T00:00:00",
        },
        headers=AUTH,
    )

    assert response.status_code == 200
    query, params = _query_with(listing_db, "COUNT(*) AS total FROM deltallm_batch_job j")
    assert "j.created_by_team_id IN ($1)" in query
    assert 'j.status IN ($2::"DeltaLLM_BatchJobStatus", $3::"DeltaLLM_BatchJobStatus")' in query
    assert params[:6] == (
        "team-1",
        "in_progress",
        "finalizing",
        "bge-m3",
        "/v1/embeddings",
        "team-1",
    )
    assert params[6] == datetime(2026, 10, 1)


@pytest.mark.asyncio
async def test_list_route_rejects_unknown_endpoint(client, listing_db) -> None:  # noqa: ANN001
    response = await client.get("/ui/api/batches", params={"endpoint": "/v1/images"}, headers=AUTH)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_summary_route_reports_new_status_keys(client, listing_db) -> None:  # noqa: ANN001
    response = await client.get("/ui/api/batches/summary", params={"model": "bge-m3"}, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "total": 1,
        "queued": 0,
        "in_progress": 1,
        "finalizing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "expired": 0,
    }
    query, params = _query_with(listing_db, "COUNT(*) FILTER")
    assert "model = $2" in query
    assert params == ("team-1", "bge-m3")


@pytest.mark.asyncio
async def test_facets_route_is_scoped(client, listing_db) -> None:  # noqa: ANN001
    response = await client.get("/ui/api/batches/facets", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "models": [{"value": "bge-m3", "label": "bge-m3", "count": 3}],
        "teams": [{"value": "team-1", "label": "Search", "count": 3}],
    }
    query, params = _query_with(listing_db, "GROUP BY j.model")
    assert "j.created_by_team_id IN ($1)" in query
    assert params == ("team-1",)


@pytest.mark.asyncio
async def test_facets_route_returns_nothing_without_scope(client, test_app, monkeypatch) -> None:  # noqa: ANN001
    db = ListingDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False, org_ids=[], team_ids=[]
        ),
    )

    response = await client.get("/ui/api/batches/facets", headers=AUTH)

    assert response.json() == {"models": [], "teams": []}
    assert db.queries == []


@pytest.mark.asyncio
async def test_detail_filters_items_and_uses_job_counters(client, listing_db) -> None:  # noqa: ANN001
    response = await client.get(
        "/ui/api/batches/batch-1", params={"item_status": "failed"}, headers=AUTH
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["input_file_id"] == "file-in"
    assert payload["items"]["pagination"]["total"] == 2
    query, params = _query_with(listing_db, "FROM deltallm_batch_item")
    assert "status = $2" in query
    assert params[:2] == ("batch-1", "failed")
    assert not any("COUNT(*) AS total FROM deltallm_batch_item" in q for q, _ in listing_db.queries)


@pytest.mark.asyncio
async def test_detail_pending_total_is_the_remainder(client, listing_db) -> None:  # noqa: ANN001
    response = await client.get(
        "/ui/api/batches/batch-1", params={"item_status": "pending"}, headers=AUTH
    )

    assert response.json()["items"]["pagination"]["total"] == 1


@pytest.mark.asyncio
async def test_detail_rejects_unknown_item_status(client, listing_db) -> None:  # noqa: ANN001
    response = await client.get(
        "/ui/api/batches/batch-1", params={"item_status": "weird"}, headers=AUTH
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_error_groups_return_public_messages_only(client, listing_db) -> None:  # noqa: ANN001
    response = await client.get("/ui/api/batches/batch-1/errors", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "batch_id": "batch-1",
        "data": [
            {"message": "Batch request failed", "retry_category": None, "code": None, "count": 5},
            {
                "message": "Provider request timed out",
                "retry_category": "timeout",
                "code": None,
                "count": 2,
            },
        ],
    }
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_error_groups_deny_other_scopes(client, test_app, monkeypatch) -> None:  # noqa: ANN001
    db = ListingDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=[],
            team_ids=["team-2"],
            team_permissions_by_id={"team-2": {Permission.KEY_READ}},
        ),
    )

    response = await client.get("/ui/api/batches/batch-1/errors", headers=AUTH)

    assert response.status_code in {403, 404}
    assert not any("GROUP BY 1, 2" in query for query, _ in db.queries)


@pytest.mark.asyncio
async def test_error_groups_merge_raw_codes_before_the_response_limit(
    client, listing_db, monkeypatch
) -> None:  # noqa: ANN001
    # 25 unknown raw codes become one public error, so a small real group still shows.
    groups = [
        BatchItemErrorGroup(retry_category="nope", code=f"raw-{index}", count=10)
        for index in range(25)
    ]
    groups.append(BatchItemErrorGroup(retry_category="timeout", code=None, count=1))

    async def fake_groups(db, batch_id):  # noqa: ANN001, ANN202
        return groups

    monkeypatch.setattr("src.api.admin.endpoints.batches.group_batch_item_errors", fake_groups)

    response = await client.get("/ui/api/batches/batch-1/errors", headers=AUTH)

    assert len(groups) > ERROR_GROUP_LIMIT
    assert [(row["retry_category"], row["count"]) for row in response.json()["data"]] == [
        (None, 250),
        ("timeout", 1),
    ]
