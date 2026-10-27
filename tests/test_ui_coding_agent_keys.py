from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

from src.api.admin.key_coding_agent import (
    first_budget_reset_at,
    normalize_coding_agent,
    normalize_key_budget_duration,
)
from src.auth.roles import OrganizationRole, TeamRole
from tests.test_ui_self_service_keys import (
    _FakeKeyDB,
    _install_key_db,
    _make_context,
    _self_service_team,
    _set_auth_context,
)


class _CodingAgentKeyDB(_FakeKeyDB):
    def __init__(self, *args: Any, memberships: set[tuple[str, str]] | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.memberships = set(memberships or set())
        self.inserts: list[tuple[Any, ...]] = []
        self.updates: list[tuple[Any, ...]] = []
        self.list_queries: list[str] = []

    async def query_raw(self, query: str, *params: Any):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if normalized.startswith("select account_id from deltallm_teammembership"):
            account_id, team_id = str(params[0]), str(params[1])
            return [{"account_id": account_id}] if (account_id, team_id) in self.memberships else []
        if normalized.startswith("select token, key_name, user_id, team_id"):
            row = self.keys.get(str(params[0]))
            return [dict(row)] if row is not None else []
        if "lu.last_used_at" in normalized and "where vt.token = $1" in normalized:
            row = self.keys.get(str(params[0]))
            return [dict(row)] if row is not None else []
        if normalized.startswith("select vt.token,"):
            self.list_queries.append(normalized)
        return await super().query_raw(query, *params)

    async def execute_raw(self, query: str, *params: Any):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if normalized.startswith("insert into deltallm_verificationtoken"):
            self.inserts.append(params)
        if normalized.startswith("update deltallm_verificationtoken set key_name = $1"):
            self.updates.append(params)
            return 1
        return await super().execute_raw(query, *params)


def _admin_context(account_id: str = "acct-admin"):  # noqa: ANN202
    return _make_context(
        account_id=account_id,
        org_memberships=[{"organization_id": "org-sandbox", "role": OrganizationRole.ADMIN}],
    )


def test_first_budget_reset_aligns_to_utc_boundaries():
    now = datetime(2026, 10, 17, 15, 42, 7, tzinfo=UTC)

    assert first_budget_reset_at("1d", now=now) == datetime(2026, 10, 18)
    assert first_budget_reset_at("7d", now=now) == datetime(2026, 10, 24)
    assert first_budget_reset_at("1h", now=now) == datetime(2026, 10, 17, 16)
    assert first_budget_reset_at("1mo", now=now) == datetime(2026, 11, 1)
    assert first_budget_reset_at("3mo", now=now) == datetime(2027, 1, 1)
    assert first_budget_reset_at(None, now=now) is None


@pytest.mark.parametrize("value", ["1w", "0d", "d", 5, "-1d"])
def test_budget_duration_rejects_values_the_budget_service_cannot_reset(value: Any):
    with pytest.raises(HTTPException) as exc_info:
        normalize_key_budget_duration(value)
    assert exc_info.value.status_code == 400


def test_coding_agent_tag_is_normalized():
    assert normalize_coding_agent(
        {"agent": "claude-code", "main_model": " claude-sonnet-4-6 ", "small_model": ""}
    ) == {"agent": "claude-code", "main_model": "claude-sonnet-4-6", "small_model": None}
    assert normalize_coding_agent(None) is None


@pytest.mark.parametrize(
    "value",
    [
        {"agent": "cursor"},
        {"agent": "codex", "token": "x"},
        "codex",
        {"agent": "codex", "main_model": 3},
    ],
)
def test_coding_agent_tag_rejects_unknown_values(value: Any):
    with pytest.raises(HTTPException) as exc_info:
        normalize_coding_agent(value)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_admin_creates_daily_coding_agent_key_for_team_member(
    client, test_app, monkeypatch: pytest.MonkeyPatch
):
    db = _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
        account_ids={"acct-sara"},
        db_cls=_CodingAgentKeyDB,
    )
    db.memberships.add(("acct-sara", "team-sandbox"))
    _set_auth_context(monkeypatch, _admin_context())

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "sara · Claude Code",
            "team_id": "team-sandbox",
            "owner_account_id": "acct-sara",
            "max_budget": 20,
            "budget_duration": "1d",
            "coding_agent": {"agent": "claude-code", "main_model": "claude-sonnet-4-6"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["owner_account_id"] == "acct-sara"
    assert body["budget_duration"] == "1d"
    assert body["coding_agent"] == {
        "agent": "claude-code",
        "main_model": "claude-sonnet-4-6",
        "small_model": None,
    }
    budget_duration, reset_at, metadata = db.inserts[-1][13:]
    assert budget_duration == "1d"
    tomorrow = (datetime.now(tz=UTC) + timedelta(days=1)).date()
    assert reset_at == datetime(tomorrow.year, tomorrow.month, tomorrow.day)
    assert json.loads(metadata) == {"coding_agent": body["coding_agent"]}


@pytest.mark.asyncio
async def test_admin_cannot_issue_key_to_account_outside_team(
    client, test_app, monkeypatch: pytest.MonkeyPatch
):
    db = _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
        account_ids={"acct-lina"},
        db_cls=_CodingAgentKeyDB,
    )
    _set_auth_context(monkeypatch, _admin_context())

    response = await client.post(
        "/ui/api/keys",
        json={"key_name": "lina", "team_id": "team-sandbox", "owner_account_id": "acct-lina"},
    )

    assert response.status_code == 400
    assert "member of the team" in response.json()["detail"]
    assert db.inserts == []


