from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.batch.backpressure import BatchModelGroupDeferral
from src.batch.models import BatchModelBacklogRecord, BatchModelInFlightRecord
from src.batch.scheduling import BatchModelCapacityConfig, BatchModelCapacityResolver
from src.router.router import Deployment


class _Repository:
    def __init__(
        self,
        *,
        backlog: list[BatchModelBacklogRecord],
        in_flight: list[BatchModelInFlightRecord] | None = None,
    ) -> None:
        self.backlog = backlog
        self.in_flight = in_flight or []
        self.backlog_calls = 0
        self.in_flight_calls = 0

    async def list_model_group_backlog(self) -> list[BatchModelBacklogRecord]:
        self.backlog_calls += 1
        return self.backlog

    async def list_model_group_in_flight(self) -> list[BatchModelInFlightRecord]:
        self.in_flight_calls += 1
        return self.in_flight


class _State:
    def __init__(
        self,
        *,
        unhealthy: set[str] | None = None,
        cooled_down: set[str] | None = None,
        usage: dict[str, dict[str, int]] | None = None,
    ) -> None:
        self.unhealthy = unhealthy or set()
        self.cooled_down = cooled_down or set()
        self.usage = usage or {}

    async def get_health_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, str]]:
        return {
            deployment_id: {"healthy": "false" if deployment_id in self.unhealthy else "true"}
            for deployment_id in deployment_ids
        }

    async def get_cooldown_batch(self, deployment_ids: list[str]) -> dict[str, bool]:
        return {deployment_id: deployment_id in self.cooled_down for deployment_id in deployment_ids}

    async def get_usage_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, int]]:
        return {deployment_id: self.usage.get(deployment_id, {}) for deployment_id in deployment_ids}


class _FailingState(_State):
    async def get_health_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, str]]:
        del deployment_ids
        raise RuntimeError("router state unavailable")


class _Backpressure:
    def __init__(self, deferrals: dict[str, BatchModelGroupDeferral]) -> None:
        self.deferrals = deferrals

    async def get_model_group_deferral(self, model_group: str):
        return self.deferrals.get(model_group)


def _backlog(model_group: str, *, queued_work_units: int = 20, oldest: datetime | None = None):
    return BatchModelBacklogRecord(
        model_group=model_group,
        service_tier="standard",
        size_class="s",
        queued_jobs=1,
        queued_work_units=queued_work_units,
        oldest_queue_entered_at=oldest or datetime(2026, 1, 1, tzinfo=UTC),
    )


def _deployment(
    deployment_id: str,
    *,
    model_group: str,
    model_info: dict | None = None,
    params: dict | None = None,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
) -> Deployment:
    return Deployment(
        deployment_id=deployment_id,
        model_name=model_group,
        deltallm_params=params or {},
        model_info=model_info or {},
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
    )


async def _snapshot(
    *,
    deployment: Deployment | None,
    config: BatchModelCapacityConfig | None = None,
    state: _State | None = None,
    backpressure: _Backpressure | None = None,
):
    model_group = deployment.model_name if deployment is not None else "missing-model"
    router = SimpleNamespace(
        deployment_registry={model_group: [deployment]} if deployment is not None else {},
        state=state or _State(),
    )
    resolver = BatchModelCapacityResolver(
        repository=_Repository(backlog=[_backlog(model_group)]),
        config=config or BatchModelCapacityConfig(enabled=True),
        router=router,
        backpressure=backpressure,
    )
    snapshots = await resolver.build_snapshots()
    assert len(snapshots) == 1
    return snapshots[0]


@pytest.mark.asyncio
async def test_model_capacity_metadata_wins_over_defaults() -> None:
    snapshot = await _snapshot(
        deployment=_deployment(
            "dep-1",
            model_group="embeddings-small",
            model_info={"batch_capacity": {"max_in_flight": 32, "max_claim_work_units": 128}},
        ),
    )

    assert snapshot.max_in_flight_items == 32
    assert snapshot.max_claim_work_units == 128
    assert snapshot.capacity_source == "model_metadata"


