from __future__ import annotations

from types import SimpleNamespace
from time import perf_counter
from typing import Any

import pytest
from pydantic import BaseModel

from src.batch.policy import BatchPolicyLease, acquire_batch_policy_lease
from src.batch import worker_persistence as worker_persistence_module
from src.batch.worker_persistence import WorkerPersistenceMixin
from src.middleware.rate_limit import _release_rate_limits
from src.models.errors import RateLimitError, ServiceUnavailableError
from src.models.responses import UserAPIKeyAuth
from src.rate_limit_lease_refresh import RateLimitLeaseRefresher
from src.rate_limit_release_retry import RateLimitReleaseRetryQueue
from src.rate_limit_policy import RateLimitLease, acquire_rate_limit_controls, build_rate_limit_checks, release_rate_limit_controls
from src.services.limit_counter import (
    FairShareLimit,
    LegacyParallelLease,
    LimitCounter,
    ParallelLimitCheck,
    ParallelLimitLease,
    RateLimitCheck,
)
from src.services.tier_policy_models import (
    CompiledTierCapacityPoolPolicy,
    CompiledTierRateLimitDescriptor,
)
from src.services.tier_policy_service import resolve_tier_policy_unavailable_decision
from tests.conftest import FakeRedis


class _TierRateLimitService:
    def __init__(
        self,
        *,
        descriptors: dict[tuple[str, str], tuple[CompiledTierRateLimitDescriptor, ...]] | None = None,
        model_policies: dict[tuple[str, str], Any] | None = None,
        pool_policies: dict[tuple[str, str], CompiledTierCapacityPoolPolicy] | None = None,
        allowed_by_org: dict[str, set[str]] | None = None,
        explicit_orgs: set[str] | None = None,
        mode: str = "enforce",
        missing_service_mode: str = "fail_open",
        snapshot_stale: bool = False,
        fail_lookup: bool = False,
    ) -> None:
        self.descriptors = descriptors or {}
        self.model_policies = model_policies or {}
        self.pool_policies = pool_policies or {}
        self.allowed_by_org = {
            org_id: frozenset(models)
            for org_id, models in (allowed_by_org or {}).items()
        }
        self.explicit_orgs = set(explicit_orgs or self.allowed_by_org or {"org-1"})
        self.mode = mode
        self.missing_service_mode = missing_service_mode
        self.snapshot_stale = snapshot_stale
        self.fail_lookup = fail_lookup

    def has_explicit_tier_policy(self, organization_id: str | None) -> bool:
        return str(organization_id or "").strip() in self.explicit_orgs

    def resolve_org_allowed_callable_keys(self, organization_id: str | None) -> frozenset[str] | None:
        normalized = str(organization_id or "").strip()
        if normalized not in self.explicit_orgs:
            return None
        return self.allowed_by_org.get(normalized, frozenset({"gpt-4o-mini"}))

    def resolve_unavailable_decision(self, organization_id: str | None) -> object:
        return resolve_tier_policy_unavailable_decision(
            self,
            organization_id,
            mode=self.mode,
            missing_service_mode=self.missing_service_mode,
        )

    def get_rate_limit_descriptors(
        self,
        organization_id: str | None,
        callable_key: str | None,
    ) -> tuple[CompiledTierRateLimitDescriptor, ...]:
        if self.fail_lookup:
            raise RuntimeError("snapshot lookup failed")
        return self.descriptors.get((str(organization_id), str(callable_key)), ())

    def get_model_policy(self, organization_id: str | None, callable_key: str | None) -> object:
        if self.fail_lookup:
            raise RuntimeError("snapshot lookup failed")
        return self.model_policies.get(
            (str(organization_id), str(callable_key)),
            SimpleNamespace(access_mode="allow", capacity_pool_key=None),
        )

    def get_capacity_pool_policy(
        self,
        pool_key: str | None,
        callable_key: str | None,
    ) -> CompiledTierCapacityPoolPolicy | None:
        if self.fail_lookup:
            raise RuntimeError("snapshot lookup failed")
        return self.pool_policies.get((str(pool_key), str(callable_key)))

    def snapshot_info(self) -> object:
        return SimpleNamespace(etag="test-tier-snapshot", org_count=len(self.explicit_orgs))


