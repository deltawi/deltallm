from prometheus_client import Gauge, Histogram

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
