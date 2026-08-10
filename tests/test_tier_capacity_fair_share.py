from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import math
import time
from types import MappingProxyType, SimpleNamespace

import pytest

from src.models.errors import RateLimitError
from src.models.responses import UserAPIKeyAuth
from src.rate_limit_policy import acquire_rate_limit_controls, build_rate_limit_checks, release_rate_limit_controls
from src.services.limit_counter import LimitCounter, ParallelLimitCheck, RateLimitCheck
from src.services.tier_capacity_fair_share import (
    FAIR_SHARE_ACTIVE_CLEANUP_LIMIT,
    FAIR_SHARE_WINDOW_SECONDS,
    FAIR_SHARE_WEIGHT_SCALE,
    TierFairShareCheck,
    fair_share_active_count_key,
    fair_share_active_key,
    fair_share_boost_index_key,
    fair_share_boost_metadata_key,
    build_tier_capacity_dashboard,
    fair_share_cleanup_lag_key,
    fair_share_limit_hit_heatmap_key,
    fair_share_limit_hit_heatmap_rank_key,
    fair_share_limit_hit_total_key,
    fair_share_org_counter_key,
    fair_share_pool_counter_key,
    static_pool_counter_key,
    fair_share_total_weight_key,
    fair_share_usage_rank_key,
    fair_share_weight_key,
    upsert_temporary_capacity_boost,
)
from src.services.tier_fair_share_counter import TierFairShareCounter
from src.services.tier_policy_models import (
    CompiledTierCapacityPoolPolicy,
    CompiledTierRateLimitDescriptor,
    TierPolicySnapshot,
    empty_tier_policy_snapshot,
)
from tests.conftest import FakeRedis


class _TierFairShareService:
    mode = "enforce"
    missing_service_mode = "fail_open"
    snapshot_stale = False

    def __init__(self, *, pool_policy: CompiledTierCapacityPoolPolicy) -> None:
        self.pool_policy = pool_policy

    def has_explicit_tier_policy(self, organization_id: str | None) -> bool:
        return bool(organization_id)

    def resolve_unavailable_decision(self, organization_id: str | None) -> object:
        del organization_id
        return SimpleNamespace(allowed=True, reason="available")

    def get_rate_limit_descriptors(self, organization_id: str | None, callable_key: str | None):
        del organization_id, callable_key
        return ()

    def get_model_policy(self, organization_id: str | None, callable_key: str | None) -> object:
        return SimpleNamespace(
            access_mode="allow",
            capacity_pool_key="shared",
            source=SimpleNamespace(
                tier_key=f"tier-{organization_id}",
                assignment_weight=1,
            ),
            callable_key=callable_key,
        )

    def get_capacity_pool_policy(
        self,
        pool_key: str | None,
        callable_key: str | None,
    ) -> CompiledTierCapacityPoolPolicy | None:
        if pool_key == self.pool_policy.pool_key and callable_key == self.pool_policy.callable_key:
            return self.pool_policy
        return None


class _SnapshotService:
    mode = "enforce"
    snapshot_stale = False
    last_reload_failed = False
    last_reload_error_at = None

    def __init__(self, snapshot: TierPolicySnapshot) -> None:
        self.snapshot = snapshot

    def get_snapshot(self) -> TierPolicySnapshot:
        return self.snapshot

    def snapshot_info(self) -> object:
        return SimpleNamespace(
            etag=self.snapshot.etag,
            generated_at=self.snapshot.generated_at,
            org_count=self.snapshot.org_count,
            assignment_count=self.snapshot.assignment_count,
            model_policy_count=self.snapshot.model_policy_count,
            capacity_pool_count=self.snapshot.capacity_pool_count,
            next_transition_at=self.snapshot.next_transition_at,
            mode="enforce",
            snapshot_stale=False,
            last_reload_failed=False,
            last_reload_error_at=None,
        )


def _auth(*, organization_id: str) -> UserAPIKeyAuth:
    return UserAPIKeyAuth(
        api_key=f"key-{organization_id}",
        organization_id=organization_id,
    )


def _rate_key(scope: str, entity_id: str, *, window_seconds: int = 60) -> str:
    return f"ratelimit:{scope}:{entity_id}:{math.floor(time.time() / window_seconds)}"


def _fair_share_pool_key(dimension: str) -> str:
    return fair_share_pool_counter_key(
        dimension=dimension,
        pool_key="shared",
        callable_key="gpt-4o-mini",
    )


