from __future__ import annotations

from types import SimpleNamespace
import math
import time

import pytest

from src.services.limit_counter import (
    fair_share_active_key,
    fair_share_boost_key,
    fair_share_denial_key,
    fair_share_weight_key,
)
from src.services.tier_capacity import TierCapacityRuntimeService


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.ttls[key] = ex

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return int(existed)

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)

    async def zrangebyscore(self, key: str, minimum: object, maximum: object) -> list[str]:
        del minimum, maximum
        return list(self.sorted_sets.get(key, {}))

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


def _policy(*, strategy: str = "weighted_fair") -> SimpleNamespace:
    return SimpleNamespace(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        strategy=strategy,
        rpm_capacity=100,
        tpm_capacity=10_000,
        max_parallel_requests=10,
        saturation_threshold=0.8,
        burst_multiplier=None,
    )


def _tier_policy_service(policy: object | None = None) -> SimpleNamespace:
    selected = policy or _policy()
    return SimpleNamespace(
        get_capacity_pool_policy=lambda pool_key, callable_key: (
            selected if (pool_key, callable_key) == ("shared", "gpt-4o-mini") else None
        ),
        get_snapshot=lambda: SimpleNamespace(
            etag="snapshot-1",
            capacity_pool_policy={("shared", "gpt-4o-mini"): selected},
        ),
    )


@pytest.mark.asyncio
async def test_temporary_capacity_boost_has_ttl_and_can_be_cleared() -> None:
    redis = _Redis()
    service = TierCapacityRuntimeService(
        redis_client=redis,
        tier_policy_service=_tier_policy_service(),
    )

    created = await service.set_temporary_boost(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        organization_id="org-1",
        multiplier=2.5,
        expires_in_seconds=300,
    )

    key = fair_share_boost_key("shared:gpt-4o-mini", "org-1")
    assert redis.values[key] == "2.5"
    assert redis.ttls[key] == 300
    assert created["multiplier"] == 2.5

    deleted = await service.clear_temporary_boost(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        organization_id="org-1",
    )

    assert deleted["deleted"] is True
    assert key not in redis.values


@pytest.mark.asyncio
async def test_hard_cap_pool_rejects_temporary_boost() -> None:
    service = TierCapacityRuntimeService(
        redis_client=_Redis(),
        tier_policy_service=_tier_policy_service(_policy(strategy="hard_cap")),
    )

    with pytest.raises(ValueError, match="weighted fair-share"):
        await service.set_temporary_boost(
            pool_key="shared",
            callable_key="gpt-4o-mini",
            organization_id="org-1",
            multiplier=2,
            expires_in_seconds=60,
        )


@pytest.mark.asyncio
async def test_capacity_dashboard_reports_top_orgs_saturation_and_limit_hits() -> None:
    redis = _Redis()
    entity_id = "shared:gpt-4o-mini"
    window_id = math.floor(time.time() / 60)
    redis.sorted_sets[fair_share_active_key(entity_id)] = {
        "org-1": time.time() * 1000 + 60_000,
        "org-2": time.time() * 1000 + 60_000,
    }
    redis.hashes[fair_share_weight_key(entity_id)] = {"org-1": "1", "org-2": "3"}
    redis.values[f"ratelimit:tier_pool_model_rpm:{entity_id}:{window_id}"] = "80"
    redis.values[f"ratelimit:tier_pool_model_tpm:{entity_id}:{window_id}"] = "7500"
    redis.values[f"ratelimit:tier_pool_model_rpm_org:{entity_id}:org-1:{window_id}"] = "20"
    redis.values[f"ratelimit:tier_pool_model_rpm_org:{entity_id}:org-2:{window_id}"] = "60"
    redis.values[f"ratelimit:tier_pool_model_tpm_org:{entity_id}:org-1:{window_id}"] = "1500"
    redis.values[f"ratelimit:tier_pool_model_tpm_org:{entity_id}:org-2:{window_id}"] = "6000"
    redis.values[fair_share_boost_key(entity_id, "org-2")] = "1.5"
    redis.ttls[fair_share_boost_key(entity_id, "org-2")] = 120
    redis.hashes[
        fair_share_denial_key("tier_pool_model_rpm", entity_id, window_id)
    ] = {"org-1": "2"}

    dashboard = await TierCapacityRuntimeService(
        redis_client=redis,
        tier_policy_service=_tier_policy_service(),
    ).dashboard(top_org_limit=1)

    pool = dashboard["pools"][0]
    assert pool["active_organization_count"] == 2
    assert pool["rpm_saturation"] == 0.8
    assert pool["tpm_saturation"] == 0.75
    assert pool["top_organizations"][0]["organization_id"] == "org-2"
    assert pool["top_organizations"][0]["boost_multiplier"] == 1.5
    assert dashboard["limit_hit_heatmap"][0]["organization_id"] == "org-1"
    assert dashboard["limit_hit_heatmap"][0]["limit_hits"] == 2
