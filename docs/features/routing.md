# Routing & Failover

DeltaLLM can route one public model name to multiple deployments, retry failed calls, and fall back to another model group when needed.

For most teams, the easiest runtime workflow is:

1. Add two or more deployments for the same public model name in [Models](../admin-ui/models.md)
2. Keep the default strategy first
3. Send traffic through the gateway
4. Check [Route Groups](../admin-ui/route-groups.md), `/health/deployments`, and `/health/fallback-events` only when you need more control

## Quick Path

If two deployments share the same `model_name`, DeltaLLM treats them as one routable group.

```yaml
model_list:
  - model_name: gpt-4o-mini
    deployment_id: openai-primary
    deltallm_params:
      provider: openai
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      weight: 1

  - model_name: gpt-4o-mini
    deployment_id: openai-secondary
    deltallm_params:
      provider: openai
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY_2
    model_info:
      weight: 1

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 1
  retry_after: 1
```

With that in place, calls to `gpt-4o-mini` can be spread across both deployments and retried on failure.

## How Routing Works

For each request, DeltaLLM:

1. Resolves the requested model name to a model group
2. Rejects a route group whose declared workload mode does not match the gateway endpoint
3. Removes unhealthy or cooled-down deployments
4. Applies request tag filtering when `metadata.tags` is present
5. Optionally skips deployments already above configured RPM or TPM limits
6. Selects one deployment with the active routing strategy
7. Retries or falls back if the call fails in a retryable way

Workload-mode rejection uses the in-memory route snapshot and adds no database or Redis round trip.
The same check applies to fallback groups, so a different-workload group cannot be reached after a
primary failure.

## Pick A Strategy Fast

If you do not want to think too hard about routing on day one, use this:

- `simple-shuffle` for most gateways
- `weighted` when you want a planned traffic split such as `90/10`
- `priority-based-routing` when you want a primary deployment with a clear standby
- `least-busy` when one deployment tends to get stuck with more in-flight work
- `rate-limit-aware` when provider quotas are the main problem

Everything else is useful, but usually only after you have a specific reason.

## Routing Strategies

Set the global default in `router_settings.routing_strategy`, or override it with a route-group policy in the Admin UI.

| Strategy | What it does | Use it when |
| --- | --- | --- |
| `simple-shuffle` | Randomly picks a healthy deployment | You want a low-maintenance default |
| `weighted` | Randomly picks by `model_info.weight` | You want a deliberate traffic split |
| `priority-based-routing` | Tries the lowest `priority` number first | You want primary and standby behavior |
| `least-busy` | Picks the deployment with the fewest active requests | Queue depth matters more than strict weighting |
| `latency-based-routing` | Prefers the best recent latency, while keeping new members eligible | Response time matters most |
| `cost-based-routing` | Prefers the lowest estimated unit cost for the request mode | You want the cheapest acceptable path |
| `usage-based-routing` | Prefers the deployment with the lowest current RPM/TPM utilization | You share quota across providers or keys |
| `rate-limit-aware` | Avoids deployments near configured RPM/TPM limits | You need to stay away from provider caps |
| `tag-based-routing` | Deprecated compatibility alias for weighted choice after the common tag filter | Existing configuration still uses the legacy value; migrate it to `weighted` |

### Strategy Details

#### `simple-shuffle`

What it does:
- Picks randomly from the healthy eligible pool
- Ignores `weight`

Use it when:
- Deployments are roughly equivalent
- You want the simplest setup
- You are starting out and want predictable behavior

Avoid it when:
- You need a planned percentage split
- One deployment should clearly be preferred over another

Setup:

```yaml
router_settings:
  routing_strategy: simple-shuffle
```

#### `weighted`

What it does:
- Picks randomly, but higher `weight` gets more traffic over time
- Good for controlled rollout, canarying, or provider mix changes

Use it when:
- You want `90/10`, `80/20`, or `50/50`
- You want gradual migration from one deployment to another

Required deployment metadata:
- `model_info.weight`

Setup:

