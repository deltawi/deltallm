from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import perf_counter

from src.db.tiers import (
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    TierPolicyAssignmentRecord,
    TierPolicyLoadResult,
)
from src.services.tier_policy_compiler import compile_tier_policy_snapshot


def _assignment(
    assignment_id: str,
    *,
    organization_id: str = "org-1",
    tier_key: str = "standard",
    version_id: str = "version-1",
    assignment_type: str = "primary",
    weight: int = 1,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    status: str = "active",
) -> TierPolicyAssignmentRecord:
    return TierPolicyAssignmentRecord(
        assignment_id=assignment_id,
        organization_id=organization_id,
        tier_id=f"tier-{tier_key}",
        tier_version_id=None,
        effective_tier_version_id=version_id,
        assignment_type=assignment_type,
        enabled=True,
        weight=weight,
        starts_at=starts_at,
        ends_at=ends_at,
        tier_key=tier_key,
        tier_name=tier_key.title(),
        tier_version_number=1,
        tier_version_status=status,
    )


def _policy(
    policy_id: str,
    *,
    version_id: str = "version-1",
    callable_key: str = "gpt-4o-mini",
    access_mode: str = "allow",
    priority: int = 0,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
    rph_limit: int | None = None,
    rpd_limit: int | None = None,
    tpd_limit: int | None = None,
    max_parallel_requests: int | None = None,
    batch_rpm_limit: int | None = None,
    batch_tpm_limit: int | None = None,
    pricing: dict[str, float] | None = None,
    capacity_pool_key: str | None = None,
) -> TierModelPolicyRecord:
    return TierModelPolicyRecord(
        tier_model_policy_id=policy_id,
        tier_version_id=version_id,
        callable_key=callable_key,
        access_mode=access_mode,
        priority=priority,
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        rph_limit=rph_limit,
        rpd_limit=rpd_limit,
        tpd_limit=tpd_limit,
        max_parallel_requests=max_parallel_requests,
        batch_rpm_limit=batch_rpm_limit,
        batch_tpm_limit=batch_tpm_limit,
        pricing=pricing,
        capacity_pool_key=capacity_pool_key,
    )


def _pool(
    pool_id: str,
    *,
    version_id: str = "version-1",
    pool_key: str = "shared",
    callable_key: str = "gpt-4o-mini",
    rpm_capacity: int | None = None,
    tpm_capacity: int | None = None,
    max_parallel_requests: int | None = None,
    strategy: str = "hard_cap",
    saturation_threshold: float | None = None,
    burst_multiplier: float | None = None,
) -> TierCapacityPoolRecord:
    return TierCapacityPoolRecord(
        tier_capacity_pool_id=pool_id,
        tier_version_id=version_id,
        pool_key=pool_key,
        callable_key=callable_key,
        rpm_capacity=rpm_capacity,
        tpm_capacity=tpm_capacity,
        max_parallel_requests=max_parallel_requests,
        strategy=strategy,
        saturation_threshold=saturation_threshold,
        burst_multiplier=burst_multiplier,
    )


def _inputs(
    *,
    assignments: list[TierPolicyAssignmentRecord],
    policies: list[TierModelPolicyRecord],
    pools: list[TierCapacityPoolRecord] | None = None,
    next_transition_at: datetime | None = None,
) -> TierPolicyLoadResult:
    return TierPolicyLoadResult(
        assignments=tuple(assignments),
        model_policies=tuple(policies),
        capacity_pools=tuple(pools or []),
        next_transition_at=next_transition_at,
    )


