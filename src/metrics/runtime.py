from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry
from src.metrics.request_phases import REQUEST_PHASE_BUCKETS

event_loop_lag = Histogram(
    "deltallm_event_loop_lag_seconds",
    "Delay of the process-owned one-second event-loop sampler",
    buckets=REQUEST_PHASE_BUCKETS,
    registry=get_prometheus_registry(),
)
event_loop_last_lag = Gauge(
    "deltallm_event_loop_last_lag_seconds",
    "Most recent event-loop scheduling delay",
    registry=get_prometheus_registry(),
)
event_loop_samplers = Gauge(
    "deltallm_event_loop_samplers",
    "Active lifecycle-owned event-loop samplers; normally one per application process",
    registry=get_prometheus_registry(),
)
metrics_snapshot_generation_seconds = Histogram(
    "deltallm_metrics_snapshot_generation_seconds",
    "Time spent encoding one process-local Prometheus snapshot",
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    registry=get_prometheus_registry(),
)
metrics_snapshot_generations = Counter(
    "deltallm_metrics_snapshot_generations_total",
    "Process-local Prometheus snapshot generation outcomes",
    ("outcome",),
    registry=get_prometheus_registry(),
)
metrics_snapshot_timestamp = Gauge(
    "deltallm_metrics_snapshot_timestamp_seconds",
    "Unix timestamp represented by the latest completed Prometheus snapshot",
    registry=get_prometheus_registry(),
)
metrics_snapshot_bytes = Gauge(
    "deltallm_metrics_snapshot_bytes",
    "Encoded bytes in the latest completed Prometheus snapshot",
    registry=get_prometheus_registry(),
)
python_gc_pause_seconds = Histogram(
    "deltallm_python_gc_pause_seconds",
    "Stop-the-world Python garbage collection duration by bounded generation",
    ("generation",),
    buckets=(
        0.0001,
        0.00025,
        0.0005,
        0.001,
        0.0025,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1,
        2.5,
        5,
    ),
    registry=get_prometheus_registry(),
)
