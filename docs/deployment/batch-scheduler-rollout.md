# Batch Scheduler Rollout Runbook

This runbook covers the embeddings batch scheduler rollout modes and the checks operators should use before advancing or rolling back.

## Prerequisites

- `embeddings_batch_enabled=true`
- API and batch-worker pods are using the same scheduler config.
- Batch-worker pods have enough DB pool capacity for short scheduler queries.
- Prometheus is scraping batch-worker metrics.
- The admin API is reachable by a platform admin.
- The Phase 6 in-progress capacity index exists before high-traffic canaries:

  ```sql
  CREATE INDEX CONCURRENTLY IF NOT EXISTS "deltallm_batch_item_in_progress_capacity_idx"
  ON "deltallm_batch_item"("scheduling_model_group", "lease_expires_at", "batch_id")
  WHERE "status" = 'in_progress';
  ```

  Verify with:

  ```sql
  SELECT indexname
  FROM pg_indexes
  WHERE tablename = 'deltallm_batch_item'
    AND indexname = 'deltallm_batch_item_in_progress_capacity_idx';
  ```

## Modes

Active mode controls real claims and leases. Shadow mode only computes recommendations and metrics.
Fair-share and smart shadow recommendations use read-only flow previews; durable flow refreshes come
from active fair-share claims, the backfill worker, or explicit admin refreshes.
Workers start shadow decisions asynchronously at the active decision point and record comparisons
after the active claim completes. Shadow decisions are bounded by
`embeddings_batch_scheduler_shadow_decision_timeout_seconds` and
`embeddings_batch_scheduler_shadow_max_pending_decisions`, so slow shadow reads can be recorded or
dropped without delaying active claims.
Fair-share and smart flow scans are bounded by
`embeddings_batch_scheduler_max_active_flows_per_decision` and
`embeddings_batch_scheduler_max_candidate_jobs_per_flow`; increase these only when scheduler
decision latency has enough headroom.

- `fifo_v1`: legacy job FIFO claims.
- `slice_v1`: work-slice claims across queued jobs.
- `model_capacity_v1`: work-slice claims gated by model capacity.
- `fair_share_v1`: model capacity plus tenant fair-share flow selection.
- `smart_v1`: fair-share selection plus size and age ranking.
- `none`: disables shadow mode.

## Rollout Sequence

Use one stage at a time and wait for metrics to stabilize before advancing.

1. `embeddings_batch_scheduler_mode=fifo_v1`
   `embeddings_batch_scheduler_shadow_mode=slice_v1`

2. `embeddings_batch_scheduler_mode=slice_v1`
   `embeddings_batch_scheduler_shadow_mode=model_capacity_v1`

3. `embeddings_batch_scheduler_mode=model_capacity_v1`
   `embeddings_batch_scheduler_shadow_mode=fair_share_v1`

4. `embeddings_batch_scheduler_mode=fair_share_v1`
   `embeddings_batch_scheduler_shadow_mode=smart_v1`

5. `embeddings_batch_scheduler_mode=smart_v1`
   `embeddings_batch_scheduler_shadow_mode=none`

Hold each stage for at least 30 minutes or one representative batch traffic cycle, whichever is
longer. Do not advance while any rollback threshold below is active.

## Helm Values

Set the active and shadow modes in both API and worker config paths. When `batchWorker.enabled=true`, verify worker overrides do not diverge from shared config unless that divergence is intentional.

```yaml
config:
  general_settings:
    embeddings_batch_scheduler_mode: model_capacity_v1
    embeddings_batch_scheduler_shadow_mode: fair_share_v1
    embeddings_batch_scheduler_strict_model_homogeneity_enabled: true
```

Render before rollout:

```bash
helm template deltallm ./helm --values helm/values-production.yaml
```

## Verification

Check the scheduler status endpoint after every deploy:

```bash
curl -sS "$DELTALLM_URL/ui/api/batches/scheduler/status" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

Expected fields:

- `active_mode` matches the intended active stage.
- `shadow_mode` matches the intended shadow stage or `none`.
- `local_worker.matches_config=true` when the serving process owns a batch worker; split API pods
  usually report `local_worker.present=false`.
- `effective.*` shows active and shadow capability flags.
- `model_capacity.enabled`, `fair_share.enabled`, and `size_aging.enabled` match the selected active or shadow mode.
- `metrics.scope=process_local` and `metrics.cluster_wide=false`.
- `metrics.metric_names.*` lists the Prometheus series to use for worker-pod counters and histograms.
- `metrics.process_local_samples.counters.shadow_comparisons` has bounded result labels.
- `metrics.process_local_samples.counters.rollbacks` is empty during forward rollout.
- `fair_share.max_active_flows_per_decision` and `fair_share.max_candidate_jobs_per_flow` match the
  intended worker bounds.

The admin status endpoint is useful for config and single-process smoke checks. In split API/worker
deployments, the embedded metric samples come from the API process that served the request, so use
Prometheus for cluster-wide worker scheduler decisions and latencies.

Check Prometheus metrics:

- `deltallm_batch_scheduler_shadow_comparisons_total`
- `deltallm_batch_scheduler_shadow_better_choice_total`
- `deltallm_batch_scheduler_rollbacks_total`
- `deltallm_batch_scheduler_mode_info`
- `deltallm_batch_scheduler_oldest_wait_seconds`
- `deltallm_batch_scheduler_fairness_deviation`
- `deltallm_batch_scheduler_decision_latency_seconds`
- `deltallm_batch_time_to_first_claim_seconds`
- `deltallm_batch_completion_latency_seconds`
- `deltallm_config_reload_events_total`

Prometheus checks for canary advancement:

- Config split-brain: `count by (active_mode, shadow_mode) (deltallm_batch_scheduler_mode_info)`.
  All worker pods must report one intended active/shadow pair before evaluating scheduler behavior.
- Config propagation: `sum by (source, result) (rate(deltallm_config_reload_events_total[10m]))`.
  Pub/sub failures must be followed by `source="poll", result="applied"` or `unchanged` samples
  on every pod. Treat `source="poll", result="failed"` as a config convergence failure until DB
  reads recover.
- Shadow mismatch rate:
  `sum(rate(deltallm_batch_scheduler_shadow_comparisons_total{result=~"job_mismatch|flow_mismatch|actual_empty"}[10m])) / sum(rate(deltallm_batch_scheduler_shadow_comparisons_total[10m]))`.
  Keep this below 5 percent unless every mismatch bucket has an understood, documented cause.
- Decision latency: p95 of `deltallm_batch_scheduler_decision_latency_seconds` should stay below
  250 ms and p99 below 500 ms for 15 minutes.
- Wait regression: oldest wait for the canary model/tier should not exceed 2x the previous stage for
  15 minutes.
- Fairness deviation: `deltallm_batch_scheduler_fairness_deviation` should not trend upward for
  active tenants sharing the same model/tier.
- Reclaims: `deltallm_batch_item_reclaims_total` should stay below 5 percent of item completion rate.

Before advancing, verify:

- Shadow mode recommendations do not increase item or job lease counts.
- `job_mismatch` and `actual_empty` shadow comparison rates are understood.
- Oldest queue wait is not regressing for small jobs or lower-volume tenants.
- Model capacity stages do not leave healthy model groups idle while eligible work exists.
- Fair-share and smart stages preserve progress for large jobs while improving smaller-job latency.

## Rollback

Use config-only rollback first:

```bash
curl -sS -X POST "$DELTALLM_URL/ui/api/batches/scheduler/mode" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"active_mode":"fifo_v1","shadow_mode":"none","reason":"scheduler rollback"}'
```

If the admin API is unavailable, apply the same rollback through Helm or the dynamic settings store:

```yaml
config:
  general_settings:
    embeddings_batch_scheduler_mode: fifo_v1
    embeddings_batch_scheduler_shadow_mode: none