```yaml
model_list:
  - model_name: gpt-4o-mini
    deployment_id: primary
    deltallm_params: {provider: openai, model: openai/gpt-4o-mini}
    model_info: {weight: 9}
  - model_name: gpt-4o-mini
    deployment_id: canary
    deltallm_params: {provider: openai, model: openai/gpt-4o-mini}
    model_info: {weight: 1}

router_settings:
  routing_strategy: weighted
```

#### `priority-based-routing`

What it does:
- Tries all priority `0` deployments first
- Only falls through to higher numbers like `1` or `2` if the higher-priority pool is unavailable

Use it when:
- You have a preferred primary provider
- You want a warm standby deployment
- Order matters more than spreading traffic

Required deployment metadata:
- `model_info.priority`

Setup:

```yaml
model_list:
  - model_name: gpt-4o-mini
    deployment_id: primary
    deltallm_params: {provider: openai, model: openai/gpt-4o-mini}
    model_info: {priority: 0}
  - model_name: gpt-4o-mini
    deployment_id: standby
    deltallm_params: {provider: openai, model: openai/gpt-4o-mini}
    model_info: {priority: 1}

router_settings:
  routing_strategy: priority-based-routing
```

#### `least-busy`

What it does:
- Chooses the deployment with the fewest active in-flight requests
- Useful when latency is mostly driven by queue depth

Use it when:
- Deployments are similar, but traffic can clump
- You want better balancing during bursts

Avoid it when:
- You need explicit rollout percentages
- Cost or provider quota is the main concern

Setup:

```yaml
router_settings:
  routing_strategy: least-busy
```

#### `latency-based-routing`

What it does:
- Uses recent observed latency to prefer faster deployments
- New or unsampled deployments stay eligible instead of being starved forever

Use it when:
- User-facing latency matters more than cost
- Providers behave differently by region or load

Avoid it when:
- You do not have enough steady traffic to build meaningful latency history

Setup:

```yaml
router_settings:
  routing_strategy: latency-based-routing
```

#### `cost-based-routing`

What it does:
- Estimates the cheapest eligible deployment for the current request mode
- Works best when deployment pricing metadata is accurate

Use it when:
- You have multiple providers for the same workload
- Cost control matters more than tiny latency differences

Make sure you set pricing metadata that matches the workload:
- token costs for chat, completions, embeddings, and rerank
- image pricing for image generation
- audio pricing for speech or transcription where applicable

Setup:

```yaml
router_settings:
  routing_strategy: cost-based-routing
```

#### `usage-based-routing`

What it does:
- Looks at recent request-per-minute and token-per-minute usage
- Prefers the least utilized deployment

Use it when:
- Several deployments share quota ceilings
- You want to spread demand before you hit provider-side limits

Best with:
- accurate `rpm_limit` and `tpm_limit`
- steady traffic patterns
- matching per-unit limits for non-text workloads, such as `image_pm_limit`, `audio_seconds_pm_limit`, `char_pm_limit`, or `rerank_units_pm_limit`

Setup:

```yaml
model_list:
  - model_name: gpt-4o-mini
    deployment_id: east
    deltallm_params: {provider: openai, model: openai/gpt-4o-mini}
    model_info: {rpm_limit: 600, tpm_limit: 300000}
  - model_name: gpt-4o-mini
    deployment_id: west
    deltallm_params: {provider: openai, model: openai/gpt-4o-mini}
    model_info: {rpm_limit: 600, tpm_limit: 300000}

router_settings:
  routing_strategy: usage-based-routing
```

#### `rate-limit-aware`

What it does:
- Filters out deployments already close to their configured RPM or TPM ceilings
- Then picks from the remaining pool

Use it when:
- Hitting provider rate limits is your biggest operational problem
- You want to stay away from hot deployments before they fail

Required deployment metadata:
- `model_info.rpm_limit`
- `model_info.tpm_limit`

For non-text workloads, `rate-limit-aware` uses matching per-unit limits when present:
- `model_info.image_pm_limit`
- `model_info.audio_seconds_pm_limit`
- `model_info.char_pm_limit`
- `model_info.rerank_units_pm_limit`