def _pool_policy(
    *,
    rpm_capacity: int | None = 10,
    tpm_capacity: int | None = None,
    saturation_threshold: float = 0.5,
    strategy: str = "weighted_fair",
    burst_multiplier: float | None = None,
) -> CompiledTierCapacityPoolPolicy:
    return CompiledTierCapacityPoolPolicy(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        rpm_capacity=rpm_capacity,
        tpm_capacity=tpm_capacity,
        max_parallel_requests=None,
        strategy=strategy,
        saturation_threshold=saturation_threshold,
        burst_multiplier=burst_multiplier,
        source_tier_version_ids=("version-1",),
        source_pool_ids=("pool-1",),
        rate_limit_descriptors=(
            CompiledTierRateLimitDescriptor(
                scope="tier_pool_model_rpm",
                entity_id="shared:gpt-4o-mini",
                limit=rpm_capacity,
                amount_kind="requests",
                window_seconds=60,
                mode="all",
            ),
        ),
    )


def _fair_share_check(
    *,
    pool_key: str = "shared",
    callable_key: str = "gpt-4o-mini",
    organization_id: str = "org-1",
    tier_key: str | None = "growth",
    assignment_weight: int = 1,
    rpm_capacity: int | None = 10,
    tpm_capacity: int | None = None,
    request_amount: int = 1,
    token_amount: int = 0,
    strategy: str = "weighted_fair",
    saturation_threshold: float | None = 0.0,
    burst_multiplier: float | None = None,
) -> TierFairShareCheck:
    return TierFairShareCheck(
        pool_key=pool_key,
        callable_key=callable_key,
        organization_id=organization_id,
        tier_key=tier_key,
        assignment_weight=assignment_weight,
        rpm_capacity=rpm_capacity,
        tpm_capacity=tpm_capacity,
        request_amount=request_amount,
        token_amount=token_amount,
        strategy=strategy,
        saturation_threshold=saturation_threshold,
        burst_multiplier=burst_multiplier,
    )


def test_advanced_pool_static_rate_checks_are_replaced_when_enabled() -> None:
    service = _TierFairShareService(pool_policy=_pool_policy())

    static_scopes = {
        check.scope
        for check in build_rate_limit_checks(
            auth=_auth(organization_id="org-1"),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )
    }
    fair_share_scopes = {
        check.scope
        for check in build_rate_limit_checks(
            auth=_auth(organization_id="org-1"),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )
    }

    assert "tier_pool_model_rpm" in static_scopes
    assert "tier_pool_model_rpm" not in fair_share_scopes


@pytest.mark.asyncio
async def test_weighted_fair_share_allows_borrowing_then_enforces_under_saturation() -> None:
    service = _TierFairShareService(pool_policy=_pool_policy())
    limiter = LimitCounter(redis_client=FakeRedis(), degraded_mode="fail_open")

    for _ in range(4):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-1"),
            tokens=0,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )
    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-2"),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )
    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-1"),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )

    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-1"),
            tokens=0,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )

    assert exc_info.value.param == "tier_pool_fair_share_rpm"
    decision = getattr(exc_info.value, "tier_fair_share_decision")
    assert decision.reason == "weighted_share_exceeded"
    assert decision.share_limit == 5


@pytest.mark.asyncio
async def test_fair_share_denial_does_not_increment_standard_rate_counters() -> None:
    redis = FakeRedis()
    service = _TierFairShareService(pool_policy=_pool_policy(rpm_capacity=2, saturation_threshold=0.0))
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=UserAPIKeyAuth(api_key="key-org-1", organization_id="org-1", key_rpm_limit=100),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )
    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=UserAPIKeyAuth(api_key="key-org-2", organization_id="org-2", key_rpm_limit=100),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )

    with pytest.raises(RateLimitError):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=UserAPIKeyAuth(api_key="key-org-1", organization_id="org-1", key_rpm_limit=100),
            tokens=0,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )

    assert int(redis.store[_rate_key("key_rpm", "key-org-1")]) == 1
    assert int(redis.store[_fair_share_pool_key("rpm")]) == 2


@pytest.mark.asyncio
async def test_standard_rate_denial_does_not_increment_fair_share_counters() -> None:
    redis = FakeRedis()
    service = _TierFairShareService(pool_policy=_pool_policy(rpm_capacity=10))
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    auth = UserAPIKeyAuth(
        api_key="key-org-1",
        organization_id="org-1",
        key_rpm_limit=1,
        max_parallel_requests=1,
    )

    redis.store[_rate_key("key_rpm", "key-org-1")] = "1"
    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=auth,
            tokens=0,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )

    assert exc_info.value.param == "key_rpm"
    assert _fair_share_pool_key("rpm") not in redis.store
    assert "parallel:key:key-org-1" not in redis.store


