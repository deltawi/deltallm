from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest


class _KeyTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def query_raw(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.queries.append((sql, params))
        return [row for row in self.rows if row["token"] == params[0]]


def _row(token: str, **values: Any) -> dict[str, Any]:
    return {
        "token": token,
        "key_name": "billing-service",
        "team_id": "team-1",
        "spend": 2.5,
        "max_budget": 10.0,
        "budget_duration": "1mo",
        "budget_reset_at": datetime(2026, 11, 1, tzinfo=UTC),
        "rpm_limit": 60,
        "tpm_limit": 100_000,
        "output_tpm_limit": None,
        "rph_limit": None,
        "rpd_limit": None,
        "tpd_limit": None,
        "expires": None,
        "created_at": datetime(2026, 10, 1, tzinfo=UTC),
        **values,
    }


def _install(test_app, rows: list[dict[str, Any]]) -> _KeyTable:
    table = _KeyTable(rows)
    test_app.state.prisma_manager = SimpleNamespace(client=table)
    return table


async def _token(client, test_app) -> str:
    del client
    # The stored token is the salted hash the auth layer resolves.
    return test_app.state.key_service.hash_key(test_app.state._test_key)


@pytest.mark.asyncio
async def test_key_reads_its_own_budget_and_limits(client, test_app):
    token = await _token(client, test_app)
    table = _install(test_app, [_row(token), _row("someone-else", key_name="other")])

    response = await client.get(
        "/v1/key", headers={"Authorization": f"Bearer {test_app.state._test_key}"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "key"
    assert body["name"] == "billing-service"
    assert body["budget"] == {
        "max_budget": 10.0,
        "spend": 2.5,
        "remaining": 7.5,
        "duration": "1mo",
        "reset_at": "2026-11-01T00:00:00+00:00",
    }
    assert body["limits"]["rpm"] == 60 and body["limits"]["tpm"] == 100_000
    assert table.queries[-1][1] == (token,)
    assert test_app.state._test_key not in response.text


@pytest.mark.asyncio
async def test_key_without_budget_reports_no_remaining_amount(client, test_app):
    token = await _token(client, test_app)
    _install(test_app, [_row(token, max_budget=None)])

    response = await client.get(
        "/v1/key", headers={"Authorization": f"Bearer {test_app.state._test_key}"}
    )

    assert response.status_code == 200
    assert response.json()["budget"]["remaining"] is None


@pytest.mark.asyncio
async def test_key_info_needs_a_valid_key(client, test_app):
    _install(test_app, [])

    response = await client.get("/v1/key", headers={"Authorization": "Bearer sk-wrong"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_master_key_is_not_an_api_key(client, test_app):
    _install(test_app, [])
    test_app.state.settings.master_key = "sk-master-key-for-tests"

    response = await client.get(
        "/v1/key", headers={"Authorization": "Bearer sk-master-key-for-tests"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
