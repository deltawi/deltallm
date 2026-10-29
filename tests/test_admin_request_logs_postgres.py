"""Admin request log SQL filters, orders and labels rows against the real spend view."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from src.billing.spend.spend_read import get_spend_read_source
from src.db.billing.admin_request_logs import RequestLogFilters, request_log_queries
from src.services.reporting.usage_filters import UsageFilters
from tests.test_usage_explorer_queries_postgres import (
    DAY,
    _insert,
    _rows,
    _visibility,
)
from tests.test_usage_explorer_queries_postgres import explorer_db as _explorer_db

pytestmark = pytest.mark.postgres
explorer_db = _explorer_db


def _queries(organization_id: str, **overrides: Any):
    arguments: dict[str, Any] = {
        "filters": RequestLogFilters(),
        "visibility": _visibility(organization_id),
        "source": get_spend_read_source(),
        "limit": 10,
        "offset": 0,
        "cursor": None,
    }
    arguments.update(overrides)
    return request_log_queries(**arguments)


async def test_request_logs_filter_and_label_page_rows(explorer_db):
    db, organization_id = explorer_db
    team_id = f"team-{uuid4()}"
    await db.execute_raw(
        "INSERT INTO deltallm_organizationtable (id, organization_id, organization_name) "
        "VALUES ($1, $1, 'Acme')",
        organization_id,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_teamtable (team_id, organization_id, team_alias, models) "
        "VALUES ($1, $2, 'Search', ARRAY[]::text[])",
        team_id,
        organization_id,
    )
    try:
        await _insert(db, organization_id, model="gpt-4o", at=DAY, spend=1.0, team_id=team_id)
        await _insert(
            db,
            organization_id,
            model="gpt-4o",
            at=DAY + timedelta(minutes=1),
            spend=2.0,
            status="error",
        )
        await _insert(db, organization_id, model="claude", at=DAY + timedelta(minutes=2), spend=3.0)

        rows = await _rows(db, _queries(organization_id).page)
        assert [float(row["spend"]) for row in rows] == [3.0, 2.0, 1.0]
        assert rows[2]["team_alias"] == "Search"
        assert rows[2]["organization_name"] == "Acme"
        assert rows[0]["team_alias"] is None
        assert rows[0]["provider"] == "openai"

        errors = _queries(organization_id, filters=RequestLogFilters(status="error"))
        assert [float(row["spend"]) for row in await _rows(db, errors.page)] == [2.0]
        [count] = await _rows(db, errors.count)
        assert int(count["total"]) == 1

        by_model = _queries(
            organization_id,
            filters=RequestLogFilters(usage=UsageFilters.build(values={"model": "gpt-4o"})),
        )
        assert len(await _rows(db, by_model.page)) == 2

        by_request = _queries(
            organization_id, filters=RequestLogFilters(request_id=rows[1]["request_id"])
        )
        assert [row["id"] for row in await _rows(db, by_request.page)] == [rows[1]["id"]]

        cached = _queries(organization_id, filters=RequestLogFilters(cache_hit=True))
        assert await _rows(db, cached.page) == []

        after_first = _queries(
            organization_id, cursor=(rows[0]["start_time"], rows[0]["id"]), limit=1
        )
        assert [row["id"] for row in await _rows(db, after_first.page)] == [rows[1]["id"]]

        exported = await _rows(db, _queries(organization_id, include_details=False).page)
        assert [float(row["spend"]) for row in exported] == [3.0, 2.0, 1.0]
        assert exported[2]["team_alias"] == "Search"
        assert all(row["metadata"] is None and row["cache_key"] is None for row in exported)
    finally:
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", team_id)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", organization_id
        )


async def _insert_detailed(
    db: Any,
    organization_id: str,
    *,
    at: Any,
    spend: float,
    latency_ms: int | None,
    end_after: timedelta = timedelta(0),
    tags: list[str] | None = None,
    end_user: str | None = None,
    call_type: str = "completion",
    deployment: str | None = None,
    http_status: int | None = None,
    error_type: str | None = None,
) -> str:
    row_id = str(uuid4())
    await db.execute_raw(
        """
        INSERT INTO deltallm_spendlog_events (
            id, request_id, call_type, api_key, organization_id, model, provider,
            deployment_model, spend, total_tokens, input_tokens, output_tokens,
            start_time, end_time, latency_ms, request_tags, end_user_id,
            status, http_status_code, error_type
        )
        VALUES (
            $1, $1, $2, 'key-hash', $3, 'gpt-4o', 'openai', $4, $5, 10, 10, 0,
            $6::timestamp, $7::timestamp, $8, $9::text[], $10,
            $11, $12, $13
        )
        """,
        row_id,
        call_type,
        organization_id,
        deployment,
        spend,
        at.replace(tzinfo=None).isoformat(),
        (at + end_after).replace(tzinfo=None).isoformat(),
        latency_ms,
        tags or [],
        end_user,
        "error" if error_type else "success",
        http_status,
        error_type,
    )
    return row_id


async def test_request_logs_detail_filters_and_value_sorts(explorer_db):
    db, organization_id = explorer_db
    cheap_slow = await _insert_detailed(
        db,
        organization_id,
        at=DAY,
        spend=0.5,
        latency_ms=None,
        end_after=timedelta(seconds=9),
        tags=["batch", "eval"],
        end_user="customer-1",
        call_type="embedding",
        deployment="azure-east",
    )
    pricey = await _insert_detailed(
        db,
        organization_id,
        at=DAY + timedelta(minutes=1),
        spend=4.0,
        latency_ms=1200,
        tags=["eval"],
        http_status=429,
        error_type="RateLimitError",
    )
    middle = await _insert_detailed(
        db, organization_id, at=DAY + timedelta(minutes=2), spend=2.0, latency_ms=3000
    )

    async def ids(**overrides: Any) -> list[str]:
        return [row["id"] for row in await _rows(db, _queries(organization_id, **overrides).page)]

    assert await ids(filters=RequestLogFilters(tag="eval")) == [pricey, cheap_slow]
    assert await ids(filters=RequestLogFilters(end_user="customer-1")) == [cheap_slow]
    assert await ids(filters=RequestLogFilters(call_type="embedding")) == [cheap_slow]
    assert await ids(filters=RequestLogFilters(deployment="azure-east")) == [cheap_slow]
    assert await ids(filters=RequestLogFilters(http_status=429)) == [pricey]
    assert await ids(filters=RequestLogFilters(error_type="RateLimitError")) == [pricey]
    assert await ids(filters=RequestLogFilters(min_spend=2.0)) == [middle, pricey]
    # A legacy row without latency_ms uses its start to end time.
    assert await ids(filters=RequestLogFilters(min_duration_ms=2500)) == [middle, cheap_slow]

    assert await ids(sort="cost") == [pricey, middle, cheap_slow]
    assert await ids(sort="duration") == [cheap_slow, middle, pricey]

    [first] = await _rows(db, _queries(organization_id, sort="cost", limit=1).page)
    assert first["sort_value"] == 4.0
    assert first["deployment_model"] is None
    assert await ids(sort="cost", sort_cursor=(first["sort_value"], first["id"])) == [
        middle,
        cheap_slow,
    ]
    [slowest] = await _rows(db, _queries(organization_id, sort="duration", limit=1).page)
    assert slowest["duration_ms"] == 9000
    assert await ids(sort="duration", sort_cursor=(9000, slowest["id"])) == [middle, pricey]
