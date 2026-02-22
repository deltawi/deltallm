# Routing & Failover

DeltaLLM routes requests across multiple model deployments with automatic failover, retries, and intelligent error handling.

## Routing Strategies

Configure the strategy in `router_settings`:

```yaml
router_settings:
  routing_strategy: simple-shuffle
```

| Strategy | Description | Best For |
|----------|-------------|----------|
| `simple-shuffle` | Random distribution | General use, even load |
| `least-busy` | Fewest in-flight requests | Uneven workloads |
| `latency-based-routing` | Lowest observed latency | Latency-sensitive apps |
| `cost-based-routing` | Cheapest deployment first | Cost optimization |
| `usage-based-routing` | Proportional to weights | Controlled distribution |
| `priority-based-routing` | Highest priority first | Primary/fallback setups |

## Automatic Retries

When a request fails, DeltaLLM automatically retries on other deployments in the same model group:

```yaml
router_settings:
  num_retries: 3
  retry_after: 1
```

Retries use exponential backoff with a configurable base delay and optional jitter.

## Deployment Cooldowns

After repeated failures, a deployment is temporarily removed from the rotation:

```yaml
router_settings:
  cooldown_time: 60
  allowed_fails: 0
```

- `allowed_fails: 0` means the deployment is cooled down after the first failure
- `cooldown_time: 60` keeps the deployment out of rotation for 60 seconds
- Health checks can re-enable cooled-down deployments

## Fallback Chains

Define explicit fallback sequences between different model groups:

### General Fallbacks

```yaml
deltallm_settings:
  fallbacks:
    - ["gpt-4o", "gpt-4o-mini"]
    - ["claude-3-opus", "claude-3-sonnet"]
```

If all deployments in the `gpt-4o` group fail, the request automatically falls back to `gpt-4o-mini`.

### Context Window Fallbacks

```yaml
deltallm_settings:
  context_window_fallbacks:
    - ["gpt-4o-mini", "gpt-4o"]
```

When a request exceeds a model's context window, it falls back to a model with a larger context.

### Content Policy Fallbacks

```yaml
deltallm_settings:
  content_policy_fallbacks:
    - ["gpt-4o", "claude-3-sonnet"]
```

When a provider rejects content due to policy violations, the request is retried on an alternative provider.

## Error Classification

DeltaLLM automatically classifies errors to determine the appropriate retry and fallback behavior:

| Error Type | Retryable | Fallback Chain |
|------------|-----------|---------------|
| Timeout | Yes | General |
| Rate limit (429) | Yes | General |
| Server error (5xx) | Yes | General |
| Context window exceeded | No | Context window |
| Content policy violation | No | Content policy |
| Authentication error (401) | No | None |
| Bad request (400) | No | None |

## Monitoring Fallback Events

View recent fallback activity through the API:

```bash
curl http://localhost:8000/health/fallback-events \
  -H "Authorization: Bearer MASTER_KEY"
```

The Settings page in the admin UI also shows a live view of recent fallback events with color-coded error classifications.
