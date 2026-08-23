from __future__ import annotations

from prometheus_client import Counter, Histogram


deltallm_organization_deletion_claims_metric = Counter(
    "deltallm_organization_deletion_claims_total",
    "Organization deletion jobs claimed by durable workers.",
)

deltallm_organization_deletion_phase_metric = Counter(
    "deltallm_organization_deletion_phases_total",
    "Organization deletion phase executions by outcome.",
    labelnames=("phase", "outcome"),
)

deltallm_organization_deletion_phase_latency_metric = Histogram(
    "deltallm_organization_deletion_phase_latency_seconds",
    "Organization deletion phase execution latency.",
    labelnames=("phase",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

deltallm_organization_deletion_jobs_metric = Counter(
    "deltallm_organization_deletion_jobs_total",
    "Organization deletion job terminal and retry outcomes.",
    labelnames=("outcome",),
)


__all__ = [
    "deltallm_organization_deletion_claims_metric",
    "deltallm_organization_deletion_jobs_metric",
    "deltallm_organization_deletion_phase_latency_metric",
    "deltallm_organization_deletion_phase_metric",
]