@pytest.mark.asyncio
async def test_parallel_denial_does_not_increment_fair_share_counters() -> None:
    redis = FakeRedis()
    service = _TierFairShareService(pool_policy=_pool_policy(rpm_capacity=10))
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    auth = UserAPIKeyAuth(api_key="key-org-1", organization_id="org-1", max_parallel_requests=1)
    lease, _state = await acquire_rate_limit_controls(
        limiter=limiter,
        auth=auth,
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )

    try:
        with pytest.raises(RateLimitError) as exc_info:
            await acquire_rate_limit_controls(
                limiter=limiter,
                auth=auth,
                tokens=0,
                model="gpt-4o-mini",
                tier_policy_service=service,
                tier_policy_mode="enforce",
                tier_capacity_fair_share_enabled=True,
            )
        assert exc_info.value.retry_after == 1
        assert int(redis.store[_fair_share_pool_key("rpm")]) == 1
    finally:
        await release_rate_limit_controls(limiter=limiter, lease=lease)


@pytest.mark.asyncio
async def test_reserved_burst_multiplier_extends_share_under_saturation() -> None:
    service = _TierFairShareService(
        pool_policy=_pool_policy(
            strategy="reserved_burst",
            burst_multiplier=2.0,
        )
    )
    limiter = LimitCounter(redis_client=FakeRedis(), degraded_mode="fail_open")

    for _ in range(4):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-1"),
            tokens=0,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )
    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-2"),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )

    for _ in range(4):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-1"),
            tokens=0,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )


@pytest.mark.asyncio
async def test_tpm_fair_share_denial_annotates_token_state() -> None:
    service = _TierFairShareService(
        pool_policy=_pool_policy(
            rpm_capacity=None,
            tpm_capacity=10,
        )
    )
    limiter = LimitCounter(redis_client=FakeRedis(), degraded_mode="fail_open")

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-1"),
        tokens=4,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )
    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-2"),
        tokens=1,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )
    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-1"),
        tokens=1,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )

    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-1"),
            tokens=1,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )

    state = getattr(exc_info.value, "rate_limit_state")
    assert state.tpm_scope == "tier_pool_fair_share_tpm"
    assert state.tpm_limit == 5
    assert state.tpm_remaining == 0
    assert state.rpm_limit == 0


@pytest.mark.asyncio
async def test_fallback_tpm_denial_does_not_consume_rpm_capacity() -> None:
    service = _TierFairShareService(
        pool_policy=_pool_policy(
            rpm_capacity=10,
            tpm_capacity=5,
            saturation_threshold=0.0,
        )
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-1"),
        tokens=4,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )
    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-2"),
        tokens=1,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )

    with pytest.raises(RateLimitError):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-1"),
            tokens=1,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )

    counter = limiter._tier_fair_share_counter
    window_id = counter._window_id(FAIR_SHARE_WINDOW_SECONDS)
    rpm_pool_key = fair_share_pool_counter_key(
        dimension="rpm",
        pool_key="shared",
        callable_key="gpt-4o-mini",
        window_id=window_id,
    )
    assert counter._fallback_counters[rpm_pool_key][1] == 2


@pytest.mark.asyncio
async def test_fallback_denial_rolls_back_previous_checks_in_same_batch() -> None:
    counter = TierFairShareCounter(redis_client=None)
    window_id = counter._window_id(FAIR_SHARE_WINDOW_SECONDS)
    now = int(time.time())
    blocked_pool_key = fair_share_pool_counter_key(
        dimension="rpm",
        pool_key="blocked",
        callable_key="gpt-4o-mini",
        window_id=window_id,
    )
    counter._fallback_counters[blocked_pool_key] = (now + FAIR_SHARE_WINDOW_SECONDS, 1)
    allowed_check = TierFairShareCheck(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        organization_id="org-1",
        tier_key="growth",
        assignment_weight=1,
        rpm_capacity=10,
        tpm_capacity=None,
        request_amount=1,
        token_amount=0,
        strategy="weighted_fair",
        saturation_threshold=0.0,
        burst_multiplier=None,
    )
    blocked_check = TierFairShareCheck(
        pool_key="blocked",
        callable_key="gpt-4o-mini",
        organization_id="org-2",
        tier_key="growth",
        assignment_weight=1,
        rpm_capacity=1,
        tpm_capacity=None,
        request_amount=1,
        token_amount=0,
        strategy="weighted_fair",
        saturation_threshold=0.0,
        burst_multiplier=None,
    )

    with pytest.raises(RateLimitError):
        await counter.check([allowed_check, blocked_check])

    allowed_pool_key = fair_share_pool_counter_key(
        dimension="rpm",
        pool_key="shared",
        callable_key="gpt-4o-mini",
        window_id=window_id,
    )
    assert allowed_pool_key not in counter._fallback_counters
    assert "shared:gpt-4o-mini" not in counter._fallback_active_orgs


