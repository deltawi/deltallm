from __future__ import annotations

from collections import Counter

import pytest

from src.router.candidates import AttemptCapacity, AttemptRejectionReason
from src.router.simulation_state import RoutingSimulationState, RoutingStateSnapshotMiss


class _SnapshotSource:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    async def get_health_batch(self, health_refs):  # noqa: ANN001, ANN201
        self.calls["health"] += 1
        return {item.deployment_id: {"healthy": "true"} for item in health_refs}

    async def get_cooldown_batch(self, health_refs):  # noqa: ANN001, ANN201
        self.calls["cooldown"] += 1
        return {item.deployment_id: False for item in health_refs}

    async def get_active_requests_batch(self, deployment_ids):  # noqa: ANN001, ANN201
        self.calls["active"] += 1
        return dict.fromkeys(deployment_ids, 2)

    async def get_usage_batch(self, deployment_ids):  # noqa: ANN001, ANN201
        self.calls["usage"] += 1
        return {deployment_id: {"rpm": 3} for deployment_id in deployment_ids}

    async def get_latency_windows_batch(self, deployment_ids, window_ms):  # noqa: ANN001, ANN201
        self.calls["latency"] += 1
        return {deployment_id: [(window_ms, 12.5)] for deployment_id in deployment_ids}


@pytest.mark.asyncio
async def test_routing_simulation_state_caches_reads_and_isolates_mutations() -> None:
    source = _SnapshotSource()
    state = RoutingSimulationState(source)  # type: ignore[arg-type]

    await state.get_health_batch(["dep-a"])
    await state.get_cooldown_batch(["dep-a"])
    await state.get_active_requests_batch(["dep-a"])
    await state.get_usage_batch(["dep-a"])
    await state.get_latency_windows_batch(["dep-a"], 60_000)
    state.freeze()

    await state.get_health_batch(["dep-a"])
    await state.get_cooldown_batch(["dep-a"])
    await state.get_active_requests_batch(["dep-a"])
    await state.get_usage_batch(["dep-a"])
    await state.get_latency_windows_batch(["dep-a"], 60_000)
    assert source.calls == Counter(
        {"health": 1, "cooldown": 1, "active": 1, "usage": 1, "latency": 1}
    )

    permit = await state.acquire_attempt("dep-a", AttemptCapacity())
    assert permit.acquired is True
    assert permit.backend == "local"
    assert await state.release_attempt(permit) == 2
    await state.increment_usage("dep-a", 100)
    await state.record_latency("dep-a", 50)
    await state.apply_manual_cooldown("dep-a", 30, "simulation")

    transition = await state.apply_health_failure(
        "dep-a",
        "simulated timeout",
        allowed_fails=0,
        cooldown_seconds=60,
    )
    assert transition.entered_cooldown is True
    rejected = await state.acquire_attempt("dep-a", AttemptCapacity())
    assert rejected.rejection_reason is AttemptRejectionReason.COOLDOWN
    state.reset_attempt_effects()
    assert (await state.acquire_attempt("dep-a", AttemptCapacity())).acquired is True

    with pytest.raises(RoutingStateSnapshotMiss, match="dep-b"):
        await state.get_health_batch(["dep-b"])
