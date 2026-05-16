from __future__ import annotations

import contextlib
from collections.abc import Iterable
from typing import Any, Mapping

from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

BATCH_LATENCY_BUCKETS = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
SCHEDULER_STATUS_METRIC_SAMPLE_LIMIT = 200

deltallm_batch_jobs_metric = Gauge(
    "deltallm_batch_jobs",
    "Current batch jobs by status",
    ["status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_create_sessions_metric = Gauge(
    "deltallm_batch_create_sessions",
    "Current batch create sessions by status",
    ["status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_items_metric = Gauge(
    "deltallm_batch_items",
    "Current batch items by status",
    ["status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_oldest_item_age_metric = Gauge(
    "deltallm_batch_oldest_item_age_seconds",
    "Age in seconds of the oldest batch item by active status",
    ["status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_worker_saturation_metric = Gauge(
    "deltallm_batch_worker_saturation_ratio",
    "Current active worker concurrency / configured concurrency",
    ["worker_id"],
    registry=get_prometheus_registry(),
)

deltallm_batch_finalization_retries_metric = Counter(
    "deltallm_batch_finalization_retries_total",
    "Batch finalization retries by result",
    ["result"],
    registry=get_prometheus_registry(),
)

deltallm_batch_artifact_failures_metric = Counter(
    "deltallm_batch_artifact_failures_total",
    "Batch artifact operation failures by operation and backend",
    ["operation", "backend"],
    registry=get_prometheus_registry(),
)

deltallm_batch_completion_outbox_failures_metric = Counter(
    "deltallm_batch_completion_outbox_failures_total",
    "Batch completion outbox failures by bounded reason",
    ["reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_repair_actions_metric = Counter(
    "deltallm_batch_repair_actions_total",
    "Batch repair actions by action and status",
    ["action", "status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_item_reclaims_metric = Counter(
    "deltallm_batch_item_reclaims_total",
    "Batch item reclaims caused by expired in-progress leases",
    registry=get_prometheus_registry(),
)

deltallm_batch_item_retries_metric = Counter(
    "deltallm_batch_item_retries_total",
    "Batch item retries scheduled by retry category",
    ["category"],
    registry=get_prometheus_registry(),
)

deltallm_batch_item_terminal_failures_metric = Counter(
    "deltallm_batch_item_terminal_failures_total",
    "Terminal batch item failures by retry category and reason",
    ["category", "reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_item_retry_delay_metric = Histogram(
    "deltallm_batch_item_retry_delay_seconds",
    "Batch item retry delay by retry category",
    ["category"],
    buckets=[1, 2, 5, 10, 30, 60, 120, 300, 600],
    registry=get_prometheus_registry(),
)

deltallm_batch_create_latency_metric = Histogram(
    "deltallm_batch_create_latency_seconds",
    "Batch create latency",
    ["status"],
    buckets=BATCH_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_batch_finalize_latency_metric = Histogram(
    "deltallm_batch_finalize_latency_seconds",
    "Batch finalization latency",
    ["status"],
    buckets=BATCH_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_batch_item_execution_latency_metric = Histogram(
    "deltallm_batch_item_execution_latency_seconds",
    "Individual batch item execution latency",
    ["status"],
    buckets=BATCH_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_batch_create_session_actions_metric = Counter(
    "deltallm_batch_create_session_actions_total",
    "Batch create-session actions by action and status",
    ["action", "status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_microbatch_requests_metric = Counter(
    "deltallm_batch_microbatch_requests_total",
    "Upstream embedding microbatch requests executed by the batch worker",
    registry=get_prometheus_registry(),
)

deltallm_batch_microbatch_inputs_metric = Counter(
    "deltallm_batch_microbatch_inputs_total",
    "Upstream embedding inputs sent through grouped batch worker microbatches",
    registry=get_prometheus_registry(),
)

deltallm_batch_microbatch_isolation_fallback_metric = Counter(
    "deltallm_batch_microbatch_isolation_fallback_total",
    "Grouped embedding microbatch chunks isolated back to single-item execution",
    registry=get_prometheus_registry(),
)

deltallm_batch_microbatch_ineligible_items_metric = Counter(
    "deltallm_batch_microbatch_ineligible_items_total",
    "Batch items that could not be grouped into embedding microbatches by reason",
    ["reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_microbatch_requeues_metric = Counter(
    "deltallm_batch_microbatch_requeues_total",
    "Grouped embedding microbatch retry decisions by retry category and result",
    ["category", "result"],
    registry=get_prometheus_registry(),
)

deltallm_batch_microbatch_retry_delay_metric = Histogram(
    "deltallm_batch_microbatch_retry_delay_seconds",
    "Grouped embedding microbatch retry delay by retry category",
    ["category"],
    buckets=[1, 2, 5, 10, 30, 60, 120, 300, 600],
    registry=get_prometheus_registry(),
)

deltallm_batch_model_group_deferrals_metric = Counter(
    "deltallm_batch_model_group_deferrals_total",
    "Model-group backpressure deferrals created by reason",
    ["reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_model_group_deferred_items_metric = Counter(
    "deltallm_batch_model_group_deferred_items_total",
    "Batch items deferred by model-group backpressure by reason",
    ["reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_model_group_deferral_seconds_metric = Histogram(
    "deltallm_batch_model_group_deferral_seconds",
    "Model-group backpressure deferral duration by reason",
    ["reason"],
    buckets=[1, 2, 5, 10, 30, 60, 120, 300, 600],
    registry=get_prometheus_registry(),
)

deltallm_batch_microbatch_size_metric = Histogram(
    "deltallm_batch_microbatch_size",
    "Number of inputs in each grouped embedding microbatch request",
    buckets=[1, 2, 4, 8, 16, 32, 64],
    registry=get_prometheus_registry(),
)

deltallm_batch_chat_items_executed_metric = Counter(
    "deltallm_batch_chat_items_executed_total",
    "Chat batch items executed by worker execution mode and status",
    ["mode", "status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_chat_microbatch_requests_metric = Counter(
    "deltallm_batch_chat_microbatch_requests_total",
    "Upstream sync chat microbatch requests executed by the batch worker",
    ["status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_chat_microbatch_fallbacks_metric = Counter(
    "deltallm_batch_chat_microbatch_fallbacks_total",
    "Chat microbatch candidates executed per-item by fallback reason",
    ["reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_chat_microbatch_size_metric = Histogram(
    "deltallm_batch_chat_microbatch_size",
    "Number of chat requests in each upstream sync chat microbatch request",
    buckets=[1, 2, 4, 8, 16, 32],
    registry=get_prometheus_registry(),
)

deltallm_batch_chat_provider_latency_metric = Histogram(
    "deltallm_batch_chat_provider_latency_seconds",
    "Upstream chat batch worker provider latency by execution mode and status",
    ["mode", "status"],
    buckets=BATCH_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_batch_policy_allowed_metric = Counter(
    "deltallm_batch_policy_allowed_total",
    "Batch items allowed by gateway policy preflight",
    ["endpoint"],
    registry=get_prometheus_registry(),
)

deltallm_batch_policy_rejected_metric = Counter(
    "deltallm_batch_policy_rejected_total",
    "Batch items rejected by gateway policy preflight",
    ["endpoint", "reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_policy_retryable_failures_metric = Counter(
    "deltallm_batch_policy_retryable_failures_total",
    "Batch policy preflight failures that are eligible for normal item retry",
    ["endpoint", "reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_preflight_latency_metric = Histogram(
    "deltallm_batch_preflight_latency_seconds",
    "Batch policy preflight latency by endpoint and status",
    ["endpoint", "status"],
    buckets=BATCH_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_batch_queue_jobs_metric = Gauge(
    "deltallm_batch_queue_jobs",
    "Current batch queue jobs by scheduler dimensions",
    ["status", "model_group", "tenant_scope_type", "service_tier", "size_class"],
    registry=get_prometheus_registry(),
)

deltallm_batch_queue_work_units_metric = Gauge(
    "deltallm_batch_queue_work_units",
    "Current batch queue remaining work units by scheduler dimensions",
    ["status", "model_group", "tenant_scope_type", "service_tier", "size_class"],
    registry=get_prometheus_registry(),
)

deltallm_batch_oldest_job_age_metric = Gauge(
    "deltallm_batch_oldest_job_age_seconds",
    "Age in seconds of the oldest batch job by scheduler dimensions",
    ["status", "model_group", "service_tier", "size_class"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_missing_dimensions_metric = Gauge(
    "deltallm_batch_scheduler_missing_dimensions_total",
    "Current nonterminal batch jobs missing scheduler dimensions",
    ["dimension"],
    registry=get_prometheus_registry(),
)

deltallm_batch_estimated_work_units_metric = Histogram(
    "deltallm_batch_estimated_work_units",
    "Estimated scheduler work units per batch job",
    buckets=[1, 2, 5, 10, 25, 50, 100, 250, 500, 1_000, 2_500, 5_000, 10_000],
    registry=get_prometheus_registry(),
)

deltallm_batch_queue_wait_metric = Histogram(
    "deltallm_batch_queue_wait_seconds",
    "Batch queue wait seconds before worker claim by scheduler dimensions",
    ["model_group", "service_tier", "size_class"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1_800, 3_600, 7_200, 21_600],
    registry=get_prometheus_registry(),
)

deltallm_batch_time_to_first_claim_metric = Histogram(
    "deltallm_batch_time_to_first_claim_seconds",
    "Batch seconds from queue entry to first worker claim by scheduler dimensions",
    ["mode", "model_group", "service_tier", "size_class"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1_800, 3_600, 7_200, 21_600],
    registry=get_prometheus_registry(),
)

deltallm_batch_completion_latency_metric = Histogram(
    "deltallm_batch_completion_latency_seconds",
    "Batch seconds from queue entry to terminal completion by scheduler mode and dimensions",
    ["mode", "model_group", "service_tier", "size_class"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1_800, 3_600, 7_200, 21_600],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_job_rank_metric = Histogram(
    "deltallm_batch_scheduler_job_rank",
    "Rank score used by size-aware batch scheduling",
    ["model_group", "service_tier", "size_class"],
    buckets=[0, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1_000, 2_500, 5_000, 10_000],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_age_credit_work_units_metric = Histogram(
    "deltallm_batch_scheduler_age_credit_work_units",
    "Age credit work units used by size-aware batch scheduling",
    ["model_group", "service_tier", "size_class"],
    buckets=[0, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1_000],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_size_claims_metric = Counter(
    "deltallm_batch_scheduler_size_claims_total",
    "Size-aware scheduler claims by size class and result",
    ["size_class", "result"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_large_job_floor_claims_metric = Counter(
    "deltallm_batch_scheduler_large_job_floor_claims_total",
    "Large job progress-floor scheduler claims by model group and service tier",
    ["model_group", "service_tier"],
    registry=get_prometheus_registry(),
)

deltallm_batch_work_claims_metric = Counter(
    "deltallm_batch_work_claims_total",
    "Batch work-slice claims by result and claim mode",
    ["result", "claim_mode"],
    registry=get_prometheus_registry(),
)

deltallm_batch_work_claim_items_metric = Histogram(
    "deltallm_batch_work_claim_items",
    "Number of batch items returned by each work claim",
    ["claim_mode"],
    buckets=[1, 2, 4, 8, 16, 32, 64, 128, 200],
    registry=get_prometheus_registry(),
)

deltallm_batch_work_claim_units_metric = Histogram(
    "deltallm_batch_work_claim_units",
    "Estimated work units returned by each work claim",
    ["claim_mode"],
    buckets=[1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1_024],
    registry=get_prometheus_registry(),
)

deltallm_batch_work_claim_latency_metric = Histogram(
    "deltallm_batch_work_claim_latency_seconds",
    "Batch work claim latency by claim mode",
    ["claim_mode"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
    registry=get_prometheus_registry(),
)

deltallm_batch_model_capacity_slots_metric = Gauge(
    "deltallm_batch_model_capacity_slots",
    "Configured batch in-flight capacity slots by model group and service tier",
    ["model_group", "service_tier", "source"],
    registry=get_prometheus_registry(),
)

deltallm_batch_model_in_flight_items_metric = Gauge(
    "deltallm_batch_model_in_flight_items",
    "Current batch in-flight items by model group and service tier",
    ["model_group", "service_tier"],
    registry=get_prometheus_registry(),
)

deltallm_batch_model_available_slots_metric = Gauge(
    "deltallm_batch_model_available_slots",
    "Available batch in-flight capacity slots by model group and service tier",
    ["model_group", "service_tier"],
    registry=get_prometheus_registry(),
)

deltallm_batch_model_backlog_work_units_metric = Gauge(
    "deltallm_batch_model_backlog_work_units",
    "Queued batch work units by model group, service tier, and size class",
    ["model_group", "service_tier", "size_class"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_model_skips_metric = Counter(
    "deltallm_batch_scheduler_model_skips_total",
    "Batch scheduler model-group skips by bounded reason",
    ["model_group", "reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_model_claims_metric = Counter(
    "deltallm_batch_scheduler_model_claims_total",
    "Batch scheduler model-group claims by result",
    ["model_group", "result"],
    registry=get_prometheus_registry(),
)

deltallm_batch_model_capacity_snapshot_failures_metric = Counter(
    "deltallm_batch_model_capacity_snapshot_failures_total",
    "Batch model-capacity snapshot failures by bounded reason",
    ["reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_model_selection_latency_metric = Histogram(
    "deltallm_batch_scheduler_model_selection_latency_seconds",
    "Batch scheduler model selection latency",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_active_flows_metric = Gauge(
    "deltallm_batch_scheduler_active_flows",
    "Active batch scheduler tenant flows by model group, service tier, and tenant scope type",
    ["model_group", "service_tier", "tenant_scope_type"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_flow_deficit_metric = Gauge(
    "deltallm_batch_scheduler_flow_deficit",
    "Batch scheduler tenant flow deficit work units",
    ["model_group", "service_tier", "tenant_scope_type"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_flow_queued_work_units_metric = Gauge(
    "deltallm_batch_scheduler_flow_queued_work_units",
    "Batch scheduler tenant flow queued work units",
    ["model_group", "service_tier", "tenant_scope_type"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_flow_in_flight_work_units_metric = Gauge(
    "deltallm_batch_scheduler_flow_in_flight_work_units",
    "Batch scheduler tenant flow in-flight work units",
    ["model_group", "service_tier", "tenant_scope_type"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_flow_claims_metric = Counter(
    "deltallm_batch_scheduler_flow_claims_total",
    "Batch scheduler tenant flow claims by result",
    ["model_group", "service_tier", "tenant_scope_type", "result"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_flow_skips_metric = Counter(
    "deltallm_batch_scheduler_flow_skips_total",
    "Batch scheduler tenant flow skips by bounded reason",
    ["reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_deficit_refills_metric = Counter(
    "deltallm_batch_scheduler_deficit_refills_total",
    "Batch scheduler tenant flow deficit refills by model group and service tier",
    ["model_group", "service_tier"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_flow_wait_metric = Histogram(
    "deltallm_batch_scheduler_flow_wait_seconds",
    "Batch scheduler tenant flow queue wait seconds",
    ["model_group", "service_tier", "tenant_scope_type"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1_800, 3_600, 7_200, 21_600],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_fairness_ratio_metric = Histogram(
    "deltallm_batch_scheduler_fairness_ratio",
    "Batch scheduler selected tenant actual share divided by expected weighted share",
    ["model_group", "service_tier"],
    buckets=[0.1, 0.25, 0.5, 0.75, 1.0, 1.25, 2.0, 4.0, 8.0],
    registry=get_prometheus_registry(),
)

deltallm_batch_claim_wait_by_model_metric = Histogram(
    "deltallm_batch_claim_wait_by_model_seconds",
    "Batch queue wait seconds before first claim by model group and service tier",
    ["model_group", "service_tier"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1_800, 3_600, 7_200, 21_600],
    registry=get_prometheus_registry(),
)

deltallm_batch_finalization_claims_metric = Counter(
    "deltallm_batch_finalization_claims_total",
    "Batch finalization job claims by result",
    ["result"],
    registry=get_prometheus_registry(),
)

deltallm_batch_claim_empty_jobs_metric = Counter(
    "deltallm_batch_claim_empty_jobs_total",
    "Batch work-slice claim attempts that found no executable item by bounded reason",
    ["reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_mixed_model_jobs_metric = Counter(
    "deltallm_batch_mixed_model_jobs_total",
    "Batch jobs with mixed scheduler model dimensions by handling mode",
    ["mode"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_shadow_decisions_metric = Counter(
    "deltallm_batch_scheduler_shadow_decisions_total",
    "Batch scheduler shadow decisions by model group, service tier, and result",
    ["model_group", "service_tier", "result"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_shadow_records_metric = Counter(
    "deltallm_batch_scheduler_shadow_records_total",
    "Batch scheduler shadow flow-state records by result",
    ["result"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_shadow_skips_metric = Counter(
    "deltallm_batch_scheduler_shadow_skips_total",
    "Batch scheduler shadow flow skips by model group, service tier, and bounded reason",
    ["model_group", "service_tier", "reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_shadow_share_ratio_metric = Histogram(
    "deltallm_batch_scheduler_shadow_share_ratio",
    "Ratio of actual shadow claim share to expected fair-share weight share",
    ["model_group", "service_tier"],
    buckets=[0.1, 0.25, 0.5, 0.75, 1.0, 1.25, 2.0, 4.0, 8.0],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_shadow_comparisons_metric = Counter(
    "deltallm_batch_scheduler_shadow_comparisons_total",
    "Batch scheduler active-vs-shadow comparisons by bounded result",
    ["active_mode", "shadow_mode", "result"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_shadow_better_choice_metric = Counter(
    "deltallm_batch_scheduler_shadow_better_choice_total",
    "Batch scheduler shadow recommendations that differed for a bounded policy reason",
    ["reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_rollbacks_metric = Counter(
    "deltallm_batch_scheduler_rollbacks_total",
    "Batch scheduler mode rollback events by mode pair and bounded reason",
    ["from_mode", "to_mode", "reason"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_mode_info_metric = Gauge(
    "deltallm_batch_scheduler_mode_info",
    "Batch scheduler active and shadow mode info gauge; value is always 1 for the current process mode",
    ["active_mode", "shadow_mode"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_oldest_wait_seconds_metric = Gauge(
    "deltallm_batch_scheduler_oldest_wait_seconds",
    "Oldest queued batch wait seconds by scheduler dimensions",
    ["model_group", "service_tier", "size_class"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_fairness_deviation_metric = Gauge(
    "deltallm_batch_scheduler_fairness_deviation",
    "Absolute deviation between tenant actual in-flight share and expected weighted share",
    ["model_group", "service_tier", "tenant_scope_type"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_decision_latency_metric = Histogram(
    "deltallm_batch_scheduler_decision_latency_seconds",
    "Batch scheduler decision latency by effective scheduler mode",
    ["mode"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_backfill_runs_metric = Counter(
    "deltallm_batch_scheduler_backfill_runs_total",
    "Batch scheduler dimension backfill runs by status",
    ["status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_backfill_rows_metric = Counter(
    "deltallm_batch_scheduler_backfill_rows_total",
    "Batch scheduler dimension rows repaired by kind",
    ["kind"],
    registry=get_prometheus_registry(),
)

deltallm_batch_scheduler_backfill_duration_metric = Histogram(
    "deltallm_batch_scheduler_backfill_duration_seconds",
    "Batch scheduler dimension backfill duration",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    registry=get_prometheus_registry(),
)


def set_batch_job_count(*, status: str, count: int) -> None:
    deltallm_batch_jobs_metric.labels(status=sanitize_label(status)).set(max(0, int(count)))


def set_batch_create_session_count(*, status: str, count: int) -> None:
    deltallm_batch_create_sessions_metric.labels(status=sanitize_label(status)).set(max(0, int(count)))


def set_batch_item_count(*, status: str, count: int) -> None:
    deltallm_batch_items_metric.labels(status=sanitize_label(status)).set(max(0, int(count)))


def set_batch_oldest_item_age(*, status: str, age_seconds: float) -> None:
    deltallm_batch_oldest_item_age_metric.labels(status=sanitize_label(status)).set(max(0.0, float(age_seconds)))


def set_batch_worker_saturation(*, worker_id: str, active: int, capacity: int) -> None:
    denominator = max(1, int(capacity))
    deltallm_batch_worker_saturation_metric.labels(worker_id=sanitize_label(worker_id)).set(
        max(0.0, float(active) / float(denominator))
    )


def increment_batch_finalization_retry(*, result: str) -> None:
    deltallm_batch_finalization_retries_metric.labels(result=sanitize_label(result)).inc()


def increment_batch_artifact_failure(*, operation: str, backend: str) -> None:
    deltallm_batch_artifact_failures_metric.labels(
        operation=sanitize_label(operation),
        backend=sanitize_label(backend),
    ).inc()


def increment_batch_completion_outbox_failure(*, reason: str) -> None:
    deltallm_batch_completion_outbox_failures_metric.labels(reason=sanitize_label(reason)).inc()


def increment_batch_repair_action(*, action: str, status: str) -> None:
    deltallm_batch_repair_actions_metric.labels(
        action=sanitize_label(action),
        status=sanitize_label(status),
    ).inc()


def increment_batch_item_reclaim() -> None:
    deltallm_batch_item_reclaims_metric.inc()


def increment_batch_item_retry(*, category: str) -> None:
    deltallm_batch_item_retries_metric.labels(category=sanitize_label(category)).inc()


def increment_batch_item_terminal_failure(*, category: str, reason: str) -> None:
    deltallm_batch_item_terminal_failures_metric.labels(
        category=sanitize_label(category),
        reason=sanitize_label(reason),
    ).inc()


def observe_batch_item_retry_delay(*, category: str, delay_seconds: float) -> None:
    deltallm_batch_item_retry_delay_metric.labels(category=sanitize_label(category)).observe(
        max(0.0, float(delay_seconds))
    )


def observe_batch_create_latency(*, status: str, latency_seconds: float) -> None:
    deltallm_batch_create_latency_metric.labels(status=sanitize_label(status)).observe(max(0.0, float(latency_seconds)))


def observe_batch_finalize_latency(*, status: str, latency_seconds: float) -> None:
    deltallm_batch_finalize_latency_metric.labels(status=sanitize_label(status)).observe(max(0.0, float(latency_seconds)))


def observe_batch_item_execution_latency(*, status: str, latency_seconds: float) -> None:
    deltallm_batch_item_execution_latency_metric.labels(status=sanitize_label(status)).observe(max(0.0, float(latency_seconds)))


def increment_batch_create_session_action(*, action: str, status: str) -> None:
    deltallm_batch_create_session_actions_metric.labels(
        action=sanitize_label(action),
        status=sanitize_label(status),
    ).inc()


def increment_batch_microbatch_requests() -> None:
    deltallm_batch_microbatch_requests_metric.inc()


def increment_batch_microbatch_inputs(*, count: int) -> None:
    deltallm_batch_microbatch_inputs_metric.inc(max(0, int(count)))


def increment_batch_microbatch_isolation_fallback() -> None:
    deltallm_batch_microbatch_isolation_fallback_metric.inc()


def increment_batch_microbatch_ineligible_item(*, reason: str) -> None:
    deltallm_batch_microbatch_ineligible_items_metric.labels(reason=sanitize_label(reason)).inc()


def increment_batch_microbatch_requeue(*, category: str, result: str) -> None:
    deltallm_batch_microbatch_requeues_metric.labels(
        category=sanitize_label(category),
        result=sanitize_label(result),
    ).inc()


def observe_batch_microbatch_retry_delay(*, category: str, delay_seconds: float) -> None:
    deltallm_batch_microbatch_retry_delay_metric.labels(category=sanitize_label(category)).observe(
        max(0.0, float(delay_seconds))
    )


def increment_batch_model_group_deferral(*, reason: str) -> None:
    deltallm_batch_model_group_deferrals_metric.labels(reason=sanitize_label(reason)).inc()


def increment_batch_model_group_deferred_items(*, reason: str, count: int = 1) -> None:
    deltallm_batch_model_group_deferred_items_metric.labels(reason=sanitize_label(reason)).inc(
        max(0, int(count))
    )


def observe_batch_model_group_deferral_seconds(*, reason: str, delay_seconds: float) -> None:
    deltallm_batch_model_group_deferral_seconds_metric.labels(reason=sanitize_label(reason)).observe(
        max(0.0, float(delay_seconds))
    )


def observe_batch_microbatch_size(*, batch_size: int) -> None:
    deltallm_batch_microbatch_size_metric.observe(max(0, int(batch_size)))


def increment_batch_chat_item_executed(*, mode: str, status: str, count: int = 1) -> None:
    deltallm_batch_chat_items_executed_metric.labels(
        mode=sanitize_label(mode),
        status=sanitize_label(status),
    ).inc(max(0, int(count)))


def increment_batch_chat_microbatch_request(*, status: str) -> None:
    deltallm_batch_chat_microbatch_requests_metric.labels(status=sanitize_label(status)).inc()


def increment_batch_chat_microbatch_fallback(*, reason: str, count: int = 1) -> None:
    deltallm_batch_chat_microbatch_fallbacks_metric.labels(reason=sanitize_label(reason)).inc(max(0, int(count)))


def observe_batch_chat_microbatch_size(*, batch_size: int) -> None:
    deltallm_batch_chat_microbatch_size_metric.observe(max(0, int(batch_size)))


def observe_batch_chat_provider_latency(*, mode: str, status: str, latency_seconds: float) -> None:
    deltallm_batch_chat_provider_latency_metric.labels(
        mode=sanitize_label(mode),
        status=sanitize_label(status),
    ).observe(max(0.0, float(latency_seconds)))


def increment_batch_policy_allowed(*, endpoint: str) -> None:
    deltallm_batch_policy_allowed_metric.labels(endpoint=sanitize_label(endpoint)).inc()


def increment_batch_policy_rejected(*, endpoint: str, reason: str) -> None:
    deltallm_batch_policy_rejected_metric.labels(
        endpoint=sanitize_label(endpoint),
        reason=sanitize_label(reason),
    ).inc()


def increment_batch_policy_retryable_failure(*, endpoint: str, reason: str) -> None:
    deltallm_batch_policy_retryable_failures_metric.labels(
        endpoint=sanitize_label(endpoint),
        reason=sanitize_label(reason),
    ).inc()


def observe_batch_preflight_latency(*, endpoint: str, status: str, latency_seconds: float) -> None:
    deltallm_batch_preflight_latency_metric.labels(
        endpoint=sanitize_label(endpoint),
        status=sanitize_label(status),
    ).observe(max(0.0, float(latency_seconds)))


def set_batch_queue_jobs(
    *,
    status: str,
    model_group: str,
    tenant_scope_type: str,
    service_tier: str,
    size_class: str,
    count: int,
) -> None:
    deltallm_batch_queue_jobs_metric.labels(
        status=sanitize_label(status),
        model_group=sanitize_label(model_group),
        tenant_scope_type=sanitize_label(tenant_scope_type),
        service_tier=sanitize_label(service_tier),
        size_class=sanitize_label(size_class),
    ).set(max(0, int(count)))


def set_batch_queue_work_units(
    *,
    status: str,
    model_group: str,
    tenant_scope_type: str,
    service_tier: str,
    size_class: str,
    work_units: int,
) -> None:
    deltallm_batch_queue_work_units_metric.labels(
        status=sanitize_label(status),
        model_group=sanitize_label(model_group),
        tenant_scope_type=sanitize_label(tenant_scope_type),
        service_tier=sanitize_label(service_tier),
        size_class=sanitize_label(size_class),
    ).set(max(0, int(work_units)))


def set_batch_oldest_job_age(
    *,
    status: str,
    model_group: str,
    service_tier: str,
    size_class: str,
    age_seconds: float,
) -> None:
    deltallm_batch_oldest_job_age_metric.labels(
        status=sanitize_label(status),
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier),
        size_class=sanitize_label(size_class),
    ).set(max(0.0, float(age_seconds)))


def set_batch_scheduler_missing_dimension(*, dimension: str, count: int) -> None:
    deltallm_batch_scheduler_missing_dimensions_metric.labels(
        dimension=sanitize_label(dimension),
    ).set(max(0, int(count)))


def observe_batch_estimated_work_units(*, work_units: int) -> None:
    deltallm_batch_estimated_work_units_metric.observe(max(0, int(work_units)))


def observe_batch_queue_wait(
    *,
    model_group: str,
    service_tier: str,
    size_class: str,
    wait_seconds: float,
) -> None:
    deltallm_batch_queue_wait_metric.labels(
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier),
        size_class=sanitize_label(size_class),
    ).observe(max(0.0, float(wait_seconds)))


def observe_batch_time_to_first_claim(
    *,
    mode: str = "fifo_v1",
    model_group: str,
    service_tier: str,
    size_class: str,
    wait_seconds: float,
) -> None:
    deltallm_batch_time_to_first_claim_metric.labels(
        mode=sanitize_label(mode, fallback="fifo_v1"),
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier),
        size_class=sanitize_label(size_class),
    ).observe(max(0.0, float(wait_seconds)))


def observe_batch_completion_latency(
    *,
    mode: str,
    model_group: str,
    service_tier: str,
    size_class: str,
    latency_seconds: float,
) -> None:
    deltallm_batch_completion_latency_metric.labels(
        mode=sanitize_label(mode, fallback="fifo_v1"),
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier),
        size_class=sanitize_label(size_class),
    ).observe(max(0.0, float(latency_seconds)))


def observe_batch_scheduler_job_rank(
    *,
    model_group: str,
    service_tier: str,
    size_class: str,
    rank: float,
) -> None:
    deltallm_batch_scheduler_job_rank_metric.labels(
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier),
        size_class=sanitize_label(size_class),
    ).observe(max(0.0, float(rank)))


def observe_batch_scheduler_age_credit_work_units(
    *,
    model_group: str,
    service_tier: str,
    size_class: str,
    work_units: int,
) -> None:
    deltallm_batch_scheduler_age_credit_work_units_metric.labels(
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier),
        size_class=sanitize_label(size_class),
    ).observe(max(0, int(work_units)))


def increment_batch_scheduler_size_claim(*, size_class: str, result: str) -> None:
    deltallm_batch_scheduler_size_claims_metric.labels(
        size_class=sanitize_label(size_class),
        result=sanitize_label(result),
    ).inc()


def increment_batch_scheduler_large_job_floor_claim(*, model_group: str, service_tier: str) -> None:
    deltallm_batch_scheduler_large_job_floor_claims_metric.labels(
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier),
    ).inc()


def increment_batch_work_claim(*, result: str, claim_mode: str) -> None:
    deltallm_batch_work_claims_metric.labels(
        result=sanitize_label(result),
        claim_mode=sanitize_label(claim_mode),
    ).inc()


def observe_batch_work_claim_items(*, claim_mode: str, count: int) -> None:
    deltallm_batch_work_claim_items_metric.labels(claim_mode=sanitize_label(claim_mode)).observe(
        max(0, int(count))
    )


def observe_batch_work_claim_units(*, claim_mode: str, work_units: int) -> None:
    deltallm_batch_work_claim_units_metric.labels(claim_mode=sanitize_label(claim_mode)).observe(
        max(0, int(work_units))
    )


def observe_batch_work_claim_latency(*, claim_mode: str, latency_seconds: float) -> None:
    deltallm_batch_work_claim_latency_metric.labels(claim_mode=sanitize_label(claim_mode)).observe(
        max(0.0, float(latency_seconds))
    )


def clear_batch_model_capacity_metrics() -> None:
    for metric in (
        deltallm_batch_model_capacity_slots_metric,
        deltallm_batch_model_in_flight_items_metric,
        deltallm_batch_model_available_slots_metric,
        deltallm_batch_model_backlog_work_units_metric,
    ):
        with contextlib.suppress(AttributeError):
            metric.clear()


def publish_batch_model_capacity_snapshot(snapshot: Any) -> None:
    model_group = sanitize_label(getattr(snapshot, "model_group", None))
    service_tier = sanitize_label(getattr(snapshot, "service_tier", None), fallback="standard")
    source = sanitize_label(getattr(snapshot, "capacity_source", None))
    deltallm_batch_model_capacity_slots_metric.labels(
        model_group=model_group,
        service_tier=service_tier,
        source=source,
    ).set(max(0, int(getattr(snapshot, "max_in_flight_items", 0) or 0)))
    deltallm_batch_model_in_flight_items_metric.labels(
        model_group=model_group,
        service_tier=service_tier,
    ).set(max(0, int(getattr(snapshot, "in_flight_items", 0) or 0)))
    deltallm_batch_model_available_slots_metric.labels(
        model_group=model_group,
        service_tier=service_tier,
    ).set(max(0, int(getattr(snapshot, "available_in_flight_items", 0) or 0)))


def set_batch_model_backlog_work_units(
    *,
    model_group: str,
    service_tier: str,
    size_class: str,
    work_units: int,
) -> None:
    deltallm_batch_model_backlog_work_units_metric.labels(
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier, fallback="standard"),
        size_class=sanitize_label(size_class),
    ).set(max(0, int(work_units)))


def increment_batch_scheduler_model_skip(*, model_group: str, reason: str) -> None:
    deltallm_batch_scheduler_model_skips_metric.labels(
        model_group=sanitize_label(model_group),
        reason=sanitize_label(reason),
    ).inc()


def increment_batch_scheduler_model_claim(*, model_group: str, result: str) -> None:
    deltallm_batch_scheduler_model_claims_metric.labels(
        model_group=sanitize_label(model_group),
        result=sanitize_label(result),
    ).inc()


def increment_batch_model_capacity_snapshot_failure(*, reason: str) -> None:
    deltallm_batch_model_capacity_snapshot_failures_metric.labels(reason=sanitize_label(reason)).inc()


def observe_batch_scheduler_model_selection_latency(*, latency_seconds: float) -> None:
    deltallm_batch_scheduler_model_selection_latency_metric.observe(max(0.0, float(latency_seconds)))


def _scheduler_flow_metric_labels(flow: Any) -> tuple[str, str, str]:
    return (
        sanitize_label(getattr(flow, "model_group", None)),
        sanitize_label(getattr(flow, "service_tier", None), fallback="standard"),
        sanitize_label(getattr(flow, "tenant_scope_type", None), fallback="anonymous"),
    )


def _clear_batch_scheduler_fairness_deviation_scope(*, model_group: str, service_tier: str) -> None:
    tenant_scope_types: set[str] = set()
    for family in deltallm_batch_scheduler_fairness_deviation_metric.collect():
        for sample in family.samples:
            if sample.name != "deltallm_batch_scheduler_fairness_deviation":
                continue
            labels = sample.labels
            if labels.get("model_group") == model_group and labels.get("service_tier") == service_tier:
                tenant_scope_types.add(str(labels.get("tenant_scope_type") or "anonymous"))
    for tenant_scope_type in tenant_scope_types:
        with contextlib.suppress(AttributeError, KeyError, ValueError):
            deltallm_batch_scheduler_fairness_deviation_metric.remove(
                model_group,
                service_tier,
                tenant_scope_type,
            )


def publish_batch_scheduler_flows(flows: Iterable[Any]) -> None:
    aggregates: dict[tuple[str, str, str], dict[str, int]] = {}
    fairness_groups: dict[tuple[str, str], list[tuple[str, int, int]]] = {}
    fairness_scopes: set[tuple[str, str]] = set()
    for flow in flows:
        labels_key = _scheduler_flow_metric_labels(flow)
        model_group, service_tier, tenant_scope_type = labels_key
        fairness_scopes.add((model_group, service_tier))
        aggregate = aggregates.setdefault(
            labels_key,
            {
                "active_flows": 0,
                "deficit_work_units": 0,
                "queued_work_units": 0,
                "in_flight_work_units": 0,
            },
        )
        if bool(getattr(flow, "active", False)):
            aggregate["active_flows"] += 1
        aggregate["deficit_work_units"] += int(getattr(flow, "deficit_work_units", 0) or 0)
        aggregate["queued_work_units"] += max(0, int(getattr(flow, "queued_work_units", 0) or 0))
        aggregate["in_flight_work_units"] += max(0, int(getattr(flow, "in_flight_work_units", 0) or 0))
        if bool(getattr(flow, "active", False)):
            fairness_groups.setdefault((model_group, service_tier), []).append(
                (
                    tenant_scope_type,
                    max(1, int(getattr(flow, "weight", 1) or 1)),
                    max(0, int(getattr(flow, "in_flight_work_units", 0) or 0)),
                )
            )
    for (model_group, service_tier, tenant_scope_type), aggregate in aggregates.items():
        labels = {
            "model_group": model_group,
            "service_tier": service_tier,
            "tenant_scope_type": tenant_scope_type,
        }
        deltallm_batch_scheduler_active_flows_metric.labels(**labels).set(aggregate["active_flows"])
        deltallm_batch_scheduler_flow_deficit_metric.labels(**labels).set(aggregate["deficit_work_units"])
        deltallm_batch_scheduler_flow_queued_work_units_metric.labels(**labels).set(
            aggregate["queued_work_units"]
        )
        deltallm_batch_scheduler_flow_in_flight_work_units_metric.labels(**labels).set(
            aggregate["in_flight_work_units"]
        )
    for model_group, service_tier in fairness_scopes:
        _clear_batch_scheduler_fairness_deviation_scope(
            model_group=model_group,
            service_tier=service_tier,
        )
    for (model_group, service_tier), group_flows in fairness_groups.items():
        total_weight = sum(weight for _, weight, _ in group_flows)
        total_in_flight = sum(in_flight for _, _, in_flight in group_flows)
        if total_weight <= 0:
            continue
        deviations: dict[str, float] = {}
        for tenant_scope_type, weight, in_flight in group_flows:
            expected_share = float(weight) / float(total_weight)
            actual_share = 0.0 if total_in_flight <= 0 else float(in_flight) / float(total_in_flight)
            deviations[tenant_scope_type] = max(
                deviations.get(tenant_scope_type, 0.0),
                abs(actual_share - expected_share),
            )
        for tenant_scope_type, deviation in deviations.items():
            set_batch_scheduler_fairness_deviation(
                model_group=model_group,
                service_tier=service_tier,
                tenant_scope_type=tenant_scope_type,
                deviation=deviation,
            )


def publish_batch_scheduler_flow(flow: Any) -> None:
    publish_batch_scheduler_flows([flow])


def increment_batch_scheduler_flow_claim(
    *,
    model_group: str,
    service_tier: str,
    tenant_scope_type: str,
    result: str,
) -> None:
    deltallm_batch_scheduler_flow_claims_metric.labels(
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier, fallback="standard"),
        tenant_scope_type=sanitize_label(tenant_scope_type, fallback="anonymous"),
        result=sanitize_label(result),
    ).inc()


def increment_batch_scheduler_flow_skip(*, reason: str) -> None:
    deltallm_batch_scheduler_flow_skips_metric.labels(reason=sanitize_label(reason)).inc()


def increment_batch_scheduler_deficit_refill(*, model_group: str, service_tier: str, count: int) -> None:
    deltallm_batch_scheduler_deficit_refills_metric.labels(
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier, fallback="standard"),
    ).inc(max(0, int(count)))


def observe_batch_scheduler_flow_wait(
    *,
    model_group: str,
    service_tier: str,
    tenant_scope_type: str,
    wait_seconds: float,
) -> None:
    deltallm_batch_scheduler_flow_wait_metric.labels(
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier, fallback="standard"),
        tenant_scope_type=sanitize_label(tenant_scope_type, fallback="anonymous"),
    ).observe(max(0.0, float(wait_seconds)))


def observe_batch_scheduler_fairness_ratio(*, model_group: str, service_tier: str, ratio: float) -> None:
    deltallm_batch_scheduler_fairness_ratio_metric.labels(
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier, fallback="standard"),
    ).observe(max(0.0, float(ratio)))


def observe_batch_claim_wait_by_model(*, model_group: str, service_tier: str, wait_seconds: float) -> None:
    deltallm_batch_claim_wait_by_model_metric.labels(
        model_group=sanitize_label(model_group),
        service_tier=sanitize_label(service_tier, fallback="standard"),
    ).observe(max(0.0, float(wait_seconds)))


def increment_batch_finalization_claim(*, result: str) -> None:
    deltallm_batch_finalization_claims_metric.labels(result=sanitize_label(result)).inc()


def increment_batch_claim_empty_job(*, reason: str) -> None:
    deltallm_batch_claim_empty_jobs_metric.labels(reason=sanitize_label(reason)).inc()


def increment_batch_mixed_model_job(*, mode: str) -> None:
    deltallm_batch_mixed_model_jobs_metric.labels(mode=sanitize_label(mode)).inc()


def increment_batch_scheduler_shadow_decision(
    *,
    result: str,
    model_group: str = "unknown",
    service_tier: str = "standard",
) -> None:
    deltallm_batch_scheduler_shadow_decisions_metric.labels(
        model_group=sanitize_label(model_group, fallback="unknown"),
        service_tier=sanitize_label(service_tier, fallback="standard"),
        result=sanitize_label(result),
    ).inc()


def increment_batch_scheduler_shadow_record(*, result: str) -> None:
    deltallm_batch_scheduler_shadow_records_metric.labels(result=sanitize_label(result)).inc()


def increment_batch_scheduler_shadow_skip(
    *,
    model_group: str,
    service_tier: str,
    reason: str,
) -> None:
    deltallm_batch_scheduler_shadow_skips_metric.labels(
        model_group=sanitize_label(model_group, fallback="unknown"),
        service_tier=sanitize_label(service_tier, fallback="standard"),
        reason=sanitize_label(reason),
    ).inc()


def observe_batch_scheduler_shadow_share_ratio(
    *,
    model_group: str,
    service_tier: str,
    ratio: float,
) -> None:
    deltallm_batch_scheduler_shadow_share_ratio_metric.labels(
        model_group=sanitize_label(model_group, fallback="unknown"),
        service_tier=sanitize_label(service_tier, fallback="standard"),
    ).observe(max(0.0, float(ratio)))


def increment_batch_scheduler_shadow_comparison(
    *,
    active_mode: str,
    shadow_mode: str,
    result: str,
) -> None:
    deltallm_batch_scheduler_shadow_comparisons_metric.labels(
        active_mode=sanitize_label(active_mode, fallback="fifo_v1"),
        shadow_mode=sanitize_label(shadow_mode, fallback="none"),
        result=sanitize_label(result),
    ).inc()


def increment_batch_scheduler_shadow_better_choice(*, reason: str) -> None:
    deltallm_batch_scheduler_shadow_better_choice_metric.labels(
        reason=sanitize_label(reason),
    ).inc()


def increment_batch_scheduler_rollback(*, from_mode: str, to_mode: str, reason: str) -> None:
    deltallm_batch_scheduler_rollbacks_metric.labels(
        from_mode=sanitize_label(from_mode, fallback="unknown"),
        to_mode=sanitize_label(to_mode, fallback="unknown"),
        reason=sanitize_label(reason),
    ).inc()


def _metric_counter_samples(metric: Any, sample_name: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for family in metric.collect():
        for sample in family.samples:
            if sample.name != sample_name:
                continue
            samples.append(
                {
                    "labels": dict(sample.labels),
                    "value": float(sample.value),
                }
            )
    return sorted(samples, key=lambda item: tuple(sorted(item["labels"].items())))[
        :SCHEDULER_STATUS_METRIC_SAMPLE_LIMIT
    ]


def _metric_histogram_count_sum(metric: Any, base_name: str) -> list[dict[str, Any]]:
    rows: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
    for family in metric.collect():
        for sample in family.samples:
            if sample.name not in {f"{base_name}_count", f"{base_name}_sum"}:
                continue
            labels = tuple(sorted((str(key), str(value)) for key, value in sample.labels.items()))
            row = rows.setdefault(labels, {"labels": dict(labels), "count": 0.0, "sum": 0.0})
            if sample.name.endswith("_count"):
                row["count"] = float(sample.value)
            else:
                row["sum"] = float(sample.value)
    return [rows[key] for key in sorted(rows)][:SCHEDULER_STATUS_METRIC_SAMPLE_LIMIT]


def collect_batch_scheduler_status_metrics() -> dict[str, Any]:
    return {
        "scope": "process_local",
        "cluster_wide": False,
        "warning": (
            "Metric samples in this payload are from the current process only. In split "
            "API/worker deployments, read these metric names from Prometheus for worker-pod "
            "scheduler counters and histograms."
        ),
        "metric_names": {
            "shadow_decisions": "deltallm_batch_scheduler_shadow_decisions_total",
            "shadow_comparisons": "deltallm_batch_scheduler_shadow_comparisons_total",
            "shadow_better_choice": "deltallm_batch_scheduler_shadow_better_choice_total",
            "rollbacks": "deltallm_batch_scheduler_rollbacks_total",
            "decision_latency": "deltallm_batch_scheduler_decision_latency_seconds",
            "time_to_first_claim": "deltallm_batch_time_to_first_claim_seconds",
            "completion_latency": "deltallm_batch_completion_latency_seconds",
        },
        "sample_limit": SCHEDULER_STATUS_METRIC_SAMPLE_LIMIT,
        "process_local_samples": {
            "counters": {
                "shadow_decisions": _metric_counter_samples(
                    deltallm_batch_scheduler_shadow_decisions_metric,
                    "deltallm_batch_scheduler_shadow_decisions_total",
                ),
                "shadow_comparisons": _metric_counter_samples(
                    deltallm_batch_scheduler_shadow_comparisons_metric,
                    "deltallm_batch_scheduler_shadow_comparisons_total",
                ),
                "shadow_better_choice": _metric_counter_samples(
                    deltallm_batch_scheduler_shadow_better_choice_metric,
                    "deltallm_batch_scheduler_shadow_better_choice_total",
                ),
                "rollbacks": _metric_counter_samples(
                    deltallm_batch_scheduler_rollbacks_metric,
                    "deltallm_batch_scheduler_rollbacks_total",
                ),
            },
            "histograms": {
                "decision_latency": _metric_histogram_count_sum(
                    deltallm_batch_scheduler_decision_latency_metric,
                    "deltallm_batch_scheduler_decision_latency_seconds",
                ),
                "time_to_first_claim": _metric_histogram_count_sum(
                    deltallm_batch_time_to_first_claim_metric,
                    "deltallm_batch_time_to_first_claim_seconds",
                ),
                "completion_latency": _metric_histogram_count_sum(
                    deltallm_batch_completion_latency_metric,
                    "deltallm_batch_completion_latency_seconds",
                ),
            },
        },
    }


def set_batch_scheduler_mode_info(*, active_mode: str, shadow_mode: str) -> None:
    with contextlib.suppress(AttributeError):
        deltallm_batch_scheduler_mode_info_metric.clear()
    deltallm_batch_scheduler_mode_info_metric.labels(
        active_mode=sanitize_label(active_mode, fallback="fifo_v1"),
        shadow_mode=sanitize_label(shadow_mode, fallback="none"),
    ).set(1)


def set_batch_scheduler_oldest_wait(
    *,
    model_group: str,
    service_tier: str,
    size_class: str,
    wait_seconds: float,
) -> None:
    deltallm_batch_scheduler_oldest_wait_seconds_metric.labels(
        model_group=sanitize_label(model_group, fallback="unknown"),
        service_tier=sanitize_label(service_tier, fallback="standard"),
        size_class=sanitize_label(size_class, fallback="unknown"),
    ).set(max(0.0, float(wait_seconds)))


def set_batch_scheduler_fairness_deviation(
    *,
    model_group: str,
    service_tier: str,
    tenant_scope_type: str,
    deviation: float,
) -> None:
    deltallm_batch_scheduler_fairness_deviation_metric.labels(
        model_group=sanitize_label(model_group, fallback="unknown"),
        service_tier=sanitize_label(service_tier, fallback="standard"),
        tenant_scope_type=sanitize_label(tenant_scope_type, fallback="anonymous"),
    ).set(max(0.0, float(deviation)))


def observe_batch_scheduler_decision_latency(*, mode: str, latency_seconds: float) -> None:
    deltallm_batch_scheduler_decision_latency_metric.labels(
        mode=sanitize_label(mode, fallback="fifo_v1"),
    ).observe(max(0.0, float(latency_seconds)))


def increment_batch_scheduler_backfill_run(*, status: str) -> None:
    deltallm_batch_scheduler_backfill_runs_metric.labels(status=sanitize_label(status)).inc()


def increment_batch_scheduler_backfill_rows(*, kind: str, count: int) -> None:
    deltallm_batch_scheduler_backfill_rows_metric.labels(kind=sanitize_label(kind)).inc(
        max(0, int(count))
    )


def observe_batch_scheduler_backfill_duration(*, duration_seconds: float) -> None:
    deltallm_batch_scheduler_backfill_duration_metric.observe(max(0.0, float(duration_seconds)))


def publish_batch_runtime_summary(summary: Mapping[str, Any]) -> None:
    """Publish gauges from a summarize_runtime_statuses() result dict.

    Centralizes the mapping so new gauges only need to be added in one place.
    Note: deltallm_batch_jobs only exposes queued/in_progress/finalizing. For
    completed/failed/cancelled throughput, use per-item counters or the
    execution latency histogram's _count series.
    """
    set_batch_job_count(status="queued", count=int(summary.get("queued", 0)))
    set_batch_job_count(status="in_progress", count=int(summary.get("in_progress", 0)))
    set_batch_job_count(status="finalizing", count=int(summary.get("finalizing", 0)))
    set_batch_item_count(status="pending", count=int(summary.get("pending_items", 0)))
    set_batch_item_count(status="in_progress", count=int(summary.get("in_progress_items", 0)))
    set_batch_oldest_item_age(
        status="pending",
        age_seconds=float(summary.get("oldest_pending_item_age_seconds", 0.0)),
    )
    set_batch_oldest_item_age(
        status="in_progress",
        age_seconds=float(summary.get("oldest_in_progress_item_age_seconds", 0.0)),
    )
    for metric in (
        deltallm_batch_queue_jobs_metric,
        deltallm_batch_queue_work_units_metric,
        deltallm_batch_oldest_job_age_metric,
        deltallm_batch_scheduler_missing_dimensions_metric,
        deltallm_batch_scheduler_oldest_wait_seconds_metric,
    ):
        with contextlib.suppress(AttributeError):
            metric.clear()
    oldest_job_age_by_label: dict[tuple[str, str, str, str], float] = {}
    for row in summary.get("scheduler_queue_rows", []) or []:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "unknown")
        model_group = str(row.get("model_group") or "unknown")
        tenant_scope_type = str(row.get("tenant_scope_type") or "unknown")
        service_tier = str(row.get("service_tier") or "standard")
        size_class = str(row.get("size_class") or "unknown")
        set_batch_queue_jobs(
            status=status,
            model_group=model_group,
            tenant_scope_type=tenant_scope_type,
            service_tier=service_tier,
            size_class=size_class,
            count=int(row.get("jobs") or 0),
        )
        set_batch_queue_work_units(
            status=status,
            model_group=model_group,
            tenant_scope_type=tenant_scope_type,
            service_tier=service_tier,
            size_class=size_class,
            work_units=int(row.get("work_units") or 0),
        )
        oldest_job_age_key = (status, model_group, service_tier, size_class)
        oldest_job_age_by_label[oldest_job_age_key] = max(
            oldest_job_age_by_label.get(oldest_job_age_key, 0.0),
            float(row.get("oldest_job_age_seconds") or 0.0),
        )
        if status == "queued":
            set_batch_scheduler_oldest_wait(
                model_group=model_group,
                service_tier=service_tier,
                size_class=size_class,
                wait_seconds=float(row.get("oldest_job_age_seconds") or 0.0),
            )
    for (status, model_group, service_tier, size_class), age_seconds in oldest_job_age_by_label.items():
        set_batch_oldest_job_age(
            status=status,
            model_group=model_group,
            service_tier=service_tier,
            size_class=size_class,
            age_seconds=age_seconds,
        )
    for dimension, count in (summary.get("scheduler_missing_dimensions") or {}).items():
        set_batch_scheduler_missing_dimension(dimension=str(dimension), count=int(count or 0))


def publish_batch_create_session_summary(summary: Mapping[str, Any]) -> None:
    for status, count in summary.items():
        set_batch_create_session_count(status=str(status), count=int(count))
