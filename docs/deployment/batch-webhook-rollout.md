# Batch Webhook Rollout Runbook

Terminal batch webhooks are opt-in and disabled by default. Delivery state is durable in Postgres, and delivery workers can run separately from API pods.

## Prerequisites

- Apply all Prisma migrations, including the webhook outbox and retention index.
- Generate one URL-safe base64 32-byte encryption key and store it in the deployment secret manager.
- Provide the same encryption key to every API and batch-worker pod. A worker with a different key cannot decrypt queued destinations.
- Keep `embeddings_batch_gc_enabled=true` so delivered and failed rows are removed after the configured retention period.
- Confirm worker egress and DNS policy permits the intended public destinations and ports.
- Scrape metrics from batch-worker pods as well as API pods.

Queue gauges are refreshed by a lightweight observer controlled by `batch_webhook_observability_enabled`. It is independent of outbound delivery, garbage collection, and encryption, so gauges remain current when those roles are temporarily disabled or the key is unavailable. In split deployments, enable observation on the batch-worker role, disable it on API replicas, and scrape both roles because replay counters originate on the API.

At startup, verify that the designated publisher reports `batch_webhook_observability=ready` in the batch bootstrap summary. Other replicas should report `batch_webhook_observability=disabled` when the role is intentionally disabled.

Generate a key without writing it to shell history:

```bash
python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

Store the result as `DELTALLM_BATCH_WEBHOOK_ENCRYPTION_KEY`; do not place the literal key in values files.

## Single-Process Configuration

```yaml
general_settings:
  batch_webhook_enabled: true
  batch_webhook_worker_enabled: true
  batch_webhook_observability_enabled: true
  batch_webhook_encryption_key: os.environ/DELTALLM_BATCH_WEBHOOK_ENCRYPTION_KEY
  batch_webhook_allowed_ports: [443]
  batch_webhook_allow_http: false
```

## Split API And Worker Configuration

The Helm chart disables background loops on API pods when `batchWorker.enabled=true` and enables them on the worker deployment. Both roles inherit the shared webhook encryption key and network policy.

```yaml
config:
  general_settings:
    embeddings_batch_enabled: true
    batch_webhook_enabled: true
    batch_webhook_encryption_key: os.environ/DELTALLM_BATCH_WEBHOOK_ENCRYPTION_KEY
    batch_webhook_allowed_ports: [443]
    batch_webhook_delivery_retention_days: 30
    batch_webhook_cleanup_max_rows_per_run: 10000

api:
  config:
    general_settings:
      batch_webhook_worker_enabled: false
      batch_webhook_observability_enabled: false

batchWorker:
  enabled: true
  replicaCount: 2
  config:
    general_settings:
      batch_webhook_worker_enabled: true
      batch_webhook_observability_enabled: true
