from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest

from src.batch.models import BatchFairShareClaimResult, BatchSchedulerFlowRecord, BatchWorkRecommendation
from src.batch.scheduling import BatchModelCapacitySelection, BatchModelCapacitySnapshot
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig


def _worker(repository: object, resolver: object | None = None) -> BatchExecutorWorker:
    worker = object.__new__(BatchExecutorWorker)
    worker.repository = repository
    worker.model_capacity_resolver = resolver
    worker.config = BatchWorkerConfig(
        worker_id="rollout-sim",
        tenant_fair_share_base_quantum_work_units=16,
        tenant_fair_share_max_deficit_multiplier=8,
        tenant_max_in_flight_work_units=0,
        aging_seconds_per_work_unit=30,
        max_age_credit_work_units=1_000,
        min_large_job_claim_interval_seconds=30,
        small_job_max_work_units=100,
    )
    return worker


def _capacity_selection() -> BatchModelCapacitySelection:
    snapshot = BatchModelCapacitySnapshot(
        model_group="m1",
        service_tier="standard",
        max_in_flight_items=4,
        max_claim_work_units=8,
        available_in_flight_items=3,
        available_work_units=5,
        rpm_remaining=None,
        tpm_remaining=5,
        healthy_deployments=1,
        backpressure_until=None,
        reason=None,
        capacity_source="test",
        queued_jobs=2,
        queued_work_units=8,
        oldest_queue_entered_at=datetime(2026, 5, 14, tzinfo=UTC),
    )
    return BatchModelCapacitySelection(snapshot=snapshot, max_items=3, max_work_units=5)


class _RolloutRepository:
    def __init__(self) -> None:
        self.recommend_work_calls: list[dict[str, object]] = []
        self.recommend_fair_share_calls: list[dict[str, object]] = []
        self.claim_next_work_calls: list[dict[str, object]] = []
        self.claim_next_fair_share_work_calls: list[dict[str, object]] = []

    async def recommend_next_work(self, **kwargs: object) -> BatchWorkRecommendation:
        self.recommend_work_calls.append(kwargs)
        return BatchWorkRecommendation(
            batch_id=f"shadow-{kwargs.get('reason')}",
            endpoint="/v1/embeddings",
            model_group="m1",
            tenant_scope_type="team",
            tenant_scope_id="team-a",
            service_tier="standard",
            size_class="s",
            item_count=1,
            work_units=1,
            reason=str(kwargs.get("reason") or "work_slice"),
        )

    async def recommend_next_fair_share_flow(self, **kwargs: object) -> BatchFairShareClaimResult:
        self.recommend_fair_share_calls.append(kwargs)
        size_aware = bool(kwargs.get("size_aware_scheduling_enabled"))
        return BatchFairShareClaimResult(
            claim=None,
            result="recommended",
            flow=BatchSchedulerFlowRecord(
                flow_id="flow-team-a",
                service_tier="standard",
                model_group="m1",
                tenant_scope_type="team",
                tenant_scope_id="team-a",
                weight=1,
                quantum_work_units=16,
                deficit_work_units=16,
                active=True,
                queued_jobs=1,
                queued_work_units=1,
                in_flight_work_units=0,
                last_selected_at=None,
                last_refilled_at=None,
                created_at=None,
                updated_at=None,
                next_batch_id="shadow-smart" if size_aware else "shadow-fair-share",
                next_size_class="xl" if size_aware else "s",
                next_scheduler_rank=0.1 if size_aware else 1.0,
                next_age_credit_work_units=8 if size_aware else 0,
                next_policy_reason="aging_credit" if size_aware else "deficit",
            ),
            expected_share=1.0,
            active_flow_count=1,
            total_in_flight_work_units=0,
            recommended_batch_id="shadow-smart" if size_aware else "shadow-fair-share",
            recommended_size_class="xl" if size_aware else "s",
            recommended_scheduler_rank=0.1 if size_aware else 1.0,
            recommended_age_credit_work_units=8 if size_aware else 0,
            recommended_policy_reason="aging_credit" if size_aware else "deficit",
        )

    async def claim_next_work(self, **kwargs: object) -> None:
        self.claim_next_work_calls.append(kwargs)
        return None

    async def claim_next_fair_share_work(self, **kwargs: object) -> None:
        self.claim_next_fair_share_work_calls.append(kwargs)
        return None


