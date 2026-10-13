"""The spend read view keeps time filters inside both union branches."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
import json
import os
from uuid import uuid4

import pytest
from prisma import Prisma

pytestmark = pytest.mark.postgres

VIEW = "deltallm_spend_read_events_v2"


@pytest.fixture
async def database() -> AsyncIterator[Prisma]:
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires DATABASE_URL")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    try:
        yield db
    finally:
        await db.disconnect()


def _nodes(plan: dict[str, object]) -> list[dict[str, object]]:
    found = [plan]
    for child in plan.get("Plans", []):
        found.extend(_nodes(child))
    return found


async def test_time_range_and_order_reach_each_union_branch(database):
    async with database.tx() as tx:
        rows = await tx.query_raw(
            f"""
            EXPLAIN (FORMAT JSON)
            SELECT id FROM {VIEW}
            WHERE organization_id = 'org' AND team_id = 'team'
              AND start_time >= '2026-10-01T00:00:00Z'::timestamptz
              AND start_time < '2026-10-02T00:00:00Z'::timestamptz
            ORDER BY start_time DESC, id DESC
            LIMIT 51
            """
        )
    raw = rows[0]["QUERY PLAN"]
    plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"]
    nodes = _nodes(plan)

    # Before the fix, the view was a Subquery Scan that filtered start_time
    # above the Append, after it read every row of the team.
    assert not any(
        node.get("Node Type") == "Subquery Scan" and node.get("Alias") == VIEW for node in nodes
    )
    native = [
        node
        for node in nodes
        if node.get("Relation Name") == "deltallm_accounting_usage_facts_v2"
        and node.get("Alias") != "v2"
    ]
    assert native and all(
        "start_time" in f"{node.get('Index Cond', '')} {node.get('Filter', '')}" for node in native
    )
    legacy = [node for node in nodes if node.get("Alias") == "legacy"]
    assert legacy and all("start_time" in str(node.get("Filter", "")) for node in legacy)


async def test_legacy_times_are_utc_whatever_the_session_time_zone(database):
    row_id = "spend-view-tz-" + uuid4().hex
    await database.execute_raw(
        """
        INSERT INTO deltallm_spendlog_events
          (id, request_id, call_type, api_key, model, spend, start_time, end_time)
        VALUES ($1, $1, 'completion', 'key', 'model', 0,
                TIMESTAMP '2026-10-12 12:00:00', TIMESTAMP '2026-10-12 12:00:01')
        """,
        row_id,
    )
    try:
        async with database.tx() as tx:
            await tx.query_raw("SELECT set_config('TimeZone', 'America/New_York', true)")
            rows = await tx.query_raw(
                f"SELECT start_time, end_time FROM {VIEW} WHERE id = $1", row_id
            )
        start = rows[0]["start_time"]
        end = rows[0]["end_time"]
        if isinstance(start, str):
            start, end = datetime.fromisoformat(start), datetime.fromisoformat(end)
        assert start == datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
        assert end == datetime(2026, 10, 12, 12, 0, 1, tzinfo=UTC)
    finally:
        await database.execute_raw("DELETE FROM deltallm_spendlog_events WHERE id = $1", row_id)
