"""Batch jobs list filters, filter options, item filters and error groups on real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from prisma import Prisma

from src.api.admin.endpoints.common import AuthScope
from src.auth.roles import Permission

pytestmark = pytest.mark.postgres

AUTH = {"Authorization": "Bearer mk-test"}


class _RollbackFixture(Exception):
    pass


@pytest.fixture
async def listing_database() -> AsyncIterator[tuple[Prisma, str]]:
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires DATABASE_URL")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    prefix = "batch-list-" + uuid4().hex[:12]
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
    for team, alias in (("search", "Search"), ("support", "Support"), ("other", "Other")):
        await db.execute_raw(
            "INSERT INTO deltallm_teamtable (team_id, team_alias) VALUES ($1, $2)",
            f"{prefix}-{team}",
            alias,
        )
    await db.execute_raw(
        "INSERT INTO deltallm_batch_file (file_id, purpose, filename, bytes, storage_key) VALUES ($1, 'batch', 'in.jsonl', 12, $1)",
        f"{prefix}-file",
    )
    jobs = (
        # (batch, team, endpoint, status, model, age in days)
        ("emb-run", "search", "/v1/embeddings", "in_progress", "bge-m3", 0),
        ("emb-final", "search", "/v1/embeddings", "finalizing", "bge-m3", 0),
        ("chat-done", "support", "/v1/chat/completions", "completed", "gpt-4o-mini", 1),
        ("chat-old", "support", "/v1/chat/completions", "expired", "gpt-4o-mini", 20),
        ("hidden", "other", "/v1/embeddings", "failed", "bge-m3", 0),
    )
    for batch, team, endpoint, status, model, age in jobs:
        await db.execute_raw(
            """INSERT INTO deltallm_batch_job (batch_id, endpoint, status, model, input_file_id,
                created_by_team_id, total_items, completed_items, failed_items, created_at)
            VALUES ($1, $2, $3::"DeltaLLM_BatchJobStatus", $4, $5, $6, 4, 1, 2,
                    NOW() - ($7::int * INTERVAL '1 day'))""",
            f"{prefix}-{batch}",
            endpoint,
            status,
            model,
            f"{prefix}-file",
            f"{prefix}-{team}",
            age,
        )
    items = (
        (1, "completed", None),
        (2, "failed", '{"retry_category":"timeout","message":"provider text"}'),
        (3, "failed", '{"retry_category":"timeout"}'),
        (4, "pending", None),
    )
    for line, status, error in items:
        await db.execute_raw(
            """INSERT INTO deltallm_batch_item (item_id, batch_id, line_number, custom_id, status,
                request_body, error_body) VALUES ($1, $2, $3, $4, $5, '{"input":"x"}', $6::jsonb)""",
            f"{prefix}-item-{line}",
            f"{prefix}-emb-run",
            line,
            f"doc-{line}",
            status,
            error,
        )


@pytest.fixture
def listing_app(test_app, listing_database, monkeypatch):  # noqa: ANN001, ANN201
    db, prefix = listing_database
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    teams = [f"{prefix}-search", f"{prefix}-support"]
    scope = AuthScope(
        is_platform_admin=False,
        org_ids=[],
        team_ids=teams,
        team_permissions_by_id={team: {Permission.KEY_READ} for team in teams},
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: scope,
    )
    return prefix


def _names(payload: dict, prefix: str) -> set[str]:
    return {row["batch_id"].removeprefix(f"{prefix}-") for row in payload["data"]}


async def test_status_list_and_filters_narrow_the_visible_jobs(client, listing_app):  # noqa: ANN001
    prefix = listing_app

    running = await client.get(
        "/ui/api/batches", params={"status": "in_progress,finalizing"}, headers=AUTH
    )
    chat = await client.get(
        "/ui/api/batches",
        params={"endpoint": "/v1/chat/completions", "team_id": f"{prefix}-support"},
        headers=AUTH,
    )
    model = await client.get("/ui/api/batches", params={"model": "bge-m3"}, headers=AUTH)

    assert _names(running.json(), prefix) == {"emb-run", "emb-final"}
    assert _names(chat.json(), prefix) == {"chat-done", "chat-old"}
    assert _names(model.json(), prefix) == {"emb-run", "emb-final"}


async def test_summary_counts_follow_the_filters(client, listing_app):  # noqa: ANN001
    response = await client.get(
        "/ui/api/batches/summary",
        params={"created_after": "2000-01-01T00:00:00Z", "endpoint": "/v1/embeddings"},
        headers=AUTH,
    )

    counts = response.json()
    assert counts["total"] == 2
    assert counts["in_progress"] == 2
    assert counts["finalizing"] == 1
    assert counts["failed"] == 0


async def test_facets_list_only_visible_models_and_teams(client, listing_app):  # noqa: ANN001
    prefix = listing_app

    response = await client.get("/ui/api/batches/facets", headers=AUTH)

    payload = response.json()
    assert {(row["value"], row["count"]) for row in payload["models"]} == {
        ("bge-m3", 2),
        ("gpt-4o-mini", 2),
    }
    assert {(row["value"], row["label"]) for row in payload["teams"]} == {
        (f"{prefix}-search", "Search"),
        (f"{prefix}-support", "Support"),
    }


async def test_item_filters_and_error_groups(client, listing_app):  # noqa: ANN001
    prefix = listing_app
    batch = f"{prefix}-emb-run"

    failed = await client.get(
        f"/ui/api/batches/{batch}", params={"item_status": "failed"}, headers=AUTH
    )
    by_id = await client.get(
        f"/ui/api/batches/{batch}", params={"custom_id": "doc-4"}, headers=AUTH
    )
    errors = await client.get(f"/ui/api/batches/{batch}/errors", headers=AUTH)
    hidden = await client.get(f"/ui/api/batches/{prefix}-hidden/errors", headers=AUTH)

    assert [row["line_number"] for row in failed.json()["items"]["data"]] == [2, 3]
    assert failed.json()["items"]["pagination"]["total"] == 2
    assert [row["custom_id"] for row in by_id.json()["items"]["data"]] == ["doc-4"]
    assert by_id.json()["items"]["pagination"]["total"] == 1
    assert errors.json()["data"] == [
        {
            "message": "Provider request timed out",
            "retry_category": "timeout",
            "code": None,
            "count": 2,
        }
    ]
    assert "provider text" not in errors.text
    assert hidden.status_code == 403
