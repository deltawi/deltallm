# Health, Diagnostics, and Metrics

DeltaLLM exposes process probes, dependency readiness, routing diagnostics, and Prometheus
metrics. The current application does **not** enforce authentication on these routes. Treat all
routes except coarse liveness as internal operational surfaces and restrict them with ingress,
service-mesh, firewall, or private-network policy.

## Exposure matrix

| Endpoint | Application auth | Intended exposure | Purpose |
| --- | --- | --- | --- |
| `GET /health/liveliness` | None | Public or orchestrator probe | Confirms that the process can answer HTTP; performs no dependency I/O |
| `GET /health` | None | Internal only | Combines liveness with detailed readiness state |
| `GET /health/readiness` | None | Internal only | Checks required dependencies and supervised runtime services |
| `GET /health/deployments` | None | Operator network only | Reports deployment health, counts, and routing topology |
| `GET /health/fallback-events` | None | Operator network only | Returns recent sanitized routing/failover events |
| `GET /metrics` | None | Prometheus/private network only | Exposes process and gateway metrics |

!!! danger "Do not publish detailed operational routes"
    Navigation or documentation visibility is not an access control. Configure the reverse
    proxy or cluster network so internet clients cannot reach readiness, deployment health,
    fallback events, or metrics. Protect the running application's `/docs`, `/redoc`, and
    `/openapi.json` routes similarly when the full control-plane schema is not public.

## Liveness

```http
GET /health/liveliness
```

Returns `200` while the process can serve HTTP:

```json
{"status": "ok"}
```

Use this for container liveness. It deliberately does not query PostgreSQL, Redis, providers,
or workers.

## Readiness

```http
GET /health/readiness
```

Returns `200` when all configured critical checks are ready and `503` when one is degraded.
Depending on enabled features, checks can include PostgreSQL, Redis, telemetry storage, routing
reconciliation, spend/audit/email workers, batch webhook delivery, and organization lifecycle
services.

```json
{
  "status": "ok",
  "checks": {
    "redis": true,
    "database": true
  },
  "details": {}
}
```

Disabled and unavailable components have different meanings. Do not coerce a missing check or
failed response to healthy.

## Combined health

```http
GET /health
```

Returns coarse liveness plus the full readiness payload. Its HTTP status follows readiness, so
use `/health/liveliness`—not `/health`—for a process-only liveness probe.

## Deployment diagnostics

```http
GET /health/deployments
GET /health/deployments?model=app-chat
```

Reports provider-deployment routing health. The optional `model` query narrows the response.
Because the payload reveals deployment counts and state, expose it only to operator networks.

## Fallback events

```http
GET /health/fallback-events?limit=50
```

Returns recent in-process fallback events. `limit` is capped at 200. This journal is bounded
diagnostic history, not a durable audit log or cluster-wide event stream.
For a context fallback selected before any provider attempt, `from_deployment` is `null` because
the primary route group was rejected locally rather than represented by a failed deployment.

## Prometheus metrics

```http
GET /metrics
```

Returns the Prometheus text format. Scrape the private service/pod address or an authenticated
monitoring proxy; do not route this path through a public wildcard ingress.

See [Observability](../features/observability.md) for metrics and alerting guidance and
[Security hardening](../security/hardening.md) for path isolation.