class _FailingRedis:
    async def eval(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("redis unavailable")


class _RecordingLimitCounter(LimitCounter):
    def __init__(self) -> None:
        super().__init__(redis_client=None, degraded_mode="fail_open")
        self.atomic_calls = 0
        self.seen_checks: list[list[str]] = []

    async def check_rate_limits_atomic(self, checks):
        self.atomic_calls += 1
        self.seen_checks.append([check.scope for check in checks])
        return await super().check_rate_limits_atomic(checks)


class _SnapshotMutatingLimitCounter(LimitCounter):
    def __init__(self, *, service: _TierRateLimitService, failed_scope: str) -> None:
        super().__init__(redis_client=None, degraded_mode="fail_open")
        self.service = service
        self.failed_scope = failed_scope

    async def check_rate_limits_atomic(self, checks):
        self.service.snapshot_stale = True
        self.service.missing_service_mode = "fail_closed"
        raise RateLimitError(
            message=f"Rate limit exceeded for scope '{self.failed_scope}'",
            param=self.failed_scope,
            code=f"{self.failed_scope}_exceeded",
            retry_after=60,
        )


class _SnapshotMutatingParallelLimitCounter(LimitCounter):
    def __init__(self, *, service: _TierRateLimitService, failed_scope: str = "key") -> None:
        super().__init__(redis_client=None, degraded_mode="fail_open")
        self.service = service
        self.failed_scope = failed_scope

    async def acquire_parallel(self, scope: str, entity_id: str, limit: int | None) -> None:
        del entity_id, limit
        self.service.snapshot_stale = True
        self.service.missing_service_mode = "fail_closed"
        if self.failed_scope == "key":
            raise RateLimitError(message="Parallel request limit exceeded", retry_after=1)
        raise RateLimitError(
            message=f"Parallel request limit exceeded for scope '{scope}'",
            param=self.failed_scope,
            code=f"{self.failed_scope}_parallel_exceeded",
            retry_after=1,
        )

    async def acquire_legacy_parallel_lease(
        self,
        scope: str,
        entity_id: str,
        limit: int | None,
        *,
        ttl_seconds: int = 300,
    ):
        await self.acquire_parallel(scope, entity_id, limit)
        return LegacyParallelLease(
            scope=scope,
            entity_id=entity_id,
            limit=int(limit or 1),
            backend="fallback",
            ttl_seconds=ttl_seconds,
        )

    async def acquire_parallel_leases(self, checks, *, ttl_seconds=300):
        del ttl_seconds
        failed = next((check for check in checks if check.scope == self.failed_scope), checks[0])
        await self.acquire_parallel(failed.scope, failed.entity_id, failed.limit)
        return ()


class _PoolAcquireUnavailableLimitCounter(LimitCounter):
    async def acquire_parallel(self, scope: str, entity_id: str, limit: int | None) -> None:
        if scope == "tier_pool_model_parallel":
            raise ServiceUnavailableError(message="Rate limit backend unavailable")
        await super().acquire_parallel(scope, entity_id, limit)

    async def acquire_parallel_leases(self, checks, *, ttl_seconds=300):
        if any(check.scope == "tier_pool_model_parallel" for check in checks):
            raise ServiceUnavailableError(message="Rate limit backend unavailable")
        return await super().acquire_parallel_leases(checks, ttl_seconds=ttl_seconds)


class _FlakyReleaseLimitCounter:
    def __init__(self, *, fail_scope: str, failure_count: int) -> None:
        self.fail_scope = fail_scope
        self.failure_count = failure_count
        self.release_calls: list[tuple[str, str]] = []

    async def release_parallel(self, scope: str, entity_id: str) -> None:
        self.release_calls.append((scope, entity_id))
        if scope == self.fail_scope and self.failure_count > 0:
            self.failure_count -= 1
            raise ServiceUnavailableError(message="Rate limit backend unavailable")

    async def release_legacy_parallel_lease(self, lease: LegacyParallelLease) -> None:
        await self.release_parallel(lease.scope, lease.entity_id)

    async def release_parallel_leases(self, leases) -> None:
        self.release_calls.extend((lease.scope, lease.entity_id) for lease in leases)
        if any(lease.scope == self.fail_scope for lease in leases) and self.failure_count > 0:
            self.failure_count -= 1
            raise ServiceUnavailableError(message="Rate limit backend unavailable")


class _RecordingRedis:
    def __init__(self, *, fail_release_count: int = 0) -> None:
        self.fail_release_count = fail_release_count
        self.eval_calls: list[tuple[int, tuple[object, ...]]] = []

    async def eval(self, script: str, key_count: int, *args: object) -> list[int]:
        del script
        self.eval_calls.append((key_count, args))
        if len(self.eval_calls) > 1 and self.fail_release_count > 0:
            self.fail_release_count -= 1
            raise RuntimeError("redis release unavailable")
        return [1, 0]


class _FailingOnceRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_eval = True

    async def eval(self, script: str, numkeys: int, *args):
        if self.fail_next_eval:
            self.fail_next_eval = False
            raise RuntimeError("redis unavailable")
        return await super().eval(script, numkeys, *args)


class _PolicyReleaseWorker(WorkerPersistenceMixin):
    def __init__(self, app: object) -> None:
        self.app = app


class _BatchPayload(BaseModel):
    model: str
    input: str = "hello"


def _auth(
    *,
    api_key: str = "key-1",
    organization_id: str = "org-1",
    rpm_limit: int | None = 100,
    tpm_limit: int | None = 100_000,
    max_parallel_requests: int | None = None,
) -> UserAPIKeyAuth:
    return UserAPIKeyAuth(
        api_key=api_key,
        organization_id=organization_id,
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        max_parallel_requests=max_parallel_requests,
    )


def _descriptor(
    scope: str,
    *,
    entity_id: str = "org-1:gpt-4o-mini",
    limit: int = 1,
    amount_kind: str = "requests",
    window_seconds: int = 60,
    mode: str = "sync",
) -> CompiledTierRateLimitDescriptor:
    return CompiledTierRateLimitDescriptor(
        scope=scope,
        entity_id=entity_id,
        limit=limit,
        amount_kind=amount_kind,
        window_seconds=window_seconds,
        mode=mode,
    )


def _pool_policy(
    *,
    pool_key: str = "shared",
    callable_key: str = "gpt-4o-mini",
    rpm_capacity: int | None = None,
    tpm_capacity: int | None = None,
    max_parallel_requests: int | None = None,
    strategy: str = "hard_cap",
    saturation_threshold: float | None = None,
    burst_multiplier: float | None = None,
) -> CompiledTierCapacityPoolPolicy:
    descriptors = []
    entity_id = f"{pool_key}:{callable_key}"
    if rpm_capacity is not None:
        descriptors.append(
            _descriptor(
                "tier_pool_model_rpm",
                entity_id=entity_id,
                limit=rpm_capacity,
                mode="all",
            )
        )
    if tpm_capacity is not None:
        descriptors.append(
            _descriptor(
                "tier_pool_model_tpm",
                entity_id=entity_id,
                limit=tpm_capacity,
                amount_kind="tokens",
                mode="all",
            )
        )
    return CompiledTierCapacityPoolPolicy(
        pool_key=pool_key,
        callable_key=callable_key,
        rpm_capacity=rpm_capacity,
        tpm_capacity=tpm_capacity,
        max_parallel_requests=max_parallel_requests,
        strategy=strategy,
        saturation_threshold=saturation_threshold,
        burst_multiplier=burst_multiplier,
        source_tier_version_ids=("version-1",),
        source_pool_ids=("pool-1",),
        rate_limit_descriptors=tuple(descriptors),
    )


def _parallel_lease() -> RateLimitLease:
    return RateLimitLease(
        parallel_leases=(
            ParallelLimitLease(
                scope="tier_org_model_parallel",
                entity_id="org-1:gpt-4o-mini",
                limit=1,
                token="org-token",
                backend="fallback",
            ),
            ParallelLimitLease(
                scope="tier_pool_model_parallel",
                entity_id="shared:gpt-4o-mini",
                limit=1,
                token="pool-token",
                backend="fallback",
            ),
        )
    )


@pytest.mark.asyncio
async def test_tier_org_model_rpm_is_enforced() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=1),
            )
        }
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(),
        tokens=10,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )
    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    assert exc_info.value.param == "tier_org_model_rpm"
    assert exc_info.value.code == "tier_org_model_rpm_exceeded"