@pytest.mark.asyncio
async def test_self_service_daily_key_keeps_team_budget_ceiling(
    client, test_app, monkeypatch: pytest.MonkeyPatch
):
    expires = (datetime.now(tz=UTC) + timedelta(days=7)).isoformat()
    db = _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
        users={"acct-dev": {"user_id": "acct-dev", "team_id": "team-sandbox"}},
        db_cls=_CodingAgentKeyDB,
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )
    payload = {
        "key_name": "my agent",
        "team_id": "team-sandbox",
        "budget_duration": "1d",
        "expires": expires,
        "coding_agent": {"agent": "codex"},
    }

    over = await client.post("/ui/api/keys", json={**payload, "max_budget": 6})
    created = await client.post("/ui/api/keys", json={**payload, "max_budget": 5})

    assert over.status_code == 400
    assert created.status_code == 200, created.text
    assert created.json()["self_service"] is True
    assert created.json()["owner_account_id"] == "acct-dev"
    assert db.inserts[-1][13] == "1d"


@pytest.mark.asyncio
async def test_list_filters_coding_agent_keys(client, test_app, monkeypatch: pytest.MonkeyPatch):
    db = _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
        db_cls=_CodingAgentKeyDB,
    )
    _set_auth_context(monkeypatch, _admin_context())

    agents = await client.get("/ui/api/keys", params={"coding_agent": "true"})
    others = await client.get("/ui/api/keys", params={"coding_agent": "false"})
    every = await client.get("/ui/api/keys")

    assert agents.status_code == others.status_code == every.status_code == 200
    agent_query, other_query, every_query = db.list_queries
    assert "vt.metadata->'coding_agent' is not null" in agent_query
    assert "vt.metadata->'coding_agent' is null" in other_query
    assert "vt.metadata->'coding_agent' is" not in every_query.split("from")[-1]
    assert "vt.budget_duration" in agent_query


@pytest.mark.asyncio
async def test_update_keeps_reset_time_until_period_changes(
    client, test_app, monkeypatch: pytest.MonkeyPatch
):
    reset_at = "2026-10-18T00:00:00+00:00"
    db = _install_key_db(
        test_app,
        {
            "hash-1": {
                "token": "hash-1",
                "key_name": "sara · Claude Code",
                "team_id": "team-sandbox",
                "owner_account_id": "acct-sara",
                "budget_duration": "1d",
                "budget_reset_at": reset_at,
            }
        },
        teams={"team-sandbox": _self_service_team()},
        account_ids={"acct-sara"},
        db_cls=_CodingAgentKeyDB,
    )
    _set_auth_context(monkeypatch, _admin_context())

    same = await client.put("/ui/api/keys/hash-1", json={"max_budget": 25})
    changed = await client.put(
        "/ui/api/keys/hash-1",
        json={"budget_duration": "1mo", "coding_agent": {"agent": "opencode"}},
    )
    cleared = await client.put(
        "/ui/api/keys/hash-1", json={"budget_duration": None, "coding_agent": None}
    )

    assert same.status_code == changed.status_code == cleared.status_code == 200
    same_params, changed_params, cleared_params = db.updates
    # The existing owner is kept although the admin is not that account.
    assert same_params[3] == "acct-sara"
    assert same_params[13:] == ("1d", reset_at, False, None)
    assert changed_params[13] == "1mo"
    assert changed_params[14].day == 1
    assert changed_params[15] is True
    assert json.loads(changed_params[16])["agent"] == "opencode"
    assert cleared_params[13:] == (None, None, True, None)


@pytest.mark.asyncio
async def test_list_sends_filters_and_sort_to_the_query(
    client, test_app, monkeypatch: pytest.MonkeyPatch
):
    db = _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
        db_cls=_CodingAgentKeyDB,
    )
    _set_auth_context(monkeypatch, _admin_context())

    response = await client.get(
        "/ui/api/keys",
        params={
            "organization_id": "org-1",
            "owner": "service_account",
            "status": "expiring",
            "last_used": "never",
            "sort_by": "last_used",
            "sort_direction": "asc",
        },
    )
    default = await client.get("/ui/api/keys")
    invalid = await client.get("/ui/api/keys", params={"sort_by": "spend"})

    assert response.status_code == default.status_code == 200
    query, default_query = db.list_queries
    assert "t.organization_id = $" in query
    assert "vt.owner_service_account_id is not null" in query
    assert "vt.expires > now() and vt.expires <= now() + interval '7 days'" in query
    assert "lu.last_used_at is null" in query
    assert "order by lu.last_used_at asc nulls last" in query
    assert "order by vt.created_at desc nulls last" in default_query
    assert "deltallm_accounting_budget_windows" not in query
    assert invalid.status_code == 422
