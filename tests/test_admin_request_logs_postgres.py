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
    finally:
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", team_id)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", organization_id
        )
