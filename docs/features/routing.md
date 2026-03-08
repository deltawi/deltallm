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
3. Applies any tag and priority filters
4. Selects one deployment with the active routing strategy
5. Retries or falls back if the call fails in a retryable way

## Routing Strategies

Set the global default in `router_settings.routing_strategy`, or override it with a route-group policy in the Admin UI.

| Strategy | What it does | Good default for |
| --- | --- | --- |
| `simple-shuffle` | Weighted random choice across healthy deployments | Most teams |
| `least-busy` | Chooses the deployment with the fewest in-flight requests | Uneven traffic |
| `latency-based-routing` | Prefers the deployment with the best recent latency | Latency-sensitive workloads |
| `cost-based-routing` | Chooses the cheapest deployment by configured token cost | Cost control |
| `usage-based-routing` | Prefers deployments with the lowest current RPM/TPM utilization | Shared fleets |
| `tag-based-routing` | Matches request metadata tags to deployment tags | Region or capability routing |
| `priority-based-routing` | Uses the lowest priority number first | Primary and fallback setups |
| `weighted` | Weighted random choice using `weight` | Planned traffic splits |
| `rate-limit-aware` | Avoids deployments that are near configured RPM/TPM limits | Provider quota protection |

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

Use these fields on each deployment when you need more control:

- `model_info.weight` for weighted routing
- `model_info.priority` for primary and standby ordering
- `model_info.tags` for tag-based routing
- `model_info.rpm_limit` and `model_info.tpm_limit` for rate-limit-aware routing
- `router_settings.enable_pre_call_checks` if you want DeltaLLM to filter out deployments already above their configured RPM or TPM limits before the provider call

You can also define `router_settings.model_group_alias` if clients should request a friendly alias instead of the underlying group name.

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
