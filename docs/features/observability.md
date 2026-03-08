# Observability

DeltaLLM exposes health endpoints, Prometheus metrics, spend views, and callback integrations so you can monitor both gateway behavior and provider traffic.

## Quick Path

For a practical first setup:

1. Check `/health` after startup
2. Scrape `/metrics` from Prometheus
3. Use the [Usage & Spend](../admin-ui/usage.md) page for request and cost trends
4. Add callback integrations only when you need external sinks such as S3, Langfuse, or OpenTelemetry

![Usage & Spend](../admin-ui/images/usage-and-spend.png)

## Health Endpoints

These endpoints are the fastest way to confirm the service is alive and dependencies are reachable.

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
      - targets: ["localhost:8000"]
    metrics_path: /metrics
```

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
| `deltallm_prompt_resolutions_total` | Counter | Prompt registry resolution results |
| `deltallm_prompt_resolution_latency_seconds` | Histogram | Prompt resolution latency |
| `deltallm_audit_queue_depth` | Gauge | Audit ingestion backlog |
| `deltallm_audit_write_failures_total` | Counter | Audit write failures |
| `deltallm_audit_events_dropped_total` | Counter | Dropped audit events |
| `deltallm_audit_ingestion_latency_seconds` | Histogram | Audit write latency |

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
