# Observability

DeltaLLM exposes health endpoints, Prometheus metrics, spend views, and callback integrations so you can monitor both gateway behavior and provider traffic.

## Quick Path

For a practical first setup:

1. Use `/health/liveliness` for the public process probe
2. Check `/health/readiness` from a trusted operator network
3. Scrape `/metrics` from Prometheus over the private service network
4. Use the [Usage & Spend](../admin-ui/usage.md) page for request and cost trends
5. Add callback integrations only when you need external sinks such as S3, Langfuse, or OpenTelemetry

![Usage & Spend](../admin-ui/images/usage-and-spend.png)

## Health Endpoints

These endpoints are the fastest way to confirm the service is alive and dependencies are reachable.

!!! danger "Operational endpoints are unauthenticated"
    The application does not currently authenticate `/health/*` or `/metrics`. Expose only
    coarse liveness outside the operator network. Restrict readiness, deployment diagnostics,
    fallback events, and metrics with ingress, service-mesh, firewall, or network policy.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Combined liveliness and readiness view |
| `GET /health/liveliness` | Process is up |
| `GET /health/readiness` | Redis and database readiness |
| `GET /health/deployments` | Deployment health summary |
| `GET /health/fallback-events` | Recent retry and failover events |

Enable background deployment checks if you want proactive health updates:

```yaml
general_settings:
  background_health_checks: true
  health_check_interval: 300
```

## Prometheus Metrics

Metrics are exposed at `/metrics` in Prometheus format.

Example scrape config:

```yaml
scrape_configs:
  - job_name: deltallm
    scrape_interval: 15s
    static_configs:
      - targets: ["deltallm.deltallm.svc.cluster.local:4000"]
    metrics_path: /metrics
```

The target above is an example private Kubernetes service address. Use the service discovery
mechanism and port for your deployment; do not scrape through the public ingress.

Core metrics include:

| Metric | Type | Meaning |
| --- | --- | --- |
| `deltallm_requests_total` | Counter | Total proxied requests |
| `deltallm_request_failures_total` | Counter | Failed requests by error type |
| `deltallm_input_tokens_total` | Counter | Input tokens processed |
| `deltallm_output_tokens_total` | Counter | Output tokens processed |
| `deltallm_spend_total` | Counter | Recorded spend |
| `deltallm_cache_hit_total` | Counter | Cache hits |
| `deltallm_cache_miss_total` | Counter | Cache misses |
| `deltallm_request_total_latency_seconds` | Histogram | End-to-end latency |
| `deltallm_llm_api_latency_seconds` | Histogram | Provider-only latency |
| `deltallm_deployment_state` | Gauge | Deployment health state |
| `deltallm_deployment_active_requests` | Gauge | In-flight requests per deployment |
| `deltallm_deployment_cooldown` | Gauge | Whether a deployment is cooled down |
| `deltallm_router_health_transitions_total` | Counter | Actual cooldown, manual-cooldown, and recovery transitions |
| `deltallm_router_health_update_failures_total` | Counter | Post-outcome router health updates that could not be persisted |
| `deltallm_provider_error_body_discards_total` | Counter | Encoded or oversized provider error bodies excluded from classification |
| `deltallm_prompt_resolutions_total` | Counter | Prompt registry resolution results |
| `deltallm_prompt_resolution_latency_seconds` | Histogram | Prompt resolution latency |
| `deltallm_prompt_singleflight_inflight` | Gauge | Distinct process-owned prompt cold-load tasks currently running |
| `deltallm_prompt_singleflight_outcomes_total` | Counter | Prompt cold-load outcomes, including overload, timeout, cancellation, and shutdown rejection |
| `deltallm_email_queue_depth` | Gauge | Deliverable email rows waiting, claimed, or sending |
| `deltallm_email_delivery_audit_backlog` | Gauge | Required email delivery-audit rows by pending, retrying, processing, or blocked state |
| `deltallm_email_delivery_unknown_total` | Counter | Provider sends whose outcome cannot be proven and must not be retried automatically |
| `deltallm_email_worker_failures_total` | Counter | Supervised email-worker failures by iteration, record, or shutdown phase |
| `deltallm_audit_queue_depth` | Gauge | Audit ingestion backlog |
| `deltallm_audit_write_failures_total` | Counter | Audit write failures |
| `deltallm_audit_events_dropped_total` | Counter | Dropped audit events |
| `deltallm_audit_ingestion_latency_seconds` | Histogram | Audit write latency |
| `deltallm_audit_oldest_event_age_seconds` | Gauge | Age of the oldest active durable audit record |
| `deltallm_audit_capacity_utilization` | Gauge | Fraction of the configured durable audit capacity in use |
| `deltallm_audit_enqueue_total` | Counter | Durable audit enqueue outcomes by record type and delivery class |
| `deltallm_audit_cleanup_deleted_total` | Counter | Terminal audit outbox rows removed by cleanup |
| `deltallm_spend_ingestion_backlog` | Gauge | Active durable spend outbox records |
| `deltallm_spend_ingestion_oldest_event_age_seconds` | Gauge | Age of the oldest active spend record |
| `deltallm_spend_ingestion_capacity_utilization` | Gauge | Fraction of the configured spend capacity in use |
| `deltallm_spend_ingestion_fallback_active` | Gauge | Synchronous fallback transactions executing in this process |
| `deltallm_spend_ingestion_fallback_waiters` | Gauge | Requests waiting for a synchronous fallback slot in this process |
| `deltallm_spend_ingestion_enqueue_total` | Counter | Spend enqueue, duplicate, full, and fallback outcomes |
| `deltallm_spend_ingestion_batch_size` | Histogram | Spend records committed per worker transaction |
| `deltallm_spend_ingestion_ledger_rows` | Histogram | Unique ledger rows updated per entity type and spend batch |
| `deltallm_tier_policy_shadow_mismatches_total` | Counter | Differences observed while tier policy runs in shadow mode |
| `deltallm_tier_capacity_requests_total` | Counter | Allowed and denied pool admissions by pool, model, tier, scope, and outcome |
| `deltallm_tier_capacity_fair_share_decisions_total` | Counter | Advanced fair-share decisions and reasons |
| `deltallm_tier_capacity_pool_saturation` | Gauge | Current RPM or TPM pool saturation ratio |
| `deltallm_tier_capacity_pool_active_organizations` | Gauge | Active organizations in advanced fair-share pools |
| `deltallm_tier_capacity_fair_share_latency_seconds` | Histogram | Advanced fair-share admission latency |

