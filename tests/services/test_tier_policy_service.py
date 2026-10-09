from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from src.db.tiers import (
    TierModelPolicyRecord,
    TierPolicyAssignmentRecord,
    TierPolicyLoadResult,
    TierPolicyRepositoryUnavailableError,
    TierRepository,
)
from src.services.tier_policy_service import (
    TierPolicyBackendUnavailableError,
    TierPolicyService,
    resolve_tier_policy_unavailable_decision,
)


class _FakeTierPolicyRepository:
    def __init__(self, inputs: TierPolicyLoadResult) -> None:
        self.inputs = inputs
        self.calls = 0
        self.fail = False
        self.reference_times: list[datetime] = []

    async def load_active_tier_policy_inputs(
        self,
        *,
        reference_time: datetime | None = None,
    ) -> TierPolicyLoadResult:
        self.calls += 1
        if reference_time is not None:
            self.reference_times.append(reference_time)
        if self.fail:
            raise RuntimeError("reload failed")
        return self.inputs


def _inputs(
    *,
    price: float = 0.001,
    next_transition_at: datetime | None = None,
) -> TierPolicyLoadResult:
    return TierPolicyLoadResult(
        assignments=(
            TierPolicyAssignmentRecord(
                assignment_id="assignment-1",
                organization_id="org-1",
                tier_id="tier-1",
                tier_version_id=None,
                effective_tier_version_id="version-1",
                tier_key="standard",
                tier_version_number=1,
                tier_version_status="active",
            ),
        ),
        model_policies=(
            TierModelPolicyRecord(
                tier_model_policy_id="policy-1",
                tier_version_id="version-1",
                callable_key="gpt-4o-mini",
                rpm_limit=100,
                pricing={"input_cost_per_token": price},
            ),
        ),
        capacity_pools=(),
        next_transition_at=next_transition_at,
    )


async def _wait_for(
    condition: Callable[[], bool],
    *,
    timeout_seconds: float = 1.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.01)
    assert condition()


@pytest.mark.asyncio
async def test_tier_policy_service_reload_swaps_snapshot_and_serves_sync_lookups() -> None:
    generated_at = datetime(2026, 6, 20, tzinfo=UTC)
    repository = _FakeTierPolicyRepository(_inputs())
    service = TierPolicyService(
        repository=repository,
        mode="shadow",
        clock=lambda: generated_at,
    )

    snapshot = await service.reload()

    snapshot_info = service.snapshot_info()
    assert repository.calls == 1
    assert repository.reference_times == [generated_at]
    assert service.get_snapshot() is snapshot
    assert snapshot_info.etag == snapshot.etag
    assert snapshot_info.mode == "shadow"
    assert snapshot_info.next_transition_at is None
    assert snapshot_info.snapshot_stale is False
    assert snapshot_info.last_reload_failed is False
    assert snapshot_info.last_reload_error_at is None
    assert service.has_explicit_tier_policy("org-1") is True
    assert service.resolve_org_allowed_callable_keys("org-1") == frozenset({"gpt-4o-mini"})
    assert service.get_model_policy("org-1", "gpt-4o-mini").limits.rpm_limit == 100  # type: ignore[union-attr]
    assert (
        service.get_pricing_policy("org-1", "gpt-4o-mini").pricing[  # type: ignore[union-attr]
            "input_cost_per_token"
        ]
        == 0.001
    )
    assert service.get_rate_limit_descriptors("org-1", "gpt-4o-mini")[0].scope


@pytest.mark.asyncio
async def test_tier_policy_service_failed_reload_preserves_previous_snapshot() -> None:
    repository = _FakeTierPolicyRepository(_inputs(price=0.001))
    service = TierPolicyService(repository=repository, mode="shadow")
    first = await service.reload()

    repository.inputs = _inputs(price=0.002)
    repository.fail = True
    with pytest.raises(RuntimeError, match="reload failed"):
        await service.reload()

    snapshot_info = service.snapshot_info()
    assert service.get_snapshot() is first
    assert service.snapshot_stale is True
    assert service.last_reload_failed is True
    assert service.last_reload_error_at is not None
    assert snapshot_info.snapshot_stale is True
    assert snapshot_info.last_reload_failed is True
    assert snapshot_info.last_reload_error_at == service.last_reload_error_at
    assert (
        service.get_pricing_policy("org-1", "gpt-4o-mini").pricing[  # type: ignore[union-attr]
            "input_cost_per_token"
        ]
        == 0.001
    )