@pytest.mark.asyncio
async def test_redis_batch_denial_uses_staged_pool_counters_without_committing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_timestamp = 1_800_000_000.0
    monkeypatch.setattr("src.services.tier_fair_share_counter.time.time", lambda: fixed_timestamp)
    window_id = math.floor(fixed_timestamp / FAIR_SHARE_WINDOW_SECONDS)
    redis = FakeRedis()
    counter = TierFairShareCounter(redis_client=redis)

    with pytest.raises(RateLimitError) as exc_info:
        await counter.check(
            [
                _fair_share_check(organization_id="org-1", rpm_capacity=1),
                _fair_share_check(organization_id="org-2", rpm_capacity=1),
            ]
        )

    decision = getattr(exc_info.value, "tier_fair_share_decision")
    assert exc_info.value.param == "tier_pool_fair_share_rpm"
    assert decision.reason == "pool_capacity_exceeded"
    assert fair_share_pool_counter_key(
        dimension="rpm",
        pool_key="shared",
        callable_key="gpt-4o-mini",
        window_id=window_id,
    ) not in redis.store
    assert fair_share_active_key("shared", "gpt-4o-mini") not in redis.zset_store
    assert fair_share_weight_key("shared", "gpt-4o-mini") not in redis.hash_store
    assert fair_share_active_count_key("shared", "gpt-4o-mini") not in redis.store
    assert fair_share_usage_rank_key(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        window_id=window_id,
    ) not in redis.zset_store


@pytest.mark.asyncio
async def test_redis_batch_success_commits_staged_fair_share_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_timestamp = 1_800_000_000.0
    monkeypatch.setattr("src.services.tier_fair_share_counter.time.time", lambda: fixed_timestamp)
    window_id = math.floor(fixed_timestamp / FAIR_SHARE_WINDOW_SECONDS)
    redis = FakeRedis()
    counter = TierFairShareCounter(redis_client=redis)

    decisions = await counter.check(
        [
            _fair_share_check(organization_id="org-1", rpm_capacity=2),
            _fair_share_check(organization_id="org-2", rpm_capacity=2),
        ]
    )

    pool_key = fair_share_pool_counter_key(
        dimension="rpm",
        pool_key="shared",
        callable_key="gpt-4o-mini",
        window_id=window_id,
    )
    org_1_key = fair_share_org_counter_key(
        dimension="rpm",
        pool_key="shared",
        callable_key="gpt-4o-mini",
        organization_id="org-1",
        window_id=window_id,
    )
    org_2_key = fair_share_org_counter_key(
        dimension="rpm",
        pool_key="shared",
        callable_key="gpt-4o-mini",
        organization_id="org-2",
        window_id=window_id,
    )
    usage_scores = {
        member: score
        for score, member in redis.zset_store[
            fair_share_usage_rank_key(pool_key="shared", callable_key="gpt-4o-mini", window_id=window_id)
        ]
    }

    assert len(decisions) == 2
    assert int(redis.store[pool_key]) == 2
    assert int(redis.store[org_1_key]) == 1
    assert int(redis.store[org_2_key]) == 1
    assert int(redis.store[fair_share_active_count_key("shared", "gpt-4o-mini")]) == 2
    assert int(redis.store[fair_share_total_weight_key("shared", "gpt-4o-mini")]) == 2 * FAIR_SHARE_WEIGHT_SCALE
    assert sorted(member for _score, member in redis.zset_store[fair_share_active_key("shared", "gpt-4o-mini")]) == [
        "org-1",
        "org-2",
    ]
    assert usage_scores == {"org-1": 1, "org-2": 1}


