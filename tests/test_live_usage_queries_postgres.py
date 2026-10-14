"""Live Dashboard SQL returns correct aggregates against the real spend read view."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from src.billing.spend.spend_read import get_spend_read_source
from src.db.billing.live_usage_queries import (
    budget_alerts_query,
    live_daily_query,
    live_hourly_query,
    live_providers_query,
    live_top_query,
)
from src.services.reporting.spend_visibility import SpendVisibility
from tests.test_usage_explorer_queries_postgres import DAY, _insert, _rows, _visibility
from tests.test_usage_explorer_queries_postgres import explorer_db as _explorer_db

pytestmark = pytest.mark.postgres
explorer_db = _explorer_db
END = DAY + timedelta(hours=3)


async def _seed(db, organization_id: str, team_id: str | None = None) -> None:
    await _insert(db, organization_id, model="gpt-4o", at=DAY, spend=1.0, team_id=team_id)
    await _insert(
        db, organization_id, model="glm", at=DAY + timedelta(hours=2), spend=0.0, status="error"
    )
    await _insert(
        db,
        organization_id,
        model="glm",
        at=DAY + timedelta(hours=2, minutes=5),
        spend=0.0,
        status="error",
    )
    await _insert(
        db, organization_id, model="gpt-4o", at=DAY + timedelta(hours=2, minutes=10), spend=2.0
    )
    await _insert(db, organization_id, model="gpt-4o", at=DAY - timedelta(days=3), spend=5.0)


async def test_hourly_and_daily_totals(explorer_db):
    db, organization_id = explorer_db
    await _seed(db, organization_id)
    hourly = await _rows(
        db,
        live_hourly_query(
            start=DAY,
            end=END,
            active_keys_since=DAY,
            visibility=_visibility(organization_id),
            source=get_spend_read_source(),
        ),
    )
    assert [int(row["request_count"]) for row in hourly] == [1, 3]
    assert [int(row["failed_requests"]) for row in hourly] == [0, 2]
    assert int(hourly[0]["active_keys"]) == 1

    daily = await _rows(
        db,
        live_daily_query(
            start=date(2026, 9, 1),
            end=date(2026, 9, 10),
            visibility=_visibility(organization_id),
            source=get_spend_read_source(),
        ),
    )
    assert [(str(row["bucket"])[:10], float(row["total_spend"])) for row in daily] == [
        ("2026-09-07", 5.0)
    ]


async def test_providers_name_the_failing_model(explorer_db):
    db, organization_id = explorer_db
    await _seed(db, organization_id)
    [provider] = await _rows(
        db,
        live_providers_query(
            start=DAY,
            end=END,
            visibility=_visibility(organization_id),
            source=get_spend_read_source(),
        ),
    )
    assert provider["provider"] == "openai"
    assert int(provider["request_count"]) == 4
    assert int(provider["failed_requests"]) == 2
    assert provider["top_failing_model"] == "glm"
    assert int(provider["top_failing_model_failures"]) == 2


async def test_top_returns_busiest_group_per_dimension(explorer_db):
    db, organization_id = explorer_db
    team_id = f"team-{uuid4()}"
    await _seed(db, organization_id, team_id=team_id)
    rows = await _rows(
        db,
        live_top_query(
            start=DAY,
            end=END,
            dimensions=("api_key", "model", "team"),
            visibility=_visibility(organization_id),
            source=get_spend_read_source(),
        ),
    )
    by_dimension = {row["dimension"]: row for row in rows}
    assert set(by_dimension) == {"api_key", "model", "team"}
    assert by_dimension["api_key"]["group_key"] == "key-hash"
    assert int(by_dimension["api_key"]["request_count"]) == 4
    assert by_dimension["model"]["group_key"] in {"gpt-4o", "glm"}
    assert int(by_dimension["model"]["request_count"]) == 2
    assert by_dimension["team"]["group_key"] == team_id
    assert int(rows[0]["total_requests"]) == 4


async def test_budget_alerts_respect_the_reporting_view(explorer_db):
    db, organization_id = explorer_db
    other_org = f"org-{uuid4()}"
    teams = [f"team-{uuid4()}" for _ in range(3)]
    await db.execute_raw(
        "INSERT INTO deltallm_organizationtable (id, organization_id, max_budget, spend) "
        "VALUES ($1, $1, 100, 95), ($2, $2, 100, 99)",
        organization_id,
        other_org,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_teamtable (team_id, organization_id, team_alias, max_budget, spend, models)
        VALUES ($1, $4, 'Near', 500, 425, ARRAY[]::text[]),
               ($2, $4, 'Fine', 500, 10, ARRAY[]::text[]),
               ($3, $5, 'Other', 500, 499, ARRAY[]::text[])
        """,
        *teams,
        organization_id,
        other_org,
    )
    try:
        org_rows = await _rows(db, budget_alerts_query(visibility=_visibility(organization_id)))
        assert [(row["kind"], row["group_key"]) for row in org_rows] == [
            ("organization", organization_id),
            ("team", teams[0]),
        ]
        team_rows = await _rows(
            db,
            budget_alerts_query(
                visibility=SpendVisibility(
                    is_platform_admin=False, team_ids=(teams[0],), active_view="team"
                )
            ),
        )
        assert [row["display_name"] for row in team_rows] == ["Near"]
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_teamtable WHERE team_id = ANY($1::text[])", teams
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id IN ($1, $2)",
            organization_id,
            other_org,
        )