def test_compile_snapshot_applies_assignment_precedence_and_deny_wins() -> None:
    generated_at = datetime(2026, 6, 20, tzinfo=UTC)
    snapshot = compile_tier_policy_snapshot(
        _inputs(
            assignments=[
                _assignment("assignment-primary", tier_key="primary", version_id="version-primary"),
                _assignment(
                    "assignment-addon",
                    tier_key="addon",
                    version_id="version-addon",
                    assignment_type="addon",
                ),
                _assignment(
                    "assignment-override",
                    tier_key="override",
                    version_id="version-override",
                    assignment_type="override",
                ),
            ],
            policies=[
                _policy(
                    "policy-primary-gpt",
                    version_id="version-primary",
                    pricing={"input_cost_per_token": 1.0},
                    rpm_limit=100,
                ),
                _policy(
                    "policy-addon-gpt",
                    version_id="version-addon",
                    pricing={"input_cost_per_token": 2.0},
                    rpm_limit=200,
                ),
                _policy(
                    "policy-override-gpt",
                    version_id="version-override",
                    pricing={"input_cost_per_token": 3.0},
                    rpm_limit=50,
                ),
                _policy(
                    "policy-addon-denied",
                    version_id="version-addon",
                    callable_key="gpt-4o",
                    access_mode="allow",
                ),
                _policy(
                    "policy-override-denied",
                    version_id="version-override",
                    callable_key="gpt-4o",
                    access_mode="deny",
                ),
            ],
        ),
        generated_at=generated_at,
    )

    assert snapshot.org_allowed_callable_keys["org-1"] == frozenset({"gpt-4o-mini"})
    assert snapshot.org_model_policy[("org-1", "gpt-4o-mini")].source.assignment_type == "override"
    assert snapshot.org_model_policy[("org-1", "gpt-4o-mini")].limits.rpm_limit == 50
    assert (
        snapshot.pricing_policies[("org-1", "gpt-4o-mini", "sync")].pricing["input_cost_per_token"]
        == 3.0
    )
    assert snapshot.org_model_policy[("org-1", "gpt-4o")].access_mode == "deny"
    assert ("org-1", "gpt-4o", "sync") not in snapshot.pricing_policies


def test_compile_snapshot_assignment_weight_precedes_model_policy_priority() -> None:
    snapshot = compile_tier_policy_snapshot(
        _inputs(
            assignments=[
                _assignment("assignment-low", version_id="version-low", weight=1),
                _assignment("assignment-high", version_id="version-high", weight=10),
            ],
            policies=[
                _policy(
                    "policy-low",
                    version_id="version-low",
                    priority=100,
                    rpm_limit=100,
                ),
                _policy(
                    "policy-high",
                    version_id="version-high",
                    priority=1,
                    rpm_limit=500,
                ),
            ],
        ),
        generated_at=datetime(2026, 6, 20, tzinfo=UTC),
    )

    policy = snapshot.org_model_policy[("org-1", "gpt-4o-mini")]
    assert policy.source.assignment_id == "assignment-high"
    assert policy.limits.rpm_limit == 500


def test_compile_snapshot_ignores_inactive_inputs() -> None:
    generated_at = datetime(2026, 6, 20, tzinfo=UTC)
    future_start = generated_at + timedelta(days=1)
    snapshot = compile_tier_policy_snapshot(
        _inputs(
            assignments=[
                _assignment(
                    "future",
                    version_id="version-future",
                    starts_at=future_start,
                ),
                _assignment(
                    "expired",
                    version_id="version-expired",
                    ends_at=generated_at - timedelta(seconds=1),
                ),
                _assignment("active", version_id="version-active"),
            ],
            policies=[
                _policy("future-policy", version_id="version-future", callable_key="future"),
                _policy("expired-policy", version_id="version-expired", callable_key="expired"),
                _policy("active-policy", version_id="version-active", callable_key="active"),
            ],
        ),
        generated_at=generated_at,
    )

    assert snapshot.org_allowed_callable_keys["org-1"] == frozenset({"active"})
    assert snapshot.next_transition_at == future_start
    assert snapshot.assignment_count == 1
    assert snapshot.model_policy_count == 1


def test_compile_snapshot_uses_repository_next_transition_when_inputs_are_active_only() -> None:
    generated_at = datetime(2026, 6, 20, tzinfo=UTC)
    next_transition_at = generated_at + timedelta(hours=2)

    snapshot = compile_tier_policy_snapshot(
        _inputs(
            assignments=[_assignment("active", version_id="version-active")],
            policies=[_policy("active-policy", version_id="version-active", callable_key="active")],
            next_transition_at=next_transition_at,
        ),
        generated_at=generated_at,
    )

    assert snapshot.next_transition_at == next_transition_at


