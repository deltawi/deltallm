from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from time import monotonic
from uuid import uuid4

import pytest
from prisma import Prisma

from src.billing.spend.spend_read import get_spend_read_source
from src.db.billing.workspace_request_logs import (
    WorkspaceLogFilters,
    WorkspaceLogScope,
    get_workspace_request_log,
    list_workspace_request_logs,
)
from src.services.reporting.workspace_request_logs import to_workspace_request_log

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
KEY_A = "a" * 64
KEY_B = "b" * 64
PROJECTION = {
    "id",
    "request_id",
    "start_time",
    "end_time",
    "latency_ms",
    "call_type",
    "model",
    "api_key",
    "status",
    "http_status_code",
    "error_type",
    "prompt_tokens",
    "completion_tokens",
    "prompt_tokens_cached",
    "completion_tokens_cached",
    "spend",
    "cache_hit",
}


@dataclass(frozen=True)
class Tenants:
    prefix: str
    workspace: WorkspaceLogScope
    sibling: WorkspaceLogScope
    foreign: WorkspaceLogScope


@pytest.fixture
async def log_database() -> AsyncIterator[tuple[Prisma, Tenants]]:
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires DATABASE_URL")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    prefix = "wslog-" + uuid4().hex
    tenants = Tenants(
        prefix=prefix,
        workspace=WorkspaceLogScope(f"{prefix}-org-a", f"{prefix}-team-a1"),
        sibling=WorkspaceLogScope(f"{prefix}-org-a", f"{prefix}-team-a2"),
        foreign=WorkspaceLogScope(f"{prefix}-org-b", f"{prefix}-team-b1"),
    )
    rows = [
        # id suffix, scope, minutes after T0, model, api key, status
        ("w1", tenants.workspace, 0, "gpt-4o", KEY_A, "success"),
        ("w2", tenants.workspace, 10, "gpt-4o-mini", KEY_B, "error"),
        ("w3", tenants.workspace, 20, "gpt-4o", "jwt:person@example.com", "success"),
        ("w4", tenants.workspace, 30, "gpt-4o", KEY_A, "success"),
        ("w5", tenants.workspace, 40, "gpt-4o", "master_key", "success"),
        ("s1", tenants.sibling, 5, "gpt-4o", KEY_A, "success"),
        ("f1", tenants.foreign, 15, "gpt-4o", KEY_A, "success"),
        # A foreign organization row that reuses the workspace team ID.
        (
            "f2",
            WorkspaceLogScope(tenants.foreign.organization_id, tenants.workspace.team_id),
            25,
            "gpt-4o",
            KEY_A,
            "success",
        ),
    ]
    for suffix, scope, minutes, model, api_key, status in rows:
        start = T0 + timedelta(minutes=minutes)
        await db.execute_raw(
            """
            INSERT INTO deltallm_spendlog_events
              (id, request_id, call_type, api_key, user_id, team_id, organization_id,
               end_user_id, model, api_base, spend, spend_exact, input_tokens, output_tokens,
               total_tokens, start_time, end_time, latency_ms, cache_key, request_tags,
               metadata, status, http_status_code, error_type, owner_account_id)
            VALUES ($1, $2, 'completion', $3, 'user-1', $4, $5, 'end-user-1', $6,
                    'https://internal.example', 0.25, 0.25, 7, 3, 10, $7::timestamp, $8::timestamp, 120,
                    'cache-key', ARRAY['tag'], '{"secret":"x"}'::jsonb, $9,
                    CASE WHEN $9 = 'error' THEN 429 ELSE 200 END,
                    CASE WHEN $9 = 'error' THEN 'rate_limit_error' END, 'owner-1')
            """,
            f"{prefix}-{suffix}",
            f"req-{suffix}",
            api_key,
            scope.team_id,
            scope.organization_id,
            model,
            start.replace(tzinfo=None),
            (start + timedelta(seconds=1)).replace(tzinfo=None),
            status,
        )
    try:
        yield db, tenants
    finally:
        await db.execute_raw("DELETE FROM deltallm_spendlog_events WHERE id LIKE $1", prefix + "-%")
        await db.disconnect()


