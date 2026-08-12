# Organization Tiers Rollout Runbook

This runbook covers the database migration, staged activation, monitoring, and rollback of organization tiers and weighted capacity pools.

## Release and Migration Notes

Organization tiers add tier, version, model-policy, capacity-pool, and organization-assignment tables. The migrations also add database constraints for one active version per tier, valid assignment windows, non-overlapping primary assignments, and model-policy references to capacity pools.

Before deploying:

- Back up the database and verify the deploy role can create the PostgreSQL `btree_gist` extension.
- Run the normal production migration command: `prisma migrate deploy --schema=./prisma/schema.prisma`.
- Do not use `prisma db push` for an existing production database.
- Keep `tier_policy_mode: disabled` for the first deploy.

The migration does not convert existing organizations, model access, prices, or rate limits into tiers. Existing behavior remains authoritative until an admin creates assignments and changes the runtime mode. This makes the release opt-in and avoids a required data backfill.

New creation behavior is mode-aware: `enforce` requires a primary tier, `shadow` defaults to a tier but retains an explicit legacy migration exception, and `disabled` preserves legacy creation. Existing organizations are never assigned or migrated silently. The historically supported POST upsert also remains non-migrating: updating an existing organization by ID in `enforce` does not invent a tier assignment, while a genuinely new ID still requires one. For tier-managed creation, the organization row, primary assignment, and cache-invalidation outbox row commit atomically. Because tier decisions are observational in `shadow`, creation also mirrors the selected tier version's allowed callable targets into legacy Asset Access inside that transaction. This keeps the new organization usable while producing meaningful shadow comparisons; the mirror is not refreshed when a later tier version is published.

Request-time policy is evaluated against the final normalized payload. Prompt templates, pre-call callbacks, and guardrails run once; validation, model access, budget checks, rate admission, and cache-key generation then use the resulting model and content. A callback therefore cannot rewrite a request to a model that the organization is not allowed to use. Parallel-request leases remain held until the final response body is sent, including streaming and cached responses, and are released on disconnect or cancellation.

Tier pricing keeps both the public callable model and the resolved provider model in spend metadata. When a deployment aliases a public name such as `premium-chat` to a catalogued provider model, catalog fallback pricing uses the provider model while access policy and customer-facing metadata continue to use the public name.

## Prerequisites

- PostgreSQL migrations are current.
- Redis is reachable from every API and batch-worker instance. Shared hard caps and weighted sharing use one atomic Redis call per rate-limit decision and need shared Redis state for multi-instance correctness.
- Prometheus scrapes every serving instance.
- A platform admin can access the tier preview and simulation endpoints.
- API and batch workers use the same tier-policy configuration.

The Helm chart exposes the complete runtime configuration under `config.general_settings`:

| Setting | Safe default | Constraint |
| --- | --- | --- |
| `tier_policy_mode` | `disabled` | `disabled`, `shadow`, or `enforce` |
| `tier_policy_missing_service_mode` | `fail_open` | `fail_open` or `fail_closed` |
| `tier_policy_refresh_interval_seconds` | `300` | Greater than `0` |
| `tier_policy_refresh_jitter_seconds` | `1` | At least `0` |
| `tier_policy_transition_grace_seconds` | `0.05` | At least `0` |
| `tier_policy_refresh_retry_delay_seconds` | `5` | Greater than `0` |
| `tier_capacity_fair_share_enabled` | `false` | Boolean |
| `tier_capacity_fair_share_active_ttl_seconds` | `10` | `1` to `300` |

Keep these values identical in API and split batch-worker ConfigMaps. The chart defaults do this automatically; use role-specific overrides only when deliberately testing configuration convergence failures.

## Rollout Sequence

Advance one stage at a time.

1. Deploy with tier policy disabled:

   ```yaml
   general_settings:
     tier_policy_mode: disabled
     tier_policy_missing_service_mode: fail_open
     tier_capacity_fair_share_enabled: false
   ```

2. Create draft tiers, model policies, and capacity pools. Publish the intended versions, assign only test organizations, and use the effective-policy preview and simulation endpoints.

3. Switch to shadow mode. Tier snapshots load and model-access differences are reported, but tier access, pricing, and rate limits are not authoritative:

   ```yaml
   general_settings:
     tier_policy_mode: shadow
     tier_policy_missing_service_mode: fail_open
     tier_capacity_fair_share_enabled: false
   ```

4. Hold shadow mode for a representative traffic cycle. Resolve unexpected `deltallm_tier_policy_shadow_mismatches_total` results and confirm snapshot refreshes succeed on every instance.

5. Canary enforcement with test organizations, conservative `hard_cap` pools, and `fail_open`. Then enable `weighted_fair` or `reserved_burst` on selected pools and opt into advanced admission:

   ```yaml
   general_settings:
     tier_policy_mode: enforce
     tier_policy_missing_service_mode: fail_open
     tier_capacity_fair_share_enabled: true
     tier_capacity_fair_share_active_ttl_seconds: 10
   ```

