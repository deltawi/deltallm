# Router Settings

Use `router_settings` for the gateway-wide defaults that control deployment selection, retries, timeouts, and aliases.

## Quick Path

Start with a small, predictable config:

```yaml
router_settings:
  routing_strategy: simple-shuffle
  num_retries: 1
  retry_after: 1
  timeout: 600
  cooldown_time: 60
  allowed_fails: 0
```

That is enough for most first deployments.

## Reference

| Setting | Default | What it controls |
| --- | --- | --- |
| `routing_strategy` | `simple-shuffle` | Global default strategy for choosing a deployment |
| `num_retries` | `0` | Extra retry attempts after the first failure |
| `retry_after` | `0` | Base backoff delay in seconds |
| `timeout` | `600` | Request timeout in seconds |
| `cooldown_time` | `60` | Seconds a failing deployment stays out of rotation |
| `allowed_fails` | `0` | Failures allowed before cooldown starts |
| `enable_pre_call_checks` | `false` | Skip deployments already over configured RPM or TPM metadata |
| `model_group_alias` | `{}` | Friendly names that map to real model groups |
| `route_groups` | `[]` | File-defined route groups and membership |

## Supported Strategies

These strategy names are valid today:

- `simple-shuffle`
- `least-busy`
- `latency-based-routing`
- `cost-based-routing`
- `usage-based-routing`
- `tag-based-routing`
- `priority-based-routing`
- `weighted`
- `rate-limit-aware`

See [Routing & Failover](../features/routing.md) for when to use each one.

## Fallback Configuration

Fallback chains live under `deltallm_settings`, not `router_settings`:

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

## Model Aliases

Aliases let clients request a stable name while you map it to the real group:

```yaml
router_settings:
  model_group_alias:
    best-model: gpt-4o
    fast-model: gpt-4o-mini
```

## Related Pages

- [Routing & Failover](../features/routing.md)
- [Model Deployments](models.md)