def _filters(**overrides: object) -> WorkspaceLogFilters:
    values: dict[str, object] = {
        "start": T0 - timedelta(hours=1),
        "end": T0 + timedelta(hours=1),
        "limit": 50,
    }
    values.update(overrides)
    return WorkspaceLogFilters(**values)  # type: ignore[arg-type]


async def _list(
    db: Prisma, scope: WorkspaceLogScope, filters: WorkspaceLogFilters
) -> list[dict[str, object]]:
    async with db.tx() as tx:
        return await list_workspace_request_logs(
            tx,
            deadline=monotonic() + 30,
            cancellation_grace=0.01,
            source=get_spend_read_source(),
            scope=scope,
            filters=filters,
        )


def _suffixes(tenants: Tenants, rows: list[dict[str, object]]) -> list[str]:
    return [str(row["id"]).removeprefix(tenants.prefix + "-") for row in rows]


async def test_workspace_reads_only_its_own_team_with_the_fixed_projection(log_database):
    db, tenants = log_database
    rows = await _list(db, tenants.workspace, _filters())

    assert _suffixes(tenants, rows) == ["w5", "w4", "w3", "w2", "w1"]
    assert all(set(row) == PROJECTION for row in rows)
    logs = {
        log.id.removeprefix(tenants.prefix + "-"): log
        for log in map(to_workspace_request_log, rows)
    }
    assert logs["w3"].api_key is None and logs["w5"].api_key is None
    assert logs["w1"].api_key == KEY_A
    assert logs["w2"].http_status_code == 429 and logs["w2"].error_type == "rate_limit_error"
    assert logs["w1"].prompt_tokens == 7 and logs["w1"].spend == 0.25


async def test_workspaces_never_see_each_other_in_one_or_two_organizations(log_database):
    db, tenants = log_database
    assert _suffixes(tenants, await _list(db, tenants.sibling, _filters())) == ["s1"]
    assert _suffixes(tenants, await _list(db, tenants.foreign, _filters())) == ["f1"]


async def test_filters_and_cursor_pages_return_each_row_once(log_database):
    db, tenants = log_database
    scope = tenants.workspace
    assert _suffixes(tenants, await _list(db, scope, _filters(model="gpt-4o-mini"))) == ["w2"]
    assert _suffixes(tenants, await _list(db, scope, _filters(api_key=KEY_A))) == ["w4", "w1"]
    assert _suffixes(tenants, await _list(db, scope, _filters(status="error"))) == ["w2"]
    window = _filters(start=T0 + timedelta(minutes=10), end=T0 + timedelta(minutes=30))
    assert _suffixes(tenants, await _list(db, scope, window)) == ["w3", "w2"]

    seen: list[str] = []
    after = None
    while True:
        rows = await _list(db, scope, _filters(limit=2, after=after))
        page = rows[:2]
        seen.extend(_suffixes(tenants, page))
        if len(rows) <= 2:
            break
        last = page[-1]
        after = (last["start_time"], str(last["id"]))
    assert seen == ["w5", "w4", "w3", "w2", "w1"]


async def test_detail_read_hides_rows_outside_the_workspace(log_database):
    db, tenants = log_database

    async def detail(scope: WorkspaceLogScope, suffix: str) -> dict[str, object] | None:
        async with db.tx() as tx:
            return await get_workspace_request_log(
                tx,
                deadline=monotonic() + 30,
                cancellation_grace=0.01,
                source=get_spend_read_source(),
                scope=scope,
                log_id=f"{tenants.prefix}-{suffix}",
            )

    own = await detail(tenants.workspace, "w1")
    assert own is not None and set(own) == PROJECTION
    assert await detail(tenants.workspace, "s1") is None
    assert await detail(tenants.workspace, "f2") is None
    assert await detail(tenants.sibling, "w1") is None
