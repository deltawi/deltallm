"""The usage "user" dimension attributes spend to the person who owns the key."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from src.billing.spend.spend_read import get_spend_read_source
from src.db.billing.admin_request_logs import RequestLogFilters, request_log_queries
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
WINDOW = ReportWindow(start=DAY, end=DAY + timedelta(days=1) - timedelta(microseconds=1))


@pytest.fixture
async def people_db():
    if Prisma is None or not DATABASE_URL:  # pragma: no cover
        if os.getenv("CI"):
            pytest.fail("CI must provide DATABASE_URL and the generated prisma client")
        pytest.skip("DATABASE_URL and prisma client are required")
    db = Prisma(datasource={"url": DATABASE_URL})
    await db.connect()
    suffix = uuid4().hex
    ids = {
        "organization": f"org-{suffix}",
        "team": f"team-{suffix}",
        "account": f"acct-{suffix}",
        "self_account": f"self-{suffix}",
        "runtime_user": f"runtime-{suffix}",
        "service_account": f"sa-{suffix}",
        "admin_key": f"admin-key-{suffix}",
        "self_key": f"self-key-{suffix}",
        "runtime_key": f"runtime-key-{suffix}",
        "service_key": f"service-key-{suffix}",
        "orphan_key": f"orphan-key-{suffix}",
    }
    try:
        yield db, ids
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_spendlog_events WHERE organization_id = $1", ids["organization"]
        )
        await db.execute_raw(
            "DELETE FROM deltallm_verificationtoken WHERE token = ANY($1::text[])",
            [ids[name] for name in ("admin_key", "self_key", "runtime_key", "service_key")],
        )
        await db.execute_raw(
            "DELETE FROM deltallm_serviceaccount WHERE service_account_id = $1",
            ids["service_account"],
        )
        await db.execute_raw(
            "DELETE FROM deltallm_usertable WHERE user_id = ANY($1::text[])",
            [ids["runtime_user"], ids["self_account"]],
        )
        await db.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE account_id = ANY($1::text[])",
            [ids["account"], ids["self_account"]],
        )
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", ids["team"])
        await db.disconnect()


async def _seed(db: Any, ids: dict[str, str]) -> None:
    for account, email in (
        (ids["account"], "dana@example.com"),
        (ids["self_account"], "sam@example.com"),
    ):
        await db.execute_raw(
            "INSERT INTO deltallm_platformaccount (account_id, email, updated_at) VALUES ($1, $2, NOW())",
            account,
            email,
        )
    # A self-service runtime user shares the account ID; a legacy runtime user has its own.
    for user_id, email in (
        (ids["self_account"], "sam-runtime@example.com"),
        (ids["runtime_user"], "legacy@example.com"),
    ):
        await db.execute_raw(
            "INSERT INTO deltallm_usertable (user_id, user_email, updated_at) VALUES ($1, $2, NOW())",
            user_id,
            email,
        )
    await db.execute_raw(
        "INSERT INTO deltallm_teamtable (team_id, updated_at) VALUES ($1, NOW())",
        ids["team"],
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_serviceaccount (service_account_id, team_id, name, updated_at)
        VALUES ($1, $2, 'billing-bot', NOW())
        """,
        ids["service_account"],
        ids["team"],
    )
    for token, owner, service_account in (
        (ids["admin_key"], ids["account"], None),
        (ids["self_key"], ids["self_account"], None),
        (ids["runtime_key"], None, None),
        (ids["service_key"], None, ids["service_account"]),
    ):
        await db.execute_raw(
            """
            INSERT INTO deltallm_verificationtoken (
                id, token, owner_account_id, owner_service_account_id, updated_at
            )
            VALUES ($1, $2, $3, $4, NOW())
            """,
            str(uuid4()),
            token,
            owner,
            service_account,
        )

    rows = (
        # Admin-created key: owner account only, no runtime user.
        (ids["admin_key"], None, ids["account"], 4.0),
        # Self-service key: its runtime user ID is the account ID, so both rows group as one.
        (ids["self_key"], ids["self_account"], ids["self_account"], 2.0),
        (ids["self_key"], ids["self_account"], None, 1.0),
        (ids["runtime_key"], ids["runtime_user"], None, 1.5),
        (ids["service_key"], None, None, 0.75),
        (ids["orphan_key"], None, None, 0.25),
    )
    for api_key, user_id, owner, spend in rows:
        await db.execute_raw(
            """
            INSERT INTO deltallm_spendlog_events (
                id, request_id, call_type, api_key, user_id, owner_account_id, organization_id,
                model, provider, spend, total_tokens, input_tokens, output_tokens,
                start_time, end_time, status
            )
            VALUES (
                $1, $1, 'completion', $2, $3, $4, $5, 'gpt-4o', 'openai', $6, 10, 10, 0,
                $7::timestamp, $7::timestamp, 'success'
            )
            """,
            str(uuid4()),
            api_key,
            user_id,
            owner,
            ids["organization"],
            spend,
            (DAY + timedelta(hours=1)).replace(tzinfo=None).isoformat(),
        )


