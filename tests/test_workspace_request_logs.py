from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from src.auth.external_policy import CUSTOMER_PERMISSION_CEILING
from src.auth.roles import Permission
from src.models.platform_auth import PlatformAuthContext
from src.services.reporting.request_log_cursor import encode_request_log_cursor
from tests.test_external_customer_scope import customer
from tests.test_ui_mcp import _IdentityService
from tests.test_ui_rbac_scoping import FakeSpendDB

KEY = "c" * 64
CUSTOMER_FIELDS = {
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


def _row(row_id: str, api_key: str, minute: int) -> dict[str, Any]:
    start = datetime(2026, 10, 12, 12, minute, tzinfo=UTC)
    return {
        "id": row_id,
        "request_id": f"req-{row_id}",
        "start_time": start,
        "end_time": start,
        "latency_ms": 120,
        "call_type": "completion",
        "model": "gpt-4o",
        "api_key": api_key,
        "status": "success",
        "http_status_code": 200,
        "error_type": None,
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "prompt_tokens_cached": 0,
        "completion_tokens_cached": 0,
        "spend": 0.25,
        "cache_hit": False,
    }


class WorkspaceLogsDB(FakeSpendDB):
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.rows = rows or []

    async def query_raw(self, query: str, *params):
        if "FROM deltallm_spend_read_events_v2" in query:
            self.calls.append((query, params))
            if "LIMIT 1" in query:
                return [row for row in self.rows if row["id"] == params[-1]]
            return self.rows[: params[-1]]
        return await super().query_raw(query, *params)


def _customer(**update: Any) -> PlatformAuthContext:
    return customer().model_copy(
        update={"role": "org_user", "permissions": list(CUSTOMER_PERMISSION_CEILING), **update}
    )


@pytest.fixture
def workspace_db(test_app, client) -> WorkspaceLogsDB:
    db = WorkspaceLogsDB(
        [
            _row("log-3", KEY, 30),
            _row("log-2", "jwt:person@example.com", 20),
            _row("log-1", "master_key", 10),
        ]
    )
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    test_app.state.platform_identity_service = _IdentityService(_customer())
    client.cookies.set("deltallm_session", "mcp-asset-session")
    return db


async def test_customer_reads_its_team_with_the_fixed_fields(client, workspace_db):
    response = await client.get("/ui/api/workspace/logs?limit=2&model=gpt-4o&status=success")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [row["id"] for row in payload["logs"]] == ["log-3", "log-2"]
    assert all(set(row) == CUSTOMER_FIELDS for row in payload["logs"])
    assert [row["api_key"] for row in payload["logs"]] == [KEY, None]
    assert payload["pagination"]["has_more"] is True
    assert payload["pagination"]["next_cursor"] == encode_request_log_cursor(
        datetime(2026, 10, 12, 12, 20, tzinfo=UTC), "log-2"
    )
    query, params = workspace_db.calls[0]
    assert "organization_id = $1 AND team_id = $2" in query
    assert params[:2] == ("bound-org", "bound-team")
    assert "gpt-4o" in params and "success" in params and params[-1] == 3
    for hidden in ("metadata", "api_base", "cache_key", "end_user", "request_tags", "owner"):
        assert hidden not in query


async def test_jwt_and_master_key_labels_are_hidden_in_detail(client, workspace_db):
    detail = await client.get("/ui/api/workspace/logs/log-2")
    assert detail.status_code == 200, detail.text
    assert set(detail.json()) == CUSTOMER_FIELDS and detail.json()["api_key"] is None
    query, params = workspace_db.calls[-1]
    assert "organization_id = $1 AND team_id = $2" in query
    assert params == ("bound-org", "bound-team", "log-2")

    missing = await client.get("/ui/api/workspace/logs/other-workspace-log")
    assert missing.status_code == 404


@pytest.mark.parametrize("name", ["team_id", "organization_id", "user_id", "view"])
async def test_customer_cannot_name_another_tenant(client, workspace_db, name):
    response = await client.get(f"/ui/api/workspace/logs?{name}=other")
    detail = await client.get(f"/ui/api/workspace/logs/log-3?{name}=other")

    assert response.status_code == detail.status_code == 422
    assert workspace_db.calls == []


@pytest.mark.parametrize(
    "query",
    [
        "api_key=jwt:person@example.com",
        "api_key=" + "z" * 64,
        "cursor=not-a-cursor",
        "start=2026-10-12T12:00:00Z&end=2026-10-12T11:00:00Z",
        "limit=101",
        "status=pending",
    ],
)
async def test_invalid_filters_are_rejected_before_reading(client, workspace_db, query):
    response = await client.get(f"/ui/api/workspace/logs?{query}")

    assert response.status_code == 422
    assert workspace_db.calls == []


async def test_time_window_defaults_to_the_last_day_and_accepts_timestamps(client, workspace_db):
    default = await client.get("/ui/api/workspace/logs")
    assert default.status_code == 200
    start = datetime.fromisoformat(default.json()["start"])
    end = datetime.fromisoformat(default.json()["end"])
    assert (end - start).total_seconds() == 24 * 3600

    explicit = await client.get(
        "/ui/api/workspace/logs?start=2026-10-12T12:15:00%2B02:00&end=2026-10-12T13:00:00Z"
    )
    assert explicit.status_code == 200
    _, params = workspace_db.calls[-1]
    assert params[2] == datetime(2026, 10, 12, 10, 15, tzinfo=UTC)
    assert params[3] == datetime(2026, 10, 12, 13, 0, tzinfo=UTC)


async def test_operator_sessions_and_master_key_have_no_workspace(client, test_app, workspace_db):
    operator = _customer(external_workspace=None)
    test_app.state.platform_identity_service = _IdentityService(operator)
    assert (await client.get("/ui/api/workspace/logs")).status_code == 403

    client.cookies.clear()
    setattr(test_app.state.settings, "master_key", "mk-test")
    master = await client.get("/ui/api/workspace/logs", headers={"Authorization": "Bearer mk-test"})
    assert master.status_code == 403
    assert workspace_db.calls == []


async def test_customer_without_self_spend_permission_is_denied(client, test_app, workspace_db):
    permissions = [
        item for item in CUSTOMER_PERMISSION_CEILING if item != Permission.SPEND_READ_SELF
    ]
    test_app.state.platform_identity_service = _IdentityService(_customer(permissions=permissions))

    assert (await client.get("/ui/api/workspace/logs")).status_code == 403
    assert (await client.get("/ui/api/workspace/logs/log-3")).status_code == 403
    assert workspace_db.calls == []


async def test_route_stays_closed_until_scoped_reporting_is_enabled(client, test_app, workspace_db):
    setattr(test_app.state.app_config.general_settings, "spend_reporting_v2_enabled", False)

    assert (await client.get("/ui/api/workspace/logs")).status_code == 403
    assert workspace_db.calls == []


async def test_customer_reads_share_the_reporting_capacity_gate(client, test_app, workspace_db):
    query_started = asyncio.Event()
    release_query = asyncio.Event()
    original = workspace_db.query_raw

    async def held_query(query: str, *params):
        if "FROM deltallm_spend_read_events_v2" in query and not query_started.is_set():
            query_started.set()
            await release_query.wait()
        return await original(query, *params)

    workspace_db.query_raw = held_query  # type: ignore[method-assign]
    settings = test_app.state.app_config.general_settings
    setattr(settings, "spend_reporting_max_concurrency", 1)
    setattr(settings, "spend_reporting_queue_timeout_seconds", 0.02)

    first = asyncio.create_task(client.get("/ui/api/workspace/logs"))
    await query_started.wait()
    second = await client.get("/ui/api/workspace/logs/log-3")

    assert second.status_code == 503
    assert second.headers["retry-after"] == "2"
    release_query.set()
    assert (await first).status_code == 200


def test_customer_ceiling_still_excludes_organization_spend_and_audit():
    assert Permission.SPEND_READ not in CUSTOMER_PERMISSION_CEILING
    assert Permission.AUDIT_READ not in CUSTOMER_PERMISSION_CEILING
    assert Permission.SPEND_READ_SELF in CUSTOMER_PERMISSION_CEILING
