from __future__ import annotations

from types import SimpleNamespace

from prometheus_client import generate_latest

from src.metrics import (
    get_prometheus_registry,
    record_tier_capacity_observation,
    set_tier_capacity_pool_saturation,
)
from src.services.tier_capacity_fair_share import TierFairShareDecision


def test_tier_capacity_observation_exports_aggregate_pool_and_tier_metrics() -> None:
    for organization_id in ("org-metrics-a", "org-metrics-b"):
        record_tier_capacity_observation(
            TierFairShareDecision(
                allowed=False,
                pool_key="cardinality-test",
                callable_key="gpt-4o-mini",
                organization_id=organization_id,
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
        pool_key="cardinality-test",
        model="gpt-4o-mini",
        dimension="rpm",
        saturation=0.75,
    )

    lines = generate_latest(get_prometheus_registry()).decode("utf-8").splitlines()
    request_line = next(
        line
        for line in lines
        if line.startswith("deltallm_tier_capacity_requests_total{")
        and 'pool_key="cardinality-test"' in line
    )
    assert 'model="gpt-4o-mini"' in request_line
    assert 'tier_key="enterprise"' in request_line
    assert 'outcome="denied"' in request_line
    assert 'organization_id=' not in request_line
    assert request_line.endswith(" 2.0")
    assert not any(
        line.startswith("deltallm_tier_capacity_requests_total{")
        and "organization_id=" in line
        for line in lines
    )

    saturation_line = next(
        line
        for line in lines
        if line.startswith("deltallm_tier_capacity_pool_saturation{")
        and 'pool_key="cardinality-test"' in line
    )
    active_line = next(
        line
        for line in lines
        if line.startswith("deltallm_tier_capacity_pool_active_organizations{")
        and 'pool_key="cardinality-test"' in line
    )
    assert saturation_line.endswith(" 0.75")
    assert active_line.endswith(" 2.0")


def test_static_hard_cap_metrics_do_not_reset_active_fair_share_orgs() -> None:
    record_tier_capacity_observation(
        SimpleNamespace(
            pool_key="hard-cap-metrics",
            callable_key="metrics-model",
            organization_id="org-active",
            tier_key="enterprise",
            scope="tier_pool_fair_share_rpm",
            active_org_count=3,
        ),
        outcome="allowed",
    )
    static_observation = SimpleNamespace(
        pool_key="hard-cap-metrics",
        callable_key="metrics-model",
        organization_id="org-static",
        tier_key="growth",
        scope="tier_pool_model_rpm",
    )
    record_tier_capacity_observation(static_observation, outcome="allowed")
    record_tier_capacity_observation(static_observation, outcome="denied")

    lines = generate_latest(get_prometheus_registry()).decode("utf-8").splitlines()
    static_lines = [
        line
        for line in lines
        if line.startswith("deltallm_tier_capacity_requests_total{")
        and 'pool_key="hard-cap-metrics"' in line
        and 'scope="tier_pool_model_rpm"' in line
    ]
    active_line = next(
        line
        for line in lines
        if line.startswith("deltallm_tier_capacity_pool_active_organizations{")
        and 'pool_key="hard-cap-metrics"' in line
    )

    assert any('outcome="allowed"' in line and line.endswith(" 1.0") for line in static_lines)
    assert any('outcome="denied"' in line and line.endswith(" 1.0") for line in static_lines)
    assert active_line.endswith(" 3.0")