@pytest.mark.asyncio
async def test_tier_org_model_tpm_is_enforced() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_tpm", limit=10, amount_kind="tokens"),
            )
        }
    )

    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=LimitCounter(redis_client=None, degraded_mode="fail_open"),
            auth=_auth(),
            tokens=11,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    assert exc_info.value.param == "tier_org_model_tpm"
    assert exc_info.value.code == "tier_org_model_tpm_exceeded"


@pytest.mark.asyncio
async def test_shared_pool_rpm_is_enforced_across_organizations() -> None:
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(access_mode="allow", capacity_pool_key="shared"),
            ("org-2", "gpt-4o-mini"): SimpleNamespace(access_mode="allow", capacity_pool_key="shared"),
        },
        pool_policies={("shared", "gpt-4o-mini"): _pool_policy(rpm_capacity=1)},
        allowed_by_org={"org-1": {"gpt-4o-mini"}, "org-2": {"gpt-4o-mini"}},
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-1", api_key="key-1"),
        tokens=10,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )
    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-2", api_key="key-2"),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    assert exc_info.value.param == "tier_pool_model_rpm"
    assert exc_info.value.code == "tier_pool_model_rpm_exceeded"


@pytest.mark.asyncio
async def test_shared_pool_tpm_is_enforced_across_organizations() -> None:
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(access_mode="allow", capacity_pool_key="shared"),
            ("org-2", "gpt-4o-mini"): SimpleNamespace(access_mode="allow", capacity_pool_key="shared"),
        },
        pool_policies={("shared", "gpt-4o-mini"): _pool_policy(tpm_capacity=10)},
        allowed_by_org={"org-1": {"gpt-4o-mini"}, "org-2": {"gpt-4o-mini"}},
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-1", api_key="key-1"),
        tokens=6,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )
    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-2", api_key="key-2"),
            tokens=6,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    assert exc_info.value.param == "tier_pool_model_tpm"
    assert exc_info.value.code == "tier_pool_model_tpm_exceeded"


@pytest.mark.asyncio
async def test_weighted_pool_allows_borrowing_then_enforces_share_at_saturation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capacity_outcomes: list[str] = []
    monkeypatch.setattr(
        "src.rate_limit_policy.record_tier_capacity_observation",
        lambda observation, *, outcome: capacity_outcomes.append(outcome),
    )
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(
                access_mode="allow",
                capacity_pool_key="shared",
                source=SimpleNamespace(assignment_weight=1, tier_key="standard"),
            ),
            ("org-2", "gpt-4o-mini"): SimpleNamespace(
                access_mode="allow",
                capacity_pool_key="shared",
                source=SimpleNamespace(assignment_weight=1, tier_key="standard"),
            ),
        },
        pool_policies={
            ("shared", "gpt-4o-mini"): _pool_policy(
                rpm_capacity=10,
                strategy="weighted_fair",
                saturation_threshold=0.8,
            )
        },
        allowed_by_org={"org-1": {"gpt-4o-mini"}, "org-2": {"gpt-4o-mini"}},
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")

    for _ in range(7):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(
                organization_id="org-1",
                api_key="key-1",
                rpm_limit=None,
                tpm_limit=None,
            ),
            tokens=1,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(
            organization_id="org-2",
            api_key="key-2",
            rpm_limit=None,
            tpm_limit=None,
        ),
        tokens=1,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )

    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(
                organization_id="org-1",
                api_key="key-1",
                rpm_limit=None,
                tpm_limit=None,
            ),
            tokens=1,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    assert exc_info.value.param == "tier_pool_model_rpm_fair_share"
    observation = exc_info.value.fair_share_observation
    assert observation.active_organizations == 2
    assert observation.share_limit == 5
    assert observation.saturated is True
    assert exc_info.value.rate_limit_state.rpm_scope == "tier_pool_model_rpm_fair_share"
    assert capacity_outcomes == (["allowed"] * 8) + ["denied"]


@pytest.mark.asyncio
async def test_weighted_pool_uses_assignment_weights_when_saturated() -> None:
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(
                access_mode="allow",
                capacity_pool_key="shared",
                source=SimpleNamespace(assignment_weight=1, tier_key="standard"),
            ),
            ("org-2", "gpt-4o-mini"): SimpleNamespace(
                access_mode="allow",
                capacity_pool_key="shared",
                source=SimpleNamespace(assignment_weight=3, tier_key="enterprise"),
            ),
        },
        pool_policies={
            ("shared", "gpt-4o-mini"): _pool_policy(
                rpm_capacity=8,
                strategy="weighted_fair",
                saturation_threshold=0.1,
            )
        },
        allowed_by_org={"org-1": {"gpt-4o-mini"}, "org-2": {"gpt-4o-mini"}},
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")

    for organization_id, api_key in (("org-1", "key-1"), ("org-2", "key-2"), ("org-1", "key-1")):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(
                organization_id=organization_id,
                api_key=api_key,
                rpm_limit=None,
                tpm_limit=None,
            ),
            tokens=1,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(
                organization_id="org-1",
                api_key="key-1",
                rpm_limit=None,
                tpm_limit=None,
            ),
            tokens=1,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    observation = exc_info.value.fair_share_observation
    assert observation.share_limit == 2
    assert observation.total_active_weight == 4


@pytest.mark.asyncio
async def test_reserved_burst_expands_share_without_bypassing_pool_hard_cap() -> None:
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")

    def check(organization_id: str, amount: int = 1) -> RateLimitCheck:
        return RateLimitCheck(
            scope="tier_pool_model_rpm",
            entity_id="reserved:gpt-4o-mini",
            limit=10,
            amount=amount,
            fair_share=FairShareLimit(
                organization_id=organization_id,
                weight=1,
                strategy="reserved_burst",
                saturation_threshold=0.5,
                burst_multiplier=2,
            ),
        )

    await limiter.check_rate_limits_atomic([check("org-1")])
    await limiter.check_rate_limits_atomic([check("org-2")])
    result = await limiter.check_rate_limits_atomic([check("org-1", amount=5)])

    observation = result.fair_share_observations[0]
    assert observation.saturated is True
    assert observation.share_limit == 10

    with pytest.raises(RateLimitError) as exc_info:
        await limiter.check_rate_limits_atomic([check("org-1", amount=4)])
    assert exc_info.value.param == "tier_pool_model_rpm"