```

Then verify:

- `/ui/api/batches/scheduler/status` reports `active_mode=fifo_v1` and `shadow_mode=none`.
- A worker process serving the status endpoint reports `local_worker.matches_config=true`; in split
  deployments, verify worker pods through logs/metrics because API-only pods cannot report worker
  local state.
- `deltallm_batch_scheduler_rollbacks_total` increments with a bounded reason.
- Workers continue claiming and completing batches.

If only the shadow stage is noisy, keep active mode unchanged and set:

```yaml
embeddings_batch_scheduler_shadow_mode: none
```

## Troubleshooting

- If model-capacity stages stop claiming, check healthy deployments, backpressure, RPM/TPM remaining values, and `model_capacity.fail_open`.
- If fair-share stages starve a tenant, check `/ui/api/batches/scheduler/flows` for deficit, weight, queued work units, and in-flight work units.
- If smart mode over-prioritizes old large jobs, reduce `embeddings_batch_max_age_credit_work_units` or increase `embeddings_batch_aging_seconds_per_work_unit`.
- If admin status and worker behavior disagree, compare API and worker rendered ConfigMaps.

## Incident Runbooks

### Model Has Backlog But No Claims

- Metrics: `deltallm_batch_oldest_job_age_seconds`, `deltallm_batch_scheduler_oldest_wait_seconds`, `deltallm_batch_scheduler_decision_latency_seconds`, `deltallm_batch_model_group_deferrals_total`.
- Admin endpoint: `/ui/api/batches/scheduler/status`, then `/ui/api/batches/scheduler/flows?model_group=<model>&service_tier=<tier>`.
- Likely causes: no healthy deployment for the model group, capacity snapshot reports zero available items or work units, model group is excluded from fair-share, or API and worker scheduler modes diverged.
- Config mitigations: temporarily set `embeddings_batch_scheduler_mode=model_capacity_v1`, disable shadow with `embeddings_batch_scheduler_shadow_mode=none`, or enable model-capacity fail-open only after confirming provider health telemetry is stale.
- Rollback threshold: oldest wait for a healthy model exceeds the previous stage by 2x for 15 minutes, or no claims occur for a model with queued work for 10 minutes.

### Healthy Models Idle While Another Model Is Saturated

- Metrics: `deltallm_batch_model_capacity_slots`, `deltallm_batch_scheduler_decision_latency_seconds`, `deltallm_batch_scheduler_shadow_comparisons_total`.
- Admin endpoint: `/ui/api/batches/scheduler/status`.
- Likely causes: workers are still using FIFO or slice mode, capacity groups are missing for the idle model, or a saturated head-of-line model is not being skipped.
- Config mitigations: advance only to `model_capacity_v1`, keep `fair_share_v1` or `smart_v1` in shadow, and verify worker ConfigMaps match the API ConfigMap.
- Rollback threshold: idle healthy model utilization remains at zero while eligible queued jobs exist for 10 minutes after switching modes.

### Tenant Starvation Or Noisy Tenant Dominance

- Metrics: `deltallm_batch_scheduler_fairness_deviation`, `deltallm_batch_scheduler_shadow_better_choice_total`, `deltallm_batch_scheduler_shadow_comparisons_total`.
- Admin endpoint: `/ui/api/batches/scheduler/flows?model_group=<model>&service_tier=<tier>`.
- Likely causes: tenant weights are wrong, deficits are not refilling, one tenant has excessive in-flight work, or fair-share is disabled for the model group.
- Config mitigations: reduce `embeddings_batch_tenant_max_in_flight_work_units`, lower `embeddings_batch_scheduler_max_deficit_multiplier`, or fall back to `model_capacity_v1`.
- Rollback threshold: one active tenant receives no first claims for 15 minutes while another tenant continues claiming the same model and tier.

### Smart Mode Starves Large Jobs

- Metrics: `deltallm_batch_time_to_first_claim_seconds`, `deltallm_batch_completion_latency_seconds`, `deltallm_batch_scheduler_oldest_wait_seconds`, `deltallm_batch_scheduler_shadow_better_choice_total`.
- Admin endpoint: `/ui/api/batches/scheduler/status`.
- Likely causes: age credit is too weak, small-job threshold is too high, or minimum large-job interval is too long for current backlog.
- Config mitigations: lower `embeddings_batch_aging_seconds_per_work_unit`, increase `embeddings_batch_max_age_credit_work_units`, reduce `embeddings_batch_small_job_max_work_units`, or return active mode to `fair_share_v1`.
- Rollback threshold: oldest large-job first-claim latency exceeds the prior stage by 2x for 30 minutes.

### Retry Storm Requeues Too Aggressively

- Metrics: `deltallm_batch_item_retries_total`, `deltallm_batch_item_retry_delay_seconds`, `deltallm_batch_microbatch_requeues_total`, `deltallm_batch_scheduler_oldest_wait_seconds`.
- Admin endpoint: `/ui/api/batches/scheduler/status`.
- Likely causes: retry `not_before_at` gates are ignored, transient provider errors are classified too broadly, or microbatch isolation is repeatedly requeueing the same work.
- Config mitigations: keep active mode at the last stable stage, disable shadow if logs are noisy, and tune retry delay or microbatch isolation settings before re-enabling smart mode.
- Rollback threshold: retry counters grow faster than successful item completions for 10 minutes, or the same batch is reclaimed more than twice without progress.

### Worker Crash Or Lease Expiry Causes Duplicate Work

- Metrics: `deltallm_batch_item_reclaims_total`, `deltallm_batch_claim_empty_jobs_total`, `deltallm_batch_completion_latency_seconds`, `deltallm_batch_scheduler_decision_latency_seconds`.
- Admin endpoint: `/ui/api/batches/scheduler/status`.
- Likely causes: item lease is shorter than provider latency, workers are terminated without graceful shutdown, or DB transactions are timing out under contention.
- Config mitigations: increase `embeddings_batch_item_lease_seconds`, reduce worker concurrency, or scale DB pool capacity before retrying the rollout.
- Rollback threshold: duplicate provider executions are observed, or reclaim rate exceeds 5 percent of item completion rate for 15 minutes.

### Redis Unavailable Or Degraded

- Metrics: `deltallm_config_reload_events_total`, `deltallm_batch_scheduler_decision_latency_seconds`, `deltallm_batch_claim_empty_jobs_total`, Redis client error logs, and batch completion counters.
- Admin endpoint: `/ui/api/batches/scheduler/status`.
- Likely causes: Redis outage, Redis latency, config pub/sub listener failure, lock TTL churn, or transient counter failures.
- Config mitigations: verify the active stage still uses Postgres as source of truth, disable shadow mode if comparison logging amplifies error volume, and avoid enabling Redis-dependent optimizations until Redis is healthy.
- Fallback check: dynamic config should still converge through the periodic DB poll; investigate pods that report `source="pubsub", result="listener_failed"` without later `source="poll"` samples.
- Rollback threshold: claims stop or duplicate work appears during Redis degradation; otherwise keep the current stage if DB-backed progress continues.

### Postgres Contention Or Slow Scheduler Decisions

- Metrics: `deltallm_batch_scheduler_decision_latency_seconds`, DB pool saturation, transaction timeout logs, `deltallm_batch_claim_empty_jobs_total`.
- Admin endpoint: `/ui/api/batches/scheduler/status`.
- Likely causes: too many worker pods, high worker concurrency, broad fair-share scans, missing indexes for queue filters, long transactions, or finalization work competing with claim queries.
- Config mitigations: reduce worker concurrency, scale the DB pool, lower `embeddings_batch_scheduler_max_active_flows_per_decision` or `embeddings_batch_scheduler_max_candidate_jobs_per_flow`, keep active mode at `model_capacity_v1`, and pause advancement to `fair_share_v1` or `smart_v1`.
- Rollback threshold: scheduler decision p95 is above 500 ms for 10 minutes, or DB transaction timeouts coincide with rising oldest queue wait.

### Finalization Backlog Blocks New Claims

- Metrics: `deltallm_batch_finalize_latency_seconds`, `deltallm_batch_finalization_retries_total`, `deltallm_batch_completion_outbox_failures_total`, `deltallm_batch_scheduler_oldest_wait_seconds`.
- Admin endpoint: `/ui/api/batches/scheduler/status`.
- Likely causes: artifact storage failures, callback/outbox delays, finalization retries monopolizing worker capacity, or too few dedicated worker resources.
- Config mitigations: reduce batch execution concurrency, increase worker resources, isolate finalization retries, or return active mode to the last stable stage while clearing finalization failures.
- Rollback threshold: queued first-claim latency increases while finalization latency p95 remains above 5 minutes for 15 minutes.
