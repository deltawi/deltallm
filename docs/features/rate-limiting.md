# Rate Limiting

DeltaLLM enforces rate limits through two independent systems that operate in sequence: **identity limits** (applied to the caller before routing) and **deployment limits** (applied to individual model backends during routing). Understanding the distinction matters when you have limits configured at multiple levels.

---

## Quick Start

For most teams, start with limits on API keys:

```bash
curl -X POST http://localhost:8000/ui/api/keys \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_name": "rate-limited-key",
    "rpm_limit": 60,
    "tpm_limit": 100000,
    "rph_limit": 500,
    "rpd_limit": 5000,
    "tpd_limit": 500000
  }'
```

Add team or organization limits only when you need a shared cap across multiple keys.

---

## Two Separate Systems

### System 1 — Identity limits (Org → Team → User → API Key)

This runs **before any routing happens**, as a middleware check on every endpoint. It enforces limits on the caller's identity across four levels:

```
Organization → Team → User → API Key
```

Each level supports six rate limit dimensions across three time windows:

| Window | Request limit | Token limit |
| --- | --- | --- |
| Per minute | `rpm_limit` | `tpm_limit` |
| Per hour | `rph_limit` | _(not applicable)_ |
| Per day | `rpd_limit` | `tpd_limit` |

A request must pass every configured scope and window. If **any single check** is over its limit, the request is rejected immediately with a `429` and no counters are modified.

The check is **atomic**: a Redis Lua script validates all scopes and windows in a first pass, then increments all counters in a second pass only if every check passed. This prevents partial updates where one scope is charged but another fails.

### System 2 — Deployment limits (model-level capacity)

Each model deployment can declare its own `rpm_limit` and `tpm_limit` as configuration metadata. These represent the capacity of that specific backend, not the caller's identity.

Deployment limits are only enforced during routing and only if `enable_pre_call_checks` is set to `true` in your router config (it is **off by default**). When enabled, the router filters out any deployment that has reached 100% of its configured capacity before selecting a backend.

If all deployments for a model group are at capacity, no candidate is available and the request fails with a `503 Service Unavailable` — not a `429`.

---

## Identity Limit Enforcement in Detail

### What gets checked

For every authenticated request, the gateway resolves the caller's organization, team, user, and API key, then checks all configured windows at each scope:

| Scope | Checked when |
| --- | --- |
| `org_rpm` / `org_tpm` / `org_rph` / `org_rpd` / `org_tpd` | The API key belongs to an org with that limit set |
| `team_rpm` / `team_tpm` / `team_rph` / `team_rpd` / `team_tpd` | The API key belongs to a team with that limit set |
| `user_rpm` / `user_tpm` / `user_rph` / `user_rpd` / `user_tpd` | The user account has that limit set |
| `key_rpm` / `key_tpm` / `key_rph` / `key_rpd` / `key_tpd` | The API key itself has that limit set |

Any scope or window without a configured limit is skipped and does not restrict the request.

There is also a separate `max_parallel_requests` limit per API key, tracked with its own Redis counter. It increments when the request starts and decrements when the response finishes, effectively bounding concurrent in-flight requests for a single key.

### Multi-window behavior

The three time windows — minute, hour, and day — are enforced independently with their own Redis counters and TTLs:

- **Per-minute** counters expire after 60 seconds
- **Per-hour** counters expire at the end of the current clock hour (aligned to the top of the hour)
- **Per-day** counters expire at the end of the current UTC day (midnight UTC)

Each window uses a separate Redis key with an appropriate TTL. This means a request that passes the per-minute check can still be rejected by the per-hour or per-day check if those budgets are exhausted.

A common pattern is to set a generous per-minute limit for burst tolerance while using tighter hourly or daily limits for cost control:

```json
{
  "rpm_limit": 60,
  "rph_limit": 500,
  "rpd_limit": 2000,
  "tpm_limit": 100000,
  "tpd_limit": 1000000
}
```

In this example, the key can burst up to 60 requests in a single minute, but is capped at 500 requests total within any clock hour and 2,000 requests per UTC day.

### Token estimation