@pytest.mark.asyncio
async def test_model_capacity_fraction_is_applied_to_router_limit() -> None:
    snapshot = await _snapshot(
        deployment=_deployment(
            "dep-1",
            model_group="chat-small",
            params={"chat_batching": {"max_in_flight": 100}},
        ),
        config=BatchModelCapacityConfig(enabled=True, capacity_fraction=0.25),
    )

    assert snapshot.max_in_flight_items == 25
    assert snapshot.capacity_source == "router_limit"


@pytest.mark.asyncio
async def test_rpm_remaining_caps_available_in_flight_slots() -> None:
    snapshot = await _snapshot(
        deployment=_deployment(
            "dep-1",
            model_group="chat-small",
            model_info={"batch_capacity": {"max_in_flight": 16}},
            rpm_limit=10,
        ),
        state=_State(usage={"dep-1": {"rpm": 9}}),
    )

    assert snapshot.rpm_remaining == 1
    assert snapshot.max_in_flight_items == 16
    assert snapshot.available_in_flight_items == 1
    assert snapshot.reason is None


@pytest.mark.asyncio
async def test_tpm_remaining_caps_available_work_units() -> None:
    snapshot = await _snapshot(
        deployment=_deployment(
            "dep-1",
            model_group="chat-small",
            model_info={"batch_capacity": {"max_in_flight": 16, "max_claim_work_units": 64}},
            tpm_limit=100,
        ),
        state=_State(usage={"dep-1": {"tpm": 92}}),
    )

    assert snapshot.tpm_remaining == 8
    assert snapshot.max_claim_work_units == 64
    assert snapshot.available_work_units == 8
    assert snapshot.reason is None


@pytest.mark.asyncio
async def test_zero_rpm_remaining_blocks_with_explicit_reason() -> None:
    snapshot = await _snapshot(
        deployment=_deployment(
            "dep-1",
            model_group="chat-small",
            model_info={"batch_capacity": {"max_in_flight": 16}},
            rpm_limit=10,
        ),
        state=_State(usage={"dep-1": {"rpm": 10}}),
    )

    assert snapshot.available_in_flight_items == 0
    assert snapshot.available_work_units == 0
    assert snapshot.reason == "rpm_exhausted"


@pytest.mark.asyncio
async def test_zero_tpm_remaining_blocks_with_explicit_reason() -> None:
    snapshot = await _snapshot(
        deployment=_deployment(
            "dep-1",
            model_group="chat-small",
            model_info={"batch_capacity": {"max_in_flight": 16}},
            tpm_limit=100,
        ),
        state=_State(usage={"dep-1": {"tpm": 100}}),
    )

    assert snapshot.available_in_flight_items == 0
    assert snapshot.available_work_units == 0
    assert snapshot.reason == "tpm_exhausted"


@pytest.mark.asyncio
async def test_unknown_rpm_and_tpm_do_not_reduce_configured_capacity() -> None:
    snapshot = await _snapshot(
        deployment=_deployment(
            "dep-1",
            model_group="chat-small",
            model_info={"batch_capacity": {"max_in_flight": 16, "max_claim_work_units": 64}},
        ),
    )

    assert snapshot.rpm_remaining is None
    assert snapshot.tpm_remaining is None
    assert snapshot.available_in_flight_items == 16
    assert snapshot.available_work_units == 20


@pytest.mark.asyncio
async def test_unknown_model_capacity_blocks_when_fail_closed() -> None:
    snapshot = await _snapshot(
        deployment=_deployment("dep-1", model_group="embeddings-small"),
        config=BatchModelCapacityConfig(enabled=True, fail_open=False),
    )

    assert snapshot.max_in_flight_items == 0
    assert snapshot.available_in_flight_items == 0
    assert snapshot.reason == "unknown_capacity"


