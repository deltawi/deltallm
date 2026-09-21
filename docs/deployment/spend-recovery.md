# Spend recovery and reserved settlement capacity

PR 6 extends the spend outbox with an ordinary inference operation journal. Enable
it only after its additive migration and the dependency allocations from PR 3 are
installed. This is durable accounting recovery, not a hard monetary budget: ordinary
budget admission remains a soft preflight check.

## Ownership and guarantees

Before an ordinary provider attempt, the API commits a server-owned event identity,
owner token, verified attribution, bounded attempt history and pricing snapshot in
PostgreSQL. That intent occupies one existing spend-outbox slot. No transaction or
connection remains held during provider execution. Chat, streaming chat, compatibility
aliases, embeddings, images, speech, transcription and rerank use this boundary.
Batch keeps its existing completion outbox; selector classification keeps its billing
operation journal and requires the API spend worker.

The final receipt replaces the reserved intent. It does not compete for another
queue slot or the global admission lock. A worker inserts the spend event, updates
all ledgers and acknowledges its fenced claim in one transaction. Replaying an
accepted receipt cannot increment a ledger twice. A conflict in owner, attribution
or receipt fails closed. Cache hits require the existing single outbox acceptance
and do not reserve external-execution intents.

A lost process, disconnect, cancellation or persistence outage can leave an outcome
unknown. Expired intents become `operation_state='unknown'` after their 15-minute
observation window. This is not a refund, a claim that execution stopped, or permission
to retry a provider. A late receipt from the same owner can still be accepted.
Unknown intents remain blocked from consumption and generic replay, retained and
capacity-accounted until investigated. Missing receipts never become zero-cost events.
Earlier failed attempts remain explicitly ambiguous in the frozen attempt history;
a final successful receipt does not prove those attempts incurred no provider cost.

Required persistence failure produces a safe local `503`, including when it occurs
while reporting an earlier preflight error. It does not mark a provider unhealthy.
If response streaming has started, HTTP status cannot change; failed settlement must
not emit a successful terminal marker. The durable intent remains recoverable.

## Configuration and capacity

Both operation settings and `spend_ingestion_worker_enabled` are startup-only;
change them with a rolling restart:

```yaml
general_settings:
  spend_ingestion_mode: outbox
  spend_ingestion_worker_enabled: true
  spend_operation_intents_enabled: true
  telemetry_db_pool_size: 5
  spend_settlement_db_pool_size: 1
  telemetry_worker_db_pool_size: 5
```

`spend_settlement_db_pool_size` is **included** in `telemetry_db_pool_size`. The example
allocates four connections to admission and one to already-admitted settlement.
Consumers and maintenance use the five worker connections. Each allocation has its
own bounded admission and existing acquisition/lock/statement/transaction deadlines.
New work, optional reporting and consumers cannot occupy settlement connections.
The connection itself does not guarantee successful persistence during a database
outage; the previously committed intent records that unresolved work.

The API process therefore still budgets `20 + 8 + 5 + 5 = 38` PostgreSQL connections
with the default control, foreground and telemetry sizes. The production API peak
of `12 + 1 + 12 = 25` processes still budgets 950 connections before batch-worker
roles and the deployment reserve. Use the rendered dependency-capacity ConfigMap and
[full capacity calculation](dependency-capacity.md), including retiring pods. There
is no added worker or per-request client. Readiness probes the settlement allocation
when enabled and retains the existing required spend-worker check.

Local/file defaults leave operation intents disabled for the coordinated cutover.
The production overlay explicitly enables them and rejects a disabled spend worker,
legacy spend mode, or a settlement share that leaves no admission connection. A split
batch-worker Deployment does not replace API spend workers. Do not disable every
API spend worker. Dynamic configuration rejects worker changes before persistence. Cross-field
cutover validation uses the resolved file/environment values at startup; adding an
explicit default cannot silently change environment precedence during reload.

The protocol adds a short pre-provider transaction per attempted deployment. Its
whole-operation deadline is 250 ms, further bounded by the existing routing attempt
deadline; statement, lock and acquisition limits remain enforced by the database
allocation. Final receipt acceptance also has a 250 ms bound and skips the global
admission/capacity lock. The additional call/latency cost must be included in local
comparisons and production qualification; this change does not certify a pod RPS.

The [local comparison](../project/benchmarks/spend-recovery-2026-09-15/README.md)
records the added transaction/latency cost and controlled overload behavior. It is
acceptance evidence for this change, not a production capacity certificate.

