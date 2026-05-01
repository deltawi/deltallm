# Upstream HTTP Tuning

DeltaLLM uses a shared async HTTP client for outbound provider calls. Explicit pool limits make gateway behavior predictable under streaming load and prevent local connection pressure from being mistaken for provider slowness.

Authentication, SSO, and email-provider HTTP calls use a separate smaller control-plane client. This keeps login, JWKS refresh, and transactional email delivery from sharing the provider traffic pool.

## Recommended Defaults

```yaml
general_settings:
  upstream_http_connect_timeout_seconds: 10
  upstream_http_read_timeout_seconds: 300
  upstream_http_write_timeout_seconds: 30
  upstream_http_pool_timeout_seconds: 10
  upstream_http_max_connections: 500
  upstream_http_max_keepalive_connections: 100
  upstream_http_keepalive_expiry_seconds: 60
```

These defaults are a conservative production starting point. Streaming requests hold upstream connections for the life of the stream, so streaming-heavy deployments may need a higher `upstream_http_max_connections` value.

`upstream_http_read_timeout_seconds` is the global provider read timeout when a deployment does not set `deltallm_params.timeout`. Deployment-level `timeout` values still override the read phase for that deployment, while connect, write, and pool timeouts remain explicit across upstream requests.

Request duration can also be limited by the router failover wrapper timeout. Audio transcription defaults both the upstream read timeout and the failover wrapper timeout to `600` seconds when the deployment does not set `deltallm_params.timeout`; an explicit route-group timeout still takes priority for operators who need a stricter policy.

The upstream HTTP settings are startup-time settings. DeltaLLM stores one startup snapshot and uses it for provider calls, live model discovery, MCP upstream calls, and health probes. Apply changes with a process restart or Kubernetes rollout; runtime config reloads do not rebuild the HTTP client or partially change timeout behavior.

## Kubernetes Capacity Math

The configured connection limit is per process:

```text
effective upstream connection ceiling =
  upstream_http_max_connections * worker_processes_per_pod * pod_count
```

Before raising limits, check:

- provider account or endpoint connection limits
- node and container file descriptor limits
- NAT gateway or egress proxy connection tracking capacity
- CPU and memory headroom per pod
- expected streaming concurrency and average stream duration

For Helm deployments, set the values under `config.general_settings` and roll the deployment. Existing processes keep their current HTTP client and upstream HTTP settings snapshot until restart.

## Sizing Profiles

| Profile | Suggested max connections | Suggested keep-alive | Notes |
|---------|---------------------------|----------------------|-------|
| Local/dev | `100` | `20` | Keeps resource use low on laptops |
| Small production | `300`-`500` | `75`-`100` | Good starting point for normal chat and embedding traffic |
| Streaming-heavy | `500`-`1500` | `100`-`300` | Validate egress and provider limits before rollout |

Keep `upstream_http_pool_timeout_seconds` bounded. A value around `5` to `10` seconds usually gives short bursts room to clear without allowing requests to pile up invisibly inside the gateway.

## Failure Behavior

If the upstream connection pool is exhausted, DeltaLLM returns a controlled `503` gateway capacity error with code `upstream_pool_timeout`. This does not mark the selected model deployment unhealthy because the bottleneck is local gateway capacity, not the provider.

Background health checks cap their upstream pool wait below the health-check wrapper timeout. This keeps an oversized `upstream_http_pool_timeout_seconds` from turning local connection pressure into false provider-unhealthy state.

Provider read, connect, and write timeouts still count as provider-side failures where appropriate and can affect passive health tracking.

## Operating Guidance

- Increase pod replicas and `upstream_http_max_connections` together only after checking the effective cluster-wide connection ceiling.
- Keep request timeouts realistic for the workload. Long read timeouts are useful for streaming but can hide stalled non-streaming calls.
- Watch `/metrics`, provider latency, request failure type, pod CPU, pod memory, file descriptors, and egress/NAT saturation during load tests.
- Prefer a rolling deployment when changing these settings so old streams can drain while new pods pick up the new pool configuration.
