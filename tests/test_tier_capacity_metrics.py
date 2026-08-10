from __future__ import annotations

from prometheus_client import generate_latest

from src.metrics import (
    get_prometheus_registry,
    record_tier_capacity_observation,
    set_tier_capacity_pool_saturation,
)
from src.services.tier_capacity_fair_share import TierFairShareDecision


def test_tier_capacity_observation_exports_pool_org_and_tier_metrics() -> None:
    record_tier_capacity_observation(
        TierFairShareDecision(
            allowed=False,
            pool_key="growth",
            callable_key="gpt-4o-mini",
            organization_id="org-metrics",
            tier_key="enterprise",
            scope="tier_pool_fair_share_rpm",
            reason="weighted_share_exceeded",
            dimension="rpm",
            active_org_count=2,
            total_weight=4,
            effective_weight=3,
            pool_current=749,
            org_current=750,
            pool_limit=1_000,
            share_limit=750,
            saturation=0.75,
        ),
        outcome="denied",
    )
    set_tier_capacity_pool_saturation(
        pool_key="growth",
        model="gpt-4o-mini",
        dimension="rpm",
        saturation=0.75,
    )

    lines = generate_latest(get_prometheus_registry()).decode("utf-8").splitlines()
    request_line = next(
        line
        for line in lines
        if line.startswith("deltallm_tier_capacity_requests_total{")
        and 'organization_id="org-metrics"' in line
    )
    assert 'pool_key="growth"' in request_line
    assert 'model="gpt-4o-mini"' in request_line
    assert 'tier_key="enterprise"' in request_line
    assert 'outcome="denied"' in request_line

    saturation_line = next(
        line
        for line in lines
        if line.startswith("deltallm_tier_capacity_pool_saturation{")
        and 'pool_key="growth"' in line
    )
    active_line = next(
        line
        for line in lines
        if line.startswith("deltallm_tier_capacity_pool_active_organizations{")
        and 'pool_key="growth"' in line
    )
    assert saturation_line.endswith(" 0.75")
    assert active_line.endswith(" 2.0")
