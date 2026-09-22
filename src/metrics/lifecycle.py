"""Fixed process phases and bootstrap component names; no request/tenant labels."""

from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry

_registry = get_prometheus_registry()
shutdown_phases = Histogram(
    "deltallm_shutdown_phase_seconds",
    "Elapsed process shutdown phases",
    ["phase"],
    registry=_registry,
    buckets=(0.01, 0.1, 1, 5, 10, 20, 45, 80),
)
forced_exit_intent = Counter(
    "deltallm_shutdown_forced_exit_intent_total",
    "Processes retaining unfinished cleanup at a shutdown cutoff",
    registry=_registry,
)
process_state = Gauge(
    "deltallm_process_state", "Current process lifecycle state", ["state"], registry=_registry
)
readiness_probes = Counter(
    "deltallm_readiness_probes_total",
    "Dependency readiness samples",
    ["component", "outcome"],
    registry=_registry,
)
readiness_seconds = Histogram(
    "deltallm_readiness_refresh_seconds",
    "Owned readiness refresh duration",
    registry=_registry,
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 2),
)
cleanup_seconds = Histogram(
    "deltallm_shutdown_cleanup_seconds",
    "Owned cleanup duration",
    ["phase"],
    registry=_registry,
    buckets=(0.01, 0.1, 1, 5, 10, 20, 45, 80),
)
cleanup_events = Counter(
    "deltallm_shutdown_cleanup_total",
    "Cleanup completion or timeout",
    ["phase", "outcome"],
    registry=_registry,
)