Token counts are estimated from the raw request body before the provider call using a simple heuristic: **1 token per 4 characters** of the serialized JSON body. This is intentionally fast and slightly pessimistic. File uploads and multipart requests fall back to a minimal estimate of 1 token so RPM limits still apply even when TPM cannot be estimated accurately.

### Response headers

Every proxied response includes standard rate limit headers, regardless of whether the request was rate-limited:

| Header | Description |
| --- | --- |
| `x-ratelimit-limit-requests` | The configured request limit for the tightest scope |
| `x-ratelimit-remaining-requests` | Remaining requests in the current window |
| `x-ratelimit-reset-requests` | Unix timestamp when the request counter resets |
| `x-ratelimit-limit-tokens` | The configured token limit for the tightest scope |
| `x-ratelimit-remaining-tokens` | Remaining tokens in the current window |
| `x-ratelimit-reset-tokens` | Unix timestamp when the token counter resets |
| `x-deltallm-ratelimit-scope` | Comma-separated list of scopes that were checked (e.g., `key_rpm,team_rpm,org_tpm`) |
| `x-ratelimit-warning` | Present when usage is near the limit (value: `near_limit`) |
| `retry-after` | Seconds until the limiting window resets (only on `429` responses) |

The `x-ratelimit-warning: near_limit` header appears when usage exceeds 80% of any configured limit. This gives client applications an early signal to throttle before hitting a hard `429`.

### Error response

When an identity limit is exceeded, the response is:

```
HTTP 429 Too Many Requests
Retry-After: <seconds until window resets>
```

```json
{
  "error": {
    "message": "Rate limit exceeded for scope 'key_rph'",
    "type": "rate_limit_error",
    "param": "key_rph",
    "code": "key_rph_exceeded"
  }
}
```

The `param` and `code` fields identify which specific scope and window failed, which is useful for debugging when limits exist at multiple levels. For multi-window limits, the scope indicates the window that was exceeded (e.g., `key_rph` for hourly, `team_rpd` for daily).

The `Retry-After` header reflects the reset time for the specific window that was exceeded — a per-hour violation will show a larger `Retry-After` value (up to 3600 seconds) than a per-minute violation (up to 60 seconds).

### Limits are global, not per-model

A critical constraint: **identity limits apply to the entire scope, not to specific models**. An org with `rpm_limit = 100` shares that 100 RPM budget across all models and all API keys in that org. There is no built-in way to express "this team gets 50 RPM on GPT-4 but 200 RPM on a cheaper model."

The workaround is to issue separate API keys for different use cases, each with its own `key_rpm_limit`, and rely on key-level limits for per-model budgeting.

### Cache invalidation

When an admin updates rate limits on a key, team, or organization through the admin API, the key validation cache is automatically invalidated. This ensures new limits take effect immediately on the next request — there is no delay or stale-cache window.

---

## Deployment Limit Enforcement in Detail

Deployment limits are declared in model configuration:

```yaml
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
    model_info:
      rpm_limit: 500
      tpm_limit: 100000
```

These represent the maximum throughput you want to send to that specific provider deployment — typically matching provider-side quotas.

When `enable_pre_call_checks: true` is set:

1. The router fetches current utilization for every candidate deployment.
2. Any deployment at or above 100% of its configured limit is excluded from the candidate list.
3. The remaining healthy candidates are passed to the routing strategy for selection.

If `RateLimitAwareStrategy` is configured, it also soft-deprioritizes deployments above 90% utilization before they hit 100%, reducing the chance of hitting provider-side `429` errors.

If a provider returns a `429` despite these checks, the `FailoverManager` can catch it and retry with a different deployment in the same group, if your route policy allows retries.

### Deployment limits vs identity limits

| | Identity limits | Deployment limits |
| --- | --- | --- |
| Applied to | The caller (org / team / user / key) | A specific model backend |
| Windows | Per-minute, per-hour, per-day | Per-minute only |
| Enforced | Before routing, always | During routing, only if enabled |
| Failure response | `429 Too Many Requests` | `503` if no capacity remains, or failover to another deployment |
| Atomic | Yes (all-or-nothing Redis Lua) | No (per-deployment utilization check) |
| Default | On (when limits are configured) | Off (`enable_pre_call_checks` must be set) |

A request must pass identity limits first. If it does, it then enters the router where deployment limits optionally apply. The two systems do not share counters or interact — they are fully independent.

