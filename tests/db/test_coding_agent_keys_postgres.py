from __future__ import annotations

from collections.abc import AsyncIterator
import os
from uuid import uuid4

import pytest
from prisma import Prisma

pytestmark = pytest.mark.postgres

AUTH = {"X-Master-Key": "mk-test"}


@pytest.fixture
async def key_app(test_app) -> AsyncIterator[tuple[Prisma, str]]:  # noqa: ANN001
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires DATABASE_URL")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    prefix = "coding-agent-" + uuid4().hex[:12]
    await db.execute_raw(
        "INSERT INTO deltallm_organizationtable (id, organization_id, updated_at) VALUES ($1, $1, NOW())",
        f"{prefix}-org",
    )
    await db.execute_raw(
        "INSERT INTO deltallm_teamtable (team_id, organization_id) VALUES ($1, $2)",
        f"{prefix}-team",
        f"{prefix}-org",
    )
    await db.execute_raw(
        "INSERT INTO deltallm_platformaccount (account_id, email, updated_at) VALUES ($1, $2, NOW())",
        f"{prefix}-sara",
        f"{prefix}-sara@example.com",
    )
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    try:
        yield db, prefix
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_verificationtoken WHERE team_id = $1", f"{prefix}-team"
        )
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", f"{prefix}-team")
        await db.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE account_id = $1", f"{prefix}-sara"
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", f"{prefix}-org"
        )
        await db.disconnect()


async def test_coding_agent_key_round_trips_through_postgres(client, key_app):  # noqa: ANN001
    db, prefix = key_app
    team_id = f"{prefix}-team"
    created = await client.post(
        "/ui/api/keys",
        headers=AUTH,
        json={
            "key_name": "sara · Claude Code",
            "team_id": team_id,
            "owner_account_id": f"{prefix}-sara",
            "max_budget": 20,
            "budget_duration": "1d",
            "coding_agent": {"agent": "claude-code", "main_model": "claude-sonnet-4-6"},
        },
    )
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    await db.execute_raw(
        'UPDATE deltallm_verificationtoken SET metadata = metadata || \'{"guardrails": ["pii"]}\'::jsonb WHERE token = $1',
        token,
    )
    plain = await client.post(
        "/ui/api/keys",
        headers=AUTH,
        json={"key_name": "service", "team_id": team_id, "owner_account_id": f"{prefix}-sara"},
    )
    assert plain.status_code == 200, plain.text

    listed = await client.get(
        "/ui/api/keys", headers=AUTH, params={"team_id": team_id, "coding_agent": "true"}
    )
    assert listed.status_code == 200, listed.text
    (row,) = listed.json()["data"]
    assert row["token"] == token
    assert row["budget_duration"] == "1d"
    assert row["budget_reset_at"].endswith("T00:00:00+00:00")
    assert row["coding_agent"]["agent"] == "claude-code"

    edited = await client.put(f"/ui/api/keys/{token}", headers=AUTH, json={"max_budget": 25})
    assert edited.status_code == 200, edited.text
    assert edited.json()["budget_reset_at"] == row["budget_reset_at"]

    retagged = await client.put(
        f"/ui/api/keys/{token}",
        headers=AUTH,
        json={"budget_duration": "1mo", "coding_agent": {"agent": "codex"}},
    )
    assert retagged.status_code == 200, retagged.text
    assert retagged.json()["coding_agent"]["agent"] == "codex"
    assert "-01T00:00:00" in retagged.json()["budget_reset_at"]

    cleared = await client.put(
        f"/ui/api/keys/{token}", headers=AUTH, json={"budget_duration": None, "coding_agent": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["coding_agent"] is None
    metadata = await db.query_raw(
        "SELECT metadata FROM deltallm_verificationtoken WHERE token = $1", token
    )
    # Clearing the tag keeps the other metadata, such as guardrails.
    assert metadata[0]["metadata"] == {"guardrails": ["pii"]}

    remaining = await client.get(
        "/ui/api/keys", headers=AUTH, params={"team_id": team_id, "coding_agent": "true"}
    )
    assert remaining.json()["data"] == []
