from prometheus_client import Counter, Gauge, Histogram

external_auth_requests = Counter(
    "deltallm_external_auth_requests_total",
    "External authentication results.",
    ("operation", "outcome"),
)
external_auth_latency = Histogram(
    "deltallm_external_auth_duration_seconds",
    "External authentication duration.",
    ("operation",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1),
)
external_auth_cleanup = Counter(
    "deltallm_external_auth_cleanup_total",
    "External authentication retention cleanup results.",
    ("kind",),
)

external_auth_denials = Counter(
    "deltallm_external_auth_denials_total",
    "External authentication rejection categories.",
    ("reason",),
)
external_auth_saturation = Counter(
    "deltallm_external_auth_saturation_total",
    "Bounded external work rejected at admission.",
    ("owner", "reason"),
)
external_auth_cleanup_backlog = Gauge(
    "deltallm_external_auth_cleanup_backlog_lower_bound",
    "Expired rows in a bounded retention sample.",
    ("kind",),
)
external_auth_cleanup_oldest = Gauge(
    "deltallm_external_auth_cleanup_oldest_seconds",
    "Age of the oldest row past its retention deadline.",
    ("kind",),
)
external_key_revocation_delay = Histogram(
    "deltallm_external_key_revocation_delay_seconds",
    "Time from durable removal to worker cache enforcement.",
    buckets=(0.1, 0.5, 1, 5, 10, 30, 61, 120, 300),
)
