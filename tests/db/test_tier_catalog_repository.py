from __future__ import annotations

import json

import pytest

from src.db.tiers import TierRepository

from tests.db.tier_repository_fakes import _FakePrisma


@pytest.mark.asyncio
async def test_list_tiers_applies_search_enabled_and_pagination() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    rows, total = await repository.list_tiers(search="pro", enabled=True, limit=25, offset=10)

    assert total == 1
    assert len(rows) == 1
    assert rows[0].tier_key == "pro"
    assert rows[0].metadata == {"segment": "growth"}
    assert rows[0].active_version_id == "ver-active"
    assert rows[0].version_count == 2
    assert rows[0].assignment_count == 3
    assert prisma.calls[0][1] == ("%pro%", True)
    assert prisma.calls[1][1] == ("%pro%", True, 25, 10)
    assert "ILIKE" in prisma.calls[1][0]
    assert "enabled = $2" in prisma.calls[1][0]


@pytest.mark.asyncio
async def test_create_tier_serializes_metadata() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    record = await repository.create_tier(
        tier_key="enterprise",
        name="Enterprise",
        description="Dedicated capacity",
        enabled=True,
        metadata={"segment": "enterprise"},
    )

    assert record.tier_key == "enterprise"
    assert record.description == "Dedicated capacity"
    assert record.metadata == {"segment": "enterprise"}
    assert "INSERT INTO deltallm_tier" in prisma.calls[0][0]
    assert json.loads(str(prisma.calls[0][1][4])) == {"segment": "enterprise"}


@pytest.mark.asyncio
async def test_count_active_tier_assignments_filters_current_enabled_windows() -> None:
    prisma = _FakePrisma(active_assignment_count=2)
    repository = TierRepository(prisma)

    count = await repository.count_active_tier_assignments("tier-1")

    assert count == 2
    query, params = prisma.calls[0]
    assert params == ("tier-1",)
    assert "enabled = TRUE" in query
    assert "starts_at IS NULL OR starts_at <= NOW()" in query
    assert "ends_at IS NULL OR ends_at > NOW()" in query


@pytest.mark.asyncio
async def test_count_live_or_scheduled_tier_assignments_includes_future_windows() -> None:
    prisma = _FakePrisma(active_assignment_count=2)
    repository = TierRepository(prisma)

    count = await repository.count_live_or_scheduled_tier_assignments("tier-1")

    assert count == 2
    query, params = prisma.calls[0]
    assert params == ("tier-1",)
    assert "enabled = TRUE" in query
    assert "ends_at IS NULL OR ends_at > NOW()" in query
    assert "starts_at" not in query
