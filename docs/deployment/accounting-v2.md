# Accounting protocol v2

The ownership and trade-offs behind this runtime are recorded in the
[accounting protocol v2 architecture decision](../design/accounting-protocol-v2.md).

This clean-main integration is not ready for production activation. HTTP provider
calls and charged cache hits have v2 adapters. Realtime, batch, and selector billing
still need adapters to the same budget and recovery authority. Startup and Helm
reject the unsupported combinations. Legacy mode keeps current main's features.

Accounting v2 removes the per-request sequence of budget reads, operation writes,
dispatch writes, spend writes, and audit writes from the gateway hot path. PostgreSQL
remains the durable authority, but each request has only two required acknowledgements:

1. One short atomic admission transaction reuses or allocates a process-scoped,
   short-lived PostgreSQL grant, consumes it for the microbatch, records immutable
   reservation events, and grants provider dispatch. Allocation escrows money from
   every applicable hard-budget window and leases bounded partition slots once for
   several operations.
2. A separate microbatched finalization records the exact charge or an explicit
   provisional debit. Grant reconciliation applies all settled operations to their
   budget windows in one set-based update and releases unused escrow.

Finalization does not mutate the active grant row. The worker derives grant
completion from operation state, so reservation and finalization queues do not
serialize on the same process grant.

The queues are bounded and independent. Admission pressure cannot consume the
finalization queue. A reservation replay never returns a dispatch token, so retrying a
database call cannot repeat provider work. All identities, pricing inputs, request
fingerprints, attribution, spend payloads, and redacted audit envelopes are frozen in
the durable event stream.

## Failure semantics

- No reservation ACK means no provider dispatch.
- Budget exhaustion returns HTTP 429 before dispatch.
- Capacity, timeout, stale generation, or database uncertainty returns HTTP 503 before
  dispatch.
- Confirmed success commits the exact charge before a non-stream response or terminal
  stream frame is released.
- Provider failure or lost acknowledgement after dispatch keeps the remaining
  allowance as a provisional debit. It is never silently released.
- Process crashes are recovered by converting expired dispatched reservations to
  provisional debits without resending requests.
- A failover deployment may run only if the first reservation covers its worst-case
  customer charge. Earlier attempts stay provisional until evidence resolves them.

## Compatibility projection

`deltallm_accounting_events` is the source of truth. The dedicated accounting worker
uses fenced per-partition checkpoints to project completed events to the existing
spend ledger and required audit outbox. Projection is idempotent. API pods do not run
this worker when the Helm `accountingWorker` role is enabled.

The worker also rolls renewable budget windows. If a renewable window expires before
its successor is ready, admission fails closed with 503; it never treats a missing
window as unlimited. Hour, day, and calendar-month resets preserve the configured
monthly anchor day.

Projection claims up to `accounting_projection_max_concurrent_partitions` partitions
at once. Spend rows and required audit envelopes are written in sink batches, while
expiry recovery, grant closure, window rollover, and backlog measurement run on the
separate maintenance interval.

## Database preparation

Apply the Prisma migrations first. Stop all legacy API and worker writers. Let
accepted billing work settle. Then prepare a generation from exact legacy balances:

```bash
uv run python scripts/prepare_accounting_v2.py \
  --database-url "$DATABASE_URL" \
  --generation 1 \
  --partitions 16 \
  --max-outstanding-per-partition 4096 \
  --activate
```

Preparation is transactional and idempotent while the generation is `prepared`. It
also recognizes an already active generation after a lost commit acknowledgement. It
refuses to continue when legacy reservations are outstanding, an exact committed
balance exceeds its limit, a reset policy is invalid or overdue, or the resulting
window count does not match finite legacy budgets. A deployment with no hard budgets
is valid and produces zero windows.

Preparation and activation also reject unresolved realtime intents, spend outbox
rows, selector operations, batch jobs, and batch completion receipts. Review blocked
work; do not delete it to make activation pass. These checks assume stopped writers.
They do not support an online rollout with both legacy and v2 billing writers.

Partition capacity should cover maximum admitted provider calls, with headroom for
the 14-minute crash-recovery horizon. More partitions reduce lock contention; they do
not weaken budget scopes because window rows are locked independently in stable order.

## Runtime configuration

All accounting settings are startup-only. A typical Helm overlay is:

```yaml
accountingWorker:
  enabled: true
  replicaCount: 1
  autoscaling:
    enabled: true
    minReplicas: 1
    maxReplicas: 4
    oldestEventAge:
      enabled: true

prometheus:
  customMetrics:
    enabled: true

config:
  general_settings:
    accounting_protocol_enabled: true
    accounting_protocol_generation: 1
    accounting_microbatch_max_size: 8
    accounting_microbatch_dwell_ms: 2
    accounting_reservation_max_pending: 4096
    accounting_finalization_max_pending: 8192
    accounting_reservation_max_pending_bytes: 8388608
    accounting_finalization_max_pending_bytes: 8388608
    accounting_statement_timeout_ms: 250
    accounting_finalization_ack_timeout_ms: 1000
    accounting_max_provider_attempts: 3
    accounting_grants_enabled: true
    accounting_grant_target_operations: 32
    accounting_grant_ttl_seconds: 30
    accounting_projection_batch_size: 64
    accounting_projection_max_concurrent_partitions: 4
    accounting_projection_maintenance_interval_ms: 1000
    spend_ingestion_mode: outbox
    audit_ingestion_mode: outbox
    audit_enabled: true
```