@pytest.mark.asyncio
async def test_weighted_pool_uses_one_atomic_redis_round_trip() -> None:
    redis = _RecordingRedis()
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(
                access_mode="allow",
                capacity_pool_key="shared",
                source=SimpleNamespace(assignment_weight=1, tier_key="standard"),
            )
        },
        pool_policies={
            ("shared", "gpt-4o-mini"): _pool_policy(
                rpm_capacity=10,
                strategy="weighted_fair",
                saturation_threshold=0.8,
            )
        },
    )

    await acquire_rate_limit_controls(
        limiter=LimitCounter(redis_client=redis, degraded_mode="fail_open"),
        auth=_auth(rpm_limit=None, tpm_limit=None),
        tokens=1,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )

    assert len(redis.eval_calls) == 1
    assert redis.eval_calls[0][0] == 6


@pytest.mark.asyncio
async def test_weighted_pool_atomic_path_synthetic_benchmark() -> None:
    redis = _RecordingRedis()
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    check = RateLimitCheck(
        scope="tier_pool_model_rpm",
        entity_id="shared:gpt-4o-mini",
        limit=1_000_000,
        fair_share=FairShareLimit(
            organization_id="org-1",
            weight=1,
            tier_key="standard",
            saturation_threshold=0.8,
        ),
    )

    started = perf_counter()
    for _ in range(1_000):
        await limiter.check_rate_limits_atomic([check])
    elapsed = perf_counter() - started

    assert len(redis.eval_calls) == 1_000
    assert elapsed < 1.0


@pytest.mark.parametrize(
    ("policy_mode", "expected_tier_checks"),
    [("disabled", 0), ("shadow", 0), ("enforce", 1)],
)
def test_tier_lookup_latency_is_bounded_in_each_rollout_mode(
    policy_mode: str,
    expected_tier_checks: int,
) -> None:
    service = _TierRateLimitService(
        mode=policy_mode,
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=100),
            )
        },
    )
    auth = _auth(rpm_limit=None, tpm_limit=None)

    started = perf_counter()
    for _ in range(25_000):
        checks = build_rate_limit_checks(
            auth=auth,
            tokens=100,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode=policy_mode,
        )
    elapsed = perf_counter() - started

    assert sum(check.scope.startswith("tier_") for check in checks) == expected_tier_checks
    assert elapsed < 2


@pytest.mark.asyncio
async def test_tier_checks_are_sent_in_one_atomic_rate_limit_call() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=100),
                _descriptor("tier_org_model_tpm", limit=100_000, amount_kind="tokens"),
            )
        },
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(access_mode="allow", capacity_pool_key="shared"),
        },
        pool_policies={("shared", "gpt-4o-mini"): _pool_policy(rpm_capacity=500, tpm_capacity=500_000)},
    )
    limiter = _RecordingLimitCounter()

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(),
        tokens=25,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )

    assert limiter.atomic_calls == 1
    assert "tier_org_model_rpm" in limiter.seen_checks[0]
    assert "tier_org_model_tpm" in limiter.seen_checks[0]
    assert "tier_pool_model_rpm" in limiter.seen_checks[0]
    assert "tier_pool_model_tpm" in limiter.seen_checks[0]


@pytest.mark.asyncio
async def test_existing_key_limit_remains_hard_cap_when_tier_limit_is_higher() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=100),
            )
        }
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")
    auth = _auth(rpm_limit=1)

    await acquire_rate_limit_controls(
        limiter=limiter,
        auth=auth,
        tokens=10,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )
    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=auth,
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    assert exc_info.value.param == "key_rpm"
    assert exc_info.value.code == "key_rpm_exceeded"


@pytest.mark.asyncio
async def test_tier_model_parallel_limit_is_enforced_and_released() -> None:
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(
                access_mode="allow",
                capacity_pool_key=None,
                limits=SimpleNamespace(max_parallel_requests=1),
            ),
        },
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")

    lease, _state = await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(),
        tokens=10,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )
    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(api_key="key-2"),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
    )

    assert exc_info.value.param == "tier_org_model_parallel"
    assert exc_info.value.code == "tier_org_model_parallel_exceeded"

    await release_rate_limit_controls(limiter=limiter, lease=lease)
    lease_after_release, _state = await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(api_key="key-2"),
        tokens=10,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )
    await release_rate_limit_controls(limiter=limiter, lease=lease_after_release)


@pytest.mark.asyncio
async def test_key_parallel_limit_uses_legacy_redis_counter_for_rollout_compatibility() -> None:
    redis = FakeRedis()
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    await limiter.acquire_parallel("key", "key-1", 1)

    try:
        with pytest.raises(RateLimitError):
            await acquire_rate_limit_controls(
                limiter=limiter,
                auth=_auth(max_parallel_requests=1),
                tokens=10,
                model="gpt-4o-mini",
            )
    finally:
        await limiter.release_parallel("key", "key-1")


@pytest.mark.asyncio
async def test_legacy_parallel_lease_tracks_fallback_backend_across_redis_recovery() -> None:
    redis = _FailingOnceRedis()
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")

    lease = await limiter.acquire_legacy_parallel_lease("key", "key-1", 1)

    assert lease is not None
    assert lease.backend == "fallback"
    assert limiter._fallback_parallel["key:key-1"] == 1

    await limiter.release_legacy_parallel_lease(lease)

    assert "key:key-1" not in limiter._fallback_parallel
    assert "parallel:key:key-1" not in redis.store


@pytest.mark.asyncio
async def test_legacy_parallel_redis_release_does_not_create_negative_counter() -> None:
    redis = FakeRedis()
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    lease = LegacyParallelLease(scope="key", entity_id="key-1", limit=1, backend="redis")
    key = "parallel:key:key-1"

    redis.store[key] = 0
    await limiter.release_legacy_parallel_lease(lease)

    assert key not in redis.store

    redis.store[key] = -3
    await limiter.release_legacy_parallel_lease(lease)

    assert key not in redis.store


@pytest.mark.asyncio
async def test_legacy_parallel_lease_refresh_extends_redis_ttl() -> None:
    redis = FakeRedis()
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    lease = await limiter.acquire_legacy_parallel_lease("key", "key-1", 1, ttl_seconds=5)
    key = "parallel:key:key-1"

    assert lease is not None
    assert lease.backend == "redis"
    assert redis.ttl_store[key] == 5

    await limiter.refresh_legacy_parallel_lease(lease, ttl_seconds=30)

    assert redis.ttl_store[key] == 30
    await limiter.release_legacy_parallel_lease(lease)