@pytest.mark.asyncio
async def test_tier_policy_service_handles_missing_repository_as_empty_snapshot_when_disabled() -> (
    None
):
    service = TierPolicyService(repository=None)

    snapshot = await service.reload()

    assert snapshot.org_count == 0
    assert service.resolve_org_allowed_callable_keys("org-1") is None
    assert service.get_rate_limit_descriptors("org-1", "gpt-4o-mini") == ()


@pytest.mark.asyncio
async def test_tier_policy_service_rejects_missing_repository_when_enabled() -> None:
    service = TierPolicyService(repository=None, mode="enforce")

    with pytest.raises(TierPolicyBackendUnavailableError, match="repository unavailable"):
        await service.reload()


@pytest.mark.asyncio
async def test_tier_policy_service_rejects_repository_without_loader_when_enabled() -> None:
    service = TierPolicyService(repository=object(), mode="shadow")

    with pytest.raises(TierPolicyBackendUnavailableError, match="loader unavailable"):
        await service.reload()


@pytest.mark.asyncio
async def test_tier_policy_service_rejects_repository_with_missing_database_when_enabled() -> None:
    service = TierPolicyService(repository=TierRepository(None), mode="enforce")

    with pytest.raises(TierPolicyRepositoryUnavailableError, match="database unavailable"):
        await service.reload()


@pytest.mark.asyncio
async def test_tier_policy_service_startup_reload_failure_retries_after_retry_delay() -> None:
    repository = _FakeTierPolicyRepository(_inputs(price=0.001))
    repository.fail = True
    service = TierPolicyService(
        repository=repository,
        mode="shadow",
        refresh_interval_seconds=60.0,
        refresh_jitter_seconds=0.0,
        refresh_retry_delay_seconds=0.03,
    )

    with pytest.raises(RuntimeError, match="reload failed"):
        await service.reload()

    repository.fail = False
    repository.inputs = _inputs(price=0.002)

    try:
        await service.start()
        await _wait_for(lambda: repository.calls >= 2)
    finally:
        await service.close()

    assert (
        service.get_pricing_policy("org-1", "gpt-4o-mini").pricing[  # type: ignore[union-attr]
            "input_cost_per_token"
        ]
        == 0.002
    )


@pytest.mark.asyncio
async def test_tier_policy_service_schedules_retry_from_failure_time() -> None:
    now = datetime(2026, 6, 20, tzinfo=UTC)

    class _SlowFailingRepository:
        def __init__(self) -> None:
            self.calls = 0

        async def load_active_tier_policy_inputs(
            self,
            *,
            reference_time: datetime | None = None,
        ) -> TierPolicyLoadResult:
            nonlocal now
            self.calls += 1
            now += timedelta(seconds=10)
            raise RuntimeError("reload failed")

    repository = _SlowFailingRepository()
    service = TierPolicyService(
        repository=repository,
        mode="shadow",
        refresh_interval_seconds=60.0,
        refresh_jitter_seconds=0.0,
        refresh_retry_delay_seconds=5.0,
        clock=lambda: now,
    )

    with pytest.raises(RuntimeError, match="reload failed"):
        await service.reload()

    assert repository.calls == 1
    assert service._next_refresh_delay_seconds() == 5.0


@pytest.mark.asyncio
async def test_tier_policy_service_scheduled_refresh_uses_next_transition() -> None:
    transition_at = datetime.now(tz=UTC) + timedelta(seconds=0.03)
    repository = _FakeTierPolicyRepository(_inputs(price=0.001, next_transition_at=transition_at))
    service = TierPolicyService(
        repository=repository,
        mode="shadow",
        refresh_interval_seconds=60.0,
        refresh_jitter_seconds=0.0,
        transition_grace_seconds=0.0,
    )

    await service.reload()
    repository.inputs = _inputs(price=0.002)

    try:
        await service.start()
        await _wait_for(lambda: repository.calls >= 2)
    finally:
        await service.close()

    assert repository.calls >= 2
    assert (
        service.get_pricing_policy("org-1", "gpt-4o-mini").pricing[  # type: ignore[union-attr]
            "input_cost_per_token"
        ]
        == 0.002
    )