6. Move to `fail_closed` only after Redis, database, snapshot-refresh, and invalidation reliability meet the service's availability target:

   ```yaml
   general_settings:
     tier_policy_mode: enforce
     tier_policy_missing_service_mode: fail_closed
     tier_capacity_fair_share_enabled: true
     tier_capacity_fair_share_active_ttl_seconds: 10
   ```

Publishing or assigning a tier triggers snapshot invalidation. Verify all instances converge before expanding the canary.

After at least one enabled tier has an active version, a platform admin can create a tier-managed organization directly:

```bash
curl -sS -X POST "$DELTALLM_URL/ui/api/organizations" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "organization_name":"Acme",
    "primary_tier":{"tier_id":"tier-id","tier_version_id":null},
    "rpm_limit":1000
  }'
```

`tier_version_id: null` means the assignment follows the tier's active version. The optional `rpm_limit` in this example is an organization-wide hard cap, not a replacement for per-model tier limits. Tier-managed creation rejects direct organization model bindings and legacy per-model limit maps; create or clone a custom tier for those differences.

In `shadow` mode, the API derives the legacy access mirror on the server rather than accepting client-supplied direct bindings. Every allowed tier callable must exist in the current callable-target catalog; otherwise the whole create transaction is rejected. A deliberate new legacy organization must omit `primary_tier` and send `"legacy_policy_exception": true`; omission without that marker is rejected. Review the organization Asset Access tab and shadow-mismatch telemetry after publishing a new tier version. In `disabled` and `shadow`, that legacy access remains the runtime authority. In `enforce`, the tier is authoritative and organization Asset Access is hidden from normal editing.

## Verification

Preview and simulate an organization before enforcement:

```bash
curl -sS "$DELTALLM_URL/ui/api/organizations/$ORGANIZATION_ID/tier-policy-preview" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"

curl -sS -X POST "$DELTALLM_URL/ui/api/organizations/$ORGANIZATION_ID/tier-policy/simulate" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"callable_key":"gpt-4o-mini","billing_mode":"chat","prompt_tokens":750,"completion_tokens":250,"mode":"sync"}'
```

Set `billing_mode` to the route workload (`chat`, `embedding`, `rerank`, `image_generation`, `audio_speech`, or `audio_transcription`) and supply its matching usage fields. Audio modes accept `prompt_tokens` and `completion_tokens` in addition to their character, audio-token, or duration usage. Provider-specific transcription rules, including minimum billable durations, are applied independently to each configured route.

Treat `calculated_price.status` as part of the quote contract: `available` covers every configured route, `partial` excludes one or more unpriced routes, and `unavailable` means no reliable quote exists. `unpriced_candidate_count` covers routes whose pricing was evaluated but could not produce a price; `unevaluated_candidate_count` covers routes skipped because of an unsupported, mixed, or mismatched workload type. In particular, `reason: no_configured_routes` is not a zero-cost quote. Missing prices are never interpreted as zero: configure an explicit zero for the matching usage unit when a route is intentionally free. Known token models can use catalog pricing with source `default` when no regular token override exists; cache-only and sync-irrelevant batch fields do not suppress that fallback. A partial regular input/output override must cover every used token dimension and is never completed from the catalog. Unknown models require complete deployment or tier pricing. Image usage with input images likewise requires an input-image price even when an output-image price exists. Embedding and rerank quotes reject non-zero completion tokens. `pricing_sources` contains only the tier, deployment, or default fields that contributed to the displayed price. Static checks also include the organization's global and legacy per-model hard caps, matching request-time enforcement.

For aliased deployments, verify that runtime spend metadata contains the expected `callable_model` and `provider_model`. A default catalog price must resolve from the provider model; tier and deployment overrides still take precedence.

`amount_scope` is `aggregate`: `amount`, `minimum_amount`, and `maximum_amount` cover the full `request_count`. Use `per_request_amount`, `per_request_minimum_amount`, and `per_request_maximum_amount` when presenting a unit quote. The simulator uses configured routes but does not evaluate their current health or predict which route will serve a request. Runtime spend metadata uses `billing_status: unpriced` plus `missing_pricing_fields` when observed usage cannot be priced completely; do not interpret the numeric zero sentinel on such an event as an intentionally free request.

Inspect live capacity state:

```bash
curl -sS "$DELTALLM_URL/ui/api/tier-capacity/dashboard?top_org_limit=20&pool_limit=100" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

The dashboard reports the current 60-second window, RPM/TPM saturation, active organizations, top organization usage, temporary boost TTLs, and fair-share/pool limit hits. Check `live_data.status` before interpreting those values: failed Redis sections are listed in `live_data.failed_sections`, and unavailable numeric values are returned as `null` rather than a misleading zero.

Static `hard_cap` admissions emit the same capacity request and saturation metrics as advanced strategies. A rejected RPM or TPM admission records its dashboard heatmap entry in the same Lua transaction that rejects the request, without incrementing the rejected rate counter. Active-organization and fair-share decision metrics remain specific to `weighted_fair` and `reserved_burst`.

Prometheus capacity counters are aggregated by pool, model, tier, scope, and outcome. They do not expose an `organization_id` label, which keeps long-running series cardinality bounded. Use the admin capacity dashboard for bounded per-organization top-consumer and limit-hit diagnostics.

Monitor these Prometheus series:

- `deltallm_tier_policy_shadow_mismatches_total`
- `deltallm_tier_capacity_requests_total`
- `deltallm_tier_capacity_fair_share_decisions_total`
- `deltallm_tier_capacity_pool_saturation`
- `deltallm_tier_capacity_pool_active_organizations`
- `deltallm_tier_capacity_fair_share_latency_seconds`
- `deltallm_config_reload_events_total`

Useful starting queries:

```promql
sum by (pool_key, model, scope, outcome) (
  rate(deltallm_tier_capacity_requests_total[5m])
)

max by (pool_key, model, dimension) (
  deltallm_tier_capacity_pool_saturation
)

sum by (auth_source, difference_type, reason) (
  rate(deltallm_tier_policy_shadow_mismatches_total[10m])
)
```

Before expanding enforcement, confirm:

- Existing model visibility and customer prices match preview results.
- Allowed traffic continues below the configured saturation threshold.
- At saturation, observed organization shares track assignment weights within normal request-size rounding.
- Absolute RPM/TPM and parallel-request pool limits are never exceeded.
- API and batch traffic select the expected sync or batch limits.
- No request-path tier policy database reads appear in database traces.

Before publishing a release candidate, execute the production Lua suite against Redis explicitly; the test must not be allowed to pass by skipping:

```bash
DELTALLM_TEST_REDIS_URL=redis://localhost:6379/0 \
  uv run pytest -v -rs tests/services/test_tier_fair_share_redis_integration.py
```

## Temporary Capacity Boosts

Apply a short-lived boost only during an active incident or an approved customer event:

```bash
curl -sS -X POST "$DELTALLM_URL/ui/api/tier-capacity/boosts" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "pool_key":"growth-premium",
    "callable_key":"gpt-4o",
    "organization_id":"org-id",
    "weight_multiplier":2,
    "ttl_seconds":3600,
    "reason":"approved launch event"
  }'
```

Remove it early with:

```bash
curl -sS -X DELETE \
  "$DELTALLM_URL/ui/api/tier-capacity/boosts?pool_key=growth-premium&callable_key=gpt-4o&organization_id=org-id" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

Both actions require platform-admin permission and produce audit events attributed to the affected organization. Never use a boost as a permanent plan change; update and publish the tier instead.

## Rollback

For an individual pool, publish a new tier version using `hard_cap`. This immediately removes fair-share enforcement while preserving the shared ceiling.

For a system-wide rollback, set:

```yaml
   general_settings:
     tier_policy_mode: disabled
     tier_policy_missing_service_mode: fail_open
     tier_capacity_fair_share_enabled: false
```

Restart or roll all API and batch-worker instances with the same configuration. Existing non-tier Asset Access, deployment pricing, and standard rate limits become authoritative again. Database rows and temporary Redis state can remain in place for diagnosis; no destructive rollback is required.

## Troubleshooting

| Symptom | Check | Action |
| --- | --- | --- |
| `429` with `tier_pool_fair_share_*` scope and `weighted_share_exceeded` reason | Dashboard active weights, organization usage, threshold, and boost TTL | Correct the assignment weight or pool settings; use an audited temporary boost only when approved |
| `429` with `tier_pool_fair_share_*` scope and `pool_capacity_exceeded` reason | Absolute pool usage | Increase real provider capacity or reduce traffic; a weight boost cannot bypass the hard cap |
| `429` with `tier_pool_model_rpm` or `tier_pool_model_tpm` | Static pool counter, saturation metric, and dashboard limit-hit heatmap | Increase real provider capacity or reduce traffic; the rejected request did not consume counter capacity |
| Unexpected active-organization count | Requests within `tier_capacity_fair_share_active_ttl_seconds` and clock synchronization | Wait for the activity TTL or investigate missing/repeated organization identity |
| `503` after enabling `fail_closed` | Redis connectivity and tier snapshot freshness | Restore the backend; temporarily return to `fail_open` only under an approved availability policy |
| Different decisions across instances | Config values, snapshot etag, invalidation delivery, and refresh logs | Converge configuration and force a tier policy reload |
| Correct access but unexpected price | Tier mode, published model policy, sync/batch pricing fields, and spend metadata | Compare simulation output with the recorded tier pricing metadata |
| Capacity dashboard shows unavailable values | `live_data.status`, `live_data.failed_sections`, and Redis connectivity | Restore Redis; do not interpret `null` live values as zero usage |