@pytest.mark.asyncio
async def test_unknown_model_capacity_uses_default_when_fail_open() -> None:
    snapshot = await _snapshot(
        deployment=_deployment("dep-1", model_group="embeddings-small"),
        config=BatchModelCapacityConfig(
            enabled=True,
            fail_open=True,
            default_model_max_in_flight=16,
            default_model_max_claim_work_units=64,
        ),
    )

    assert snapshot.max_in_flight_items == 16
    assert snapshot.max_claim_work_units == 64
    assert snapshot.available_in_flight_items == 16
    assert snapshot.capacity_source == "default"
    assert snapshot.reason is None


@pytest.mark.asyncio
async def test_fail_open_does_not_allow_unknown_model_group() -> None:
    router = SimpleNamespace(deployment_registry={}, state=_State())
    resolver = BatchModelCapacityResolver(
        repository=_Repository(backlog=[_backlog("missing-model")]),
        config=BatchModelCapacityConfig(enabled=True, fail_open=True),
        router=router,
    )

    snapshots = await resolver.build_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0].max_in_flight_items == 0
    assert snapshots[0].available_in_flight_items == 0
    assert snapshots[0].capacity_source == "unknown"
    assert snapshots[0].reason == "unknown_model_group"


@pytest.mark.asyncio
async def test_fail_open_does_not_allow_unavailable_router_health_state() -> None:
    snapshot = await _snapshot(
        deployment=_deployment(
            "dep-1",
            model_group="embeddings-small",
            model_info={"batch_capacity": {"max_in_flight": 16}},
        ),
        config=BatchModelCapacityConfig(enabled=True, fail_open=True),
        state=_FailingState(),
    )

    assert snapshot.max_in_flight_items == 0
    assert snapshot.available_in_flight_items == 0
    assert snapshot.capacity_source == "unknown"
    assert snapshot.reason == "health_state_unavailable"


@pytest.mark.asyncio
async def test_no_healthy_deployments_returns_zero_availability() -> None:
    snapshot = await _snapshot(
        deployment=_deployment(
            "dep-1",
            model_group="embeddings-small",
            model_info={"batch_capacity": {"max_in_flight": 16}},
        ),
        state=_State(unhealthy={"dep-1"}),
    )

    assert snapshot.healthy_deployments == 0
    assert snapshot.available_in_flight_items == 0
    assert snapshot.reason == "no_healthy_deployments"


@pytest.mark.asyncio
async def test_backpressure_deferral_returns_zero_availability() -> None:
    snapshot = await _snapshot(
        deployment=_deployment(
            "dep-1",
            model_group="embeddings-small",
            model_info={"batch_capacity": {"max_in_flight": 16}},
        ),
        backpressure=_Backpressure(
            {
                "embeddings-small": BatchModelGroupDeferral(
                    model_group="embeddings-small",
                    reason="no_healthy_deployments",
                    until_epoch_seconds=1_800_000_000,
                )
            }
        ),
    )

    assert snapshot.available_in_flight_items == 0
    assert snapshot.available_work_units == 0
    assert snapshot.backpressure_until == datetime.fromtimestamp(1_800_000_000, tz=UTC)
    assert snapshot.reason == "no_healthy_deployments"


@pytest.mark.asyncio
async def test_select_model_group_skips_saturated_oldest_model() -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 1, 2, tzinfo=UTC)
    repository = _Repository(
        backlog=[
            _backlog("model-a", oldest=older),
            _backlog("model-b", oldest=newer),
        ],
        in_flight=[
            BatchModelInFlightRecord(
                model_group="model-a",
                service_tier="standard",
                in_flight_items=1,
                in_flight_work_units=1,
            )
        ],
    )
    router = SimpleNamespace(
        deployment_registry={
            "model-a": [
                _deployment("dep-a", model_group="model-a", model_info={"batch_capacity": {"max_in_flight": 1}})
            ],
            "model-b": [
                _deployment("dep-b", model_group="model-b", model_info={"batch_capacity": {"max_in_flight": 1}})
            ],
        },
        state=_State(),
    )
    resolver = BatchModelCapacityResolver(
        repository=repository,
        config=BatchModelCapacityConfig(enabled=True),
        router=router,
    )

    selection = await resolver.select_model_group(max_items=10, max_work_units=20)

    assert selection is not None
    assert selection.snapshot.model_group == "model-b"
    assert selection.max_items == 1


