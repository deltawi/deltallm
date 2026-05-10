from __future__ import annotations

from prometheus_client import generate_latest

from src.metrics import (
    increment_batch_create_session_action,
    get_prometheus_registry,
    increment_batch_artifact_failure,
    increment_batch_claim_empty_job,
    increment_batch_finalization_claim,
    increment_batch_finalization_retry,
    increment_batch_item_reclaim,
    increment_batch_item_retry,
    increment_batch_item_terminal_failure,
    increment_batch_model_group_deferral,
    increment_batch_model_group_deferred_items,
    increment_batch_microbatch_requeue,
    increment_batch_repair_action,
    increment_batch_work_claim,
    observe_batch_create_latency,
    observe_batch_finalize_latency,
    observe_batch_item_execution_latency,
    observe_batch_item_retry_delay,
    observe_batch_model_group_deferral_seconds,
    observe_batch_microbatch_retry_delay,
    observe_batch_work_claim_items,
    observe_batch_work_claim_latency,
    observe_batch_work_claim_units,
    publish_batch_create_session_summary,
    publish_batch_runtime_summary,
    set_batch_create_session_count,
    set_batch_item_count,
    set_batch_job_count,
    set_batch_oldest_item_age,
    set_batch_worker_saturation,
)


def _metric_value(metrics_text: str, metric_name: str, labels: dict[str, str]) -> float | None:
    for line in metrics_text.splitlines():
        if not line.startswith(f"{metric_name}{{"):
            continue
        if all(f'{key}="{value}"' in line for key, value in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    return None


def test_batch_metrics_are_exported() -> None:
    set_batch_job_count(status="queued", count=2)
    set_batch_job_count(status="finalizing", count=1)
    set_batch_create_session_count(status="staged", count=3)
    set_batch_item_count(status="pending", count=5)
    set_batch_oldest_item_age(status="pending", age_seconds=12.5)
    set_batch_worker_saturation(worker_id="worker-1", active=2, capacity=4)
    increment_batch_create_session_action(action="stage", status="success")
    increment_batch_create_session_action(action="promotion_precheck", status="rejected")
    increment_batch_finalization_retry(result="scheduled")
    increment_batch_artifact_failure(operation="delete", backend="s3")
    increment_batch_repair_action(action="retry_finalization", status="success")
    increment_batch_item_reclaim()
    increment_batch_item_retry(category="rate_limit")
    increment_batch_item_terminal_failure(category="budget", reason="not_retryable")
    increment_batch_model_group_deferral(reason="no_healthy_deployments")
    increment_batch_model_group_deferred_items(reason="no_healthy_deployments")
    increment_batch_microbatch_requeue(category="upstream_5xx", result="scheduled")
    increment_batch_work_claim(result="claimed", claim_mode="work_slice")
    increment_batch_finalization_claim(result="claimed")
    increment_batch_claim_empty_job(reason="no_available_work")
    observe_batch_create_latency(status="success", latency_seconds=0.25)
    observe_batch_finalize_latency(status="error", latency_seconds=0.5)
    observe_batch_item_execution_latency(status="success", latency_seconds=0.1)
    observe_batch_item_retry_delay(category="rate_limit", delay_seconds=5.0)
    observe_batch_model_group_deferral_seconds(reason="no_healthy_deployments", delay_seconds=5.0)
    observe_batch_microbatch_retry_delay(category="upstream_5xx", delay_seconds=5.0)
    observe_batch_work_claim_items(claim_mode="work_slice", count=10)
    observe_batch_work_claim_units(claim_mode="work_slice", work_units=40)
    observe_batch_work_claim_latency(claim_mode="work_slice", latency_seconds=0.01)
    publish_batch_create_session_summary({"completed": 2, "failed_retryable": 1})

    metrics_text = generate_latest(get_prometheus_registry()).decode("utf-8")

    assert "deltallm_batch_jobs" in metrics_text
    assert "deltallm_batch_create_sessions" in metrics_text
    assert "deltallm_batch_items" in metrics_text
    assert "deltallm_batch_oldest_item_age_seconds" in metrics_text
    assert "deltallm_batch_worker_saturation_ratio" in metrics_text
    assert "deltallm_batch_create_session_actions_total" in metrics_text
    assert 'action="promotion_precheck"' in metrics_text
    assert 'status="rejected"' in metrics_text
    assert "deltallm_batch_finalization_retries_total" in metrics_text
    assert "deltallm_batch_artifact_failures_total" in metrics_text
    assert "deltallm_batch_repair_actions_total" in metrics_text
    assert "deltallm_batch_item_reclaims_total" in metrics_text
    assert "deltallm_batch_item_retries_total" in metrics_text
    assert 'category="rate_limit"' in metrics_text
    assert "deltallm_batch_item_terminal_failures_total" in metrics_text
    assert 'reason="not_retryable"' in metrics_text
    assert "deltallm_batch_model_group_deferrals_total" in metrics_text
    assert "deltallm_batch_model_group_deferred_items_total" in metrics_text
    assert "deltallm_batch_model_group_deferral_seconds" in metrics_text
    assert "deltallm_batch_microbatch_requeues_total" in metrics_text
    assert "deltallm_batch_work_claims_total" in metrics_text
    assert "deltallm_batch_work_claim_items" in metrics_text
    assert "deltallm_batch_work_claim_units" in metrics_text
    assert "deltallm_batch_work_claim_latency_seconds" in metrics_text
    assert "deltallm_batch_finalization_claims_total" in metrics_text
    assert "deltallm_batch_claim_empty_jobs_total" in metrics_text
    assert 'result="scheduled"' in metrics_text
    assert "deltallm_batch_create_latency_seconds" in metrics_text
    assert "deltallm_batch_finalize_latency_seconds" in metrics_text
    assert "deltallm_batch_item_execution_latency_seconds" in metrics_text
    assert "deltallm_batch_item_retry_delay_seconds" in metrics_text
    assert "deltallm_batch_microbatch_retry_delay_seconds" in metrics_text


def test_batch_runtime_summary_oldest_job_age_uses_max_across_tenant_scopes() -> None:
    publish_batch_runtime_summary(
        {
            "scheduler_queue_rows": [
                {
                    "status": "queued",
                    "model_group": "embeddings-small",
                    "tenant_scope_type": "team",
                    "service_tier": "standard",
                    "size_class": "s",
                    "jobs": 1,
                    "work_units": 12,
                    "oldest_job_age_seconds": 10.0,
                },
                {
                    "status": "queued",
                    "model_group": "embeddings-small",
                    "tenant_scope_type": "api_key",
                    "service_tier": "standard",
                    "size_class": "s",
                    "jobs": 1,
                    "work_units": 8,
                    "oldest_job_age_seconds": 45.0,
                },
                {
                    "status": "queued",
                    "model_group": "embeddings-small",
                    "tenant_scope_type": "user",
                    "service_tier": "standard",
                    "size_class": "s",
                    "jobs": 1,
                    "work_units": 3,
                    "oldest_job_age_seconds": 20.0,
                },
            ]
        }
    )

    metrics_text = generate_latest(get_prometheus_registry()).decode("utf-8")

    assert (
        _metric_value(
            metrics_text,
            "deltallm_batch_oldest_job_age_seconds",
            {
                "status": "queued",
                "model_group": "embeddings-small",
                "service_tier": "standard",
                "size_class": "s",
            },
        )
        == 45.0
    )
