# Observability

DeltaLLM provides comprehensive observability through Prometheus metrics, structured logging, and callback integrations.

The Usage page in the admin UI provides visual analytics including daily spend trends, per-model and per-key breakdowns, and detailed request logs.

![Usage & Spend](../admin-ui/images/usage-and-spend.png)

## Prometheus Metrics

Metrics are exposed at the `/metrics` endpoint in Prometheus format.

### Available Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_requests_total` | Counter | Total LLM API requests |
| `deltallm_request_failures_total` | Counter | Total failed requests |
| `deltallm_input_tokens_total` | Counter | Total input tokens processed |
| `deltallm_output_tokens_total` | Counter | Total output tokens generated |
| `deltallm_spend_total` | Counter | Total spend in USD |
| `deltallm_cache_hit_total` | Counter | Cache hits |
| `deltallm_cache_miss_total` | Counter | Cache misses |
| `deltallm_request_total_latency_seconds` | Histogram | End-to-end request latency |
| `deltallm_llm_api_latency_seconds` | Histogram | Provider API latency only |
| `deltallm_deployment_state` | Gauge | Deployment health (0=healthy, 1=partial, 2=degraded) |
| `deltallm_deployment_active_requests` | Gauge | In-flight requests per deployment |
| `deltallm_deployment_cooldown` | Gauge | Whether a deployment is in cooldown (0/1) |
| `deltallm_audit_queue_depth` | Gauge | Current audit event queue depth |
| `deltallm_audit_write_failures_total` | Counter | Failed audit write attempts |
| `deltallm_audit_events_dropped_total` | Counter | Dropped audit events (by reason) |
| `deltallm_audit_ingestion_latency_seconds` | Histogram | Audit persistence latency |

### Prometheus Configuration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: deltallm
    scrape_interval: 15s
    static_configs:
      - targets: ["localhost:8000"]
    metrics_path: /metrics
```

## Callbacks

Configure callbacks to trigger on successful or failed requests:

```yaml
deltallm_settings:
  success_callback:
    - prometheus
  failure_callback:
    - prometheus
```

### S3 Logging

Log request/response payloads to S3:

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

## Message Logging

By default, request and response messages are logged in spend records. Disable for privacy:

```yaml
deltallm_settings:
  turn_off_message_logging: true
```

When disabled, spend records still track tokens, cost, and metadata, but the actual message content is not stored.

## Health Endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /health` | Optional | Full health check (Redis + database) |
| `GET /health/liveliness` | None | Kubernetes liveness probe |
| `GET /health/readiness` | None | Kubernetes readiness probe |
| `GET /health/fallback-events` | Required | Recent fallback events |

### Background Health Checks

Enable periodic health checks to proactively detect unhealthy deployments:

```yaml
general_settings:
  background_health_checks: true
  health_check_interval: 300
```
