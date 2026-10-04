---
title: Durable telemetry ingestion rollout
description: Release-specific steps for moving audit and spend ingestion to durable outboxes.
status: stable
audience: operators
applies_to: Releases whose notes explicitly link this runbook.
---

# Durable telemetry ingestion rollout

Durable telemetry mode moves spend aggregation, audit persistence, and prompt-render logging onto bounded outboxes with separate Prisma allocations for acceptance and background workers. Both ingestion modes default to `legacy` and are restart-bound. In legacy audit mode, required audit and prompt-render records are persisted synchronously and fail closed; only best-effort audit events use the bounded in-process queue.

!!! warning "Check your release notes"
    Use this runbook only when the release notes for your target version link to it. Do not assume its migration names or compatibility rules apply to another release.

## Preconditions

1. Apply all Prisma migrations through `20260817140000_fence_email_delivery` before deploying this binary. The application assumes the additive email-audit reconciliation, fenced email-delivery claims, and exact-spend columns exist before startup.
2. Provision database headroom for `telemetry_db_pool_size` acceptance connections and `telemetry_worker_db_pool_size` worker connections per process. Add the control and foreground pools and all API/worker rollout overlap using the [dependency capacity calculation](dependency-capacity.md). Consumers, cleanup and retention share the worker allocation; they are not extra pools.
3. Configure Redis for prompt cache freshness and multi-replica audit policy invalidation. PostgreSQL advisory locks and the policy-change transaction remain the privacy correctness boundary; audit content writes do not rely on Pub/Sub delivery.
4. Verify the server-owned spend event identity, Prisma transaction-client detection, blocked-event replay, and claim-token fencing tests before enabling spend producers.
5. Set the pod termination grace period above `telemetry_shutdown_drain_timeout_seconds`.
   If email is enabled, also set it above `email_worker_shutdown_drain_timeout_seconds`.
   The Helm default is 30 seconds. Each shutdown deadline defaults to 20 seconds.
   This permits cancellation and connection cleanup before `SIGKILL`.
6. Keep the audit and spend ingestion modes on `legacy` until all replicas use the corrected admission sequence.
   This requirement includes API and worker replicas.
   The corrected version acquires telemetry admission locks in a transaction statement that only acquires locks.
   It reads capacity or content policy in the next statement.
   An older waiter can retain a PostgreSQL snapshot from before the lock.
   Outbox mode is not safe while these versions operate together.

## Audit and prompt-render rollout

1. Deploy the fixed binary to every replica with `audit_ingestion_mode: legacy`. Confirm that no older API or worker replica remains, then confirm the migration and capacity rows exist:

   ```sql
   SELECT queue_name, pending_count
   FROM deltallm_telemetry_ingestion_capacity
   WHERE queue_name IN ('audit', 'spend');
   ```

2. Only after the binary rollout is complete, enable `audit_ingestion_mode: outbox` on one canary replica and restart it. Keep `audit_ingestion_worker_enabled: true`.
3. Verify required events produce `outcome="accepted"`. Required writes fail closed with a controlled `503` at the hard capacity bound; investigate any such response or best-effort drop immediately.
4. Disable audit content storage for a test organization. Confirm the policy update and active-envelope redaction commit atomically, claimed rows are scrubbed before completion, and other replicas receive the Redis invalidation.
5. Confirm successful cached prompt resolutions enqueue a prompt-render record without writing directly through the request database pool.
6. Roll the remaining replicas. Do not change the ingestion mode through dynamic configuration without a restart.

## Spend rollout

For ordinary operation intents and reserved settlement capacity, follow the
[spend recovery runbook](spend-recovery.md) after this outbox rollout. It preserves
unknown outcomes across process loss and does not provide hard monetary budgets.

After the P0 migration, lock-snapshot concurrency tests, and fixed-binary rollout are complete:

1. Start with `spend_ingestion_overload_policy: sync_fallback`, a conservative `spend_ingestion_batch_size`, and `spend_ingestion_max_pending_events` sized for the tolerated outage window.
2. Confirm that no replica with the same-statement admission implementation remains.
   Enable outbox mode on one canary.
   Verify that a claimed batch completes all these writes in one transaction:

   - One bulk spend-event insert.
   - At most one deterministic update per ledger entity type.
   - One bulk acknowledgement.

3. Compare the spend-event total with key, user, team, organization, and team-model ledger deltas. Retries must not increment a ledger twice.
4. Increase the canary share while watching foreground, telemetry acceptance, telemetry worker and control pool saturation independently.
5. Roll all replicas only after the oldest-event age returns to normal after an induced worker pause.

The exact-spend migration only adds schema elements.
New writers populate `NUMERIC(38,18)` columns and the legacy float columns in the same statement.
Each exact accumulator uses the existing float only on its first update after migration.

Do not run an unbounded backfill of the full table as release DDL.
Use this sequence for the later data migration:

1. Backfill existing event rows with a supervised job that uses primary-key pagination.
2. Reconcile the exact totals with the legacy totals.
3. After reconciliation, switch readers to the exact columns.
4. Remove float columns in a separate contract release.

Test-email delivery results use the email row as their durable reconciliation source. A terminal provider result and `delivery_audit_status='pending'` are committed together, after which workers claim the audit with a fenced renewable lease and stable event ID. Audit retry must never move the email back to `queued` or `retrying`; rows with unresolved required audit are excluded from retention cleanup. Exhausted delivery audits move to `blocked`, make email-worker readiness fail, and require an investigated platform-admin replay through `POST /ui/api/email/outbox/{email_id}/delivery-audit/replay`.