class _CapacityResolver:
    def __init__(self) -> None:
        self.selection = _capacity_selection()

    async def select_model_groups(self, *, max_items: int, max_work_units: int):  # noqa: ANN201
        assert max_items > 0
        assert max_work_units > 0
        return [self.selection]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active_mode", "shadow_mode", "expected_batch_id"),
    [
        ("fifo_v1", "slice_v1", "shadow-slice_v1"),
        ("slice_v1", "model_capacity_v1", "shadow-model_capacity_v1"),
        ("model_capacity_v1", "fair_share_v1", "shadow-fair-share"),
        ("fair_share_v1", "smart_v1", "shadow-smart"),
    ],
)
async def test_rollout_shadow_stages_recommend_without_claiming(
    active_mode: str,
    shadow_mode: str,
    expected_batch_id: str,
) -> None:
    repository = _RolloutRepository()
    resolver = _CapacityResolver()
    worker = _worker(repository, resolver)

    if shadow_mode in {"fifo_v1", "slice_v1", "model_capacity_v1"}:
        recommendation = await worker._recommend_shadow_work(
            shadow_mode=shadow_mode,
            max_items=4,
            max_work_units=8,
            resolver=resolver,
        )
        actual_batch_id = recommendation.batch_id if recommendation is not None else None
    else:
        recommendation, _ = await worker._recommend_shadow_fair_share(
            resolver=resolver,
            max_items=4,
            max_work_units=8,
            size_aware_scheduling_enabled=shadow_mode == "smart_v1",
        )
        actual_batch_id = getattr(recommendation, "recommended_batch_id", None)

    assert active_mode in {"fifo_v1", "slice_v1", "model_capacity_v1", "fair_share_v1"}
    assert actual_batch_id == expected_batch_id
    assert repository.claim_next_work_calls == []
    assert repository.claim_next_fair_share_work_calls == []


@pytest.mark.asyncio
async def test_model_capacity_shadow_uses_capacity_limits_and_skips_oversized_head() -> None:
    repository = _RolloutRepository()
    resolver = _CapacityResolver()
    worker = _worker(repository, resolver)

    recommendation = await worker._recommend_shadow_work(
        shadow_mode="model_capacity_v1",
        max_items=4,
        max_work_units=8,
        resolver=resolver,
    )

    assert recommendation is not None
    assert repository.recommend_work_calls[0]["capacity_model_group"] == "m1"
    assert repository.recommend_work_calls[0]["capacity_max_in_flight_items"] == 3
    assert repository.recommend_work_calls[0]["capacity_max_in_flight_work_units"] == 5
    assert repository.recommend_work_calls[0]["allow_oversized_first_item"] is False


@pytest.mark.asyncio
async def test_smart_shadow_enables_size_aware_fair_share_ranking() -> None:
    repository = _RolloutRepository()
    worker = _worker(repository, _CapacityResolver())

    recommendation, _ = await worker._recommend_shadow_fair_share(
        resolver=worker.model_capacity_resolver,
        max_items=4,
        max_work_units=8,
        size_aware_scheduling_enabled=True,
    )

    assert recommendation is not None
    assert repository.recommend_fair_share_calls[0]["size_aware_scheduling_enabled"] is True
    assert recommendation.recommended_policy_reason == "aging_credit"
    assert recommendation.recommended_size_class == "xl"