@pytest.mark.asyncio
async def test_combined_admission_fair_share_denial_does_not_commit_any_admission_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_timestamp = 1_800_000_000.0
    monkeypatch.setattr("src.services.limit_counter.time.time", lambda: fixed_timestamp)
    monkeypatch.setattr("src.services.tier_fair_share_counter.time.time", lambda: fixed_timestamp)
    window_id = math.floor(fixed_timestamp / FAIR_SHARE_WINDOW_SECONDS)
    redis = FakeRedis()
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")

    with pytest.raises(RateLimitError) as exc_info:
        await limiter.check_rate_limits_and_tier_fair_share_atomic(
            [RateLimitCheck(scope="key_rpm", entity_id="key-1", limit=100, amount=1)],
            [
                _fair_share_check(organization_id="org-1", rpm_capacity=1),
                _fair_share_check(organization_id="org-2", rpm_capacity=1),
            ],
            legacy_parallel_check=ParallelLimitCheck(scope="key", entity_id="key-1", limit=1),
            parallel_checks=[
                ParallelLimitCheck(scope="tier_pool_model_parallel", entity_id="shared:gpt-4o-mini", limit=1)
            ],
        )

    assert exc_info.value.param == "tier_pool_fair_share_rpm"
    assert f"ratelimit:key_rpm:key-1:{window_id}" not in redis.store
    assert "parallel:key:key-1" not in redis.store
    assert not redis.zset_store.get("parallel_lease:tier_pool_model_parallel:shared:gpt-4o-mini")
    assert fair_share_pool_counter_key(
        dimension="rpm",
        pool_key="shared",
        callable_key="gpt-4o-mini",
        window_id=window_id,
    ) not in redis.store
    assert fair_share_active_key("shared", "gpt-4o-mini") not in redis.zset_store


@pytest.mark.asyncio
async def test_fair_share_denial_records_heatmap_in_the_single_admission_operation() -> None:
    class _TrackingRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.eval_count = 0
            self.pipeline_count = 0

        async def eval(self, script: str, numkeys: int, *args):
            self.eval_count += 1
            return await super().eval(script, numkeys, *args)

        def pipeline(self):
            self.pipeline_count += 1
            return super().pipeline()

    redis = _TrackingRedis()
    service = _TierFairShareService(
        pool_policy=_pool_policy(rpm_capacity=2, saturation_threshold=0.0)
    )
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    for _ in range(2):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-1"),
            tokens=0,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )

    redis.eval_count = 0
    redis.pipeline_count = 0
    with pytest.raises(RateLimitError):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-1"),
            tokens=0,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )

    heatmap = redis.hash_store[fair_share_limit_hit_heatmap_key()]
    assert heatmap[
        "shared|gpt-4o-mini|org-1|tier_pool_fair_share_rpm|tier-org-1"
    ] == "1"
    assert redis.eval_count == 1
    assert redis.pipeline_count == 0


@pytest.mark.asyncio
async def test_temporary_boost_increases_effective_weight() -> None:
    redis = FakeRedis()
    service = _TierFairShareService(pool_policy=_pool_policy())
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")

    for _ in range(4):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-1"),
            tokens=0,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )
    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-2"),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )
    await upsert_temporary_capacity_boost(
        redis_client=redis,
        pool_key="shared",
        callable_key="gpt-4o-mini",
        organization_id="org-1",
        weight_multiplier=2.0,
        ttl_seconds=60,
        reason="pilot",
    )

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-1"),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )


@pytest.mark.asyncio
async def test_capacity_dashboard_reports_usage_and_boosts() -> None:
    redis = FakeRedis()
    pool_policy = _pool_policy()
    service = _TierFairShareService(pool_policy=pool_policy)
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-1"),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )
    await upsert_temporary_capacity_boost(
        redis_client=redis,
        pool_key="shared",
        callable_key="gpt-4o-mini",
        organization_id="org-1",
        weight_multiplier=2.0,
        ttl_seconds=60,
        reason="pilot",
    )
    snapshot = replace(
        empty_tier_policy_snapshot(),
        capacity_pool_policy=MappingProxyType({("shared", "gpt-4o-mini"): pool_policy}),
        capacity_pool_count=1,
    )

    dashboard = await build_tier_capacity_dashboard(
        tier_policy_service=_SnapshotService(snapshot),
        redis_client=redis,
    )

    assert dashboard["pools"][0]["rpm_used"] == 1
    assert dashboard["pools"][0]["active_org_count"] == 1
    assert dashboard["pools"][0]["active_boost_count"] == 1
    assert dashboard["pools"][0]["active_boosts"][0]["organization_id"] == "org-1"