Optional extra safety:
- enable `router_settings.enable_pre_call_checks`

Setup:

```yaml
router_settings:
  routing_strategy: rate-limit-aware
  enable_pre_call_checks: true
```

#### `tag-based-routing`

Compatibility behavior:
- Existing configuration remains valid
- It uses the same weighted implementation as `weighted`
- Request-tag eligibility filtering still runs first, as it does for every strategy

Important:
- DeltaLLM already respects `metadata.tags` as a general eligibility filter before strategy selection
- Do not select `tag-based-routing` for new configuration; use `weighted` and attach request tags
- The Admin UI shows the legacy value only while editing a configuration that already uses it

Use it when:
- You route by region, tenant tier, compliance boundary, or capability tag
- Prompts or callers attach tags such as `["eu"]` or `["vip"]`

Required deployment metadata:
- `model_info.tags`

Migration setup:

```yaml
router_settings:
  routing_strategy: weighted

model_list:
  - model_name: gpt-4o-mini
    deployment_id: eu
    deltallm_params: {provider: openai, model: openai/gpt-4o-mini}
    model_info: {tags: ["eu"]}
  - model_name: gpt-4o-mini
    deployment_id: us
    deltallm_params: {provider: openai, model: openai/gpt-4o-mini}
    model_info: {tags: ["us"]}
```

Client request:

```json
{
  "model": "gpt-4o-mini",
  "messages": [{"role": "user", "content": "hello"}],
  "metadata": {"tags": ["eu"]}
}
```

## Which Metadata Matters

Use these deployment fields when you need more control:

- `model_info.weight` for `weighted`
- `model_info.priority` for `priority-based-routing`
- `model_info.tags` for the common request-tag eligibility filter
- `model_info.rpm_limit` and `model_info.tpm_limit` for usage-aware and rate-limit-aware routing
- `model_info.image_pm_limit` for image-generation quota-aware routing
- `model_info.audio_seconds_pm_limit` and `model_info.char_pm_limit` for audio quota-aware routing
- `model_info.rerank_units_pm_limit` for rerank quota-aware routing
- pricing metadata for `cost-based-routing`

## Retries and Cooldowns

These settings apply to gateway-level retries:

```yaml
router_settings:
  num_retries: 2
  retry_after: 1
  timeout: 600
  cooldown_time: 60
  allowed_fails: 0
```

- `num_retries`: how many extra attempts DeltaLLM makes after the first failure
- `retry_after`: base delay before retrying; backoff increases automatically
- `timeout`: maximum request time before DeltaLLM treats the call as failed
- `cooldown_time`: how long a failing deployment stays out of rotation
- `allowed_fails`: how many failures are allowed before cooldown starts

Failover owns health records for routed provider attempts.
It records each attempt that affects health once against the deployment it called.
Authentication, policy, budget, guardrail, local gateway-capacity, and cancellation failures do not penalize a provider.

Cooldown expiry puts an unhealthy deployment into bounded recovery.
Shared Redis permits one owner-scoped half-open request at a time.
Success clears the cooldown and failure state. A failed half-open request immediately starts a new cooldown.
This recovery works even with background health checks disabled.

With background checks enabled, duplicate registry members receive one probe per interval.
Gateway replicas coordinate the probe claim.
An operator's manual cooldown remains authoritative until its TTL expires.
After an automatic or manual cooldown, only the owner-scoped half-open request or probe can restore health.
Stale in-flight successes and failures cannot override that decision.

Manual health checks use a separate short-lived probe claim but compete for the same recovery token.
They can run on demand without concurrent background or request-driven recovery.
A concurrent manual recovery returns HTTP `409`.

Streaming attempts keep the same single health owner.
After response bytes are sent, a router-health persistence failure cannot replace the stream.
It cannot suppress usage, spend, audit, or cleanup finalization.
The system logs and counts these update failures separately for reconciliation.