def test_compile_snapshot_prebuilds_pricing_and_rate_limit_descriptors() -> None:
    snapshot = compile_tier_policy_snapshot(
        _inputs(
            assignments=[_assignment("assignment-1")],
            policies=[
                _policy(
                    "policy-1",
                    rpm_limit=100,
                    tpm_limit=10_000,
                    rph_limit=1_000,
                    rpd_limit=10_000,
                    tpd_limit=1_000_000,
                    max_parallel_requests=25,
                    batch_rpm_limit=50,
                    batch_tpm_limit=5_000,
                    pricing={
                        "input_cost_per_token": 0.001,
                        "output_cost_per_token": 0.002,
                        "batch_input_cost_per_token": 0.0005,
                    },
                )
            ],
        ),
        generated_at=datetime(2026, 6, 20, tzinfo=UTC),
    )

    sync_pricing = snapshot.pricing_policies[("org-1", "gpt-4o-mini", "sync")]
    batch_pricing = snapshot.pricing_policies[("org-1", "gpt-4o-mini", "batch")]
    assert "batch_input_cost_per_token" not in sync_pricing.pricing
    assert batch_pricing.pricing["batch_input_cost_per_token"] == 0.0005

    descriptors = snapshot.rate_limit_descriptors[("org-1", "gpt-4o-mini")]
    descriptor_by_scope = {descriptor.scope: descriptor for descriptor in descriptors}
    assert descriptor_by_scope["tier_org_model_rpm"].limit == 100
    assert descriptor_by_scope["tier_org_model_tpm"].amount_kind == "tokens"
    assert descriptor_by_scope["tier_org_model_parallel"].window_seconds == 0
    assert descriptor_by_scope["tier_org_model_batch_tpm"].mode == "batch"


def test_compile_snapshot_merges_capacity_pools_conservatively() -> None:
    snapshot = compile_tier_policy_snapshot(
        _inputs(
            assignments=[
                _assignment("assignment-1", version_id="version-1"),
                _assignment("assignment-2", version_id="version-2", assignment_type="addon"),
            ],
            policies=[],
            pools=[
                _pool(
                    "pool-1",
                    version_id="version-1",
                    rpm_capacity=1000,
                    tpm_capacity=None,
                    max_parallel_requests=50,
                    strategy="reserved_burst",
                    saturation_threshold=0.9,
                    burst_multiplier=2.0,
                ),
                _pool(
                    "pool-2",
                    version_id="version-2",
                    rpm_capacity=800,
                    tpm_capacity=500_000,
                    max_parallel_requests=25,
                    strategy="hard_cap",
                    saturation_threshold=0.8,
                    burst_multiplier=1.5,
                ),
            ],
        ),
        generated_at=datetime(2026, 6, 20, tzinfo=UTC),
    )

    pool = snapshot.capacity_pool_policy[("shared", "gpt-4o-mini")]
    assert pool.rpm_capacity == 800
    assert pool.tpm_capacity == 500_000
    assert pool.max_parallel_requests == 25
    assert pool.strategy == "hard_cap"
    assert pool.saturation_threshold == 0.8
    assert pool.burst_multiplier == 1.5
    assert pool.source_tier_version_ids == ("version-1", "version-2")


def test_compile_snapshot_etag_is_stable_for_same_logical_inputs() -> None:
    inputs = _inputs(
        assignments=[_assignment("assignment-1")],
        policies=[_policy("policy-1", pricing={"input_cost_per_token": 0.001})],
    )

    first = compile_tier_policy_snapshot(
        inputs,
        generated_at=datetime(2026, 6, 20, tzinfo=UTC),
    )
    second = compile_tier_policy_snapshot(
        inputs,
        generated_at=datetime(2026, 6, 21, tzinfo=UTC),
    )
    changed = compile_tier_policy_snapshot(
        _inputs(
            assignments=[_assignment("assignment-1")],
            policies=[_policy("policy-1", pricing={"input_cost_per_token": 0.002})],
        ),
        generated_at=datetime(2026, 6, 20, tzinfo=UTC),
    )

    assert first.etag == second.etag
    assert first.etag != changed.etag


def test_compiled_lookup_overhead_is_small() -> None:
    snapshot = compile_tier_policy_snapshot(
        _inputs(
            assignments=[_assignment("assignment-1")],
            policies=[_policy("policy-1", pricing={"input_cost_per_token": 0.001})],
        ),
        generated_at=datetime(2026, 6, 20, tzinfo=UTC),
    )

    started = perf_counter()
    for _ in range(50_000):
        assert snapshot.org_model_policy.get(("org-1", "gpt-4o-mini")) is not None
        assert snapshot.org_allowed_callable_keys.get("org-1") == frozenset({"gpt-4o-mini"})
    elapsed = perf_counter() - started

    assert elapsed < 0.5
