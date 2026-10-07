# Rate Limiting

DeltaLLM enforces rate limits through two independent systems in sequence.
**Identity limits** apply to the caller before routing.
**Deployment limits** apply to individual model backends during routing.
These limits can apply at different levels.

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

Text requests pass caller admission after pre-call policy processing and before provider dispatch. Other proxy endpoints use their existing admission path. Caller limits apply across four levels:

```
Organization → Team → User → API Key
```

Each level supports these rate limit dimensions:

| Window | Request limit | Token limit |
| --- | --- | --- |
| Per minute | `rpm_limit` | `tpm_limit` |
| Per hour | `rph_limit` | _(not applicable)_ |
| Per day | `rpd_limit` | `tpd_limit` |

Text requests also support a separate `output_tpm_limit` per admission minute.

A request must pass every configured scope and window. If **any single check** is over its limit, the request is rejected immediately with a `429` and no counters are modified.

The check is **atomic**.
A Redis Lua script first validates all scopes and windows.
Only if all checks pass does it increment all counters in a second pass.
Thus, a failure in one scope cannot leave another scope with a partial charge.

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

In this example, the key permits up to 60 requests in one minute.
It also limits total requests to 500 per clock hour and 2,000 per UTC day.

### Token estimation

Before the provider call, the gateway estimates tokens from the serialized JSON request body.
It counts **1 token per 4 characters**.
This fast estimate is deliberately slightly pessimistic.
File uploads and multipart requests use a minimum estimate of 1 token.
RPM limits thus still apply when an accurate TPM estimate is unavailable.

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

The `Retry-After` header gives the reset time for the exceeded window.
An hourly violation can give up to 3600 seconds.
A minute-window violation can give up to 60 seconds.

### Scope and model limits

Scalar identity limits apply across models. An organization with `rpm_limit = 100` shares that allowance across its keys and models. Key, user, team, and organization policies also have existing `model_rpm_limit` and `model_tpm_limit` maps. Tier policies can add per-model limits. `output_tpm_limit` on an identity is a total across models. Tier model policies and team/key maps add output TPM limits for individual models.

### Cache invalidation

Admin rate-limit changes invalidate the affected key validation cache. Output-policy updates for keys, runtime users, teams, and organizations queue invalidation in the same database transaction. This includes explicit clears and organization POST upserts that update an existing organization. If the queue write fails, the policy update rolls back and returns `503`. Omitted output policies do not queue output-policy invalidation.

After commit, the gateway attempts immediate invalidation with a bounded timeout. If it fails, the existing cache-invalidation worker retries the durable record. Cached authentication can retain the previous policy until invalidation completes or the cache entry expires. Keep `cache_invalidation_worker_enabled` enabled and monitor its pending and failed records when you use output policies. Team and organization invalidation use the same team relationship as authentication: the key team, then the runtime-user team, then the service-account team.

---

## Output tokens per minute

Set `output_tpm_limit` on an API key, runtime user, team, or organization. The value must be an integer from 1 through 2,147,483,647, or `null`. The admin API rejects strings, booleans, fractions, zero, and negative values. Omit the field on an update to preserve it. Send `null` to clear it. A child policy cannot remove an ancestor limit.

Use the **Output TPM** field in the corresponding admin form. For a runtime user, use the account's runtime access details. Leave the field blank for no limit at that scope.

Output TPM is independent of the existing estimated TPM limit. It counts provider output, including reasoning and tool output, across models. Local batch items and synchronous text requests share the same counters. Embedding, image, speech, and rerank tokens do not enter this output counter. Master-key requests keep their existing exemption. Selector classifiers keep their existing billing and routing controls and do not enter caller output TPM.

### Output limits for individual models

Set **Output TPM** beside RPM and TPM on a tier model policy. Each organization assigned to that tier gets its own allowance for that callable model. Organizations do not share this counter. The existing tier compiler selects the effective policy: override, add-on, then primary, with the existing deny rules. The organization preview shows the effective source and version. Use a custom tier or an override assignment for an organization-specific allowance.

Team and API-key create/update endpoints accept `model_output_tpm_limit`. For example:

```json
{"model_output_tpm_limit": {"gpt-4o-mini": 20000, "gpt-4o": 5000}}
```

Use exact caller-facing callable IDs, including route-group IDs where applicable. Each map permits at most 64 entries, 256 UTF-8 bytes per ID, and 32 KiB of stored JSON. Wildcards, control characters, and spaces at the ends are invalid. Values use the same strict positive integer range as scalar output limits. Omit the field to preserve it. Send `null` or `{}` to clear the map. Remove one model from the submitted map to clear only that model. The admin forms provide a model/output-limit row editor.