Helm disables spend, audit, and projection workers on API pods and enables them on the
accounting worker. Its PostgreSQL and Redis pools are included in dependency-capacity
validation. API pods do not open the telemetry-worker database pool when they own no
telemetry worker. The dedicated worker defaults to smaller control and foreground
pools because it is not selected by the gateway Service; override those pools only
with an updated capacity report.

Each API process has separate reservation and finalization byte budgets. Both
default to 8 MiB and are startup-only. Each budget covers immutable queued payloads,
selected work waiting for a database acknowledgement, and a fixed charge for queue
metadata. It is not an RSS limit. The item limits still apply. A full queue fails
closed; it does not evict another operation or borrow the other queue's budget.

Collection stops at 1 MiB of serialized JSON or the configured item count, whichever
comes first. Large valid terminal records can use several batches. This keeps the
per-record payload limits and avoids an oversized batch after provider completion.
Caller cancellation before collection releases its byte charge. After selection,
the charge remains until persistence finishes or the worker fails. The metric
`deltallm_accounting_queue_retained_bytes{queue="reservation|finalization"}` records
each queue's conservative charge. With `P` API processes, the two default queue
budgets total `P * 16 MiB`, in addition to clients, request bodies, and local leases.

The accounting-worker HPA always supports CPU and memory targets. Its oldest-event-age
target requires the Prometheus custom-metrics adapter and is mandatory when accounting
worker autoscaling is enabled in the production profile. Scale-down stabilization must
cover the pod termination grace period so claimed work can drain.

Grant size is a throughput/fairness trade-off. A larger target reduces hot-window
mutation frequency but temporarily escrows more of a shared budget per API process.
Start with an 8-item persistence batch, a 32-operation grant, and a 30-second TTL. Tune
the batch and grant independently from measured statement latency, grant utilization,
budget-exhaustion decisions, and tail latency. PostgreSQL remains authoritative; Redis
does not hold grant balances.

Use the isolated hot-budget profile before adding another service to the durable path:

```bash
uv run python -m tests.performance.accounting_grant_profile \
  --database-url "$DISPOSABLE_DATABASE_URL" \
  --mode both --rate 1000 --duration 10 --processes 8 --batch-size 8 \
  --output .load-results/accounting-grants
```

The command refuses a database with an active accounting generation. It reports
microbatch sizes, reservation transaction-sequence and caller latency, finalization
database-call latency, reservation decisions, errors, closed grants, and final economic
invariants. This isolates the database protocol; it does not replace the end-to-end
HTTP/provider/Redis qualification.

The clean-main integration also supports `--mode all`. It adds the inactive
pre-issued permit bank to this comparison. This mode uses the same two-connection
accounting pool and terminal owner. It does not enable permits in the gateway.
Use a new output directory for each run. Check the commit, file hashes, and
`working_tree_dirty` field before comparing results. A dirty-tree result is a
development probe, not release evidence.

For the bundled local dependencies, layer the accounting evaluation profile after the
normal evaluation profile. It sizes PostgreSQL for the complete rolling topology:

```bash
helm upgrade --install deltallm deploy/kubernetes/helm \
  -f deploy/kubernetes/helm/values-eval.yaml \
  -f deploy/kubernetes/helm/values-accounting-eval.yaml \
  --set secret.values.masterKey="$MASTER_KEY" \
  --set secret.values.saltKey="$SALT_KEY"
```

After activation, writes to legacy key, user, team, organization, and team-model hard
budget policies update the authoritative active window in the same control-plane
transaction. Removing a hard budget expires its active window. Lowering a limit below
committed, reserved, or provisional debit is rejected instead of creating overspend.

## Provisional reconciliation

After reviewing provider evidence, release an unbilled provisional amount:

```bash
uv run python scripts/reconcile_accounting_v2.py \
  --database-url "$DATABASE_URL" \
  --generation 1 \
  --operation-id 00000000-0000-0000-0000-000000000000 \
  --additional-charge 0 \
  --evidence-reason "provider confirmed no charge" \
  --provider-evidence-reviewed
```

For a confirmed additional charge, pass its exact amount and a JSON spend payload with
the same `cost_exact` through `--spend-payload-file`. The function atomically moves the
confirmed amount to committed spend, releases the remainder from every scope, and
appends an operator-audited reconciliation event. It never calls a provider.

## Qualification gates

Before production activation, run:

- migrations from an empty database and the supported upgrade fixture;
- hermetic accounting contract and request-path tests;
- real PostgreSQL tests for hot-window concurrency, replay, recovery,
  reconciliation, and reset rollover;
- Helm schema, lint, and capacity tests;
- shared realtime, batch, and selector adapters with recovery tests;
- the 50/100/200 RPS workload with zero accounting 500/503 errors, no overspend,
  bounded queues, recovered projection lag, and p95/p99 within the release SLO.

PR 10 load qualification remains separate from this architectural implementation.

## Rollback

Set `accounting_grants_enabled: false` and roll API pods to return new reservations to
the direct-window v2 implementation. This is a safe performance rollback, not a move to
eventual accounting. Continue running the accounting worker until old grants are closed
and projection backlog is zero. To disable accounting v2 entirely, follow the drain and
generation-fencing sequence in the architecture decision instead of removing the
worker or schema first.