All router keys are built as `deltallm:<app_env>:v1:<router-capability>:<identifiers>`. Mutable
provider-health keys also include an opaque deployment generation; active admission, usage, and
latency remain deployment-ID scoped. Separate environments sharing a Redis service therefore share
neither routing state nor claim ownership. Generation values are digests and never expose provider
credentials. This changes no hot-path call count: attempt admission and health transitions remain
one Lua round trip each, and batch reads remain one pipeline or `MGET`.

Health hashes have a rolling 30-day retention period. Each health transition refreshes that TTL.
A cooldown longer than 30 days extends retention through the cooldown plus the failure window.

A deployment ID addition, removal, or recreation publishes a new immutable registry generation.
Changes to that ID's model or provider parameters do the same.
The new generation has separate health, failure, cooldown, recovery, and probe keys.
In-flight attempts and probes keep the retired generation. Their late outcomes cannot affect the replacement.
Changes limited to `model_info`, such as weight or priority, keep health history.

After publication, bounded best-effort cleanup deletes exact retired health keys.
A cleanup failure is observable. It cannot corrupt or fail the new configuration.
Retained keys expire by TTL.
Admission owners, active counts, usage, and latency are not scoped to a generation and are not deleted.

This namespace replaces the previous raw router key schema.
Before namespaced replicas accept traffic, drain replicas that run the previous binary.
Use the same drain procedure for rollback.
Mixed binaries would maintain separate admission and cooldown state.
The [router Redis v1 schema cutover](../deployment/router-state-schema-cutover.md) gives the guarded Helm upgrade and rollback sequence.

During a Redis outage, `redis_degraded_mode: fail_open` uses bounded process-local health state and
reports the router backend as degraded; it does not claim that state is cluster-wide. After Redis
reconnects, shared Redis becomes authoritative and the temporary local health evidence is dropped.
With `fail_closed`, health transitions fail with service unavailable while Redis is unavailable.

### Internal health API migration

`BackgroundHealthChecker` now takes `health_manager=CooldownManager(state_backend)` so all health
transitions pass through the same fenced owner. The former positional or keyword `state_backend`
constructor form remains accepted with a deprecation warning for one migration window.

`PassiveHealthTracker` was removed from the `src.router` and `src.domain.routing` compatibility
namespaces, and `CooldownRecoveryMonitor` was removed from `src.router`. Do not recreate them beside
the current router: `FailoverManager` owns request-result health accounting, while request half-open
admission and the optional `BackgroundHealthChecker` own recovery. Running the legacy helpers as
well would duplicate health writes and could let an unfenced success override cooldown recovery.

Tip: verify the effective `allowed_fails` value in the config your environment actually applies. In practice that usually means your mounted `config.yaml`, your Helm `values.yaml`, or the rendered ConfigMap in the cluster.

When you publish a route-group policy, that group can override timeout and retry behavior without changing the global config.

## Fallback Chains

Use fallback chains when one model group should hand work to another.

```yaml
deltallm_settings:
  fallbacks:
    - gpt-4o:
        - gpt-4o-mini
  context_window_fallbacks:
    - gpt-4o-mini:
        - gpt-4o
  content_policy_fallbacks:
    - gpt-4o:
        - claude-3-sonnet
```

- `fallbacks`: used for general failures such as timeouts, rate limits, and provider errors
- `context_window_fallbacks`: used when the input is too large for the first model, including when
  context-capacity metadata proves that locally before a provider attempt
- `content_policy_fallbacks`: used when a provider rejects the content for policy reasons

Provider adapters classify context-window and content-policy failures from documented provider error fields before the router receives the error.
Chat, embeddings, images, rerank, speech, and transcription use the same adapter classifiers.
OpenAI-compatible and Azure OpenAI responses or stream events with `finish_reason: content_filter` are content-policy failures, not successful empty responses.
The router does not infer these conditions from arbitrary exception text or an unstructured response body.