All configured scopes apply independently. A tier allowance of 100,000 for a model is shared by that organization's teams and keys. A team allowance of 20,000 is shared by that team's keys. A key allowance of 5,000 applies to that key. Scalar output limits still apply across all models. Clearing a child limit cannot remove a parent limit. A request checks at most seven output counters in the existing admission operation.

The final caller-facing model after request hooks selects the counter. Provider retries and fallbacks charge that same callable model. A tier version change or limit edit keeps recorded usage because the counter identity contains the organization and model, without a version or limit value. Tier output limits enforce only in `tier_policy_mode: enforce`; team and key maps apply in all tier modes. The simulator shows completion usage projected from an empty minute separately from admission.

Tier output limits require `tier_policy_missing_service_mode: fail_closed` in enforce mode. A missing or stale tier snapshot then closes admission. Shared capacity-pool output limits and deployment output limits are outside this feature.

### Generation parameters and providers

Output caps remain generation parameters: Chat Completions accepts `max_tokens` or `max_completion_tokens`, Completions and Messages accept `max_tokens`, and Responses accepts `max_output_tokens`. Supply one Chat cap when needed. Output TPM does not require a cap, add one, or change the request cap or choice count.

The policy applies to all existing text adapters, including OpenAI, Azure, Groq, vLLM, custom OpenAI-compatible endpoints, profiled compatible providers, Anthropic, Gemini JSON, and Bedrock Converse. Usage reporting must be complete. Groq streams can report top-level usage or `x_groq.usage`; matching copies count once. vLLM streams need final usage reporting enabled. Existing provider parameter and endpoint support still applies; native Gemini streaming remains unsupported.

Realtime and provider-native asynchronous batch submission remain unsupported under an output policy. Local batch uses individual governed calls because aggregate microbatches lack per-caller output attribution. Embeddings, images, speech, transcription, and rerank retain their existing controls.

### Completion minute and accounting

Output TPM is a soft usage rate limit. Before a caller request, Redis checks recorded output at every configured scope. Usage equal to or above the limit rejects the new request with `429`. There is no reservation. An admitted request, its bounded retries, and its MCP phases can finish after a scope crosses the limit.

For example, a scope with a 1,000-token limit and 900 recorded tokens admits a call that produces 300 tokens. That call completes and records 1,200. Subsequent calls receive `429`. Concurrent admitted calls can exceed the limit further. This control does not replace provider limits, concurrency limits, or hard spending budgets.

Redis time selects fixed UTC completion accounting minutes. A call admitted at 12:00:59 that records output at 12:01:10 charges the 12:01 minute. All output from one attempt belongs to its accounting minute. This is neither a rolling 60-second window nor a measurement of tokens emitted in each minute.

Each provider attempt records complete raw output once, including reasoning and choices in its output aggregate. It does not add input tokens, cached input, billing estimates, or reasoning details already included in that aggregate. Positive counts and unknown usage each need one final Redis operation. Zero output, cache hits, and failures before dispatch need no accounting write. Stream frames have no quota Redis or SQL calls. Admission stays in the existing atomic rate-limit transaction and occurs before cache lookup.

When dispatched work ends without complete output evidence, the gateway marks all captured scopes as unknown for the accounting minute. New calls at an affected scope receive `503` with `output_tpm_usage_unknown` and `Retry-After` until reset. Already admitted calls finish. One uncertain call can thus pause a shared team or organization. Known charges do not clear unknown state in the same minute. A valid answer with missing usage is still delivered; existing malformed-response validation remains active.

Bedrock streaming retains a valid raw `outputTokens` count after `messageStop`, even if missing or invalid input or total usage causes response validation to fail. The existing response error remains in effect. Missing or invalid output counts remain unknown; explicit zero requires no accounting write.

Cancellation, a partial stream, and an ambiguous timeout are unknown usage. An upstream connection-pool timeout is known zero output because no connection was acquired. It does not mark scopes unknown. Complete usage still counts if translation or delivery then fails. Final accounting failure preserves the answer, omits unknown remaining capacity, and records an operational failure. Some usage can be lost after process death or an unrecorded Redis write. This is a soft control, not a durable billing ledger.

### Output headers and failures