---

## Configuration Reference

### Identity limits on an API key

```bash
curl -X POST http://localhost:8000/ui/api/keys \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_name": "production-key",
    "rpm_limit": 60,
    "tpm_limit": 100000,
    "rph_limit": 500,
    "rpd_limit": 5000,
    "tpd_limit": 500000,
    "max_parallel_requests": 10
  }'
```

### Identity limits on a team

```bash
curl -X PATCH http://localhost:8000/ui/api/teams/{team_id} \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "rpm_limit": 300,
    "tpm_limit": 500000,
    "rph_limit": 2000,
    "rpd_limit": 20000,
    "tpd_limit": 5000000
  }'
```

### Identity limits on an organization

```bash
curl -X PATCH http://localhost:8000/ui/api/organizations/{org_id} \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "rpm_limit": 1000,
    "tpm_limit": 2000000,
    "rph_limit": 10000,
    "rpd_limit": 100000,
    "tpd_limit": 20000000
  }'
```

### Deployment limits in router config

```yaml
router_settings:
  enable_pre_call_checks: true
  routing_strategy: rate-limit-aware
```

### All identity limit fields

| Field | Type | Window | Applies to |
| --- | --- | --- | --- |
| `rpm_limit` | integer or null | Per minute | Key, team, org, user |
| `tpm_limit` | integer or null | Per minute | Key, team, org, user |
| `rph_limit` | integer or null | Per hour | Key, team, org, user |
| `rpd_limit` | integer or null | Per day | Key, team, org, user |
| `tpd_limit` | integer or null | Per day | Key, team, org, user |
| `max_parallel_requests` | integer or null | Concurrent | Key only |

Setting any field to `null` (or omitting it) disables that check. Only configured limits are enforced.

---

## Redis and Degraded Mode

Redis is the primary backend for both identity limit counters and parallel request tracking. When Redis is unavailable, the gateway falls back to in-memory counters on the current process.

Each rate limit window uses its own Redis key pattern:

- Per-minute: `ratelimit:{scope}:{id}:rpm` — TTL 60s
- Per-hour: `ratelimit:{scope}:{id}:rph` — TTL aligned to next clock hour
- Per-day: `ratelimit:{scope}:{id}:rpd` — TTL aligned to next midnight UTC

Degraded mode behavior is controlled by the `degraded_mode` setting:

| Mode | Behavior when Redis is down |
| --- | --- |
| `fail_open` (default) | Use in-memory counters; limits are enforced per-process only, not across replicas |
| `fail_closed` | Reject all requests with `503 Service Unavailable` |

In a multi-replica deployment, `fail_open` means rate limits are per-instance during a Redis outage. Set `fail_closed` if you must enforce shared caps even at the cost of availability.

---

## Worked Example: Limits at Every Level

Suppose you have:

- Organization limits: `rpm = 1000`, `rph = 10000`, `rpd = 100000`
- Team limits: `rpm = 200`, `rph = 2000`, `rpd = 20000`
- User limits: `rpm = 100`
- API key limits: `rpm = 60`, `rph = 500`, `rpd = 5000`
- Model deployment limit: `rpm = 500` (with `enable_pre_call_checks: true`)

For a single request with this key:

1. The middleware checks all configured scopes and windows atomically. For RPM: org (1000), team (200), user (100), key (60). For RPH: org (10000), team (2000), key (500). For RPD: org (100000), team (20000), key (5000). All must pass.
2. The effective ceiling per window is the tightest scope — **60 RPM**, **500 RPH**, and **5,000 RPD**.
3. If all identity checks pass, the router picks a deployment. With `enable_pre_call_checks`, it checks whether the deployment is below its 500 RPM capacity.
4. If the deployment is at capacity and there are no alternatives, the request fails with `503`. Otherwise it proceeds.

The org, team, and user limits are shared caps — useful for ensuring one team cannot consume the entire org budget, but they only restrict the request when the tighter key-level limit alone would still allow it.

---

## Related Pages

- [API Keys](../admin-ui/api-keys.md)
- [Teams](../admin-ui/teams.md)
- [Organizations](../admin-ui/organizations.md)
- [Routing & Failover](routing.md)
- [Model Deployments](../configuration/models.md)