@pytest.mark.asyncio
async def test_capacity_dashboard_bounds_active_boost_metadata_reads() -> None:
    class _BoostTrackingRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.hgetall_keys: list[str] = []
            self.hmget_calls: list[tuple[str, list[str]]] = []
            self.zrangebyscore_calls: list[tuple[str, int | None, int | None]] = []

        async def hgetall(self, key: str):
            self.hgetall_keys.append(key)
            return await super().hgetall(key)

        async def hmget(self, key: str, fields):
            normalized_fields = list(fields)
            self.hmget_calls.append((key, normalized_fields))
            return await super().hmget(key, normalized_fields)

        async def zrangebyscore(
            self,
            key: str,
            min_score: int,
            max_score: str,
            start: int | None = None,
            num: int | None = None,
        ):
            self.zrangebyscore_calls.append((key, start, num))
            return await super().zrangebyscore(key, min_score, max_score, start=start, num=num)

    redis = _BoostTrackingRedis()
    pool_policy = _pool_policy()
    snapshot = replace(
        empty_tier_policy_snapshot(),
        capacity_pool_policy=MappingProxyType({("shared", "gpt-4o-mini"): pool_policy}),
        capacity_pool_count=1,
    )
    now_ms = 1_800_000_000_000
    expires_at_ms = now_ms + 60_000
    index_key = fair_share_boost_index_key(pool_key="shared", callable_key="gpt-4o-mini")
    metadata_key = fair_share_boost_metadata_key(pool_key="shared", callable_key="gpt-4o-mini")
    await redis.zadd(index_key, {f"org-{index:02d}": expires_at_ms for index in range(30)})
    await redis.hset(
        metadata_key,
        {
            f"org-{index:02d}": f"weight_multiplier=2.0|reason=pilot|expires_at_ms={expires_at_ms}"
            for index in range(30)
        },
    )

    dashboard = await build_tier_capacity_dashboard(
        tier_policy_service=_SnapshotService(snapshot),
        redis_client=redis,
        now=datetime.fromtimestamp(now_ms / 1000, tz=UTC),
    )

    pool = dashboard["pools"][0]
    assert pool["active_boost_count"] == 30
    assert len(pool["active_boosts"]) == 25
    assert redis.zrangebyscore_calls == [(index_key, 0, 25)]
    assert redis.hmget_calls == [(metadata_key, [f"org-{index:02d}" for index in range(25)])]
    assert metadata_key not in redis.hgetall_keys


@pytest.mark.asyncio
async def test_fair_share_active_cleanup_is_bounded_and_removes_weights() -> None:
    redis = FakeRedis()
    service = _TierFairShareService(pool_policy=_pool_policy(rpm_capacity=1_000))
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    active_key = fair_share_active_key("shared", "gpt-4o-mini")
    weight_key = fair_share_weight_key("shared", "gpt-4o-mini")
    count_key = fair_share_active_count_key("shared", "gpt-4o-mini")
    total_weight_key = fair_share_total_weight_key("shared", "gpt-4o-mini")
    stale_count = FAIR_SHARE_ACTIVE_CLEANUP_LIMIT + 36
    await redis.zadd(active_key, {f"stale-{index}": 1 for index in range(stale_count)})
    await redis.hset(
        weight_key,
        {f"stale-{index}": str(FAIR_SHARE_WEIGHT_SCALE) for index in range(stale_count)},
    )
    redis.store[count_key] = str(stale_count)
    redis.store[total_weight_key] = str(stale_count * FAIR_SHARE_WEIGHT_SCALE)

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-active"),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )

    remaining_stale = [member for _score, member in redis.zset_store[active_key] if member.startswith("stale-")]
    assert len(remaining_stale) == stale_count - FAIR_SHARE_ACTIVE_CLEANUP_LIMIT
    assert len([member for member in redis.hash_store[weight_key] if member.startswith("stale-")]) == len(remaining_stale)


@pytest.mark.asyncio
async def test_cleanup_lag_bypasses_weighted_share_but_keeps_pool_cap() -> None:
    redis = FakeRedis()
    service = _TierFairShareService(pool_policy=_pool_policy(rpm_capacity=2, saturation_threshold=0.0))
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    active_key = fair_share_active_key("shared", "gpt-4o-mini")
    weight_key = fair_share_weight_key("shared", "gpt-4o-mini")
    count_key = fair_share_active_count_key("shared", "gpt-4o-mini")
    total_weight_key = fair_share_total_weight_key("shared", "gpt-4o-mini")
    stale_count = (FAIR_SHARE_ACTIVE_CLEANUP_LIMIT * 2) + 36
    await redis.zadd(active_key, {f"stale-{index}": 1 for index in range(stale_count)})
    await redis.hset(
        weight_key,
        {f"stale-{index}": str(FAIR_SHARE_WEIGHT_SCALE) for index in range(stale_count)},
    )
    redis.store[count_key] = str(stale_count)
    redis.store[total_weight_key] = str(stale_count * FAIR_SHARE_WEIGHT_SCALE)

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-active"),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )
    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-active"),
        tokens=0,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
        tier_capacity_fair_share_enabled=True,
    )

    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-active"),
            tokens=0,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_capacity_fair_share_enabled=True,
        )

    assert exc_info.value.param == "tier_pool_fair_share_rpm"
    assert getattr(exc_info.value, "tier_fair_share_decision").reason == "pool_capacity_exceeded"
    assert redis.store[fair_share_cleanup_lag_key("shared", "gpt-4o-mini")]


