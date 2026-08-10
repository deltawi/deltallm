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

## Prerequisites

- PostgreSQL migrations are current.
- Redis is reachable from every API and batch-worker instance. Weighted sharing uses one atomic Redis call per rate-limit decision and needs shared Redis state for multi-instance correctness.
- Prometheus scrapes every serving instance.
- A platform admin can access the tier preview and simulation endpoints.
- API and batch workers use the same tier-policy configuration.

## Rollout Sequence

Advance one stage at a time.

1. Deploy with tier policy disabled:

   ```yaml
   general_settings:
     tier_policy_mode: disabled
     tier_policy_missing_service_mode: fail_open
   ```

2. Create draft tiers, model policies, and capacity pools. Publish the intended versions, assign only test organizations, and use the effective-policy preview and simulation endpoints.

3. Switch to shadow mode. Tier snapshots load and model-access differences are reported, but tier access, pricing, and rate limits are not authoritative:

   ```yaml
   general_settings:
     tier_policy_mode: shadow
     tier_policy_missing_service_mode: fail_open
   ```

4. Hold shadow mode for a representative traffic cycle. Resolve unexpected `deltallm_tier_policy_shadow_mismatches_total` results and confirm snapshot refreshes succeed on every instance.

5. Canary enforcement with test organizations, conservative `hard_cap` pools, and `fail_open`. Then enable `weighted_fair` or `reserved_burst` on selected pools.

6. Move to `fail_closed` only after Redis, database, snapshot-refresh, and invalidation reliability meet the service's availability target:

   ```yaml
   general_settings:
     tier_policy_mode: enforce
     tier_policy_missing_service_mode: fail_closed
   ```

Publishing or assigning a tier triggers snapshot invalidation. Verify all instances converge before expanding the canary.

## Verification

Preview and simulate an organization before enforcement:

```bash
curl -sS "$DELTALLM_URL/ui/api/organizations/$ORGANIZATION_ID/tier-policy-preview" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"

curl -sS -X POST "$DELTALLM_URL/ui/api/organizations/$ORGANIZATION_ID/tier-policy/simulate" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"callable_key":"gpt-4o-mini","prompt_tokens":750,"completion_tokens":250,"mode":"sync"}'
```

Inspect live capacity state:

```bash
curl -sS "$DELTALLM_URL/ui/api/tiers/capacity/dashboard?top_org_limit=20" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

The dashboard reports the current 60-second window, RPM/TPM saturation, active organizations, top organization usage, temporary boost TTLs, and fair-share/pool limit hits.

Monitor these Prometheus series:

- `deltallm_tier_policy_shadow_mismatches_total`
- `deltallm_tier_capacity_requests_total`
- `deltallm_tier_capacity_pool_saturation_ratio`
- `deltallm_tier_capacity_pool_active_organizations`
- `deltallm_config_reload_events_total`

Useful starting queries:

```promql
sum by (pool_key, model, scope, outcome) (
  rate(deltallm_tier_capacity_requests_total[5m])
)

max by (pool_key, model, scope) (
  deltallm_tier_capacity_pool_saturation_ratio
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

## Temporary Capacity Boosts

Apply a short-lived boost only during an active incident or an approved customer event:

```bash
curl -sS -X POST "$DELTALLM_URL/ui/api/tiers/capacity/boosts" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "pool_key":"growth-premium",
    "callable_key":"gpt-4o",
    "organization_id":"org-id",
    "multiplier":2,
    "expires_in_seconds":3600
  }'
```

Remove it early with:

```bash
curl -sS -X DELETE \
  "$DELTALLM_URL/ui/api/tiers/capacity/boosts?pool_key=growth-premium&callable_key=gpt-4o&organization_id=org-id" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

Both actions require platform-admin permission and produce audit events. Never use a boost as a permanent plan change; update and publish the tier instead.

## Rollback

For an individual pool, publish a new tier version using `hard_cap`. This immediately removes fair-share enforcement while preserving the shared ceiling.

For a system-wide rollback, set:

```yaml
general_settings:
  tier_policy_mode: disabled
  tier_policy_missing_service_mode: fail_open
```

Restart or roll all API and batch-worker instances with the same configuration. Existing non-tier Asset Access, deployment pricing, and standard rate limits become authoritative again. Database rows and temporary Redis state can remain in place for diagnosis; no destructive rollback is required.

## Troubleshooting

| Symptom | Check | Action |
| --- | --- | --- |
| `429` with `*_fair_share` scope | Dashboard active weights, organization usage, threshold, and boost TTL | Correct the assignment weight or pool settings; use an audited temporary boost only when approved |
| `429` with base `tier_pool_model_*` scope | Absolute pool usage | Increase real provider capacity or reduce traffic; a weight boost cannot bypass the hard cap |
| Unexpected active-organization count | Requests in the last 120 seconds and clock synchronization | Wait for the activity TTL or investigate missing/repeated organization identity |
| `503` after enabling `fail_closed` | Redis connectivity and tier snapshot freshness | Restore the backend; temporarily return to `fail_open` only under an approved availability policy |
| Different decisions across instances | Config values, snapshot etag, invalidation delivery, and refresh logs | Converge configuration and force a tier policy reload |
| Correct access but unexpected price | Tier mode, published model policy, sync/batch pricing fields, and spend metadata | Compare simulation output with the recorded tier pricing metadata |