Explicit custom providers use generic status mapping unless they belong to the supported OpenAI-compatible provider set.
An omitted provider keeps the existing OpenAI-compatible default.
HTTP status controls the public error type and deployment-health impact.
A trusted adapter classification selects the specialized fallback.
A recognized context or policy failure with `5xx` still affects health. It can try its specialized chain before the general chain.
Unclassified `5xx` responses use the general chain. A `429` remains a rate-limit failure regardless of envelope text.

Provider `408` responses use the timeout path.
Unclassified provider `401`, `403`, and `404` responses indicate unhealthy credentials, permissions, or model configuration.
They use the general fallback chain.
A trusted context-window or content-policy classification still remains a terminal request failure.

Malformed JSON or response schemas with a nominally successful provider status are general failures that affect health.
The client never receives the upstream payload.
Empty chat choices, missing or mismatched embedding and rerank results, and empty speech audio are malformed successes.
An unclassified provider client error such as `400`, `409`, or `422` does not affect health.
It skips retries on the same deployment and continues through remaining eligible deployments and configured general fallback groups.
If all candidates reject the request, DeltaLLM returns the final sanitized `400`.

Known authentication, permission, missing-model, timeout, and rate-limit statuses keep their specialized behavior.
Anthropic `refusal` and `model_context_window_exceeded` success stop reasons select the content-policy and context-window chains, respectively.
Gemini policy terminal reasons select the content-policy chain.
Unsupported, malformed, and unknown terminal reasons fail closed through the general chain. They do not become successful empty responses.
Bedrock stream exception types keep their documented meaning even when the message has a context or policy marker.
Only validation-like exceptions use those message allowlists.

Streaming fallback is allowed only before the first downstream response frame. Provider role and
metadata events are held in a bounded pre-commit buffer until output or a valid terminal event
establishes a real response. OpenAI-compatible, Azure OpenAI, Anthropic, and Bedrock classified
terminal events can therefore select a specialized fallback before output. Empty, terminal-only,
and truncated pre-output streams are malformed successes and may use the general fallback chain.
If a classified stop follows partial output, DeltaLLM completes that committed stream with
`content_filter` or `length` instead of starting another provider attempt. Any other malformed
committed stream is marked unhealthy and never cached as a complete response. OpenAI-compatible
streams close without `[DONE]`; Anthropic Messages streams emit one sanitized `event: error` frame.
Neither path starts another provider attempt after commit.

Before commit, a bounded run of non-empty, unknown delta fields is treated as a forward-compatibility
failure rather than evidence that the deployment is unhealthy. The router skips a same-deployment
retry and tries the next eligible deployment once. This applies when the pre-commit buffer reaches
its bound or a clean `[DONE]` marker follows only those unknown fields. Repeated known metadata with
no output, such as role-only deltas, remains a malformed provider stream and follows the
health-affecting general fallback path. Non-empty `reasoning`, `reasoning_content`, and
`reasoning_details` are recognized output and commit immediately.

All three maps are immutable members of one routing-runtime generation.
Each replica serializes durable configuration load, subscriber application, generation publication, and rollback.
The full generation identity fences publication.
A slower reload cannot overwrite a newer grant or route snapshot, including when they have the same route revision.
A validation or subscriber failure keeps requests on the previous complete generation.
A started request keeps its acquired generation, including all fallback maps.

## Advanced Routing Controls

Use these settings when routing needs a little more control:

- `router_settings.enable_pre_call_checks` filters out deployments already above configured RPM or TPM limits before the provider call
- `router_settings.model_group_alias` lets clients call a friendly alias instead of the real group name

## Monitor Routing

Use these endpoints during rollout and incident response:

- `GET /health/deployments` for current deployment health
- `GET /health/fallback-events` for recent retry and failover activity
- `GET /metrics` for latency, traffic, cooldown, and failure metrics

The Admin UI [Settings](../admin-ui/settings.md) and [Route Groups](../admin-ui/route-groups.md) pages provide the same controls in a more operator-friendly form.

## Related Pages

- [Model Deployments](../configuration/models.md)
- [Router Settings](../configuration/router.md)
- [Route Groups](../admin-ui/route-groups.md)
- [Health & Metrics](../api/health.md)
