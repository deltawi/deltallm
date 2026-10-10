"""Check the same-window Redis budget for the fixed cache-bypass workload."""

from tests.performance.gateway_concurrency_metrics import MetricsRecorder

CACHE_BYPASS_REDIS_CORE_ROUND_TRIP_BUDGET = 6.0
# Keep the accepted finite snapshot-edge and amortized cache-refresh allowances.
CACHE_BYPASS_REDIS_MEASUREMENT_TOLERANCE = 0.01
CACHE_BYPASS_REDIS_MAINTENANCE_ROUND_TRIP_BUDGET = 0.01
REDIS_DATA_PATH_OWNERS = (
    "authentication",
    "rate_limit",
    "concurrency",
    "routing",
    "cache",
    "mixed",
    "unknown",
)
REDIS_COMMAND_FAMILIES = ("read", "write", "cleanup", "lua", "pipeline", "other")


def redis_round_trip_budget(recorder: MetricsRecorder) -> dict[str, object]:
    requests = recorder.counter_delta(
        "deltallm_request_phase_latency_seconds_count",
        labels={"route": "chat_completions", "phase": "response_total"},
    )
    by_owner = {
        owner: recorder.counter_delta(
            "deltallm_redis_command_round_trips_total", labels={"owner": owner}
        )
        for owner in REDIS_DATA_PATH_OWNERS
    }
    by_family = {
        family: sum(
            recorder.counter_delta(
                "deltallm_redis_command_round_trips_total",
                labels={"family": family, "owner": owner},
            )
            for owner in REDIS_DATA_PATH_OWNERS
        )
        for family in REDIS_COMMAND_FAMILIES
    }
    round_trips = sum(by_owner.values())
    per_request = round_trips / requests if requests > 0 else None
    maintenance_round_trips = by_owner["cache"]
    core_round_trips = round_trips - maintenance_round_trips
    core_per_request = core_round_trips / requests if requests > 0 else None
    maintenance_per_request = maintenance_round_trips / requests if requests > 0 else None
    maximum = (
        CACHE_BYPASS_REDIS_CORE_ROUND_TRIP_BUDGET
        + CACHE_BYPASS_REDIS_MEASUREMENT_TOLERANCE
        + CACHE_BYPASS_REDIS_MAINTENANCE_ROUND_TRIP_BUDGET
    )
    return {
        "profile": "cache_bypass",
        "observed_requests": requests,
        "round_trips": round_trips,
        "round_trips_per_request": per_request,
        "core_round_trips": core_round_trips,
        "core_round_trips_per_request": core_per_request,
        "target_core_round_trips_per_request": CACHE_BYPASS_REDIS_CORE_ROUND_TRIP_BUDGET,
        "measurement_window_tolerance_per_request": (CACHE_BYPASS_REDIS_MEASUREMENT_TOLERANCE),
        "maximum_core_round_trips_per_request": (
            CACHE_BYPASS_REDIS_CORE_ROUND_TRIP_BUDGET + CACHE_BYPASS_REDIS_MEASUREMENT_TOLERANCE
        ),
        "maintenance_round_trips": maintenance_round_trips,
        "maintenance_round_trips_per_request": maintenance_per_request,
        "maximum_maintenance_round_trips_per_request": (
            CACHE_BYPASS_REDIS_MAINTENANCE_ROUND_TRIP_BUDGET
        ),
        "maximum_round_trips_per_request": maximum,
        "by_owner": by_owner,
        "by_family": by_family,
        "passed": (
            core_per_request is not None
            and core_per_request
            <= CACHE_BYPASS_REDIS_CORE_ROUND_TRIP_BUDGET + CACHE_BYPASS_REDIS_MEASUREMENT_TOLERANCE
            and maintenance_per_request is not None
            and maintenance_per_request <= CACHE_BYPASS_REDIS_MAINTENANCE_ROUND_TRIP_BUDGET
        ),
    }


def redis_client_breakdown(recorder: MetricsRecorder) -> dict[str, object]:
    """Separate client pool acquisition from command and event-loop elapsed time."""

    acquisition_count = recorder.counter_delta(
        "deltallm_redis_allocation_acquisition_seconds_count"
    )
    acquisition_seconds = recorder.counter_delta(
        "deltallm_redis_allocation_acquisition_seconds_sum"
    )
    round_trip_count = recorder.counter_delta(
        "deltallm_redis_command_round_trip_seconds_count",
        labels={"outcome": "success"},
    )
    round_trip_seconds = recorder.counter_delta(
        "deltallm_redis_command_round_trip_seconds_sum",
        labels={"outcome": "success"},
    )
    event_loop_count = recorder.counter_delta("deltallm_event_loop_lag_seconds_count")
    event_loop_seconds = recorder.counter_delta("deltallm_event_loop_lag_seconds_sum")
    outcomes = {
        outcome: recorder.counter_delta(
            "deltallm_redis_allocation_events_total",
            labels={"outcome": outcome},
        )
        for outcome in ("acquired", "full", "deadline", "cancelled", "unavailable")
    }
    residual_seconds = max(0.0, round_trip_seconds - acquisition_seconds)
    return {
        "acquisition_count": acquisition_count,
        "acquisition_seconds": acquisition_seconds,
        "acquisition_mean_ms": (
            1000.0 * acquisition_seconds / acquisition_count if acquisition_count else None
        ),
        "round_trip_success_count": round_trip_count,
        "round_trip_success_seconds": round_trip_seconds,
        "round_trip_success_mean_ms": (
            1000.0 * round_trip_seconds / round_trip_count if round_trip_count else None
        ),
        "network_server_residual_seconds": residual_seconds,
        "network_server_residual_mean_ms": (
            1000.0 * residual_seconds / round_trip_count if round_trip_count else None
        ),
        "event_loop_lag_count": event_loop_count,
        "event_loop_lag_seconds": event_loop_seconds,
        "event_loop_lag_mean_ms": (
            1000.0 * event_loop_seconds / event_loop_count if event_loop_count else None
        ),
        "acquisition_outcomes": outcomes,
        "passed": (
            round_trip_count > 0
            and outcomes["full"] == 0
            and outcomes["deadline"] == 0
            and outcomes["cancelled"] == 0
            and outcomes["unavailable"] == 0
        ),
    }