@pytest.mark.asyncio
async def test_capacity_dashboard_top_orgs_uses_ranked_top_n() -> None:
    redis = FakeRedis()
    pool_policy = _pool_policy(rpm_capacity=100)
    service = _TierFairShareService(pool_policy=pool_policy)
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    for organization_id, request_count in (("org-a", 3), ("org-b", 2), ("org-c", 1)):
        for _ in range(request_count):
            await acquire_rate_limit_controls(
                limiter=limiter,
                auth=_auth(organization_id=organization_id),
                tokens=0,
                model="gpt-4o-mini",
                tier_policy_service=service,
                tier_policy_mode="enforce",
                tier_capacity_fair_share_enabled=True,
            )
    snapshot = replace(
        empty_tier_policy_snapshot(),
        capacity_pool_policy=MappingProxyType({("shared", "gpt-4o-mini"): pool_policy}),
        capacity_pool_count=1,
    )

    dashboard = await build_tier_capacity_dashboard(
        tier_policy_service=_SnapshotService(snapshot),
        redis_client=redis,
        top_org_limit=2,
    )

    assert [row["organization_id"] for row in dashboard["pools"][0]["top_orgs"]] == ["org-a", "org-b"]


@pytest.mark.asyncio
async def test_capacity_dashboard_ranks_before_applying_pool_limit() -> None:
    redis = FakeRedis()
    cold_pool = _pool_policy(rpm_capacity=100)
    warm_pool = replace(cold_pool, pool_key="warm", source_pool_ids=("pool-warm",))
    hot_pool = replace(cold_pool, pool_key="zz-hot", source_pool_ids=("pool-hot",))
    await redis.hincrby(
        fair_share_limit_hit_heatmap_key(),
        "zz-hot|gpt-4o-mini|org-hot|tier_pool_fair_share_rpm|growth",
        5,
    )
    snapshot = replace(
        empty_tier_policy_snapshot(),
        capacity_pool_policy=MappingProxyType(
            {
                ("shared", "gpt-4o-mini"): cold_pool,
                ("warm", "gpt-4o-mini"): warm_pool,
                ("zz-hot", "gpt-4o-mini"): hot_pool,
            }
        ),
        capacity_pool_count=3,
    )

    dashboard = await build_tier_capacity_dashboard(
        tier_policy_service=_SnapshotService(snapshot),
        redis_client=redis,
        pool_limit=1,
    )

    assert dashboard["pools"][0]["pool_key"] == "zz-hot"
    assert dashboard["total_pool_count"] == 3
    assert dashboard["truncated"] is True


@pytest.mark.asyncio
async def test_capacity_dashboard_reports_aggregate_counts_when_truncated() -> None:
    redis = FakeRedis()
    cold_pool = _pool_policy(rpm_capacity=100)
    hot_pool = replace(cold_pool, pool_key="hot", rpm_capacity=10, source_pool_ids=("pool-hot",))
    hard_pool = replace(
        cold_pool,
        pool_key="hard",
        strategy="hard_cap",
        rpm_capacity=10,
        source_pool_ids=("pool-hard",),
    )
    window_id = math.floor(time.time() / 60)
    redis.store[
        fair_share_pool_counter_key(
            dimension="rpm",
            pool_key="hot",
            callable_key="gpt-4o-mini",
            window_id=window_id,
        )
    ] = "9"
    redis.store[
        static_pool_counter_key(
            scope="tier_pool_model_rpm",
            pool_key="hard",
            callable_key="gpt-4o-mini",
            window_id=window_id,
        )
    ] = "10"
    snapshot = replace(
        empty_tier_policy_snapshot(),
        capacity_pool_policy=MappingProxyType(
            {
                ("shared", "gpt-4o-mini"): cold_pool,
                ("hot", "gpt-4o-mini"): hot_pool,
                ("hard", "gpt-4o-mini"): hard_pool,
            }
        ),
        capacity_pool_count=3,
    )

    dashboard = await build_tier_capacity_dashboard(
        tier_policy_service=_SnapshotService(snapshot),
        redis_client=redis,
        now=datetime.fromtimestamp((window_id * 60) + 1, tz=UTC),
        pool_limit=1,
    )

    assert len(dashboard["pools"]) == 1
    assert dashboard["total_pool_count"] == 3
    assert dashboard["advanced_pool_count"] == 2
    assert dashboard["saturated_pool_count"] == 2
    assert dashboard["pool_limit"] == 1
    assert dashboard["truncated"] is True


