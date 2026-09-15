---
title: Budget and prompt dependency budgets
description: Combined budget checks, safe counter repair, durable alerts, and cold prompt cache fills.
status: experimental
audience: developers, operators
---

# Budget and prompt dependency budgets

The production Helm overlay explicitly selects `budget_enforcement_query_mode:
combined`. A normal decision reads key, user, team, organization and configured
team/model budgets in one PostgreSQL query. Legacy mode remains available for
query rollout/rollback. Both modes use the same decision order and reject missing
or corrupt budgeted economic state with HTTP 503, code `budget_state_unavailable`.
They never reconstruct spend by scanning historical events during admission.

These are preflight checks against recorded spend. Concurrent or unsettled
requests can overshoot a threshold. They are not atomic monetary reservations.
Changing the query mode does not establish a hard spending guarantee or a pod
throughput rating.

```yaml
general_settings:
  budget_enforcement_query_mode: combined
  budget_enforcement_query_timeout_seconds: 2
  budget_enforcement_shadow_sample_rate: 0.01
```

The two-second deadline covers the complete budget operation, including legacy
reads and resets. Optional sampled shadow comparison adds at most 100 ms and cannot
change a completed legacy decision; optional notification acceptance separately
adds at most 100 ms. Normal combined work is one SQL RPC. Reset transitions preserve
the guarded compare-and-swap and calendar anchor: at most four resets, with at
most nine SQL RPCs if every reset loses a race and must reread. No loop scales with
elapsed reset periods or historical event volume. Required reads use the existing
foreground pool; no additional database or Redis pool is created.

## Team/model counter repair

Apply the additive reconciliation migration before rolling out PR5. Existing
counters retain their prior authority. Newly inserted or recreated counters are
unverified until reconciled; subsequent ledger increments do not mark a partial
counter complete. A configured team/model budget therefore requires a verified
counter, including an explicitly verified zero for a genuinely unused model.

If a budgeted counter is missing, unverified, nonfinite or corrupt:

1. Pause inference and all spend/selector writers for the maintenance window.
   Drain accepted spend work and reconcile outstanding reservations first.
2. Establish the complete authoritative team/model total, including archived
   history and any legacy accounting corrections. Retained event rows alone do
   not prove completeness. Preserve the source calculation in the maintenance record.
3. Read the current `COALESCE(spend_exact, spend::numeric)` and `updated_at` from
   `deltallm_teammodelspend`. Run the bounded maintenance command with those exact
   expected values and the reviewed total:

   ```bash
   uv run python -m scripts.reconcile_budget_counter \
     --database-url "$DATABASE_URL" --team-id TEAM --model MODEL \
     --verified-total 12.345 --expected-spend 1.000 \
     --expected-updated-at 2026-09-15T10:00:00Z \
     --writers-paused-and-drained
   ```

   Omit both expected fields only when the row is absent. Concurrent changes or
   outstanding reservations prevent replacement. The command uses one bounded
   control connection and one guarded statement; it never scans spend history.
4. Verify the stored exact value and `reconciled_at`, run a scoped budget check,
   and resume writers. Never repair unknown state to zero to restore availability.

## Optional budget notifications

Organizations expose the soft-budget threshold used by admission. Enable the
existing governance/budget switches and configure email and/or Slack delivery.
Changing `budget_notifications_enabled` or `budget_alert_ttl_seconds` now requires
a restart. The global governance switch remains reloadable.

After a successful budget decision, alert acceptance uses one control-pool SQL
call with a 100-ms caller deadline. It performs no recipient discovery, Redis
dedupe or channel delivery on admission. Optional busy/full/unavailable outcomes
are measured and leave the budget decision intact. Accepted intents are durable,
coalesced per organization, and deduplicated across replicas for the configured
silence window. Total retained intents are capped at 10,000.

Each enabled API process owns one worker and one leased record at a time. It
borrows the existing control pool. Preparation has five bounded attempts;
preparation and delivery each have a five-second deadline under a 30-second fenced
lease. Accepted emails are delivered by the existing email outbox worker.
An ambiguous crash after dispatch begins is recorded as `delivery_unknown` and
is not blindly resent. Reconcile the downstream result before manual recovery.

Protected readiness details expose `budget_notification_worker` degradation
without withdrawing inference capacity. Monitor
`deltallm_notification_enqueue_total` with `kind="budget_threshold"` and
`channel="intent"` for acceptance/delivery outcomes. Unknown delivery outcomes retain their intent and suppress automatic re-enqueue for
seven days so a later threshold check cannot erase unresolved evidence. Other
terminal intents can be replaced after the silence window. Cleanup removes at
most 100 terminal rows per cycle after seven days and the silence window.
Keep a compatible worker running until accepted work drains before rollback.
The worker's drain deadline is 11 seconds. It drains concurrently with spend
workers before the dependent runtime services close. Size overall pod termination
grace against the complete application drain, as required by the lifecycle work
in PR8. This change does not qualify the existing total termination-grace budget.

The production example's 25 peak API processes can therefore own at most 25
notification tasks/leased records, with no in-memory backlog. Existing connection
ceilings remain 38 PostgreSQL and 96 Redis per API process: 950 and 2,400 at that
peak, before reserves or separately enabled batch workers. These are connection
ceilings, not supported throughput. See [dependency capacity](dependency-capacity.md).

See the [design decision](../design/pr5-budget-prompts.md) for state and capacity details.

## Prompt cache fills

Cold implicit bindings retain user/key/team/organization/group precedence.
One chain uses one Redis MGET, one batched SQL lookup, and one nontransactional
pipeline containing at most five SETEX commands. Warm L1 resolution adds no cache
or binding SQL calls. Separate replicas can reuse L2 positive and negative entries.
The pipeline uses the existing ordinary-cache pool and its connection/socket bounds.

Each cached binding is schema- and tenant-checked, bounded to 64 KiB with keys up
to 1 KiB. Invalid or oversized cache entries are misses; oversized durable results
are returned without caching. Cache failures preserve the durable result.
In-flight fills retain their original epoch, and requests after invalidation do
not join older fills. Existing bounded single-flight and negative-cache settings
remain in force. Distributed fill coordination is conditional on measured cold
start pressure, rather than adding a lock to every lookup.

## Measurements

The reproducible SQL probe uses the isolated `deltallm_concurrency` database:

```bash
uv run python -m tests.performance.measure_budget_dependencies \
  --entities 5000 --events 100000 --rate 50 --duration 10 \
  --output .load-results/budget-dependencies
```

It seeds and removes only uniquely named synthetic fixtures, captures indexed
five-row EXPLAIN plans before/after historical growth, and records constant-arrival
raw samples and SQL counts for both modes. This service-level probe excludes HTTP,
provider, Redis and pod resources. The completed evidence and HTTP comparison are
linked from the [PR5 measurement directory](../project/benchmarks/budget-prompts-2026-09-15/README.md).