## Rollout and rollback

1. Keep operation intents disabled. Pause telemetry producers/workers during the
   coordinated migration window described in the [design](../design/pr6-spend-recovery.md).
   Validate the migration on a restored production-size copy first. DDL waits at most
   two seconds for locks and rolls back if a statement exceeds 30 seconds.
2. Apply the additive migration before deploying APIs. Verify legacy queued and
   blocked events retain their payloads and NULL operation columns.
3. Complete the [spend outbox rollout](telemetry-ingestion-rollout.md#spend-rollout),
   including ledger parity and worker-pause recovery, before enabling operation
   intents on a canary. Keep the required worker enabled.
4. Verify the admission/settlement split, safe rejection under a full queue, accepted
   receipt drain and unknown-outcome alerting. Increase the canary share only after
   the measured admitted throughput and oldest event age recover after an outage.
5. To roll back, disable new intent production on the current binary while preserving
   the new worker and schema. Drain known receipts and investigate unknown intents
   before removing the last capable worker. Do not delete blocked rows to free space.

## Investigating unknown work

Use an operator-only primary database session. Look up a known server event ID:

```sql
SELECT event_id, operation_state, operation_expires_at, operation_intent
FROM deltallm_spend_ingestion_outbox
WHERE event_id = '<server-event-id>' AND operation_state = 'unknown';
```

The result contains tenant attribution; retain it in restricted incident evidence.
Reconcile against the provider's authoritative receipt or proof of non-execution.
Never infer non-execution from a client timeout. A provider invoice may include
charges for earlier failed attempts; resolve their ambiguity explicitly.

Prepare a reviewed JSON evidence file (at most 8 KiB):

```json
{
  "event_id": "00000000-0000-0000-0000-000000000001",
  "organization_id": "verified-organization-id",
  "actor_id": "operator-account-id",
  "evidence_reference": "restricted-incident-ticket-123",
  "outcome": "usage_confirmed",
  "cost_exact": "0.000125000000000000",
  "provider_cost_exact": "0.000100000000000000",
  "usage": {"prompt_tokens": 10, "completion_tokens": 5}
}
```

Use `outcome: not_executed` only with authoritative proof and zero cost/usage. The
command is a privileged maintenance operation using the operator database credential:

```bash
uv run python scripts/reconcile_spend_operation.py \
  --database-url '<operator database URL>' \
  --evidence-file /restricted/reviewed-receipt.json \
  --provider-evidence-reviewed
```

The repository verifies event and organization, accepts only an unknown operation,
and commits its immutable receipt and required operator audit atomically. An identical
reconciliation is idempotent; conflicting evidence cannot overwrite an accepted
receipt. The existing worker settles it. No provider is called by this command.
Do not place credentials, prompts or raw provider payloads in the evidence reference.

## Retention and operating signals

The intent has at most 128 attempts and 64 KiB of serialized snapshot, the receipt
at most 256 KiB, and resolution evidence at most 8 KiB. Queue capacity bounds live
and unknown work deployment-wide. The recovery index covers only dispatched intents;
workers recover at most 100 expired rows in a bounded iteration using `SKIP LOCKED`.
Completed receipt retention and cleanup continue through the existing spend owner.
Unknown records are never automatic retention candidates.

Monitor existing spend backlog/count/age and database allocation saturation separately
for `telemetry`, `telemetry_settlement` and `telemetry_worker`. Operation admission,
receipt and recovery errors use fixed spend-ingestion failure labels. Use `deltallm_spend_operation_transitions_total{state="unknown"}` to alert on newly
ambiguous operations. Investigate blocked records by their operation state. Alert on any unknown outcome and sustained
backlog growth; shedding protects capacity but does not resolve missing economic
information. Use normal PostgreSQL autovacuum/analyze and size their budget for the
extra intent insert/update, receipt replacement and recovery transition.

The worker also observes shared unknown work every ten seconds through the existing
blocked-record index. Alert when `deltallm_spend_operation_unknown` is nonzero and
check `deltallm_spend_operation_observed_timestamp_seconds` for freshness. Aggregate
these shared counts with `max` across replicas, never `sum`. Failed observations
retain the last value and emit a recovery failure; they do not report an unknown
count of zero. Recovery and observation share a bounded 250 ms worker slice.

See [Process lifecycle](process-lifecycle.md) for migration-before-rollout ordering,
the managed 80-second shutdown budget, interrupted-stream behavior and recovery of
committed records after a pod exits. Keep the managed image command in production.