@pytest.mark.asyncio
async def test_select_model_groups_returns_all_eligible_models_in_fifo_order() -> None:
    oldest = datetime(2026, 1, 1, tzinfo=UTC)
    middle = datetime(2026, 1, 2, tzinfo=UTC)
    newest = datetime(2026, 1, 3, tzinfo=UTC)
    repository = _Repository(
        backlog=[
            _backlog("model-c", oldest=newest),
            _backlog("model-b", oldest=middle),
            _backlog("model-a", oldest=oldest),
        ]
    )
    router = SimpleNamespace(
        deployment_registry={
            "model-a": [
                _deployment("dep-a", model_group="model-a", model_info={"batch_capacity": {"max_in_flight": 4}})
            ],
            "model-b": [
                _deployment("dep-b", model_group="model-b", model_info={"batch_capacity": {"max_in_flight": 4}})
            ],
            "model-c": [
                _deployment("dep-c", model_group="model-c", model_info={"batch_capacity": {"max_in_flight": 4}})
            ],
        },
        state=_State(),
    )
    resolver = BatchModelCapacityResolver(
        repository=repository,
        config=BatchModelCapacityConfig(enabled=True),
        router=router,
    )

    selections = await resolver.select_model_groups(max_items=10, max_work_units=20)

    assert [selection.snapshot.model_group for selection in selections] == [
        "model-a",
        "model-b",
        "model-c",
    ]
    assert [selection.max_items for selection in selections] == [4, 4, 4]


@pytest.mark.asyncio
async def test_select_model_groups_uses_quota_capped_limits() -> None:
    repository = _Repository(backlog=[_backlog("model-a", queued_work_units=100)])
    router = SimpleNamespace(
        deployment_registry={
            "model-a": [
                _deployment(
                    "dep-a",
                    model_group="model-a",
                    model_info={"batch_capacity": {"max_in_flight": 16, "max_claim_work_units": 64}},
                    rpm_limit=10,
                    tpm_limit=100,
                )
            ],
        },
        state=_State(usage={"dep-a": {"rpm": 8, "tpm": 85}}),
    )
    resolver = BatchModelCapacityResolver(
        repository=repository,
        config=BatchModelCapacityConfig(enabled=True),
        router=router,
    )

    selections = await resolver.select_model_groups(max_items=10, max_work_units=50)

    assert len(selections) == 1
    assert selections[0].max_items == 2
    assert selections[0].max_work_units == 15


@pytest.mark.asyncio
async def test_build_snapshots_uses_refresh_cache_but_selection_forces_refresh() -> None:
    repository = _Repository(backlog=[_backlog("model-a")])
    router = SimpleNamespace(
        deployment_registry={
            "model-a": [
                _deployment("dep-a", model_group="model-a", model_info={"batch_capacity": {"max_in_flight": 4}})
            ]
        },
        state=_State(),
    )
    resolver = BatchModelCapacityResolver(
        repository=repository,
        config=BatchModelCapacityConfig(enabled=True, refresh_seconds=30),
        router=router,
    )

    await resolver.build_snapshots()
    await resolver.build_snapshots()
    assert repository.backlog_calls == 1
    assert repository.in_flight_calls == 1

    selections = await resolver.select_model_groups(max_items=10, max_work_units=20)

    assert [selection.snapshot.model_group for selection in selections] == ["model-a"]
    assert repository.backlog_calls == 2
    assert repository.in_flight_calls == 2
