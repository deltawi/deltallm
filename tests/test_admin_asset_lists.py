import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from src.api.admin.list_contracts import AdminListResponse, ModelListItem
from src.db.catalog.admin_asset_lists import list_asset_rows
from src.db.catalog.logical_models import LogicalModelRecord
from src.db.catalog.model_deployments import ModelDeploymentRepository
from src.router.health_state import DeploymentHealthRef
from src.router.state import RedisStateBackend
from src.services.reporting.admin_list_health import (
    AdminHealthReader,
    ListHealthSnapshot,
    list_health_snapshot,
)
from src.services.models.model_admin_listing import ModelSortKey, SortDirection, model_list_page


def model_rows() -> list[dict[str, object]]:
    return [
        {
            "deployment_id": "dep-old",
            "model_name": "alpha",
            "provider": "openai",
            "mode": "chat",
            "created_by_user_id": "user-b",
            "updated_at": "2026-10-01T10:00:00Z",
        },
        {
            "deployment_id": "dep-unknown",
            "model_name": "bravo",
            "provider": "anthropic",
            "mode": "chat",
            "created_by_user_id": "user-a",
            "updated_at": "2026-10-02T10:00:00Z",
            "access": {"visibility": "team"},
        },
        {
            "deployment_id": "dep-new",
            "model_name": "charlie",
            "provider": "openai",
            "mode": "embedding",
            "created_by_user_id": "platform-admin-id",
            "updated_at": "2026-10-03T10:00:00Z",
        },
        {
            "deployment_id": "dep-legacy",
            "model_name": "delta",
            "provider": "openai",
            "routable": False,
        },
    ]


@pytest.mark.parametrize(
    ("key", "direction", "expected"),
    [
        ("updated_at", "desc", ["dep-new", "dep-unknown", "dep-old", "dep-legacy"]),
        ("updated_at", "asc", ["dep-old", "dep-unknown", "dep-new", "dep-legacy"]),
        ("health", "asc", ["dep-legacy", "dep-old", "dep-unknown", "dep-new"]),
        ("health", "desc", ["dep-new", "dep-unknown", "dep-legacy", "dep-old"]),
        ("created_by", "asc", ["dep-new", "dep-unknown", "dep-old", "dep-legacy"]),
        ("provider", "asc", ["dep-unknown", "dep-legacy", "dep-new", "dep-old"]),
    ],
)
def test_model_sort_is_global_before_pagination(
    key: ModelSortKey, direction: SortDirection, expected: list[str]
) -> None:
    rows: list[str] = []
    for offset in [0, 2]:
        result = model_list_page(
            model_rows(),
            health=ListHealthSnapshot(["dep-new"], ["dep-unknown"]),
            search=None,
            provider=None,
            mode=None,
            sort_by=key,
            sort_direction=direction,
            limit=2,
            offset=offset,
        )
        response = AdminListResponse[ModelListItem].model_validate(result)
        rows.extend(row.deployment_id for row in response.data)
        assert response.pagination.total == 4
        assert response.pagination.has_more is (offset == 0)
        for row in response.data:
            assert "@" not in (row.created_by_user_id or "")
            if row.deployment_id == "dep-unknown":
                assert row.healthy is None
                assert row.health_status == "unknown"
                assert row.visibility == "team"
    assert rows == expected


def test_model_filters_preserve_sorted_total_and_input() -> None:
    rows = model_rows()
    result = model_list_page(
        rows,
        health=ListHealthSnapshot(["dep-new"], []),
        search="CHAR",
        provider="OpenAI",
        mode="embedding",
        sort_by="updated_at",
        sort_direction="desc",
        limit=1,
        offset=0,
    )
    response = AdminListResponse[ModelListItem].model_validate(result)
    assert response.pagination.total == 1
    assert response.data[0].deployment_id == "dep-new"
    assert "health_status" not in rows[0]


@pytest.mark.asyncio
async def test_health_uses_two_batches_and_current_generations() -> None:
    backend = SimpleNamespace(
        get_health_batch=AsyncMock(
            return_value={"a": {"healthy": "true"}, "b": {"healthy": "false"}}
        ),
        get_cooldown_batch=AsyncMock(return_value={"c": True}),
        get_backend_status=lambda: {"mode": "redis"},
    )
    refs = {key: DeploymentHealthRef(key, "current") for key in ["a", "b", "c", "d"]}
    result = await list_health_snapshot(cast(AdminHealthReader, backend), refs)
    assert result == ListHealthSnapshot(["a", "d"], [])
    backend.get_health_batch.assert_awaited_once_with(list(refs.values()))
    backend.get_cooldown_batch.assert_awaited_once_with(list(refs.values()))