The system does not automatically retry external email sends after an ambiguous transport result.
It also does not retry a successful provider call whose database acknowledgement failed.
After the fenced delivery lease expires, these rows move to `delivery_unknown`.
Automatic claims and retention cleanup exclude these rows.

1. Confirm the message state with the configured provider.
2. Use `POST /ui/api/email/outbox/{email_id}/resolve-delivery` with `{"resolution":"sent"}` or `{"resolution":"failed"}`.

The resolution and its necessary operator audit commit atomically.
Do not resolve an uncertain row as failed only to force another send.
Create a new server-owned email event only after you establish that the provider did not accept the original.

## Alerts and overload behavior

Alert before capacity is exhausted, using both utilization and age:

- warning: capacity utilization above 70% for 10 minutes or oldest event age above the normal processing SLO;
- critical: utilization above 90%, oldest age continuing to rise, any blocked required record, or any email in `delivery_unknown`;
- page immediately if required audit persistence fails or either required ingestion path begins returning `503` responses.

Spend overload uses a synchronous fallback bounded independently by active transactions, waiting requests, queue time, and execution time. Requests beyond any bound receive a controlled local `503`; they do not accumulate as unbounded coroutine waiters. `fail_closed` bypasses fallback and returns `503` immediately. Audit reserves `audit_ingestion_required_reserve` slots for required records; best-effort records are dropped and counted once their share is full. Required audit and prompt-render records fail closed at the full hard bound and never bypass queue capacity.

An audit-database or compatibility-sink failure has delivery-class-specific behavior. Required audit returns a controlled local `503`. Best-effort audit increments `deltallm_audit_write_failures_total` and `deltallm_audit_events_dropped_total{reason="durable_enqueue_unavailable"}`, then returns the original request or side-effect result unchanged. Cancellation is not converted into a drop.

Completed records and failed best-effort audit records are retained separately. Independent maintenance tasks drain up to `cleanup_batch_size * cleanup_max_batches_per_run` rows per interval within the configured time budget, including while ingestion is idle. Size this nominal rate above peak terminal-row creation—for example, the defaults permit up to 10,000 deletion candidates per 60-second run. Parallel cleaners use `FOR UPDATE SKIP LOCKED` so replicas select disjoint pages.

Exhausted spend and required-audit records move to `blocked`. They remain capacity-accounted, are never selected by cleanup, and retain their stable event ID and frozen payload. Claims carry a unique token and renew at one-third of the lease interval; completion, retry, redaction, and acknowledgement all validate that token. A platform administrator may replay one investigated record with `POST /ui/api/telemetry-ingestion/{spend|audit}/{event_id}/replay`. Replay resets attempts but preserves identity and payload, records operator metadata, and does not change capacity. The replay mutation and required operator-audit insert commit atomically; if either write fails, both roll back and the endpoint returns a controlled `503`.

Audit claim polling updates the shared capacity row only when it terminalizes exhausted best-effort records and releases their slots. Empty polls, ordinary claims and reclaims, and required-only exhaustion leave that row untouched. Capacity release remains atomic with terminalization in the same SQL statement. PostgreSQL executes [data-modifying CTEs](https://www.postgresql.org/docs/current/queries-with.html#QUERIES-WITH-MODIFYING) even when the main claim returns no rows, so the capacity update has its own explicit condition. This reduces unnecessary contention; it does not establish a supported concurrency limit or remove the need to measure admission locks and size connection pools.

## Rollback

Set the affected ingestion mode back to `legacy` on the fixed version and perform a rolling restart before introducing any older binary. Required audit and prompt-render writes then return to synchronous persistence and continue to fail closed; best-effort audit events use the bounded compatibility queue. The admin settings API rejects ingestion-mode and pool changes with `409 restart_required`; it never reports a hot-reloaded mode that the process did not activate. Before stopping the last outbox worker, wait for drainable backlog to reach zero. Only after producers are in legacy mode and the drain is complete may an older version be rolled back. Keep the additive tables and columns in place during rollback. A `blocked` count is not drainable and must be investigated and replayed separately; a `delivery_unknown` email must be reconciled against the provider before rollback.

## Load evidence

Use the same isolated local-provider profile, database state, API key set, client host, and harness arguments for the last-release baseline and the candidate. Capture both raw samples and summaries outside the source tree:

```bash
DELTALLM_LOAD_API_KEY=... uv run python scripts/measure_gateway_load.py \
  --url http://127.0.0.1:4000/v1/chat/completions \
  --model local-load-model --rate 50 --duration 600 \
  --output-dir /tmp/deltallm-load/baseline

DELTALLM_LOAD_API_KEY=... uv run python scripts/measure_gateway_load.py \
  --url http://127.0.0.1:4001/v1/chat/completions \
  --model local-load-model --rate 50 --duration 600 \
  --output-dir /tmp/deltallm-load/candidate
```

Do not treat unit-test timings or runs against different providers/configuration as a before/after result. Compare success count, generator drops, arrival-window throughput, drain time, scheduling lag, latency p50/p95/p99/max, and database/Redis dependency counts from the matching server metrics interval.