def _common(ids: dict[str, str], **filters: Any) -> dict[str, Any]:
    return {
        "window": WINDOW,
        "visibility": SpendVisibility(
            is_platform_admin=False,
            organization_ids=(ids["organization"],),
            active_view="organization",
        ),
        "filters": UsageFilters.build(**filters) if filters else UsageFilters(),
        "source": get_spend_read_source(),
    }


async def _rows(db: Any, query: ReportQuery) -> list[dict[str, Any]]:
    return [dict(row) for row in await db.query_raw(query.sql, *query.params)]


async def _breakdown(db: Any, ids: dict[str, str], **kwargs: Any) -> dict[Any, dict[str, Any]]:
    rows = await _rows(
        db,
        usage_breakdown_query(
            group_by="user",
            sort_by="spend",
            search=kwargs.pop("search", None),
            limit=20,
            offset=0,
            user_identity_labels_visible=kwargs.pop("labels", True),
            **_common(ids, **kwargs),
        ),
    )
    return {row["group_key"]: row for row in rows}


async def test_breakdown_groups_spend_by_key_owner(people_db):
    db, ids = people_db
    await _seed(db, ids)

    rows = await _breakdown(db, ids)

    assert {key: float(row["total_spend"]) for key, row in rows.items()} == {
        ids["account"]: 4.0,
        ids["self_account"]: 3.0,
        ids["runtime_user"]: 1.5,
        ids["service_account"]: 0.75,
        None: 0.25,
    }
    assert rows[ids["account"]]["display_name"] == "dana@example.com"
    assert rows[ids["self_account"]]["display_name"] == "sam@example.com"
    assert rows[ids["runtime_user"]]["display_name"] == "legacy@example.com"
    assert rows[ids["service_account"]]["display_name"] == "billing-bot (service account)"
    assert rows[None]["is_unassigned"] is True


async def test_hidden_labels_keep_person_grouping(people_db):
    db, ids = people_db
    await _seed(db, ids)

    rows = await _breakdown(db, ids, labels=False)

    assert float(rows[ids["account"]]["total_spend"]) == 4.0
    assert all(row["display_name"] is None for row in rows.values())


async def test_search_matches_person_labels(people_db):
    db, ids = people_db
    await _seed(db, ids)

    assert set(await _breakdown(db, ids, search="dana@")) == {ids["account"]}
    assert set(await _breakdown(db, ids, search="billing-bot")) == {ids["service_account"]}
    assert set(await _breakdown(db, ids, search="unassigned")) == {None}


@pytest.mark.parametrize(
    ("person", "expected_spend"),
    [("account", 4.0), ("self_account", 3.0), ("runtime_user", 1.5), ("service_account", 0.75)],
)
async def test_person_filter_matches_its_breakdown_row(people_db, person, expected_spend):
    db, ids = people_db
    await _seed(db, ids)
    common = _common(ids, values={"user": ids[person]})

    summary = await _rows(db, usage_summary_query(**common))
    series = await _rows(
        db,
        usage_series_query(
            interval="day",
            split_by="user",
            max_groups=8,
            user_identity_labels_visible=True,
            **common,
        ),
    )
    logs = request_log_queries(
        filters=RequestLogFilters(usage=common["filters"]),
        visibility=common["visibility"],
        source=common["source"],
        limit=20,
        offset=0,
        cursor=None,
        user_identity_labels_visible=True,
    )
    log_rows = await _rows(db, logs.page)

    assert float(summary[0]["total_spend"]) == pytest.approx(expected_spend)
    assert [row["group_key"] for row in series] == [ids[person]]
    assert {row["user"] for row in log_rows} == {ids[person]}
    assert sum(float(row["spend"]) for row in log_rows) == pytest.approx(expected_spend)


async def test_unassigned_filter_keeps_only_rows_without_a_person(people_db):
    db, ids = people_db
    await _seed(db, ids)

    summary = await _rows(db, usage_summary_query(**_common(ids, values={}, unassigned=["user"])))

    assert float(summary[0]["total_spend"]) == pytest.approx(0.25)


async def test_request_logs_show_the_person_and_label(people_db):
    db, ids = people_db
    await _seed(db, ids)
    common = _common(ids)
    logs = request_log_queries(
        filters=RequestLogFilters(),
        visibility=common["visibility"],
        source=common["source"],
        limit=20,
        offset=0,
        cursor=None,
        user_identity_labels_visible=True,
    )

    rows = await _rows(db, logs.page)
    labels = {row["api_key"]: (row["user"], row["user_email"]) for row in rows}

    assert labels[ids["admin_key"]] == (ids["account"], "dana@example.com")
    assert labels[ids["runtime_key"]] == (ids["runtime_user"], "legacy@example.com")
    assert labels[ids["service_key"]] == (ids["service_account"], "billing-bot (service account)")
    assert labels[ids["orphan_key"]] == (None, None)
    assert {row["user"] for row in rows if row["api_key"] == ids["self_key"]} == {
        ids["self_account"]
    }