Best-effort audit dependency failures increment both the write-failure counter and
`deltallm_audit_events_dropped_total{reason="durable_enqueue_unavailable"}`. They
do not change the result of the request or external side effect that produced the
optional audit event. Required audit persistence failures remain request-visible
and should page immediately.

Page on any `deltallm_email_delivery_audit_backlog{status="blocked"}` value above
zero and investigate any increase in `deltallm_email_delivery_unknown_total`.
Blocked required audits need operator replay after the dependency is repaired;
unknown delivery outcomes require provider reconciliation and explicit resolution.
Sustained prompt singleflight overload or timeout outcomes indicate that the
configured distinct-key bound or the prompt dependency latency needs attention.

Both static hard caps and advanced fair-share strategies emit `deltallm_tier_capacity_requests_total` and saturation. Capacity request metrics intentionally omit organization IDs to keep Prometheus cardinality bounded; use the admin capacity dashboard for per-organization top-consumer and limit-hit details. Active-organization, fair-share-decision, and fair-share-latency series apply only to `weighted_fair` and `reserved_burst`. See the [Organization Tiers Rollout](../deployment/organization-tiers-rollout.md) runbook for queries and release checks.

## Callback Integrations

DeltaLLM supports built-in callback integrations for:

- `prometheus`
- `langfuse`
- `otel`
- `opentelemetry`
- `s3`

Example S3 logging:

```yaml
deltallm_settings:
  success_callback:
    - s3
  callback_settings:
    s3:
      bucket: os.environ/DELTALLM_S3_BUCKET
      region: us-east-1
      prefix: deltallm-logs/
```

## Message Logging Privacy

If you do not want request and response message content stored in the standard logging payloads, disable it:

```yaml
deltallm_settings:
  turn_off_message_logging: true
```

Spend, token, and metadata tracking still continue.

## Related Pages

- [Usage & Spend](../admin-ui/usage.md)
- [Audit Log](audit-log.md)
- [Caching](caching.md)
- [Health & Metrics](../api/health.md)