@pytest.mark.asyncio
async def test_capacity_dashboard_hydrates_details_only_for_visible_ranked_pools() -> None:
    class _TrackingRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.zrevrange_keys: list[str] = []

        async def zrevrange(self, key: str, start: int, end: int):
            self.zrevrange_keys.append(key)
            return await super().zrevrange(key, start, end)

    redis = _TrackingRedis()
    cold_pool = _pool_policy(rpm_capacity=100)
    warm_pool = replace(cold_pool, pool_key="warm", source_pool_ids=("pool-warm",))
    hot_pool = replace(cold_pool, pool_key="hot", rpm_capacity=10, source_pool_ids=("pool-hot",))
    window_id = math.floor(time.time() / 60)
    redis.store[
        fair_share_pool_counter_key(
            dimension="rpm",
            pool_key="hot",
            callable_key="gpt-4o-mini",
            window_id=window_id,
        )
    ] = "9"
    snapshot = replace(
        empty_tier_policy_snapshot(),
        capacity_pool_policy=MappingProxyType(
            {
                ("shared", "gpt-4o-mini"): cold_pool,
                ("warm", "gpt-4o-mini"): warm_pool,
                ("hot", "gpt-4o-mini"): hot_pool,
            }
        ),
        capacity_pool_count=3,
    )

    dashboard = await build_tier_capacity_dashboard(
        tier_policy_service=_SnapshotService(snapshot),
        redis_client=redis,
        now=datetime.fromtimestamp((window_id * 60) + 1, tz=UTC),
        pool_limit=1,
    )

    assert dashboard["pools"][0]["pool_key"] == "hot"
    usage_rank_calls = [key for key in redis.zrevrange_keys if key.startswith("tier_fair_share:usage:")]
    assert usage_rank_calls == [
        fair_share_usage_rank_key(
            pool_key="hot",
            callable_key="gpt-4o-mini",
            window_id=window_id,
        )
    ]


@pytest.mark.asyncio
async def test_capacity_dashboard_reads_ranked_heatmap_without_full_hash_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    class _HeatmapTrackingRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.hgetall_keys: list[str] = []

        async def hgetall(self, key: str):
            self.hgetall_keys.append(key)
            return await super().hgetall(key)

    fixed_timestamp = 1_800_000_000.0
    monkeypatch.setattr("src.services.tier_fair_share_counter.time.time", lambda: fixed_timestamp)
    redis = _HeatmapTrackingRedis()
    counter = TierFairShareCounter(redis_client=redis)
    for _ in range(3):
        await counter.record_limit_hit(
            pool_key="hot",
            callable_key="gpt-4o-mini",
            organization_id="org-hot",
            scope="tier_pool_fair_share_rpm",
            tier_key="growth",
        )
    await counter.record_limit_hit(
        pool_key="warm",
        callable_key="gpt-4o-mini",
        organization_id="org-warm",
        scope="tier_pool_fair_share_rpm",
        tier_key="growth",
    )
    cold_pool = _pool_policy(rpm_capacity=100)
    hot_pool = replace(cold_pool, pool_key="hot", source_pool_ids=("pool-hot",))
    warm_pool = replace(cold_pool, pool_key="warm", source_pool_ids=("pool-warm",))
    snapshot = replace(
        empty_tier_policy_snapshot(),
        capacity_pool_policy=MappingProxyType(
            {
                ("hot", "gpt-4o-mini"): hot_pool,
                ("warm", "gpt-4o-mini"): warm_pool,
            }
        ),
        capacity_pool_count=2,
    )

    dashboard = await build_tier_capacity_dashboard(
        tier_policy_service=_SnapshotService(snapshot),
        redis_client=redis,
        now=datetime.fromtimestamp(fixed_timestamp, tz=UTC),
    )

    window_id = dashboard["window_id"]
    assert dashboard["limit_hit_count"] == 4
    assert dashboard["limit_hit_heatmap"][0]["pool_key"] == "hot"
    assert dashboard["limit_hit_heatmap"][0]["count"] == 3
    assert redis.store[fair_share_limit_hit_total_key(window_id)] == 4
    assert redis.zset_store[fair_share_limit_hit_heatmap_rank_key(window_id)]
    assert fair_share_limit_hit_heatmap_key(window_id) not in redis.hgetall_keys
