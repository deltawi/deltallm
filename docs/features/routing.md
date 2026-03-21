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
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      weight: 1

  - model_name: gpt-4o-mini
    deployment_id: openai-secondary
    deltallm_params:
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
2. Removes unhealthy or cooled-down deployments
3. Applies request tag filtering when `metadata.tags` is present
4. Optionally skips deployments already above configured RPM or TPM limits
5. Selects one deployment with the active routing strategy
6. Retries or falls back if the call fails in a retryable way

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
| `tag-based-routing` | Uses the same tag-filtered eligible pool as other strategies, then applies weighted choice | You want a route group that is explicitly tag-driven |

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
    deltallm_params: {model: openai/gpt-4o-mini}
    model_info: {weight: 9}
  - model_name: gpt-4o-mini
    deployment_id: canary
    deltallm_params: {model: openai/gpt-4o-mini}
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
    deltallm_params: {model: openai/gpt-4o-mini}
    model_info: {priority: 0}
  - model_name: gpt-4o-mini
    deployment_id: standby
    deltallm_params: {model: openai/gpt-4o-mini}
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
    deltallm_params: {model: openai/gpt-4o-mini}
    model_info: {rpm_limit: 600, tpm_limit: 300000}
  - model_name: gpt-4o-mini
    deployment_id: west
    deltallm_params: {model: openai/gpt-4o-mini}
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

What it does:
- Uses the same request-tag eligibility filtering that DeltaLLM already applies before strategy selection
- Then uses weighted choice across the remaining tag-matched pool

Important:
- DeltaLLM already respects `metadata.tags` as a general eligibility filter before strategy selection
- Choose `tag-based-routing` when you want the route-group policy itself to communicate that tags are the main routing signal

Use it when:
- You route by region, tenant tier, compliance boundary, or capability tag
- Prompts or callers attach tags such as `["eu"]` or `["vip"]`

Required deployment metadata:
- `model_info.tags`

Setup:

```yaml
model_list:
  - model_name: gpt-4o-mini
    deployment_id: eu
    deltallm_params: {model: openai/gpt-4o-mini}
    model_info: {tags: ["eu"]}
  - model_name: gpt-4o-mini
    deployment_id: us
    deltallm_params: {model: openai/gpt-4o-mini}
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
- `model_info.tags` for tag-aware routing
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
- `context_window_fallbacks`: used when the input is too large for the first model
- `content_policy_fallbacks`: used when a provider rejects the content for policy reasons

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