```

Provide the encryption-key environment variable to both roles through the same Kubernetes Secret. Render the chart before deploying:

```bash
helm template deltallm deploy/kubernetes/helm --values values-production.yaml
```

## Rollout Sequence

1. Deploy the migration and encryption key with `batch_webhook_enabled=false`. Keep workers enabled so they can drain any existing rows.
2. Confirm all pods have the same configuration, worker readiness is healthy, and webhook queue gauges are present.
3. Set `batch_webhook_enabled=true` for a small API canary. Submit a batch whose webhook points to an operator-controlled HTTPS receiver.
4. Verify the receiver checks the timestamp and signature against the exact raw request body, and deduplicates by `X-DeltaLLM-Event-Id`.
5. Confirm the delivery becomes `delivered` in the batch admin detail and terminal audit event.
6. Expand API and worker replicas while watching the alerts below.

During a rolling deployment, older binaries may still insert rows without the new ownership snapshot columns. New binaries repair those null fields on an idempotent terminal enqueue and immediately before bounded job cleanup. Cleanup excludes jobs with a known non-null ownership mismatch, then claims one safe job with a row lock, repairs missing ownership, verifies every retained snapshot using null-safe comparisons, and deletes that job's verified metadata in one transaction. The worker repeats this transaction up to its per-run scan limit and retains the committed count if a later job fails. A conflicting job remains available for investigation without pinning unrelated expired jobs behind it. A writer that already owns a job lock is skipped until the next pass. An unexpected post-claim mismatch rolls back only that job transaction. Repair never changes webhook payload material or the delivery retention timestamp.

Webhook retention cleanup deletes short pages until it drains the eligible rows or reaches `batch_webhook_cleanup_max_rows_per_run`. Set that budget above the expected number of webhook events expiring per garbage-collection interval and monitor cleanup logs if the budget is reached repeatedly.

## Alerts

Queue gauges are cluster-wide Postgres snapshots repeated by each scraping process; use `max`, not `sum`, across pods.

- Failed rows: `max(deltallm_batch_webhook_queue_depth{status="failed"}) > 0` for ten minutes.
- Oldest active event: `max(deltallm_batch_webhook_oldest_pending_age_seconds) > 900` for ten minutes, adjusted above the expected retry window.
- Worker has no progress: `max(deltallm_batch_webhook_due_depth) > 0` and `(sum(increase(deltallm_batch_webhook_delivery_attempts_total[10m])) or vector(0)) == 0`. The `vector(0)` fallback also detects a worker that has never emitted an attempt series. Due depth excludes deliveries waiting for a future retry time and includes expired processing leases that need recovery or terminalization.
- Lease instability: `sum(increase(deltallm_batch_webhook_lease_recoveries_total[15m])) > 0`.
- Permanent failures: alert on sustained increases in `deltallm_batch_webhook_permanent_failures_total`, grouped only by its bounded `reason` label.

## Rollback And Draining

- To stop accepting new webhook configurations while draining existing deliveries, set `batch_webhook_enabled=false` and leave the worker enabled with the encryption key present.
- To stop outbound delivery immediately, set `batch_webhook_worker_enabled=false` and leave `batch_webhook_observability_enabled=true` on at least one scraped process. Durable queued/retrying/processing rows remain in Postgres and resume after workers are re-enabled; processing rows become reclaimable after their leases expire.
- Do not remove or rotate the encryption key while rows created with it remain active. The current format does not support a key ring.
- A code rollback must retain the webhook schema. Do not reverse the migration while active or retained rows exist.
- Operator replay grants a failed row a fresh attempt budget while preserving the original event ID and request body. Replay scheduling and its required admin audit event commit in the same database transaction; if the audit write fails, the delivery remains failed and can be retried safely. Receivers must continue deduplicating by event ID.
- Batch metadata cleanup proceeds independently of webhook delivery state. Each outbox row retains the batch ownership scope needed for delivery, inspection, and replay after the job row is removed.

## Troubleshooting

- `encrypted_configuration_invalid`: confirm API and worker roles use the identical encryption key.
- `resolved_address_not_allowed`, `port_not_allowed`, or `http_not_allowed`: review SSRF settings; do not broadly allow private networks to bypass an incorrect destination.
- `dns_resolution_*`: confirm worker DNS and network policy.
- `connect_*`, `read_timeout`, or `5xx`: inspect receiver availability and the bounded retry metrics.
- Failed delivery: inspect the redacted Webhook Delivery card, correct the receiver, then use **Replay delivery**. A replay is accepted only while the row is failed.
- No progress with active rows: verify the worker role is enabled, has database connectivity, exposes metrics, and has not lost its encryption-key environment variable.
- Repeated cleanup-budget messages: increase `batch_webhook_cleanup_max_rows_per_run`, shorten `embeddings_batch_gc_interval_seconds`, or reduce incoming retention volume after confirming database capacity.
- Ownership-conflict warnings: inspect and repair the retained ownership snapshot for the affected batch; other safe expired batches continue to be cleaned up.