def test_active_smart_stage_has_no_shadow_mode() -> None:
    repository = _RolloutRepository()
    worker = _worker(repository, _CapacityResolver())
    worker.config = BatchWorkerConfig(
        worker_id="rollout-sim",
        scheduler_mode="smart_v1",
        scheduler_shadow_mode="none",
        scheduler_claim_mode="work_slice",
        model_capacity_enabled=True,
        tenant_fair_share_enabled=True,
        size_aware_scheduling_enabled=True,
    )

    assert worker._active_scheduler_mode() == "smart_v1"
    assert worker._shadow_scheduler_mode() == "none"
    assert repository.recommend_work_calls == []
    assert repository.recommend_fair_share_calls == []


def test_legacy_worker_shadow_does_not_demote_active_smart_mode() -> None:
    worker = _worker(_RolloutRepository(), _CapacityResolver())
    worker.config = BatchWorkerConfig(
        worker_id="rollout-sim",
        scheduler_mode=None,
        scheduler_shadow_mode=None,
        scheduler_claim_mode="work_slice",
        model_capacity_enabled=True,
        scheduler_shadow_enabled=True,
        tenant_fair_share_enabled=True,
        size_aware_scheduling_enabled=True,
    )

    assert worker._active_scheduler_mode() == "smart_v1"
    assert worker._shadow_scheduler_mode() == "smart_v1"


@dataclass(slots=True)
class _SimJob:
    batch_id: str
    tenant: str
    model_group: str
    size_class: str
    work_units: int
    queued_at: int
    service_tier: str = "standard"
    not_before_at: int = 0
    duration_ticks: int = 1
    finalization_only: bool = False
    first_claimed_at: int | None = None
    completed_at: int | None = None
    in_progress_until: int | None = None
    claim_count: int = 0
    crashed_once: bool = False


@dataclass(slots=True)
class _SimResult:
    jobs: dict[str, _SimJob]
    claim_order: list[str]
    tenant_claims: Counter[str]
    duplicate_claims: int
    reclaim_count: int
    decision_latencies: list[int]
    model_busy_ticks: Counter[str]
    elapsed_ticks: int
    metrics: dict[str, object]


_SIM_METRIC_KEYS = {
    "time_to_first_claim",
    "completion_latency",
    "fairness_share",
    "model_utilization",
    "decision_latency",
    "duplicate_claims",
    "reclaim_count",
}