@pytest.mark.asyncio
async def test_tier_policy_service_scheduled_reload_failure_preserves_snapshot_and_retries() -> (
    None
):
    transition_at = datetime.now(tz=UTC) + timedelta(seconds=0.03)
    repository = _FakeTierPolicyRepository(_inputs(price=0.001, next_transition_at=transition_at))
    service = TierPolicyService(
        repository=repository,
        mode="shadow",
        refresh_interval_seconds=60.0,
        refresh_jitter_seconds=0.0,
        transition_grace_seconds=0.0,
        refresh_retry_delay_seconds=0.03,
    )

    await service.reload()
    repository.fail = True

    try:
        await service.start()
        await _wait_for(lambda: repository.calls >= 2)
        assert (
            service.get_pricing_policy("org-1", "gpt-4o-mini").pricing[  # type: ignore[union-attr]
                "input_cost_per_token"
            ]
            == 0.001
        )

        repository.fail = False
        repository.inputs = _inputs(price=0.002)
        await _wait_for(lambda: repository.calls >= 3)
    finally:
        await service.close()

    assert (
        service.get_pricing_policy("org-1", "gpt-4o-mini").pricing[  # type: ignore[union-attr]
            "input_cost_per_token"
        ]
        == 0.002
    )


@pytest.mark.asyncio
async def test_tier_policy_service_snapshot_lookup_data_is_immutable() -> None:
    service = TierPolicyService(repository=_FakeTierPolicyRepository(_inputs()), mode="shadow")
    snapshot = await service.reload()

    with pytest.raises(TypeError):
        snapshot.org_model_policy[("org-1", "gpt-4o-mini")].pricing["input_cost_per_token"] = 1.0

    assert isinstance(snapshot.org_allowed_callable_keys["org-1"], frozenset)


@pytest.mark.asyncio
async def test_tier_policy_service_unavailable_decision_uses_explicit_policy_snapshot() -> None:
    service = TierPolicyService(
        repository=_FakeTierPolicyRepository(_inputs()),
        mode="enforce",
        missing_service_mode="fail_closed",
    )
    await service.reload()

    explicit_decision = service.resolve_unavailable_decision("org-1")
    unassigned_decision = service.resolve_unavailable_decision("org-2")

    assert explicit_decision.allowed is False
    assert explicit_decision.reason == "tier_policy_unavailable_fail_closed"
    assert explicit_decision.explicit_tier_policy is True
    assert unassigned_decision.allowed is True
    assert unassigned_decision.reason == "tier_policy_unavailable_no_explicit_policy"
    assert unassigned_decision.explicit_tier_policy is False


@pytest.mark.asyncio
async def test_tier_policy_service_fail_closed_blocks_when_snapshot_is_stale() -> None:
    repository = _FakeTierPolicyRepository(_inputs())
    service = TierPolicyService(
        repository=repository,
        mode="enforce",
        missing_service_mode="fail_closed",
    )
    await service.reload()

    repository.fail = True
    with pytest.raises(RuntimeError, match="reload failed"):
        await service.reload()

    explicit_decision = service.resolve_unavailable_decision("org-1")
    unassigned_decision = service.resolve_unavailable_decision("org-2")

    assert explicit_decision.allowed is False
    assert explicit_decision.reason == "tier_policy_unavailable_snapshot_stale"
    assert explicit_decision.explicit_tier_policy is True
    assert unassigned_decision.allowed is False
    assert unassigned_decision.reason == "tier_policy_unavailable_snapshot_stale"
    assert unassigned_decision.explicit_tier_policy is False


@pytest.mark.asyncio
async def test_tier_policy_service_fail_open_allows_when_snapshot_is_stale() -> None:
    repository = _FakeTierPolicyRepository(_inputs())
    service = TierPolicyService(
        repository=repository,
        mode="enforce",
        missing_service_mode="fail_open",
    )
    await service.reload()

    repository.fail = True
    with pytest.raises(RuntimeError, match="reload failed"):
        await service.reload()

    decision = service.resolve_unavailable_decision("org-2")

    assert decision.allowed is True
    assert decision.reason == "tier_policy_unavailable_fail_open"
    assert decision.explicit_tier_policy is False


def test_tier_policy_unavailable_decision_fails_open_without_service() -> None:
    decision = resolve_tier_policy_unavailable_decision(
        None,
        "org-1",
        mode="enforce",
        missing_service_mode="fail_open",
    )

    assert decision.allowed is True
    assert decision.reason == "tier_policy_unavailable_fail_open"
    assert decision.explicit_tier_policy is None


def test_tier_policy_unavailable_decision_fails_closed_without_service() -> None:
    decision = resolve_tier_policy_unavailable_decision(
        None,
        "org-1",
        mode="enforce",
        missing_service_mode="fail_closed",
    )

    assert decision.allowed is False
    assert decision.reason == "tier_policy_unavailable_fail_closed"
    assert decision.explicit_tier_policy is None
