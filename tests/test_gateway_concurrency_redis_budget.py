"""Verify that qualification rejects Redis call amplification."""

from pathlib import Path

import httpx
import pytest

from tests.performance import gateway_concurrency_metrics as metrics
from tests.performance import gateway_concurrency_redis as workload


@pytest.mark.asyncio
@pytest.mark.parametrize(("routing_per_request", "passed"), [(4, True), (6, False)])
async def test_cache_bypass_redis_budget_uses_same_window_process_counters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    routing_per_request: int,
    passed: bool,
) -> None:
    real_client = httpx.AsyncClient
    scrape = 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal scrape
        del request
        requests = scrape * 10
        scrape += 1
        return httpx.Response(
            200,
            text=(
                "deltallm_request_phase_latency_seconds_count"
                '{route="chat_completions",phase="response_total",outcome="success",'
                f'response_kind="nonstream"}} {requests}\n'
                "deltallm_redis_command_round_trips_total"
                '{allocation="critical",owner="authentication",family="read",'
                f'outcome="success"}} {requests}\n'
                "deltallm_redis_command_round_trips_total"
                '{allocation="critical",owner="routing",family="lua",'
                f'outcome="success"}} {requests * routing_per_request}\n'
            ),
        )

    monkeypatch.setattr(
        metrics.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(transport), **kwargs),
    )
    recorder = metrics.MetricsRecorder(
        ["http://127.0.0.1:8000/metrics"], tmp_path / f"redis-budget-{passed}.jsonl"
    )
    async with recorder:
        await recorder.snapshot()

    evidence = workload.redis_round_trip_budget(recorder)
    distribution = recorder.counter_deltas_by_source(
        "deltallm_request_phase_latency_seconds_count",
        labels={"route": "chat_completions", "phase": "response_total"},
    )
    assert distribution == [{"source_role": "api", "source_process": 0, "delta": 20}]
    assert evidence["observed_requests"] == 20
    assert evidence["round_trips_per_request"] == 1 + routing_per_request
    assert evidence["by_owner"] == {
        "authentication": 20,
        "rate_limit": 0,
        "concurrency": 0,
        "routing": 20 * routing_per_request,
        "cache": 0,
        "mixed": 0,
        "unknown": 0,
    }
    assert evidence["by_family"] == {
        "read": 20,
        "write": 0,
        "cleanup": 0,
        "lua": 20 * routing_per_request,
        "pipeline": 0,
        "other": 0,
    }
    assert evidence["passed"] is passed


@pytest.mark.parametrize(("cache_round_trips", "passed"), [(8, True), (11, False)])
def test_cache_bypass_redis_budget_bounds_amortized_cache_maintenance(
    cache_round_trips: int,
    passed: bool,
) -> None:
    class Recorder:
        def counter_delta(self, name: str, *, labels: dict[str, str]) -> float:
            if name == "deltallm_request_phase_latency_seconds_count":
                return 1_000
            if name != "deltallm_redis_command_round_trips_total":
                return 0
            owner = labels.get("owner")
            family = labels.get("family")
            if family is not None:
                return 0
            return {
                "authentication": 1_000,
                "concurrency": 2_000,
                "routing": 3_000,
                "cache": cache_round_trips,
            }.get(owner, 0)

    evidence = workload.redis_round_trip_budget(Recorder())  # type: ignore[arg-type]

    assert evidence["core_round_trips_per_request"] == 6
    assert evidence["maintenance_round_trips_per_request"] == cache_round_trips / 1_000
    assert evidence["passed"] is passed


def test_redis_client_breakdown_separates_pool_network_and_scheduling() -> None:
    values = {
        ("deltallm_redis_allocation_acquisition_seconds_count", ()): 100,
        ("deltallm_redis_allocation_acquisition_seconds_sum", ()): 0.04,
        ("deltallm_redis_command_round_trip_seconds_count", (("outcome", "success"),)): 100,
        ("deltallm_redis_command_round_trip_seconds_sum", (("outcome", "success"),)): 0.15,
        ("deltallm_event_loop_lag_seconds_count", ()): 20,
        ("deltallm_event_loop_lag_seconds_sum", ()): 0.02,
        ("deltallm_redis_allocation_events_total", (("outcome", "acquired"),)): 100,
    }

    class Recorder:
        def counter_delta(self, name: str, *, labels: dict[str, str] | None = None) -> float:
            return values.get((name, tuple(sorted((labels or {}).items()))), 0)

    evidence = workload.redis_client_breakdown(Recorder())  # type: ignore[arg-type]

    assert evidence["acquisition_mean_ms"] == pytest.approx(0.4)
    assert evidence["round_trip_success_mean_ms"] == pytest.approx(1.5)
    assert evidence["network_server_residual_mean_ms"] == pytest.approx(1.1)
    assert evidence["event_loop_lag_mean_ms"] == pytest.approx(1.0)
    assert evidence["acquisition_outcomes"]["acquired"] == 100
    assert evidence["passed"] is True
