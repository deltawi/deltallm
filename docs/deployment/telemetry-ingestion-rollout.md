# Durable Telemetry Ingestion Rollout

Durable telemetry mode moves spend aggregation, audit persistence, and prompt-render logging onto bounded outboxes and a dedicated Prisma connection pool. Both ingestion modes default to `legacy` and are restart-bound. In legacy audit mode, required audit and prompt-render records are persisted synchronously and fail closed; only best-effort audit events use the bounded in-process queue.

## Preconditions

1. Apply all Prisma migrations through `20260817140000_fence_email_delivery` before deploying this binary. The application assumes the additive email-audit reconciliation, fenced email-delivery claims, and exact-spend columns exist before startup.
2. Provision database headroom for `telemetry_db_pool_size` connections per process. These connections are separate from `db_pool_size`; size the database for the sum across all replicas.
3. Configure Redis for prompt cache freshness and multi-replica audit policy invalidation. PostgreSQL advisory locks and the policy-change transaction remain the privacy correctness boundary; audit content writes do not rely on Pub/Sub delivery.
4. Verify the server-owned spend event identity, Prisma transaction-client detection, blocked-event replay, and claim-token fencing tests before enabling spend producers.
5. Set the pod termination grace period above both `telemetry_shutdown_drain_timeout_seconds` and, when email is enabled, `email_worker_shutdown_drain_timeout_seconds` (the Helm default is 30 seconds for both 20-second deadlines) so cancellation and connection cleanup can finish before `SIGKILL`.
6. Keep both ingestion modes on `legacy` until every API and worker replica runs a version that acquires telemetry admission locks in a lock-only transaction statement and reads capacity or content policy in the following statement. An older waiter can retain a pre-lock PostgreSQL snapshot, so outbox mode is not safe during a mixed-version rollout.

Before merging a migration-sensitive change, verify both supported database paths. The verifier creates uniquely named disposable databases, seeds the last-release database, checks additive-column defaults and retained data, and drops both databases on exit:

```bash
MIGRATION_TEST_ADMIN_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  uv run python scripts/verify_migration_paths.py --base-ref v0.1.34
```

CI runs the same command. Advance `--base-ref` when the supported upgrade floor changes.

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

After the P0 migration, lock-snapshot concurrency tests, and fixed-binary rollout are complete:

1. Start with `spend_ingestion_overload_policy: sync_fallback`, a conservative `spend_ingestion_batch_size`, and `spend_ingestion_max_pending_events` sized for the tolerated outage window.
2. Confirm no replica with the same-statement admission implementation remains. Then enable outbox mode on one canary and verify a claimed batch creates one bulk spend-event insert, at most one deterministic update per ledger entity type, and one bulk acknowledgement in the same transaction.
3. Compare the spend-event total with key, user, team, organization, and team-model ledger deltas. Retries must not increment a ledger twice.
4. Increase the canary share while watching request-pool saturation and the dedicated telemetry pool independently.
5. Roll all replicas only after the oldest-event age returns to normal after an induced worker pause.

The exact-spend migration is expand-only. New writers populate `NUMERIC(38,18)` columns and the legacy float columns in the same statement; exact accumulators fall back to the existing float only on their first post-migration update. Do not run an unbounded table-wide backfill as release DDL. Backfill old event rows later with a supervised, primary-key-paginated job, reconcile exact and legacy totals, switch readers only after reconciliation, and remove float columns in a separate contract release.

Test-email delivery results use the email row as their durable reconciliation source. A terminal provider result and `delivery_audit_status='pending'` are committed together, after which workers claim the audit with a fenced renewable lease and stable event ID. Audit retry must never move the email back to `queued` or `retrying`; rows with unresolved required audit are excluded from retention cleanup. Exhausted delivery audits move to `blocked`, make email-worker readiness fail, and require an investigated platform-admin replay through `POST /ui/api/email/outbox/{email_id}/delivery-audit/replay`.

External email sends are not automatically retried after an ambiguous transport result or after a successful provider call whose database acknowledgement failed. Once the fenced delivery lease expires, those rows move to `delivery_unknown` and remain excluded from automatic claims and retention cleanup. Confirm the message state with the configured provider, then use `POST /ui/api/email/outbox/{email_id}/resolve-delivery` with `{"resolution":"sent"}` or `{"resolution":"failed"}`. Resolution and its required operator audit commit atomically. Never resolve an uncertain row as failed merely to force a resend; create a new server-owned email event only after establishing that the provider did not accept the original.

## Alerts and overload behavior

Alert before capacity is exhausted, using both utilization and age:

- warning: capacity utilization above 70% for 10 minutes or oldest event age above the normal processing SLO;
- critical: utilization above 90%, oldest age continuing to rise, any blocked required record, or any email in `delivery_unknown`;
- page immediately if required audit persistence fails or either required ingestion path begins returning `503` responses.

Spend overload uses a synchronous fallback bounded independently by active transactions, waiting requests, queue time, and execution time. Requests beyond any bound receive a controlled local `503`; they do not accumulate as unbounded coroutine waiters. `fail_closed` bypasses fallback and returns `503` immediately. Audit reserves `audit_ingestion_required_reserve` slots for required records; best-effort records are dropped and counted once their share is full. Required audit and prompt-render records fail closed at the full hard bound and never bypass queue capacity.

An audit-database or compatibility-sink failure has delivery-class-specific behavior. Required audit returns a controlled local `503`. Best-effort audit increments `deltallm_audit_write_failures_total` and `deltallm_audit_events_dropped_total{reason="durable_enqueue_unavailable"}`, then returns the original request or side-effect result unchanged. Cancellation is not converted into a drop.

Completed records and failed best-effort audit records are retained separately. Independent maintenance tasks drain up to `cleanup_batch_size * cleanup_max_batches_per_run` rows per interval within the configured time budget, including while ingestion is idle. Size this nominal rate above peak terminal-row creation—for example, the defaults permit up to 10,000 deletion candidates per 60-second run. Parallel cleaners use `FOR UPDATE SKIP LOCKED` so replicas select disjoint pages.

Exhausted spend and required-audit records move to `blocked`. They remain capacity-accounted, are never selected by cleanup, and retain their stable event ID and frozen payload. Claims carry a unique token and renew at one-third of the lease interval; completion, retry, redaction, and acknowledgement all validate that token. A platform administrator may replay one investigated record with `POST /ui/api/telemetry-ingestion/{spend|audit}/{event_id}/replay`. Replay resets attempts but preserves identity and payload, records operator metadata, and does not change capacity. The replay mutation and required operator-audit insert commit atomically; if either write fails, both roll back and the endpoint returns a controlled `503`.

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