@pytest.mark.asyncio
async def test_rate_limit_refresher_starts_for_legacy_key_lease_only() -> None:
    limiter = LimitCounter(redis_client=FakeRedis(), degraded_mode="fail_open")
    lease = RateLimitLease(
        legacy_parallel_lease=LegacyParallelLease(
            scope="key",
            entity_id="key-1",
            limit=1,
            backend="redis",
        )
    )
    refresher = RateLimitLeaseRefresher(limiter=limiter, lease=lease)

    assert refresher.start() is True
    await refresher.stop()


@pytest.mark.asyncio
async def test_tier_parallel_failure_releases_previously_acquired_key_slot() -> None:
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(
                access_mode="allow",
                capacity_pool_key=None,
                limits=SimpleNamespace(max_parallel_requests=1),
            ),
        },
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")
    await limiter.acquire_parallel("tier_org_model_parallel", "org-1:gpt-4o-mini", 1)

    try:
        with pytest.raises(RateLimitError):
            await acquire_rate_limit_controls(
                limiter=limiter,
                auth=_auth(max_parallel_requests=1),
                tokens=10,
                model="gpt-4o-mini",
                tier_policy_service=service,
                tier_policy_mode="enforce",
            )
        assert "key:key-1" not in limiter._fallback_parallel
    finally:
        await limiter.release_parallel("tier_org_model_parallel", "org-1:gpt-4o-mini")


@pytest.mark.asyncio
async def test_tier_pool_parallel_limit_is_shared_across_organizations() -> None:
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(access_mode="allow", capacity_pool_key="shared"),
            ("org-2", "gpt-4o-mini"): SimpleNamespace(access_mode="allow", capacity_pool_key="shared"),
        },
        pool_policies={("shared", "gpt-4o-mini"): _pool_policy(max_parallel_requests=1)},
        allowed_by_org={"org-1": {"gpt-4o-mini"}, "org-2": {"gpt-4o-mini"}},
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")

    lease, _state = await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(organization_id="org-1", api_key="key-1"),
        tokens=10,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )
    with pytest.raises(RateLimitError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(organization_id="org-2", api_key="key-2"),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    assert exc_info.value.param == "tier_pool_model_parallel"
    assert exc_info.value.code == "tier_pool_model_parallel_exceeded"
    await release_rate_limit_controls(limiter=limiter, lease=lease)


@pytest.mark.asyncio
async def test_partial_tier_parallel_acquisition_is_released_when_pool_limit_fails() -> None:
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(
                access_mode="allow",
                capacity_pool_key="shared",
                limits=SimpleNamespace(max_parallel_requests=1),
            ),
        },
        pool_policies={("shared", "gpt-4o-mini"): _pool_policy(max_parallel_requests=1)},
    )
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")
    await limiter.acquire_parallel("tier_pool_model_parallel", "shared:gpt-4o-mini", 1)

    try:
        with pytest.raises(RateLimitError) as exc_info:
            await acquire_rate_limit_controls(
                limiter=limiter,
                auth=_auth(),
                tokens=10,
                model="gpt-4o-mini",
                tier_policy_service=service,
                tier_policy_mode="enforce",
            )

        assert exc_info.value.param == "tier_pool_model_parallel"
        assert "tier_org_model_parallel:org-1:gpt-4o-mini" not in limiter._fallback_parallel
    finally:
        await limiter.release_parallel("tier_pool_model_parallel", "shared:gpt-4o-mini")

    lease, _state = await acquire_rate_limit_controls(
        limiter=limiter,
        auth=_auth(),
        tokens=10,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )
    await release_rate_limit_controls(limiter=limiter, lease=lease)


@pytest.mark.asyncio
async def test_partial_tier_parallel_acquisition_is_released_when_pool_backend_fails() -> None:
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(
                access_mode="allow",
                capacity_pool_key="shared",
                limits=SimpleNamespace(max_parallel_requests=1),
            ),
        },
        pool_policies={("shared", "gpt-4o-mini"): _pool_policy(max_parallel_requests=1)},
    )
    limiter = _PoolAcquireUnavailableLimitCounter(redis_client=None, degraded_mode="fail_open")

    with pytest.raises(ServiceUnavailableError):
        await acquire_rate_limit_controls(
            limiter=limiter,
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )

    assert "tier_org_model_parallel:org-1:gpt-4o-mini" not in limiter._fallback_parallel


@pytest.mark.asyncio
async def test_partial_release_retries_pending_parallel_leases_after_backend_failure() -> None:
    lease = _parallel_lease()
    limiter = _FlakyReleaseLimitCounter(
        fail_scope="tier_pool_model_parallel",
        failure_count=1,
    )

    with pytest.raises(ServiceUnavailableError):
        await release_rate_limit_controls(limiter=limiter, lease=lease)

    assert len(lease.pending_parallel_acquisitions) == 2
    assert limiter.release_calls == [
        ("tier_pool_model_parallel", "shared:gpt-4o-mini"),
        ("tier_org_model_parallel", "org-1:gpt-4o-mini"),
    ]

    await release_rate_limit_controls(limiter=limiter, lease=lease)
    await release_rate_limit_controls(limiter=limiter, lease=lease)

    assert lease.pending_parallel_acquisitions == ()
    assert limiter.release_calls == [
        ("tier_pool_model_parallel", "shared:gpt-4o-mini"),
        ("tier_org_model_parallel", "org-1:gpt-4o-mini"),
        ("tier_pool_model_parallel", "shared:gpt-4o-mini"),
        ("tier_org_model_parallel", "org-1:gpt-4o-mini"),
    ]


@pytest.mark.asyncio
async def test_parallel_lease_acquire_and_release_use_single_redis_round_trip_each() -> None:
    redis = _RecordingRedis()
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    checks = [
        ParallelLimitCheck(scope="key", entity_id="key-1", limit=2),
        ParallelLimitCheck(scope="tier_pool_model_parallel", entity_id="shared:gpt-4o-mini", limit=1),
    ]

    leases = await limiter.acquire_parallel_leases(checks)
    await limiter.release_parallel_leases(list(leases))

    assert len(redis.eval_calls) == 2
    assert redis.eval_calls[0][0] == 2
    assert redis.eval_calls[1][0] == 2
    assert [lease.backend for lease in leases] == ["redis", "redis"]


