# Router Settings

The `router_settings` section controls how DeltaLLM routes requests across model deployments, handles failures, and manages retries.

## Basic Configuration

```yaml
router_settings:
  routing_strategy: simple-shuffle
  num_retries: 3
  retry_after: 1
  timeout: 600
  cooldown_time: 60
  allowed_fails: 0
```

## Settings Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `routing_strategy` | `simple-shuffle` | How to select a deployment from a model group |
| `num_retries` | `3` | Number of retry attempts on failure |
| `retry_after` | `1` | Base delay between retries (seconds) |
| `timeout` | `600` | Global request timeout (seconds) |
| `cooldown_time` | `60` | How long to cool down a failed deployment (seconds) |
| `allowed_fails` | `0` | Number of failures before cooling down a deployment |
| `enable_pre_call_checks` | `false` | Run health checks before routing |
| `model_group_alias` | `{}` | Map alias names to model groups |

## Routing Strategies

### `simple-shuffle`
Randomly distributes requests across healthy deployments. Good default for most use cases.

### `least-busy`
Routes to the deployment with the fewest in-flight requests. Best for uneven workloads.

### `latency-based-routing`
Routes to the deployment with the lowest observed latency. Requires a warm-up period to gather metrics.

### `cost-based-routing`
Routes to the cheapest deployment first. Requires `input_cost_per_token` and `output_cost_per_token` in `model_info`.

### `usage-based-routing`
Distributes requests proportionally based on configured `weight` values.

### `priority-based-routing`
Routes to the highest-priority deployment (lowest `priority` value). Lower priority deployments are only used as fallbacks.

## Fallback Chains

Configure fallback behavior for different error types:

```yaml
deltallm_settings:
  fallbacks:
    - ["gpt-4o", "gpt-4o-mini"]
  context_window_fallbacks:
    - ["gpt-4o-mini", "gpt-4o"]
  content_policy_fallbacks:
    - ["gpt-4o", "claude-3-sonnet"]
```

| Chain | Triggered When |
|-------|---------------|
| `fallbacks` | General failures (timeouts, 5xx errors, rate limits) |
| `context_window_fallbacks` | Input exceeds the model's context window |
| `content_policy_fallbacks` | Content policy violations |

## Model Group Aliases

Map alternative names to existing model groups:

```yaml
router_settings:
  model_group_alias:
    best-model: gpt-4o
    fast-model: gpt-4o-mini
```

Clients can request `best-model` and it routes to `gpt-4o` deployments.
