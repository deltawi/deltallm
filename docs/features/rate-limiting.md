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
    "tpm_limit": 100000
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

Each level can have an RPM (requests per minute) and a TPM (tokens per minute) limit. A request must pass every configured level. If **any single level** is over its limit, the request is rejected immediately with a `429` and no counters are modified.

The check is **atomic**: a Redis Lua script validates all levels in a first pass, then increments all counters in a second pass only if every level passed. This prevents partial updates where one scope is charged but another fails.

### System 2 — Deployment limits (model-level capacity)

Each model deployment can declare its own `rpm_limit` and `tpm_limit` as configuration metadata. These represent the capacity of that specific backend, not the caller's identity.

Deployment limits are only enforced during routing and only if `enable_pre_call_checks` is set to `true` in your router config (it is **off by default**). When enabled, the router filters out any deployment that has reached 100% of its configured capacity before selecting a backend.

If all deployments for a model group are at capacity, no candidate is available and the request fails with a `503 Service Unavailable` — not a `429`.

---

## Identity Limit Enforcement in Detail

### What gets checked

For every authenticated request, the gateway resolves the caller's organization, team, user, and API key, then checks:

| Scope | Checked when |
| --- | --- |
| `org_rpm` / `org_tpm` | The API key belongs to an org with a limit set |
| `team_rpm` / `team_tpm` | The API key belongs to a team with a limit set |
| `user_rpm` / `user_tpm` | The user account has a limit set |
| `key_rpm` / `key_tpm` | The API key itself has a limit set |

Any scope without a configured limit is skipped and does not restrict the request.

There is also a separate `max_parallel_requests` limit per API key, tracked with its own Redis counter. It increments when the request starts and decrements when the response finishes, effectively bounding concurrent in-flight requests for a single key.

### Token estimation

Token counts are estimated from the raw request body before the provider call using a simple heuristic: **1 token per 4 characters** of the serialized JSON body. This is intentionally fast and slightly pessimistic. File uploads and multipart requests fall back to a minimal estimate of 1 token so RPM limits still apply even when TPM cannot be estimated accurately.

### Error response

When an identity limit is exceeded, the response is:

```
HTTP 429 Too Many Requests
Retry-After: <seconds until window resets>
```

```json
{
  "error": {
    "message": "Rate limit exceeded for scope 'team_rpm'",
    "type": "rate_limit_error",
    "param": "team_rpm",
    "code": "team_rpm_exceeded"
  }
}
```

The `param` and `code` fields identify which specific scope failed, which is useful for debugging when limits exist at multiple levels.

### Limits are global, not per-model

A critical constraint: **identity limits apply to the entire scope, not to specific models**. An org with `rpm_limit = 100` shares that 100 RPM budget across all models and all API keys in that org. There is no built-in way to express "this team gets 50 RPM on GPT-4 but 200 RPM on a cheaper model."

The workaround is to issue separate API keys for different use cases, each with its own `key_rpm_limit`, and rely on key-level limits for per-model budgeting.

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
    "tpm_limit": 500000
  }'
```

### Identity limits on an organization

```bash
curl -X PATCH http://localhost:8000/ui/api/organizations/{org_id} \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "rpm_limit": 1000,
    "tpm_limit": 2000000
  }'
```

### Deployment limits in router config

```yaml
router_settings:
  enable_pre_call_checks: true
  routing_strategy: rate-limit-aware
```

---

## Redis and Degraded Mode

Redis is the primary backend for both identity limit counters and parallel request tracking. When Redis is unavailable, the gateway falls back to in-memory counters on the current process.

Degraded mode behavior is controlled by the `degraded_mode` setting:

| Mode | Behavior when Redis is down |
| --- | --- |
| `fail_open` (default) | Use in-memory counters; limits are enforced per-process only, not across replicas |
| `fail_closed` | Reject all requests with `503 Service Unavailable` |

In a multi-replica deployment, `fail_open` means rate limits are per-instance during a Redis outage. Set `fail_closed` if you must enforce shared caps even at the cost of availability.

---

## Worked Example: Limits at Every Level

Suppose you have:

- Organization limit: `rpm = 1000`
- Team limit: `rpm = 200`
- User limit: `rpm = 100`
- API key limit: `rpm = 60`
- Model deployment limit: `rpm = 500` (with `enable_pre_call_checks: true`)

For a single request with this key:

1. The middleware checks org (1000), team (200), user (100), and key (60) atomically. All must pass.
2. The effective identity ceiling is **60 RPM** — the tightest scope wins.
3. If all identity checks pass, the router picks a deployment. With `enable_pre_call_checks`, it checks whether the deployment is below its 500 RPM capacity.
4. If the deployment is at capacity and there are no alternatives, the request fails with `503`. Otherwise it proceeds.

The org, team, and user limits are shared caps — useful for ensuring one team cannot consume the entire org budget, but they only restrict the request when the tighter key-level limit alone would still allow it (for example, if the org had `rpm = 5` and the key had `rpm = 60`, the org limit would be the binding constraint).

---

## Related Pages

- [API Keys](../admin-ui/api-keys.md)
- [Teams](../admin-ui/teams.md)
- [Organizations](../admin-ui/organizations.md)
- [Routing & Failover](routing.md)
- [Model Deployments](../configuration/models.md)