| Header | Meaning |
| --- | --- |
| `x-ratelimit-limit-output-tokens` | Limit of the configured output scope with highest utilization |
| `x-ratelimit-remaining-output-tokens` | Recorded remaining capacity, clamped to zero; omitted when unknown |
| `x-ratelimit-reset-output-tokens` | Unix timestamp at the next UTC minute |
| `x-deltallm-ratelimit-output-scope` | A scalar scope, or `org_model_output_tpm`, `team_model_output_tpm`, or `key_model_output_tpm` |

JSON headers reflect final accounting. Cache and zero-output responses retain the admission snapshot. Streaming headers retain the admission snapshot because headers precede usage. Requests without output policies have no output headers. Numeric exhaustion returns the scope in `error.param` and `error.code`, with `Retry-After`. Unknown usage returns `output_tpm_usage_unknown`; unavailable or corrupt coordination returns `output_tpm_unavailable`. Both use `503` without provider cooldown.

### Redis and authentication requirements

Output policies require Redis and `general_settings.redis_degraded_mode: fail_closed`. There is no process-local fallback. Admin writes reject unsupported settings. Startup and configuration reload reject active policies with incompatible settings. Shared user, team, and organization output policies require stored API-key authentication; they cannot coexist with enabled JWT or custom authentication. Per-key policies still apply to stored keys when those auth modes are enabled.

Use a protected standalone Redis primary with `maxmemory-policy noeviction` and sufficient memory for all quota state. Redis Cluster is not supported by this transaction. Buckets expire 30 seconds after minute reset. Final receipts expire 120 seconds after accounting. Keys are at most 512 bytes and receipts at most 4 KiB. Counters saturate at 2,147,483,647, not at the current policy limit. Receipt capacity is final events per second times 120; reserve memory headroom for receipts and active scopes. Redis state is authoritative while it is retained. The counter is a throughput control and does not provide durable accounting across state loss.

After a Redis flush, state-changing restart, restore, or failover that can lose acknowledged writes, pause governed ingress until the next UTC minute. Resume on the healthy primary after that boundary. An application restart with retained Redis state needs no pause. See [Redis replication guarantees](https://redis.io/docs/latest/operate/oss_and_stack/management/replication/).

Apply the additive database migration before upgrading the gateway. Upgrade or drain all old gateway and batch-worker replicas before enabling output policies, including model-only policies. New pods use auth cache version 6 and invalidate versions 4, 5, and 6. Old pods cannot enforce model output limits. Use one version across all API replicas and workers. Drain the reservation build before enabling v2 completion accounting. Do not mix versions with active limits or merge old reservation state. Start with one key and provider headroom, then add shared scopes. Before rollback, clear the new policies, wait for cache invalidation or expiry, and drain admitted attempts. Do not drop the additive columns on rollback.

Response cache version 6 includes `max_completion_tokens` in its default key fields. The upgrade causes one cold response-cache fill for each request key. Previous entries expire through their normal TTL.

Fixed-label `deltallm_output_tpm_events_total` counters record admission, denial, unavailable coordination, accounting, unknown usage, accounting failure, and saturation. An accounting failure does not cause a provider retry.

## Deployment Limit Enforcement in Detail

Deployment limits are declared in model configuration:

```yaml
model_list:
  - model_name: gpt-4
    litellm_params:
      provider: openai
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

A provider can return `429` despite these checks.
If the route policy permits retries, `FailoverManager` can retry with a different deployment in the same group.

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
curl -X PUT http://localhost:8000/ui/api/teams/{team_id} \
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
curl -X PUT http://localhost:8000/ui/api/organizations/{org_id} \
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
| `output_tpm_limit` | strict positive integer or null | Admission minute | Key, team, org, runtime user |
| `rph_limit` | integer or null | Per hour | Key, team, org, user |
| `rpd_limit` | integer or null | Per day | Key, team, org, user |
| `tpd_limit` | integer or null | Per day | Key, team, org, user |
| `max_parallel_requests` | integer or null | Concurrent | Key only |

Set a field to `null` to disable that scope's check. Omitted updates preserve existing values. Only configured limits are enforced.

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

Organization, team, and user limits are shared caps.
They prevent one team from consuming the full organization allowance.
They restrict a request only when the tighter key-level limit would still permit it.

---

## Related Pages

- [API Keys](../admin-ui/api-keys.md)
- [Teams](../admin-ui/teams.md)
- [Organizations](../admin-ui/organizations.md)
- [Routing & Failover](routing.md)
- [Model Deployments](../configuration/models.md)
