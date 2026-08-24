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

Tip: check the effective `allowed_fails` value in the config your deployment actually loads. Depending on how you run DeltaLLM, the source of truth is usually your `config.yaml` from `DELTALLM_CONFIG_PATH`, your Helm `values.yaml`, or the rendered Kubernetes ConfigMap.

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

Each file-defined route group should declare one workload mode. For compatibility with older files,
an omitted mode is inferred when all enabled, resolvable members have one deployment mode; an empty
or unresolved legacy group falls back to `chat`. Mixed member modes are rejected. Declare `mode`
explicitly for stable, warning-free configuration:

```yaml
router_settings:
  route_groups:
    - key: search-embeddings
      mode: embedding
      strategy: weighted
      members:
        - deployment_id: embeddings-primary
          weight: 3
        - deployment_id: embeddings-secondary
          weight: 1
```

A request whose endpoint workload does not match the route group is rejected before shared
routing-state reads. Invalid group/member combinations fail runtime snapshot validation and do not
replace the last valid live registry.

`allowed_fails: 0` starts cooldown on the first health-affecting provider failure. After
`cooldown_time` expires, the router admits one shared-Redis half-open request for that deployment;
successful recovery restores normal routing and failed recovery re-enters cooldown. Request-side
validation, policy, budget, guardrail, cancellation, and local gateway-capacity failures are
health-neutral. Background health checks are optional because request traffic can perform the
bounded recovery transition.

Operator-triggered health checks use a short-lived manual probe claim. After cooldown expiry they
may own the same fenced recovery transition as request traffic or the background checker. During an
active manual cooldown, a successful provider probe reports that the provider responded but does
not restore routing health. A concurrent owned recovery returns HTTP `409` instead of running a
second recovery probe.

Health hashes use rolling 30-day retention, extended when a longer cooldown requires it. Mutable
provider-health keys include an opaque deployment generation derived from the provider
configuration and its durable incarnation. A late result from a retired provider configuration can
therefore update only the retired generation. Runtime reload atomically publishes the new registry
generation before attempting bounded, exact cleanup of retired health keys. Cleanup failure is
reported as degraded maintenance and does not make a persisted model mutation fail. Metadata-only
changes such as weight and priority retain the existing health generation and state.

Router state currently targets the standalone Redis topology created by application bootstrap;
Redis Cluster is not supported by its multi-key admission and health-transition scripts.
Every router key is scoped as `deltallm:<app_env>:v1:<router-capability>:<identifiers>`. This is a
schema cutover from the previous unscoped ephemeral router keys: drain replicas running the old
binary before sending traffic to namespaced replicas, and use the same drain procedure for
rollback. Do not run the two key schemas concurrently because admission and cooldown ownership
would be split.

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

Short version:

- `simple-shuffle`: best default when deployments are equivalent
- `weighted`: use for planned traffic splits
- `priority-based-routing`: use for primary and standby routing
- `least-busy`: use for burst balancing
- `latency-based-routing`: use for latency-sensitive traffic
- `cost-based-routing`: use for lowest-cost routing
- `usage-based-routing`: use to spread quota usage
- `rate-limit-aware`: use to avoid hot deployments near RPM or TPM caps
- `tag-based-routing`: use when tags decide eligibility and you want the route group to make that explicit

For non-text workloads, usage-aware routing can also use these deployment fields when they are configured:

- `image_pm_limit`
- `audio_seconds_pm_limit`
- `char_pm_limit`
- `rerank_units_pm_limit`

See [Routing & Failover](../features/routing.md) for the full behavior and setup examples.

## Route-Group Policy Support

Route-group policies currently support:

- `mode`
- `strategy`
- `members`
- `timeouts.global_ms` or `timeouts.global_seconds`
- `retry.max_attempts`
- `retry.retryable_error_classes`

Helpful shortcut modes:

- `weighted` maps to `weighted`
- `fallback` maps to `priority-based-routing`

Do not treat `conditional` or `adaptive` as active runtime policy behaviors today.

The policy `mode` field is a routing shortcut (`weighted` or `fallback`); it is separate from the
route group's workload `mode`. When `members` is omitted, the policy inherits the group's enabled
members. Newly saved policies treat an explicit list as authoritative. Policies created before this
semantics version retain their legacy widening behavior, including when rolled back. A policy can
disable an eligible member but cannot reactivate a group member disabled by an operator.

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