def _simulate_rollout_scheduler(
    mode: str,
    jobs: list[_SimJob],
    *,
    model_capacity: dict[str, int] | None = None,
    unhealthy_until: dict[str, int] | None = None,
    seed_in_flight: dict[str, int] | None = None,
    seed_release_at: int = 0,
    workers: int = 1,
    max_ticks: int = 80,
    crash_batch_id: str | None = None,
    redis_available: bool = True,
    postgres_contention: bool = False,
) -> _SimResult:
    state = [replace(job) for job in jobs]
    capacity = model_capacity or {job.model_group: 1 for job in state}
    unhealthy_until = unhealthy_until or {}
    seed_in_flight = seed_in_flight or {}
    claim_order: list[str] = []
    tenant_claims: Counter[str] = Counter()
    model_busy_ticks: Counter[str] = Counter()
    decision_latencies: list[int] = []
    duplicate_claims = 0
    reclaim_count = 0
    elapsed_ticks = 0

    def _model_in_flight(model_group: str, tick: int) -> int:
        seeded = seed_in_flight.get(model_group, 0) if tick < seed_release_at else 0
        active = sum(
            1
            for job in state
            if job.model_group == model_group
            and job.completed_at is None
            and job.in_progress_until is not None
            and job.in_progress_until > tick
        )
        return seeded + active

    def _eligible_jobs(tick: int) -> list[_SimJob]:
        return [
            job
            for job in state
            if not job.finalization_only
            and job.completed_at is None
            and job.in_progress_until is None
            and job.queued_at <= tick
            and job.not_before_at <= tick
            and tick >= unhealthy_until.get(job.model_group, 0)
            and _model_in_flight(job.model_group, tick) < capacity.get(job.model_group, 1)
        ]

    def _service_tier_rank(job: _SimJob) -> int:
        return {"priority": 0, "high": 0, "standard": 1, "background": 2}.get(
            job.service_tier,
            1,
        )

    def _select_job(candidates: list[_SimJob], tick: int) -> _SimJob:
        if mode == "smart_v1":
            return min(
                candidates,
                key=lambda job: (
                    _service_tier_rank(job),
                    max(1, job.work_units - max(0, tick - job.queued_at) // 4),
                    tenant_claims[job.tenant],
                    job.queued_at,
                    job.batch_id,
                ),
            )
        if mode == "fair_share_v1":
            return min(
                candidates,
                key=lambda job: (
                    tenant_claims[job.tenant],
                    _service_tier_rank(job),
                    job.queued_at,
                    job.batch_id,
                ),
            )
        return min(candidates, key=lambda job: (job.queued_at, job.batch_id))

    for tick in range(max_ticks):
        elapsed_ticks = tick + 1
        for job in state:
            if (
                job.completed_at is None
                and job.in_progress_until is not None
                and job.in_progress_until <= tick
            ):
                job.completed_at = tick
                job.in_progress_until = None

        for _ in range(workers):
            candidates = _eligible_jobs(tick)
            decision_latency = 1
            if not redis_available:
                decision_latency += 1
            if postgres_contention:
                decision_latency += 2
            decision_latencies.append(decision_latency)
            if not candidates:
                continue
            selected = _select_job(candidates, tick)
            if selected.in_progress_until is not None:
                duplicate_claims += 1
                continue
            if selected.first_claimed_at is None:
                selected.first_claimed_at = tick
            selected.claim_count += 1
            selected.in_progress_until = tick + selected.duration_ticks
            claim_order.append(selected.batch_id)
            tenant_claims[selected.tenant] += 1
            if selected.batch_id == crash_batch_id and not selected.crashed_once:
                selected.crashed_once = True
                selected.in_progress_until = None
                selected.not_before_at = tick + 1
                reclaim_count += 1

        for model_group in capacity:
            if _model_in_flight(model_group, tick) > 0:
                model_busy_ticks[model_group] += 1

        if all(job.finalization_only or job.completed_at is not None for job in state):
            break

    jobs_by_id = {job.batch_id: job for job in state}
    total_claims = sum(tenant_claims.values())
    metrics: dict[str, object] = {
        "time_to_first_claim": {
            job.batch_id: None
            if job.first_claimed_at is None
            else job.first_claimed_at - job.queued_at
            for job in state
            if not job.finalization_only
        },
        "completion_latency": {
            job.batch_id: None if job.completed_at is None else job.completed_at - job.queued_at
            for job in state
            if not job.finalization_only
        },
        "fairness_share": {
            tenant: count / total_claims for tenant, count in tenant_claims.items()
        }
        if total_claims
        else {},
        "model_utilization": {
            model_group: busy_ticks / elapsed_ticks
            for model_group, busy_ticks in model_busy_ticks.items()
        },
        "decision_latency": {
            "max": max(decision_latencies) if decision_latencies else 0,
            "samples": len(decision_latencies),
        },
        "duplicate_claims": duplicate_claims,
        "reclaim_count": reclaim_count,
    }
    return _SimResult(
        jobs=jobs_by_id,
        claim_order=claim_order,
        tenant_claims=tenant_claims,
        duplicate_claims=duplicate_claims,
        reclaim_count=reclaim_count,
        decision_latencies=decision_latencies,
        model_busy_ticks=model_busy_ticks,
        elapsed_ticks=elapsed_ticks,
        metrics=metrics,
    )


def _assert_simulation_metrics(result: _SimResult) -> None:
    assert _SIM_METRIC_KEYS <= result.metrics.keys()
    assert result.metrics["duplicate_claims"] == result.duplicate_claims
    assert result.metrics["reclaim_count"] == result.reclaim_count
    decision_latency = _metric_dict(result, "decision_latency")
    assert decision_latency["samples"] == len(result.decision_latencies)


def _metric_dict(result: _SimResult, name: str) -> dict[str, object]:
    value = result.metrics[name]
    assert isinstance(value, dict)
    return value


def test_rollout_simulation_smart_short_jobs_improve_without_starving_large_jobs() -> None:
    jobs = [
        _SimJob("large", "team-a", "m1", "xl", 50, 0, duration_ticks=3),
        _SimJob("small-1", "team-b", "m1", "s", 1, 0),
        _SimJob("small-2", "team-b", "m1", "s", 1, 0),
        _SimJob("small-3", "team-b", "m1", "s", 1, 0),
    ]

    fifo = _simulate_rollout_scheduler("fifo_v1", jobs, model_capacity={"m1": 1})
    smart = _simulate_rollout_scheduler("smart_v1", jobs, model_capacity={"m1": 1})

    _assert_simulation_metrics(fifo)
    _assert_simulation_metrics(smart)
    fifo_small_first_claim = [
        fifo.jobs[batch_id].first_claimed_at for batch_id in ["small-1", "small-2", "small-3"]
    ]
    smart_small_first_claim = [
        smart.jobs[batch_id].first_claimed_at for batch_id in ["small-1", "small-2", "small-3"]
    ]
    assert smart_small_first_claim < fifo_small_first_claim
    assert smart.jobs["large"].first_claimed_at is not None
    assert smart.jobs["large"].completed_at is not None


def test_rollout_simulation_fair_share_limits_noisy_tenant_head_start() -> None:
    jobs = [
        _SimJob("noisy-1", "team-noisy", "m1", "s", 1, 0),
        _SimJob("noisy-2", "team-noisy", "m1", "s", 1, 0),
        _SimJob("noisy-3", "team-noisy", "m1", "s", 1, 0),
        _SimJob("quiet-1", "team-quiet", "m1", "s", 1, 0),
    ]

    fifo = _simulate_rollout_scheduler("fifo_v1", jobs, model_capacity={"m1": 1})
    fair_share = _simulate_rollout_scheduler("fair_share_v1", jobs, model_capacity={"m1": 1})

    _assert_simulation_metrics(fair_share)
    assert fair_share.jobs["quiet-1"].first_claimed_at < fifo.jobs["quiet-1"].first_claimed_at
    assert set(_metric_dict(fair_share, "fairness_share")) == {"team-noisy", "team-quiet"}
    assert fair_share.duplicate_claims == 0


def test_rollout_simulation_model_capacity_keeps_idle_model_work_conserving() -> None:
    jobs = [
        _SimJob("saturated-model", "team-a", "m1", "s", 1, 0),
        _SimJob("idle-model", "team-a", "m2", "s", 1, 0),
    ]

    result = _simulate_rollout_scheduler(
        "model_capacity_v1",
        jobs,
        model_capacity={"m1": 1, "m2": 1},
        seed_in_flight={"m1": 1},
        seed_release_at=3,
    )

    _assert_simulation_metrics(result)
    assert result.claim_order[0] == "idle-model"
    assert result.jobs["saturated-model"].first_claimed_at >= 3
    assert _metric_dict(result, "model_utilization")["m2"] > 0


def test_rollout_simulation_model_health_failure_waits_for_recovery() -> None:
    jobs = [_SimJob("recovered-model", "team-a", "m1", "s", 1, 0)]

    result = _simulate_rollout_scheduler(
        "model_capacity_v1",
        jobs,
        model_capacity={"m1": 1},
        unhealthy_until={"m1": 4},
    )

    _assert_simulation_metrics(result)
    assert result.jobs["recovered-model"].first_claimed_at == 4
    assert result.jobs["recovered-model"].completed_at is not None


def test_rollout_simulation_retry_storm_honors_not_before_gate() -> None:
    jobs = [_SimJob("retry-delayed", "team-a", "m1", "s", 1, 0, not_before_at=6)]

    result = _simulate_rollout_scheduler("smart_v1", jobs, model_capacity={"m1": 1})

    _assert_simulation_metrics(result)
    assert result.jobs["retry-delayed"].first_claimed_at == 6
    assert _metric_dict(result, "time_to_first_claim")["retry-delayed"] == 6


def test_rollout_simulation_mixed_service_tiers_preserve_priority_progress() -> None:
    jobs = [
        _SimJob("standard-job", "team-a", "m1", "s", 1, 0, service_tier="standard"),
        _SimJob("priority-job", "team-b", "m1", "s", 1, 0, service_tier="priority"),
    ]

    result = _simulate_rollout_scheduler("smart_v1", jobs, model_capacity={"m1": 1})

    _assert_simulation_metrics(result)
    assert result.claim_order[:2] == ["priority-job", "standard-job"]
    assert result.jobs["standard-job"].completed_at is not None


def test_rollout_simulation_worker_crash_reclaims_without_duplicate_completion() -> None:
    jobs = [_SimJob("crashes-once", "team-a", "m1", "s", 1, 0)]

    result = _simulate_rollout_scheduler(
        "smart_v1",
        jobs,
        model_capacity={"m1": 1},
        crash_batch_id="crashes-once",
    )

    _assert_simulation_metrics(result)
    assert result.reclaim_count == 1
    assert result.duplicate_claims == 0
    assert result.jobs["crashes-once"].claim_count == 2
    assert result.jobs["crashes-once"].completed_at is not None


def test_rollout_simulation_redis_unavailable_preserves_db_backed_progress() -> None:
    jobs = [
        _SimJob("job-1", "team-a", "m1", "s", 1, 0),
        _SimJob("job-2", "team-b", "m1", "s", 1, 0),
    ]

    healthy = _simulate_rollout_scheduler(
        "fair_share_v1",
        jobs,
        model_capacity={"m1": 1},
        redis_available=True,
    )
    result = _simulate_rollout_scheduler(
        "fair_share_v1",
        jobs,
        model_capacity={"m1": 1},
        redis_available=False,
    )

    _assert_simulation_metrics(healthy)
    _assert_simulation_metrics(result)
    assert result.claim_order == ["job-1", "job-2"]
    assert all(job.completed_at is not None for job in result.jobs.values())
    assert result.duplicate_claims == 0
    assert _metric_dict(result, "decision_latency")["max"] > _metric_dict(
        healthy,
        "decision_latency",
    )["max"]


def test_rollout_simulation_postgres_contention_serializes_parallel_workers() -> None:
    jobs = [_SimJob(f"job-{index}", f"team-{index % 3}", "m1", "s", 1, 0) for index in range(8)]

    result = _simulate_rollout_scheduler(
        "fair_share_v1",
        jobs,
        model_capacity={"m1": 4},
        workers=4,
        postgres_contention=True,
    )

    _assert_simulation_metrics(result)
    assert result.duplicate_claims == 0
    assert len(result.claim_order) == len(set(result.claim_order)) == 8
    assert _metric_dict(result, "decision_latency")["max"] == 3


def test_rollout_simulation_finalization_backlog_does_not_block_claimable_work() -> None:
    jobs = [
        _SimJob(f"finalize-{index}", "team-a", "m1", "xl", 100, 0, finalization_only=True)
        for index in range(5)
    ]
    jobs.append(_SimJob("claimable", "team-b", "m1", "s", 1, 0))

    result = _simulate_rollout_scheduler("smart_v1", jobs, model_capacity={"m1": 1})

    _assert_simulation_metrics(result)
    assert result.claim_order == ["claimable"]
    assert result.jobs["claimable"].first_claimed_at == 0
    assert result.jobs["claimable"].completed_at is not None
