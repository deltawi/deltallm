"""Usage explorer SQL returns correct aggregates against the real spend read view."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from src.billing.spend.spend_read import get_spend_read_source
from src.db.billing.usage_explorer_queries import (
    ReportQuery,
    ReportWindow,
    usage_breakdown_query,
    usage_series_query,
    usage_summary_query,
)
from src.services.reporting.spend_visibility import SpendVisibility
from src.services.reporting.usage_filters import UsageFilters

try:
    from prisma import Prisma
except Exception:  # pragma: no cover
    Prisma = None  # type: ignore[assignment]

pytestmark = pytest.mark.postgres

DATABASE_URL = os.getenv("DATABASE_URL")
DAY = datetime(2026, 9, 10, tzinfo=UTC)
WINDOW = ReportWindow(
    start=DAY,
    end=DAY + timedelta(days=2) - timedelta(microseconds=1),
    previous_start=DAY - timedelta(days=2),
)


@pytest.fixture
async def explorer_db():
    if Prisma is None or not DATABASE_URL:  # pragma: no cover
        if os.getenv("CI"):
            pytest.fail("CI must provide DATABASE_URL and the generated prisma client")
        pytest.skip("DATABASE_URL and prisma client are required")
    db = Prisma(datasource={"url": DATABASE_URL})
    await db.connect()
    organization_id = f"org-{uuid4()}"
    try:
        yield db, organization_id
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_spendlog_events WHERE organization_id = $1", organization_id
        )
        await db.disconnect()


async def _insert(
    db: Any,
    organization_id: str,
    *,
    model: str,
    at: datetime,
    spend: float,
    tokens: int = 10,
    status: str = "success",
    call_type: str = "completion",
    team_id: str | None = None,
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_spendlog_events (
            id, request_id, call_type, api_key, team_id, organization_id, model, provider,
            spend, total_tokens, input_tokens, output_tokens, start_time, end_time, status
        )
        VALUES (
            $1, $1, $2, 'key-hash', $3, $4, $5, 'openai', $6, $7, $7, 0,
            $8::timestamp, $8::timestamp, $9
        )
        """,
        str(uuid4()),
        call_type,
        team_id,
        organization_id,
        model,
        spend,
        tokens,
        at.replace(tzinfo=None).isoformat(),
        status,
    )


def _visibility(organization_id: str) -> SpendVisibility:
    return SpendVisibility(
        is_platform_admin=False,
        organization_ids=(organization_id,),
        active_view="organization",
    )


async def _rows(db: Any, query: ReportQuery) -> list[dict[str, Any]]:
    return [dict(row) for row in await db.query_raw(query.sql, *query.params)]


async def _seed(db: Any, organization_id: str) -> None:
    hour = timedelta(hours=1)
    await _insert(db, organization_id, model="gpt-4o", at=DAY + hour, spend=3.0)
    await _insert(db, organization_id, model="gpt-4o", at=DAY + hour, spend=1.0, status="error")
    await _insert(db, organization_id, model="claude", at=DAY + timedelta(days=1), spend=2.0)
    await _insert(db, organization_id, model="mini", at=DAY + timedelta(days=1), spend=0.5)
    # Previous window and a selector charge that costs money but is not a request.
    await _insert(db, organization_id, model="gpt-4o", at=DAY - timedelta(days=1), spend=4.0)
    await _insert(
        db,
        organization_id,
        model="selector",
        at=DAY + hour,
        spend=0.25,
        call_type="model_router_selector",
    )
    # Outside both windows.
    await _insert(db, organization_id, model="gpt-4o", at=DAY - timedelta(days=5), spend=99.0)


async def test_summary_compares_current_and_previous_window(explorer_db):
    db, organization_id = explorer_db
    await _seed(db, organization_id)
    query = usage_summary_query(
        window=WINDOW,
        visibility=_visibility(organization_id),
        filters=UsageFilters(),
        source=get_spend_read_source(),
    )
    [row] = await _rows(db, query)
    assert float(row["total_spend"]) == pytest.approx(6.75)
    assert int(row["request_count"]) == 4
    assert int(row["failed_requests"]) == 1
    assert float(row["previous_total_spend"]) == pytest.approx(4.0)
    assert int(row["previous_request_count"]) == 1


async def test_summary_filter_narrows_rows(explorer_db):
    db, organization_id = explorer_db
    await _seed(db, organization_id)
    query = usage_summary_query(
        window=WINDOW,
        visibility=_visibility(organization_id),
        filters=UsageFilters.build(values={"model": "gpt-4o"}),
        source=get_spend_read_source(),
    )
    [row] = await _rows(db, query)
    assert float(row["total_spend"]) == pytest.approx(4.0)
    assert int(row["request_count"]) == 2