@pytest.mark.asyncio
async def test_parallel_lease_acquire_coalesces_duplicate_checks() -> None:
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")
    checks = [
        ParallelLimitCheck(scope="tier_pool_model_parallel", entity_id="shared:gpt-4o-mini", limit=1),
        ParallelLimitCheck(scope="tier_pool_model_parallel", entity_id="shared:gpt-4o-mini", limit=1),
    ]

    with pytest.raises(RateLimitError):
        await limiter.acquire_parallel_leases(checks)

    assert "tier_pool_model_parallel:shared:gpt-4o-mini" not in limiter._fallback_parallel


@pytest.mark.asyncio
async def test_parallel_lease_refresh_extends_redis_token_expiry() -> None:
    redis = FakeRedis()
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    leases = await limiter.acquire_parallel_leases(
        [
            ParallelLimitCheck(scope="tier_pool_model_parallel", entity_id="shared:gpt-4o-mini", limit=1),
        ],
        ttl_seconds=1,
    )
    key = "parallel_lease:tier_pool_model_parallel:shared:gpt-4o-mini"
    original_expiry = redis.zset_store[key][0][0]

    await limiter.refresh_parallel_leases(list(leases), ttl_seconds=10)

    assert redis.zset_store[key][0][0] > original_expiry
    await limiter.release_parallel_leases(list(leases))


@pytest.mark.asyncio
async def test_parallel_lease_redis_release_failure_remains_retryable() -> None:
    redis = _RecordingRedis(fail_release_count=1)
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_open")
    leases = await limiter.acquire_parallel_leases(
        [
            ParallelLimitCheck(scope="key", entity_id="key-1", limit=2),
            ParallelLimitCheck(scope="tier_pool_model_parallel", entity_id="shared:gpt-4o-mini", limit=1),
        ]
    )
    lease = RateLimitLease(parallel_leases=leases)

    with pytest.raises(ServiceUnavailableError):
        await release_rate_limit_controls(limiter=limiter, lease=lease)

    assert len(lease.pending_parallel_acquisitions) == 2

    await release_rate_limit_controls(limiter=limiter, lease=lease)

    assert lease.pending_parallel_acquisitions == ()
    assert len(redis.eval_calls) == 3


@pytest.mark.asyncio
async def test_request_rate_limit_release_retries_pending_slots_after_transient_failure() -> None:
    lease = _parallel_lease()
    limiter = _FlakyReleaseLimitCounter(
        fail_scope="tier_pool_model_parallel",
        failure_count=1,
    )
    request = SimpleNamespace(
        state=SimpleNamespace(_rate_limit_lease=lease),
        app=SimpleNamespace(state=SimpleNamespace(limit_counter=limiter)),
    )

    await _release_rate_limits(request)

    assert request.state._rate_limit_released is False
    assert len(lease.pending_parallel_acquisitions) == 2

    await _release_rate_limits(request)
    await _release_rate_limits(request)

    assert request.state._rate_limit_released is True
    assert lease.pending_parallel_acquisitions == ()
    assert limiter.release_calls == [
        ("tier_pool_model_parallel", "shared:gpt-4o-mini"),
        ("tier_org_model_parallel", "org-1:gpt-4o-mini"),
        ("tier_pool_model_parallel", "shared:gpt-4o-mini"),
        ("tier_org_model_parallel", "org-1:gpt-4o-mini"),
    ]


@pytest.mark.asyncio
async def test_request_rate_limit_release_keeps_retrying_pending_slots() -> None:
    lease = _parallel_lease()
    limiter = _FlakyReleaseLimitCounter(
        fail_scope="tier_pool_model_parallel",
        failure_count=10,
    )
    request = SimpleNamespace(
        state=SimpleNamespace(_rate_limit_lease=lease),
        app=SimpleNamespace(state=SimpleNamespace(limit_counter=limiter)),
    )

    await _release_rate_limits(request)
    await _release_rate_limits(request)

    assert request.state._rate_limit_released is False
    assert len(lease.pending_parallel_acquisitions) == 2
    assert limiter.release_calls == [
        ("tier_pool_model_parallel", "shared:gpt-4o-mini"),
        ("tier_org_model_parallel", "org-1:gpt-4o-mini"),
        ("tier_pool_model_parallel", "shared:gpt-4o-mini"),
        ("tier_org_model_parallel", "org-1:gpt-4o-mini"),
    ]


@pytest.mark.asyncio
async def test_request_rate_limit_release_failure_is_queued_for_retry() -> None:
    lease = _parallel_lease()
    limiter = _FlakyReleaseLimitCounter(
        fail_scope="tier_pool_model_parallel",
        failure_count=1,
    )
    retry_queue = RateLimitReleaseRetryQueue(
        delay_seconds=lambda attempt_count: 0.0,
        auto_start=False,
    )
    request = SimpleNamespace(
        state=SimpleNamespace(_rate_limit_lease=lease),
        app=SimpleNamespace(
            state=SimpleNamespace(
                limit_counter=limiter,
                rate_limit_release_retry_queue=retry_queue,
            )
        ),
    )

    await _release_rate_limits(request)

    assert request.state._rate_limit_released is False
    assert retry_queue.pending_count == 1
    assert len(lease.pending_parallel_acquisitions) == 2

    await retry_queue.drain_due()

    assert retry_queue.pending_count == 0
    assert lease.pending_parallel_acquisitions == ()


@pytest.mark.asyncio
async def test_rate_limit_release_retry_queue_waits_for_backoff() -> None:
    lease = _parallel_lease()
    limiter = _FlakyReleaseLimitCounter(
        fail_scope="tier_pool_model_parallel",
        failure_count=0,
    )
    retry_queue = RateLimitReleaseRetryQueue(
        delay_seconds=lambda attempt_count: 30.0,
        auto_start=False,
    )

    assert retry_queue.enqueue(limiter=limiter, lease=lease)
    await retry_queue.drain_due()

    assert retry_queue.pending_count == 1
    assert limiter.release_calls == []
    assert len(lease.pending_parallel_acquisitions) == 2


def test_rate_limit_release_retry_queue_ignores_empty_leases() -> None:
    retry_queue = RateLimitReleaseRetryQueue(auto_start=False)
    limiter = _FlakyReleaseLimitCounter(
        fail_scope="tier_pool_model_parallel",
        failure_count=0,
    )

    assert retry_queue.enqueue(limiter=limiter, lease=RateLimitLease())

    assert retry_queue.pending_count == 0


