from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from prisma import Prisma

from src.api.admin.auth_scope import AuthScope
from src.api.admin.endpoints import keys as keys_endpoints

pytestmark = pytest.mark.postgres


@pytest.fixture
async def key_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provide DATABASE_URL for key list tests")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    tag = uuid4().hex[:10]
    try:
        yield db, tag
    finally:
        await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token LIKE $1", f"%-{tag}")
        await db.execute_raw("DELETE FROM deltallm_serviceaccount WHERE service_account_id = $1", f"sa-{tag}")
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = ANY($1::text[])", [f"team-{tag}", f"other-team-{tag}"])
        await db.execute_raw("DELETE FROM deltallm_platformaccount WHERE account_id = $1", f"acc-{tag}")
        await db.execute_raw("DELETE FROM deltallm_organizationtable WHERE organization_id = ANY($1::text[])", [f"org-{tag}", f"other-org-{tag}"])
        await db.disconnect()


async def _seed(db: Prisma, tag: str) -> None:
    now = datetime.now(tz=UTC)
    for org in (f"org-{tag}", f"other-org-{tag}"):
        await db.execute_raw(
            "INSERT INTO deltallm_organizationtable (id, organization_id, organization_name, updated_at) VALUES ($1, $1, $1, now())",
            org,
        )
    await db.execute_raw(
        "INSERT INTO deltallm_teamtable (team_id, team_alias, organization_id, updated_at) VALUES ($1, 'Search', $2, now()), ($3, 'Other', $4, now())",
        f"team-{tag}", f"org-{tag}", f"other-team-{tag}", f"other-org-{tag}",
    )
    await db.execute_raw(
        "INSERT INTO deltallm_platformaccount (account_id, email, role, updated_at) VALUES ($1, $2, 'org_user', now())",
        f"acc-{tag}", f"dana-{tag}@acme.test",
    )
    await db.execute_raw(
        "INSERT INTO deltallm_serviceaccount (service_account_id, team_id, name) VALUES ($1, $2, 'indexer')",
        f"sa-{tag}", f"team-{tag}",
    )
    # name, owner account, owner service account, spend, max budget, expires, created, last used
    keys: list[tuple[Any, ...]] = [
        ("alpha", f"acc-{tag}", None, 9.0, 10.0, None, now - timedelta(days=3), now - timedelta(days=1)),
        ("Bravo", None, f"sa-{tag}", 10.0, 10.0, now + timedelta(days=3), now - timedelta(days=2), now - timedelta(days=40)),
        ("charlie", None, None, 0.0, None, now - timedelta(days=1), now - timedelta(days=1), None),
    ]
    for name, owner, service_account, spend, max_budget, expires, created, last_used in keys:
        token = f"{name.lower()}-{tag}"
        await db.execute_raw(
            """
            INSERT INTO deltallm_verificationtoken
                (id, token, key_name, team_id, owner_account_id, owner_service_account_id,
                 spend, max_budget, expires, created_at, updated_at)
            VALUES ($1, $1, $2, $3, $4, $5, $6, $7, $8::timestamptz, $9::timestamptz, now())
            """,
            token, name, f"team-{tag}", owner, service_account, spend, max_budget, expires, created,
        )
        if last_used is not None:
            await db.execute_raw(
                "INSERT INTO deltallm_verificationtoken_last_used (token, last_used_at) VALUES ($1, $2::timestamptz)",
                token, last_used,
            )
    # Same org filter must leave out keys from other organizations.
    await db.execute_raw(
        "INSERT INTO deltallm_verificationtoken (id, token, key_name, team_id, updated_at) VALUES ($1, $1, 'outside', $2, now())",
        f"outside-{tag}", f"other-team-{tag}",
    )


class _Request:
    def __init__(self, db: Prisma, *, native_accounting: bool) -> None:
        state = type("State", (), {})()
        state.prisma_manager = type("Manager", (), {"client": db})()
        state.accounting_budget_reads = object() if native_accounting else None
        self.app = type("App", (), {"state": state})()


async def _names(db: Prisma, tag: str, monkeypatch, *, native_accounting: bool = False, **params) -> list[str]:
    monkeypatch.setattr(keys_endpoints, "get_auth_scope", lambda *args, **kwargs: AuthScope(is_platform_admin=True))

    async def _no_balances(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(keys_endpoints, "apply_accounting_balances", _no_balances)
    defaults = {
        "search": None, "team_id": None, "my_keys": False, "coding_agent": None,
        "organization_id": f"org-{tag}", "owner": None, "owner_account_id": None, "status": None,
        "last_used": None, "sort_by": "created_at", "sort_direction": "desc", "limit": 100, "offset": 0,
        "authorization": None, "x_master_key": None,
    }
    defaults.update(params)
    result = await keys_endpoints.list_keys(_Request(db, native_accounting=native_accounting), **defaults)
    assert result["pagination"]["total"] == len(result["data"])
    return [row["key_name"] for row in result["data"]]


@pytest.mark.parametrize("native_accounting", [False, True])
async def test_key_list_filters_and_sorts(key_db, monkeypatch, native_accounting: bool):
    db, tag = key_db
    await _seed(db, tag)

    async def names(**params: Any) -> list[str]:
        return await _names(db, tag, monkeypatch, native_accounting=native_accounting, **params)

    assert await names() == ["charlie", "Bravo", "alpha"]
    assert await names(sort_direction="asc") == ["alpha", "Bravo", "charlie"]
    assert await names(sort_by="name", sort_direction="asc") == ["alpha", "Bravo", "charlie"]
    assert await names(sort_by="last_used", sort_direction="desc") == ["alpha", "Bravo", "charlie"]
    assert await names(sort_by="expires", sort_direction="asc") == ["charlie", "Bravo", "alpha"]
    assert await names(sort_by="budget_used", sort_direction="desc") == ["Bravo", "alpha", "charlie"]

    assert await names(owner="person") == ["alpha"]
    assert await names(owner="service_account") == ["Bravo"]
    assert await names(owner="unassigned") == ["charlie"]
    assert await names(owner_account_id=f"acc-{tag}") == ["alpha"]

    assert await names(status="active") == ["Bravo", "alpha"]
    assert await names(status="expired") == ["charlie"]
    assert await names(status="expiring") == ["Bravo"]
    assert await names(status="budget_exhausted") == ["Bravo"]

    assert await names(last_used="never") == ["charlie"]
    assert await names(last_used="active_7d") == ["alpha"]
    assert await names(last_used="active_30d") == ["alpha"]
    assert await names(last_used="inactive_30d") == ["charlie", "Bravo"]
    assert await names(last_used="inactive_90d") == ["charlie"]

    assert await names(organization_id=f"other-org-{tag}") == ["outside"]
    assert await names(team_id=f"team-{tag}", status="active", owner="person") == ["alpha"]