async def test_series_merges_groups_after_the_limit_into_other(explorer_db):
    db, organization_id = explorer_db
    await _seed(db, organization_id)
    query = usage_series_query(
        window=WINDOW,
        interval="day",
        split_by="model",
        max_groups=1,
        visibility=_visibility(organization_id),
        filters=UsageFilters(),
        source=get_spend_read_source(),
    )
    rows = await _rows(db, query)
    by_bucket = {(str(row["bucket"]), int(row["series_index"])): row for row in rows}
    assert set(by_bucket) == {("2026-09-10", 1), ("2026-09-10", 0), ("2026-09-11", 0)}
    top = by_bucket[("2026-09-10", 1)]
    assert top["group_key"] == "gpt-4o"
    assert float(top["total_spend"]) == pytest.approx(4.0)
    assert int(top["failed_requests"]) == 1
    other = by_bucket[("2026-09-11", 0)]
    assert other["group_key"] is None
    assert float(other["total_spend"]) == pytest.approx(2.5)
    assert int(other["request_count"]) == 2


async def test_breakdown_ranks_pages_and_reports_previous_spend(explorer_db):
    db, organization_id = explorer_db
    await _seed(db, organization_id)

    def query(**overrides: Any) -> ReportQuery:
        arguments: dict[str, Any] = {
            "window": WINDOW,
            "group_by": "model",
            "sort_by": "spend",
            "search": None,
            "limit": 2,
            "offset": 0,
            "visibility": _visibility(organization_id),
            "filters": UsageFilters(),
            "source": get_spend_read_source(),
        }
        arguments.update(overrides)
        return usage_breakdown_query(**arguments)

    rows = await _rows(db, query())
    assert [row["group_key"] for row in rows] == ["gpt-4o", "claude"]
    assert float(rows[0]["previous_spend"]) == pytest.approx(4.0)
    assert int(rows[0]["total_count"]) == 4
    assert float(rows[0]["scope_spend"]) == pytest.approx(6.75)

    past_end = await _rows(db, query(offset=10))
    assert len(past_end) == 1
    assert past_end[0]["request_count"] is None
    assert int(past_end[0]["total_count"]) == 4

    searched = await _rows(db, query(search="CLA"))
    assert [row["group_key"] for row in searched] == ["claude"]

    by_requests = await _rows(db, query(sort_by="requests", limit=1))
    assert by_requests[0]["group_key"] == "gpt-4o"


@pytest.mark.parametrize(
    "dimension", ["model", "provider", "organization", "team", "user", "api_key"]
)
async def test_every_dimension_runs_with_labels_and_filters(explorer_db, dimension):
    db, organization_id = explorer_db
    await _seed(db, organization_id)
    common: dict[str, Any] = {
        "window": WINDOW,
        "visibility": _visibility(organization_id),
        "filters": UsageFilters.build(values={"provider": "OpenAI"}, unassigned=["user"]),
        "source": get_spend_read_source(),
        "model_provider_overrides": {"mini": "azure"},
        "user_identity_labels_visible": True,
    }
    series = await _rows(
        db, usage_series_query(interval="week", split_by=dimension, max_groups=3, **common)
    )
    breakdown = await _rows(
        db,
        usage_breakdown_query(
            group_by=dimension, sort_by="tokens", search="o", limit=5, offset=0, **common
        ),
    )
    assert sum(float(row["total_spend"]) for row in series) == pytest.approx(6.75)
    assert breakdown


async def test_provider_filter_and_search_accept_braces_and_like_wildcards(explorer_db):
    db, organization_id = explorer_db
    await _seed(db, organization_id)
    await _insert(db, organization_id, model="my{model}_x", at=DAY + timedelta(hours=2), spend=1.5)
    common: dict[str, Any] = {
        "window": WINDOW,
        "visibility": _visibility(organization_id),
        "source": get_spend_read_source(),
        # Registry model names are written into the SQL text as literals.
        "model_provider_overrides": {"my{model}_x": "azure", "mini": "azure"},
    }

    by_provider = await _rows(
        db,
        usage_breakdown_query(
            group_by="model",
            sort_by="spend",
            search=None,
            limit=10,
            offset=0,
            filters=UsageFilters.build(values={"provider": "openai"}),
            **common,
        ),
    )
    underscore = await _rows(
        db,
        usage_breakdown_query(
            group_by="model",
            sort_by="spend",
            search="}_",
            limit=10,
            offset=0,
            filters=UsageFilters.build(values={}),
            **common,
        ),
    )
    percent = await _rows(
        db,
        usage_breakdown_query(
            group_by="model",
            sort_by="spend",
            search="%",
            limit=10,
            offset=0,
            filters=UsageFilters.build(values={}),
            **common,
        ),
    )

    # The stored provider column wins over the registry override; the point is that
    # an override name with braces no longer breaks the SQL text.
    assert "my{model}_x" in {row["group_key"] for row in by_provider}
    assert [row["group_key"] for row in underscore] == ["my{model}_x"]
    assert [row["request_count"] for row in percent] == [None]