@pytest.mark.asyncio
async def test_batch_policy_release_failure_is_queued_for_retry(monkeypatch) -> None:
    monkeypatch.setattr(worker_persistence_module, "_policy_release_retry_delay_seconds", lambda attempt_count: 0.0)
    lease = BatchPolicyLease(rate_limit_lease=_parallel_lease())
    limiter = _FlakyReleaseLimitCounter(
        fail_scope="tier_pool_model_parallel",
        failure_count=1,
    )
    worker = _PolicyReleaseWorker(
        SimpleNamespace(state=SimpleNamespace(limit_counter=limiter)),
    )
    prepared = SimpleNamespace(policy_lease=lease)

    await worker._release_prepared_policy_lease(prepared)

    assert prepared.policy_lease is None
    assert len(worker._policy_release_retry_queue()) == 1
    assert len(lease.rate_limit_lease.pending_parallel_acquisitions) == 2

    await worker._drain_policy_lease_release_retries()

    assert len(worker._policy_release_retry_queue()) == 0
    assert lease.rate_limit_lease.pending_parallel_acquisitions == ()


@pytest.mark.asyncio
async def test_batch_policy_release_retry_waits_for_backoff() -> None:
    lease = BatchPolicyLease(rate_limit_lease=_parallel_lease())
    limiter = _FlakyReleaseLimitCounter(
        fail_scope="tier_pool_model_parallel",
        failure_count=2,
    )
    worker = _PolicyReleaseWorker(
        SimpleNamespace(state=SimpleNamespace(limit_counter=limiter)),
    )
    prepared = SimpleNamespace(policy_lease=lease, policy_lease_refresher=None)

    await worker._release_prepared_policy_lease(prepared)
    release_calls_after_initial_failure = list(limiter.release_calls)
    await worker._drain_policy_lease_release_retries()

    assert len(worker._policy_release_retry_queue()) == 1
    assert limiter.release_calls == release_calls_after_initial_failure


def test_build_rate_limit_checks_filters_by_tier_mode_and_request_mode() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=10, mode="sync"),
                _descriptor("tier_org_model_batch_rpm", limit=5, mode="batch"),
            )
        },
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(access_mode="allow", capacity_pool_key="shared"),
        },
        pool_policies={("shared", "gpt-4o-mini"): _pool_policy(rpm_capacity=20)},
    )

    sync_scopes = {
        check.scope
        for check in build_rate_limit_checks(
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            mode="sync",
        )
    }
    batch_scopes = {
        check.scope
        for check in build_rate_limit_checks(
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            mode="batch",
        )
    }
    shadow_scopes = {
        check.scope
        for check in build_rate_limit_checks(
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=_TierRateLimitService(
                descriptors=service.descriptors,
                mode="shadow",
            ),
            tier_policy_mode="shadow",
            mode="sync",
        )
    }

    assert "tier_org_model_rpm" in sync_scopes
    assert "tier_org_model_batch_rpm" not in sync_scopes
    assert "tier_pool_model_rpm" in sync_scopes
    assert "tier_org_model_batch_rpm" in batch_scopes
    assert "tier_org_model_rpm" not in batch_scopes
    assert "tier_pool_model_rpm" in batch_scopes
    assert "tier_org_model_rpm" not in shadow_scopes


def test_batch_tier_limits_fall_back_to_sync_rpm_tpm_when_batch_overrides_absent() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=10, mode="sync"),
                _descriptor("tier_org_model_tpm", limit=100, amount_kind="tokens", mode="sync"),
                _descriptor("tier_org_model_rph", limit=1_000, window_seconds=3600, mode="sync"),
            )
        }
    )

    scopes = {
        check.scope
        for check in build_rate_limit_checks(
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            mode="batch",
        )
    }

    assert "tier_org_model_rpm" in scopes
    assert "tier_org_model_tpm" in scopes
    assert "tier_org_model_rph" not in scopes


def test_batch_tier_limits_use_batch_override_per_dimension() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=10, mode="sync"),
                _descriptor("tier_org_model_tpm", limit=100, amount_kind="tokens", mode="sync"),
                _descriptor("tier_org_model_batch_tpm", limit=50, amount_kind="tokens", mode="batch"),
            )
        }
    )

    scopes = {
        check.scope
        for check in build_rate_limit_checks(
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            mode="batch",
        )
    }

    assert "tier_org_model_rpm" in scopes
    assert "tier_org_model_tpm" not in scopes
    assert "tier_org_model_batch_tpm" in scopes


def test_build_rate_limit_checks_skips_pool_for_denied_tier_model_policy() -> None:
    service = _TierRateLimitService(
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(access_mode="deny", capacity_pool_key="shared"),
        },
        pool_policies={("shared", "gpt-4o-mini"): _pool_policy(rpm_capacity=1)},
    )

    scopes = {
        check.scope
        for check in build_rate_limit_checks(
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )
    }

    assert "tier_pool_model_rpm" not in scopes


@pytest.mark.asyncio
async def test_tier_snapshot_stale_fail_closed_blocks_rate_limits() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=1),
            )
        },
        missing_service_mode="fail_closed",
        snapshot_stale=True,
    )

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=LimitCounter(redis_client=None, degraded_mode="fail_open"),
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
            tier_policy_missing_service_mode="fail_closed",
        )

    assert exc_info.value.code == "tier_policy_unavailable_snapshot_stale"


@pytest.mark.asyncio
async def test_tier_lookup_failure_follows_missing_service_mode() -> None:
    fail_open_service = _TierRateLimitService(fail_lookup=True, missing_service_mode="fail_open")
    await acquire_rate_limit_controls(
        limiter=LimitCounter(redis_client=None, degraded_mode="fail_open"),
        auth=_auth(),
        tokens=10,
        model="gpt-4o-mini",
        tier_policy_service=fail_open_service,
        tier_policy_mode="enforce",
        tier_policy_missing_service_mode="fail_open",
    )

    fail_closed_service = _TierRateLimitService(
        fail_lookup=True,
        missing_service_mode="fail_closed",
    )
    with pytest.raises(ServiceUnavailableError) as exc_info:
        await acquire_rate_limit_controls(
            limiter=LimitCounter(redis_client=None, degraded_mode="fail_open"),
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=fail_closed_service,
            tier_policy_mode="enforce",
            tier_policy_missing_service_mode="fail_closed",
        )

    assert exc_info.value.code == "tier_policy_unavailable_fail_closed"


