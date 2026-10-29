"""Usage report and request log exports: scope, columns, row caps and audit."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from src.api.admin.endpoints.common import AuthScope
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from tests.test_ui_rbac_scoping import FakeSpendDB

HEADERS = {"Authorization": "Bearer mk-test"}
PLATFORM = AuthScope(is_platform_admin=True)
TEAM = AuthScope(
    account_id="acct-team",
    team_permissions_by_id={"team-1": {Permission.SPEND_READ_TEAM}},
    effective_permissions={Permission.SPEND_READ_TEAM},
)


class ExportDB(FakeSpendDB):
    def __init__(self, report_rows: list[dict[str, Any]], log_rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self.report_rows = report_rows
        self.log_rows = log_rows

    async def query_raw(self, query: str, *params):
        rows = await super().query_raw(query, *params)
        if "AS bucket" in query:
            return self.report_rows
        if "SELECT page.*" in query:
            return self.log_rows
        return rows


def _install(test_app, monkeypatch, scope: AuthScope, db: FakeSpendDB) -> list[dict[str, Any]]:
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    for path in (
        "src.api.admin.usage_report_context.get_auth_scope",
        "src.api.admin.endpoints.usage_export.get_auth_scope",
    ):
        monkeypatch.setattr(
            path,
            lambda request, authorization=None, x_master_key=None, required_permission=None: scope,
        )
    audits: list[dict[str, Any]] = []

    async def record(**kwargs: Any) -> None:
        audits.append(kwargs)

    monkeypatch.setattr("src.api.admin.endpoints.usage_export.emit_control_audit_event", record)
    return audits


def _report_row(key: str | None, spend: float, **extra: Any) -> dict[str, Any]:
    return {
        "bucket": None,
        "group_key": key,
        "is_unassigned": key is None,
        "display_name": None,
        "total_spend": spend,
        "total_tokens": 30,
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "request_count": 3,
        "failed_requests": 1,
        **extra,
    }


def _log_row(index: int) -> dict[str, Any]:
    return {
        "id": f"row-{index}",
        "request_id": f"req-{index}",
        "start_time": datetime(2026, 10, 20, 12, index, tzinfo=UTC),
        "status": "success",
        "model": "gpt-4o",
        "api_key": "hash-1",
        "key_name": '=HYPERLINK("http://x")',
        "spend": 0.5,
        "request_tags": ["a", "b"],
        "metadata": {"prompt": "secret"},
        "cache_key": "cache-secret",
    }


def _csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


@pytest.mark.asyncio
async def test_report_export_writes_csv_with_every_group_and_an_audit_event(
    client, test_app, monkeypatch
):
    db = ExportDB(
        [_report_row("gpt-4o", 4.0), _report_row("-1+cmd", 2.0), _report_row(None, 1.0)], []
    )
    audits = _install(test_app, monkeypatch, PLATFORM, db)

    response = await client.get(
        "/ui/api/spend/explorer/export?group_by=model&start_date=2026-10-01&end_date=2026-10-07"
        "&provider=OpenAI",
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="usage-model-2026-10-01-to-2026-10-07.csv"'
    )
    assert response.headers["x-deltallm-export-rows"] == "3"
    assert response.headers["x-deltallm-export-truncated"] == "false"
    assert response.headers["cache-control"] == "no-store"
    rows = _csv(response.text)
    assert [row["group_key"] for row in rows] == ["gpt-4o", "'-1+cmd", ""]
    assert rows[2]["name"] == "Unassigned"
    assert rows[0]["spend"] == "4.0"
    assert "date" not in rows[0]
    query, params = db.calls[-1]
    assert "LIMIT $" in query
    assert params[-1] == 10_001
    assert "OpenAI" in params
    [audit] = audits
    assert audit["action"] == AuditAction.SPEND_REPORT_EXPORT
    assert audit["critical"] is True
    assert audit["metadata"]["rows"] == 3
    assert audit["metadata"]["filters"] == ["provider"]


@pytest.mark.asyncio
async def test_report_export_json_has_daily_rows_and_marks_truncation(
    client, test_app, monkeypatch
):
    db = ExportDB(
        [
            _report_row("team-1", 2.0, bucket="2026-10-01"),
            _report_row("team-2", 1.0, bucket="2026-10-01"),
        ],
        [],
    )
    audits = _install(test_app, monkeypatch, PLATFORM, db)

    response = await client.get(
        "/ui/api/spend/explorer/export?group_by=team&format=json&interval=day&limit=1"
        "&start_date=2026-10-01&end_date=2026-10-01",
        headers=HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["truncated"] is True
    assert payload["interval"] == "day"
    assert payload["rows"] == [
        {
            "date": "2026-10-01",
            "dimension": "team",
            "group_key": "team-1",
            "name": None,
            "spend": 2.0,
            "requests": 3,
            "failed_requests": 1,
            "total_tokens": 30,
            "prompt_tokens": 20,
            "completion_tokens": 10,
        }
    ]
    assert response.headers["x-deltallm-export-truncated"] == "true"
    assert "DATE_TRUNC" not in db.calls[-1][0]
    assert audits[0]["metadata"]["truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/ui/api/spend/explorer/export?group_by=organization", 403),
        ("/ui/api/spend/explorer/export?group_by=model&organization=org-2", 403),
        ("/ui/api/spend/explorer/export?group_by=model&format=xlsx", 422),
        ("/ui/api/spend/explorer/export?group_by=model&interval=hour", 422),
        ("/ui/api/spend/explorer/export?group_by=model&limit=50001", 422),
        ("/ui/api/logs/export", 403),
    ],
)
async def test_exports_outside_the_team_view_are_rejected_before_any_query(
    client, test_app, monkeypatch, path, status
):
    db = ExportDB([], [])
    audits = _install(test_app, monkeypatch, TEAM, db)

    response = await client.get(path, headers=HEADERS)

    assert response.status_code == status
    assert db.calls == []
    assert audits == []


@pytest.mark.asyncio
async def test_team_view_exports_only_its_teams(client, test_app, monkeypatch):
    db = ExportDB([_report_row("gpt-4o", 1.0)], [])
    _install(test_app, monkeypatch, TEAM, db)

    response = await client.get(
        "/ui/api/spend/explorer/export?group_by=model&view=team", headers=HEADERS
    )

    assert response.status_code == 200
    query, params = db.calls[-1]
    assert "team_id IN" in query
    assert "team-1" in params


@pytest.mark.asyncio
async def test_request_log_export_keeps_allowlisted_columns_and_returns_a_next_cursor(
    client, test_app, monkeypatch
):
    db = ExportDB([], [_log_row(1), _log_row(2), _log_row(3)])
    audits = _install(test_app, monkeypatch, PLATFORM, db)

    response = await client.get(
        "/ui/api/logs/export?limit=2&status=success&tag=batch&sort=cost&since=2026-10-20T00:00:00Z"
        "&until=2026-10-21T00:00:00Z",
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="requests-2026-10-20-to-2026-10-20.csv"'
    )
    rows = _csv(response.text)
    assert [row["id"] for row in rows] == ["row-1", "row-2"]
    assert "metadata" not in rows[0]
    assert "cache_key" not in rows[0]
    assert "secret" not in response.text
    assert rows[0]["key_name"].startswith("'=")
    assert rows[0]["request_tags"] == '["a","b"]'
    assert response.headers["x-deltallm-export-truncated"] == "true"
    assert response.headers["x-deltallm-export-next-cursor"]
    query, params = db.calls[-1]
    assert "s.status = $" in query
    assert "NULL::jsonb AS metadata" in query
    assert "s.request_tags @> ARRAY[" in query
    assert "ORDER BY start_time DESC, id DESC" in query
    assert params[-1] == 3
    [audit] = audits
    assert audit["action"] == AuditAction.SPEND_LOGS_EXPORT
    assert audit["metadata"]["status"] == "success"
    assert audit["metadata"]["filters"] == ["tag"]
    assert audit["metadata"]["rows"] == 2

    cursor = response.headers["x-deltallm-export-next-cursor"]
    follow = await client.get(f"/ui/api/logs/export?format=jsonl&cursor={cursor}", headers=HEADERS)
    assert follow.status_code == 200
    assert follow.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in follow.text.splitlines()]
    assert len(lines) == 3
    assert "metadata" not in lines[0]
    assert "(start_time, id) <" in db.calls[-1][0]


@pytest.mark.asyncio
async def test_request_log_export_rejects_a_bad_cursor(client, test_app, monkeypatch):
    db = ExportDB([], [])
    audits = _install(test_app, monkeypatch, PLATFORM, db)

    response = await client.get("/ui/api/logs/export?cursor=nope", headers=HEADERS)

    assert response.status_code == 422
    assert db.calls == []
    assert audits == []