@pytest.mark.asyncio
async def test_failed_or_degraded_health_is_unknown() -> None:
    backend = SimpleNamespace(
        get_health_batch=AsyncMock(side_effect=RuntimeError("unavailable")),
        get_cooldown_batch=AsyncMock(return_value={}),
    )
    assert await list_health_snapshot(
        cast(AdminHealthReader, backend), {"a": "a"}
    ) == ListHealthSnapshot([], ["a"])
    backend.get_health_batch = AsyncMock(return_value={})
    backend.get_backend_status = lambda: {"mode": "degraded"}
    assert await list_health_snapshot(
        cast(AdminHealthReader, backend), {"a": "a"}
    ) == ListHealthSnapshot([], ["a"])
    assert await list_health_snapshot(None, {"a": "a"}) == ListHealthSnapshot([], ["a"])
    assert await list_health_snapshot(None, {}) == ListHealthSnapshot([], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_read", ["health", "cooldown"])
async def test_one_successful_read_cannot_hide_a_failed_health_snapshot(
    failed_read: str,
) -> None:
    failed = asyncio.Event()
    unavailable = True

    class HealthPipeline:
        def hgetall(self, _key: str) -> None:
            pass

        async def execute(self) -> list[dict[str, str]]:
            if unavailable and failed_read == "health":
                failed.set()
                raise TimeoutError("Health read failed")
            if unavailable:
                await failed.wait()
            return [{"healthy": "true"}]

    class PartialRedis:
        def pipeline(self) -> HealthPipeline:
            return HealthPipeline()

        async def mget(self, _keys: list[str]) -> list[None]:
            if unavailable and failed_read == "cooldown":
                failed.set()
                raise TimeoutError("Cooldown read failed")
            if unavailable:
                await failed.wait()
            return [None]

    backend = RedisStateBackend(PartialRedis(), degraded_mode="fail_open")
    refs = {"a": DeploymentHealthRef("a", "current")}
    assert await list_health_snapshot(backend, refs) == ListHealthSnapshot([], ["a"])
    assert backend.get_backend_status()["mode"] == "redis"
    unavailable = False
    assert await list_health_snapshot(backend, refs) == ListHealthSnapshot(["a"], [])


class QueryReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]:
        self.calls.append((query, params))
        return [{"total": 3}] if query.startswith("SELECT COUNT") else []


@pytest.mark.asyncio
async def test_empty_scope_never_reads_an_unscoped_asset_list() -> None:
    reader = QueryReader()
    result = await list_asset_rows(
        reader,
        kind="group",
        search=None,
        limit=10,
        offset=0,
        managed_asset_ids=[],
        sort_by="health",
        sort_direction="asc",
    )
    assert result.total == 0
    assert result.rows == []
    assert reader.calls == []


@pytest.mark.asyncio
async def test_asset_list_binds_search_scope_health_and_pages_in_two_queries() -> None:
    reader = QueryReader()
    result = await list_asset_rows(
        reader,
        kind="group",
        search="support'",
        limit=10,
        offset=20,
        managed_asset_ids=["asset-1"],
        sort_by="health",
        sort_direction="asc",
        health=ListHealthSnapshot(["dep-good"], ["dep-unknown"]),
    )
    assert result.total == 3
    assert len(reader.calls) == 2
    query, params = reader.calls[1]
    assert "ORDER BY health_rank ASC NULLS LAST, group_key ASC" in query
    assert "LIMIT $6 OFFSET $7" in query
    assert params == ("%support'%", ["asset-1"], ["dep-good"], ["dep-unknown"], True, 10, 20)
    assert "support'" not in query
    assert "created_by_account_id AS created_by_user_id" in query
    assert "email" not in query


@pytest.mark.asyncio
async def test_deployment_list_normalizes_persisted_timestamps() -> None:
    db = SimpleNamespace(
        query_raw=AsyncMock(
            return_value=[
                {
                    "deployment_id": "dep",
                    "model_name": "support",
                    "deltallm_params": {},
                    "created_at": "2026-10-01T12:00:00Z",
                    "updated_at": "2026-10-02T12:00:00",
                }
            ]
        )
    )
    record = (await ModelDeploymentRepository(db).list_all())[0]
    assert record.created_at == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert record.updated_at == datetime(2026, 10, 2, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_models_api_returns_creator_id_and_stored_dates(
    client: httpx.AsyncClient, test_app: FastAPI
) -> None:
    test_app.state.settings.master_key = "mk-test"
    test_app.state.logical_model_repository = SimpleNamespace(
        list_by_names=AsyncMock(
            return_value=[
                LogicalModelRecord(
                    "model-1",
                    "gpt-4o-mini",
                    "asset-1",
                    updated_at=datetime(2026, 10, 3, tzinfo=UTC),
                    created_by_user_id="platform-admin-account-id",
                ),
            ]
        )
    )
    response = await client.get(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        params={"sort_by": "updated_at", "sort_direction": "desc", "limit": 1},
    )
    assert response.status_code == 200
    row = response.json()["data"][0]
    assert row["created_by_user_id"] == "platform-admin-account-id"
    assert row["updated_at"].startswith("2026-10-03T00:00:00")
    assert "email" not in row


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/ui/api/models", "/ui/api/route-groups", "/ui/api/prompt-registry/templates"]
)
async def test_list_api_rejects_invalid_sort(
    client: httpx.AsyncClient, test_app: FastAPI, path: str
) -> None:
    test_app.state.settings.master_key = "mk-test"
    headers = {"Authorization": "Bearer mk-test"}
    response = await client.get(
        path, params={"sort_by": "email;DROP TABLE anything"}, headers=headers
    )
    assert response.status_code == 422
    response = await client.get(path, params={"sort_direction": "sideways"}, headers=headers)
    assert response.status_code == 422