@pytest.mark.asyncio
async def test_redis_degraded_mode_applies_to_tier_checks() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=1),
            )
        }
    )
    fail_open_limiter = LimitCounter(redis_client=_FailingRedis(), degraded_mode="fail_open")

    await acquire_rate_limit_controls(
        limiter=fail_open_limiter,
        auth=_auth(),
        tokens=10,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )
    with pytest.raises(RateLimitError) as rate_exc:
        await acquire_rate_limit_controls(
            limiter=fail_open_limiter,
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )
    assert rate_exc.value.param == "tier_org_model_rpm"

    fail_closed_limiter = LimitCounter(redis_client=_FailingRedis(), degraded_mode="fail_closed")
    with pytest.raises(ServiceUnavailableError):
        await acquire_rate_limit_controls(
            limiter=fail_closed_limiter,
            auth=_auth(),
            tokens=10,
            model="gpt-4o-mini",
            tier_policy_service=service,
            tier_policy_mode="enforce",
        )


@pytest.mark.asyncio
async def test_batch_policy_lease_uses_batch_tier_limits() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=100, mode="sync"),
                _descriptor("tier_org_model_batch_rpm", limit=1, mode="batch"),
            )
        }
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            limit_counter=LimitCounter(redis_client=None, degraded_mode="fail_open"),
            tier_policy_service=service,
            app_config=None,
            settings=None,
        )
    )
    payload = _BatchPayload(model="gpt-4o-mini")

    assert await acquire_batch_policy_lease(app=app, payload=payload, auth=_auth()) is not None
    with pytest.raises(RateLimitError) as exc_info:
        await acquire_batch_policy_lease(app=app, payload=payload, auth=_auth())

    assert exc_info.value.param == "tier_org_model_batch_rpm"


@pytest.mark.asyncio
async def test_batch_policy_lease_falls_back_to_sync_tier_limit_when_batch_limit_absent() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=1, mode="sync"),
            )
        }
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            limit_counter=LimitCounter(redis_client=None, degraded_mode="fail_open"),
            tier_policy_service=service,
            app_config=None,
            settings=None,
        )
    )
    payload = _BatchPayload(model="gpt-4o-mini")

    assert await acquire_batch_policy_lease(app=app, payload=payload, auth=_auth()) is not None
    with pytest.raises(RateLimitError) as exc_info:
        await acquire_batch_policy_lease(app=app, payload=payload, auth=_auth())

    assert exc_info.value.param == "tier_org_model_rpm"


@pytest.mark.asyncio
async def test_tier_rate_limit_429_uses_tier_scope_headers(client, test_app) -> None:
    test_app.state.tier_policy_service = _TierRateLimitService(
        descriptors={
            ("org-default", "gpt-4o-mini"): (
                _descriptor(
                    "tier_org_model_rpm",
                    entity_id="org-default:gpt-4o-mini",
                    limit=1,
                ),
            )
        },
        allowed_by_org={"org-default": {"gpt-4o-mini"}},
        explicit_orgs={"org-default"},
    )

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}

    ok = await client.post("/v1/chat/completions", headers=headers, json=body)
    blocked = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert ok.status_code == 200
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "tier_org_model_rpm_exceeded"
    assert blocked.json()["error"]["param"] == "tier_org_model_rpm"
    assert "tier_org_model_rpm" in blocked.headers["x-deltallm-ratelimit-scope"].split(",")


@pytest.mark.asyncio
async def test_tier_rate_limit_429_uses_original_checks_when_snapshot_changes(client, test_app) -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-default", "gpt-4o-mini"): (
                _descriptor(
                    "tier_org_model_rpm",
                    entity_id="org-default:gpt-4o-mini",
                    limit=1,
                ),
            )
        },
        allowed_by_org={"org-default": {"gpt-4o-mini"}},
        explicit_orgs={"org-default"},
    )
    test_app.state.tier_policy_service = service
    test_app.state.limit_counter = _SnapshotMutatingLimitCounter(
        service=service,
        failed_scope="tier_org_model_rpm",
    )

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "tier_org_model_rpm_exceeded"
    assert "tier_org_model_rpm" in response.headers["x-deltallm-ratelimit-scope"].split(",")


@pytest.mark.asyncio
async def test_parallel_429_uses_original_state_when_snapshot_changes(client, test_app) -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-default", "gpt-4o-mini"): (
                _descriptor(
                    "tier_org_model_rpm",
                    entity_id="org-default:gpt-4o-mini",
                    limit=100,
                ),
            )
        },
        allowed_by_org={"org-default": {"gpt-4o-mini"}},
        explicit_orgs={"org-default"},
    )
    test_app.state.tier_policy_service = service
    test_app.state.limit_counter = _SnapshotMutatingParallelLimitCounter(service=service)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 429
    assert response.json()["error"]["type"] == "rate_limit_error"
    assert response.headers["x-deltallm-ratelimit-scope"] == "key"


def test_tier_rate_limit_check_construction_uses_compiled_tier_and_pool_checks() -> None:
    service = _TierRateLimitService(
        descriptors={
            ("org-1", "gpt-4o-mini"): (
                _descriptor("tier_org_model_rpm", limit=100),
                _descriptor("tier_org_model_tpm", limit=100_000, amount_kind="tokens"),
                _descriptor("tier_org_model_rph", limit=1_000, window_seconds=3600),
                _descriptor("tier_org_model_tpd", limit=1_000_000, amount_kind="tokens", window_seconds=86400),
            )
        },
        model_policies={
            ("org-1", "gpt-4o-mini"): SimpleNamespace(access_mode="allow", capacity_pool_key="shared"),
        },
        pool_policies={("shared", "gpt-4o-mini"): _pool_policy(rpm_capacity=500, tpm_capacity=500_000)},
    )
    auth = _auth()

    checks = build_rate_limit_checks(
        auth=auth,
        tokens=250,
        model="gpt-4o-mini",
        tier_policy_service=service,
        tier_policy_mode="enforce",
    )
    tier_checks = [check for check in checks if check.scope.startswith("tier_")]

    assert [check.scope for check in tier_checks] == [
        "tier_org_model_rpm",
        "tier_org_model_tpm",
        "tier_org_model_rph",
        "tier_org_model_tpd",
        "tier_pool_model_rpm",
        "tier_pool_model_tpm",
    ]
    assert tier_checks[-1].entity_id == "shared:gpt-4o-mini"
