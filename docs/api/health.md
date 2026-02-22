# Health & Metrics Endpoints

## Health Check

```
GET /health
```

Returns the overall health status of the gateway, including Redis and database connectivity.

**Response:**

```json
{
  "liveliness": "ok",
  "readiness": {
    "status": "ok",
    "checks": {
      "redis": true,
      "database": true
    }
  }
}
```

## Liveness Probe

```
GET /health/liveliness
```

Lightweight check suitable for Kubernetes liveness probes. Returns `200 OK` if the process is alive.

## Readiness Probe

```
GET /health/readiness
```

Checks that all dependencies are connected. Returns `200 OK` when ready to serve traffic, `503 Service Unavailable` otherwise.

## Fallback Events

```
GET /health/fallback-events
```

Returns recent fallback and failover events. Useful for monitoring routing behavior.

**Auth required:** Yes

**Response:**

```json
{
  "events": [
    {
      "timestamp": "2026-01-15T10:30:00Z",
      "source_model": "gpt-4o",
      "target_model": "gpt-4o-mini",
      "error_classification": "rate_limit",
      "success": true
    }
  ]
}
```

## Prometheus Metrics

```
GET /metrics
```

Prometheus-formatted metrics. No authentication required.

See [Observability](../features/observability.md) for the full list of available metrics.
