from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.db.tiers import TierPolicyRepositoryUnavailableError, TierRepository

from tests.db.tier_repository_fakes import _FakePrisma


@pytest.mark.asyncio
async def test_load_active_tier_policy_inputs_resolves_versions_and_bulk_loads_policy() -> None:
    next_transition_at = datetime(2026, 6, 20, tzinfo=UTC) + timedelta(minutes=10)
    prisma = _FakePrisma(
        current_active_version_id="ver-active",
        next_transition_at=next_transition_at,
    )
    repository = TierRepository(prisma)

    result = await repository.load_active_tier_policy_inputs(
        reference_time=datetime(2026, 6, 20, tzinfo=UTC),
    )

    assert len(result.assignments) == 1
    assert result.assignments[0].organization_id == "org-1"
    assert result.assignments[0].effective_tier_version_id == "ver-active"
    assert len(result.model_policies) == 1
    assert result.model_policies[0].tier_version_id == "ver-active"
    assert result.model_policies[0].rpm_limit == 100
    assert len(result.capacity_pools) == 1
    assert result.capacity_pools[0].pool_key == "shared"
    assert result.next_transition_at == next_transition_at
    assert "JOIN LATERAL" in prisma.calls[0][0]
    assert "tier_version_id IN ($1)" in prisma.calls[1][0]
    assert "tier_version_id IN ($1)" in prisma.calls[2][0]
    assert "MIN(transition_at) AS next_transition_at" in prisma.calls[3][0]


@pytest.mark.asyncio
async def test_load_active_tier_policy_inputs_rejects_missing_prisma() -> None:
    repository = TierRepository(None)

    with pytest.raises(TierPolicyRepositoryUnavailableError, match="database unavailable"):
        await repository.load_active_tier_policy_inputs()
