from src.metrics.audit import (
    increment_audit_events_dropped,
    increment_audit_write_failure,
    observe_audit_ingestion_latency,
    set_audit_queue_depth,
)
from src.metrics.counters import (
    increment_cache_hit,
    increment_cache_miss,
    increment_callable_target_policy_fallback,
    increment_callable_target_policy_shadow_mismatch,
    increment_prompt_cache_lookup,
    increment_prompt_resolution,
    increment_request,
    increment_request_failure,
    increment_spend,
    increment_usage,
)
from src.metrics.gauges import (
    set_deployment_active_requests,
    set_deployment_cooldown,
    set_deployment_latency_per_output_token,
    set_deployment_state,
)
from src.metrics.histograms import observe_api_latency, observe_prompt_resolution_latency, observe_request_latency
from src.metrics.prometheus import get_prometheus_registry, infer_provider

__all__ = [
    "get_prometheus_registry",
    "infer_provider",
    "set_audit_queue_depth",
    "increment_audit_write_failure",
    "increment_audit_events_dropped",
    "observe_audit_ingestion_latency",
    "increment_request",
    "increment_request_failure",
    "increment_usage",
    "increment_spend",
    "increment_cache_hit",
    "increment_cache_miss",
    "increment_callable_target_policy_shadow_mismatch",
    "increment_callable_target_policy_fallback",
    "increment_prompt_cache_lookup",
    "increment_prompt_resolution",
    "observe_request_latency",
    "observe_api_latency",
    "observe_prompt_resolution_latency",
    "set_deployment_state",
    "set_deployment_latency_per_output_token",
    "set_deployment_active_requests",
    "set_deployment_cooldown",
]
