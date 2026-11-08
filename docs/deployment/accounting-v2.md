# Accounting protocol v2

The ownership and trade-offs behind this runtime are recorded in the
[accounting protocol v2 architecture decision](../design/accounting-protocol-v2.md).

This integration has shared accounting adapters for HTTP provider calls, charged
cache hits, Realtime, batch, and selector calls. Legacy mode remains available.
Production activation still requires the qualification and rollout checks below.
Startup and Helm reject incompatible role combinations. Code changes after an
accepted image require a new unchanged-image qualification before a release claim.

Accounting v2 removes the per-request sequence of budget reads, operation writes,
dispatch writes, spend writes, and audit writes from the gateway hot path. PostgreSQL
remains the durable authority. The compatibility execution mode uses two required
acknowledgements. Native execution uses the local permits and terminal journal
described in the architecture decision.

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
- A draining accounting request pod refuses new calls with `gateway_draining` and
  closes the connection. The API pod sends that call again, up to five more times
  inside the same deadline (pausing 50, 100, 200, 400 and 800 ms), so a rollout
  does not turn a successful provider call into a provisional debit. Each repeat
  opens a new connection instead of reusing a kept one, because other kept
  connections can still lead to the draining pod. A refused connection is sent
  again the same way, because it never carried the request. No other failure is
  sent again. The `deltallm_accounting_transport_drain_retries_total` counter
  shows these sends.
- API and batch pods reuse a checked accounting service address for 30 seconds,
  and keep using the last checked address when a cluster DNS lookup fails. A
  first lookup that fails is tried up to three times inside the call's deadline.
  A busy DNS server during a rollout then cannot leave a finished call
  provisional. The
  `deltallm_accounting_transport_resolution_fallbacks_total` counter shows these
  calls.
- An accounting worker that starts while the database is busy repeats its
  idempotent setup statements until its startup deadline, instead of exiting on
  the first slow statement.
- A failover deployment may run only if the first reservation covers its worst-case
  customer charge. Earlier attempts stay provisional until evidence resolves them.
- Local queues and stores are bounded and refuse work when full. See
  [Bounded in-memory state](bounded-state.md) for each limit and what empties it.

## Budget edits and native balances

Adding a hard-budget scope while matching permits remain live returns admin HTTP `409`
with code `budget_policy_requires_drain`. The requested budget does not commit.
Pause inference for the affected scope, let accepted work settle,
and let owners return unused permits. Retry when matching grants and direct
reservations have drained. Other scopes can continue. Older live grants without
scope data require a one-time drain of the active generation. Do not clear receipts,
delete grants, or force counters to zero. This rule also applies to direct database
policy writes. An edit to an
existing budget window still checks all reserved and provisional money.

Adding or restoring a cap retains charges from the current budget period, even
before reporting completes. A cap below existing spend and holds returns `409`
with code `budget_policy_below_debits`. Resolve uncertain operations and unreported
grant capacity before a new hard-budget window is created. Do not use a cap change
as a spend reset. Policy edits can read retained financial history on the control
pool. If an edit times out, it rolls back; drain the affected scope and retry.
Apply migration `20261007160000_accounting_budget_policy_history` before rollout.

In native mode, organization, team, key, and user admin GET responses use native
balances. Current finite budgets use committed window balances. Unlimited scopes
use projected facts and retained legacy history for the current budget period.
These values can lag until settlement or projection completes. A missing finite
authority is an explicit `503`, not a zero-spend result. Mutation receipts remain
independent from reporting; read the entity again for its current balance.

When `budget_notifications_enabled` is true, the existing notification worker
checks accounting thresholds in pages of 32 organizations. Enable the existing
global notification policy and the required email or Slack configuration too.
Discovery is asynchronous and uses the control-plane database pool. The worker
keeps durable deduplication and fenced delivery. It does not add database or
network calls to inference admission.
If a known page fails, the scan continues to later pages and retries the failed
page on the next full pass. Delivery of accepted intents continues. An unknown
balance is never treated as zero.

Native selector billing uses shared accounting health. It does not require a
legacy spend worker on the same API process. Legacy selector billing still does.

## Compatibility projection

`deltallm_accounting_events` is the source of truth. The dedicated accounting worker
uses fenced per-partition checkpoints to project completed events to the existing
spend ledger and required audit outbox. Projection is idempotent. API pods do not run
this worker when the Helm `accountingWorker` role is enabled.

The worker also rolls renewable budget windows. If a renewable window expires before
its successor is ready, admission fails closed with 503; it never treats a missing
window as unlimited. Hour, day, and calendar-month resets preserve the configured
monthly anchor day.

In native execution, the existing recovery owner performs window renewal after
grant settlement. It uses the same batch limit and cycle deadline. Migration
`20261007170000_accounting_budget_period_sync` and its corrective migration
`20261007171000_accounting_monthly_reset_metadata` must be applied before the new
worker image. The reset trigger also repairs an unchanged expired date when an operator
edits a budget. Cap removal cancels pending renewal. Old-period spend is not copied
into a restored cap, and current-period spend is kept exactly once. Explicit
invalid reset dates still fail. No extra inference database call is added.

Projection claims up to `accounting_projection_max_concurrent_partitions` partitions
at once. Spend rows and required audit envelopes are written in sink batches, while
expiry recovery, grant closure, window rollover, and backlog measurement run on the
separate maintenance interval.

Journal and native reporting lanes use the configured projection poll interval
after the first empty claim. Each later empty successful claim doubles the idle
wait, up to the larger of that interval and one second. Completed work resets
the wait and starts the next claim immediately. A local wake also resets it.
Cross-process work can wait for that idle interval plus bounded database time
before discovery. Error backoff and required health checks do not change.

## Database preparation

### Request IDs and blocked reporting records

HTTP ingress uses one request ID for accounting and response headers. A single
caller ID is retained when it has 1 to 256 visible ASCII characters without
spaces. Missing, empty, repeated, or invalid headers receive a generated ID.
Caller correlation IDs do not replace the server's economic operation identity.
Non-HTTP finalization uses the operation ID when no valid correlation ID exists.

Apply migration `20261010130000_accounting_reporting_request_ids` before the
corrected image. It changes only the reporting function. For an accepted event
with a missing, null, or empty request ID, reporting uses the existing operation
ID. The accepted event and its source hash remain unchanged. Charge, attribution,
audit, fencing, and replay checks remain active. No second charge is created.

Restart the corrected reporting workers and allow the blocked page to retry.
Confirm that reporting drains and all API and accounting readiness checks pass
before resuming normal traffic. Do not delete the event, reset a checkpoint, or
clear budget holds to make recovery pass. Other invalid records still fail
closed and require investigation. Keep applied migrations unchanged.

The native qualification must include requests without `x-request-id`. A run
that always supplies this header does not prove that normal clients are safe.

### Find the cause of a blocked reporting page

The reporting function rejects an invalid accepted event with a fixed code, for
example `accounting_read_model_attribution` or `accounting_read_model_charge`.
The worker retries the same page and does not skip the event. When the reporting
worker cannot become ready at startup, the startup error shows the last failure:

```text
read-model processing did not become ready before its startup deadline
(last failure: unknown:accounting_read_model_attribution partition=3 sequences=41-57)
```

The worker also writes one warning when a failure starts or changes, and one
line when it recovers:

```text
Accounting read-model processing failed: worker=<id> unknown:accounting_read_model_attribution partition=3 sequences=41-57
Accounting database call failed: operation=project_read_model reason=unknown rejection=accounting_read_model_attribution sqlstate=P0001 error_type=RawQueryError
```

Every accounting database call writes the second line at most once each 30
seconds for each operation and cause. The log contains only the operation, the
failure class, the SQLSTATE, and a fixed `accounting_*` code. It never contains
database error text, because that text can contain tenant data.

Use the partition and sequence range to find the events:

```sql
SELECT sequence, operation_id, outcome, payload_json->'spend' AS spend
FROM deltallm_accounting_events
WHERE generation = <generation> AND accounting_partition = 3
  AND sequence BETWEEN 41 AND 57
ORDER BY sequence;
```

Do not delete the event or reset a checkpoint. Find the code path that wrote the
invalid event and fix it.

### Cache hits and blocked reporting records

Before this fix, a response cache hit wrote an accepted event with no
`start_time` and no `end_time`. The reporting function rejected this event with
`accounting_read_model_attribution`. The worker retried the same page and did
not continue, so the accounting worker could not become ready. This applies
when accounting v2 and the response cache are both on.

Apply migration `20261103100000_accounting_reporting_event_times` before the
corrected image. It changes only the reporting function. For a completed event
with a missing or null time, reporting uses the event's `occurred_at` (the
finalization instant). If only `start_time` is missing, reporting uses the
`end_time`. The reporting row shows a latency of zero for these events. The
accepted event and its source hash remain unchanged. Charge, attribution, audit,
fencing, and replay checks remain active. An invalid time value still fails
closed. No second charge is created.

The corrected image records the real start and end time of each cache hit. It
also fills a missing time with the finalization instant before it accepts any
completed event, so other paths cannot write such an event again.

To find events that the migration recovers:

```sql
SELECT count(*), min(created_at), max(created_at)
FROM deltallm_accounting_events
WHERE outcome = 'completed'
  AND (payload_json->'spend'->>'start_time' IS NULL
    OR payload_json->'spend'->>'end_time' IS NULL);
```

Do not edit these events by hand. After the migration, restart the accounting
workers and let the blocked page retry. If you already edited events by hand,
they remain valid and the migration does not change them.

### Grant execution policy

Apply migration `20261010083000_accounting_grant_execution_policy` before the
upgraded image. It disables JIT compilation only inside the short grant admission,
allocation, reservation and finalization functions. The caller's JIT setting is
restored after success or error. Request deadlines, budget rules, durable records
and retry identities do not change. This migration changes no table data.

### Upgrade terminal publication ordering

Migration `20261007173000_accounting_event_publication_order` orders terminal
publication within each accounting partition. It also keeps grant-backed expiry
out of the direct-reservation recovery owner. Existing migrations stay unchanged.
Apply `20261008001000_accounting_event_publication_parent_locks` in the same
stopped-writer upgrade. It orders the implicit parent locks for journal records
that retain reservations. Do not deploy the publication change without this
correction.

Stop all accounting event writers and reporting workers before applying this
migration. This includes API and batch writers, journal processing, expiry
recovery, and operator reconciliation. First let accepted work finish under its
current owner. Do not overlap old and new writer functions during this upgrade.

If this database already used native reporting, reset only its native reporting
checkpoint while the workers are stopped. This example uses generation `1`;
use the generation that you have checked for your deployment:

```sql
UPDATE deltallm_accounting_projection_checkpoints
SET last_sequence=0, lease_owner=NULL, lease_token=NULL,
    lease_expires_at=NULL, last_error_code=NULL, updated_at=NOW()
WHERE protocol_name='primary' AND generation=1
  AND projection_name='accounting-read-model-v2';
```

This update changes at most 64 fixed checkpoint cells. It does not delete
events, balances, usage facts, audit records, or rollups. The native projector
can replay existing events without adding their effects twice. Do not reset
the compatibility projector with this command.

Restart the upgraded accounting workers first. Wait until native reporting has
finished the replay and worker health is ready before restarting inference.
Replay time depends on retained history. A conflicting fact stops replay; do
not delete that fact to force progress. A new database needs no checkpoint reset.

For a deployment that still uses the supported compatibility reporting owner,
the new image also accepts expiry and reconciliation event keys. Existing audit
UUIDs and UUID-source fallback identities remain unchanged. Recovery keys use
the native stable audit identity. Restart the upgraded compatibility worker; its
blocked checkpoint retries normally. Do not reset a checkpoint just to clear this
ID error, and do not run native and compatibility reporting for the same generation.
The native replay command above is not a compatibility replay procedure. Keep
existing compatibility spend and audit deduplication rows intact.

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
work; do not delete it to make activation pass. A batch completion receipt counts as
settled once its status is `sent`; `queued`, `processing`, `retrying` and `failed`
receipts still block. Apply migration `20261028100000_accounting_cutover_sent_batch_receipts`
first: before it, every delivered receipt was reported as pending batch work.
These checks assume stopped writers.
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

The direct accounting pool sets `statement_timeout`, `lock_timeout`, and `jit=off`
on every connection. Its statements are key-bounded and must finish within
`accounting_statement_timeout_ms`. Compiling one plan with JIT can take longer than
that budget when stale table statistics overstate a statement's cost.

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

The capacity report counts two HTTP pools for each native API or batch process.
Each pool uses `accounting_rpc_max_connections`. One pool keeps connections.
The other opens fresh connections for rollout retries. Deploy the chart and
image from the same release so their capacity checks use the same pool count.

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

### Local issue proofs

In `local_journal` mode, each API and batch process keeps one issue proof for every
operation it dispatched until that operation has a durable terminal. The store
uses the finalization limits: `accounting_finalization_max_pending` entries and
`accounting_finalization_max_pending_bytes`. Each proof is charged at least 8 KiB,
so the default 8 MiB holds at most about 1,000 operations per process. When the
store is full, new local reservations fail closed with HTTP 503 (batch items show
"Gateway busy") and
`deltallm_accounting_failures_total{queue="reservation",phase="queue",reason="queue_full"}`
rises.

A proof leaves the store in one of three ways:

- The process finalizes the operation itself.
- Another process finalized it. A batch completion can be delivered by any batch
  worker. A release worker in every process checks proofs older than 10 seconds
  against the terminal journal, up to 256 per request-role call, and checks each
  running operation again every 10 seconds.
- Its grant expired in the database more than 60 seconds ago. After that expiry
  the journal refuses a first terminal for the operation, and expired-grant
  recovery owns any permit no process reported.

Watch these metrics on API and batch pods:

| Metric | Meaning |
|---|---|
| `deltallm_accounting_local_receipt_fill_ratio` | Share of the store in use, by entries or bytes, whichever is higher. New reservations fail at 1. Alert above 0.8. |
| `deltallm_accounting_local_receipts` | Proofs held |
| `deltallm_accounting_local_receipt_bytes` | Their conservative byte charge |
| `deltallm_accounting_local_receipt_releases_total{reason="finalized\|expired"}` | Proofs released after another process finalized them, or after grant expiry |
| `deltallm_accounting_failures_total{queue="receipt_release",phase="lookup"}` | Failed journal lookups. Releases wait for the next check; admission is not affected. |

The journal lookup is served by the accounting request pods at
`/internal/accounting/v1/terminals/local/batch`. Upgrade them in the same release
as API and batch pods. While an older request pod is still serving, lookups fail
and only the expiry release runs.

The request role's `/health/readiness` probe keeps the pod in the Service while
PostgreSQL reports a live accounting generation and the terminal queue can
accept work. Returns and finalization can continue during a projection pause.
New funding still requires healthy projection and backlog checks. The signed
internal health endpoint and the request role's `/health` report that funding
status. The projection role still requires its processors to be ready.

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

If a process stops before it records a terminal, the closed grant can hold a
remainder in `unknown_provisional_exact`. When provider evidence confirms the
entire remainder was unbilled, use the grant ID, generation and exact remaining
amount from that closed grant:

```bash
uv run python scripts/reconcile_accounting_v2.py \
  --database-url "$DATABASE_URL" \
  --generation 1 \
  --grant-id "$ACCOUNTING_GRANT_ID" \
  --expected-grant-hold "$ACCOUNTING_GRANT_HOLD" \
  --evidence-reason "provider statement confirmed no charge for the grant remainder" \
  --provider-evidence-reviewed
```

The migration owner can execute this function. It releases the grant remainder
from every original budget window in one transaction and records the amount,
operator and evidence reason in `deltallm_accounting_grant_reconciliations`.
An exact retry returns the recorded amount. Reconcile any known operation's
provisional debit with the operation command above.

## Qualification gates

Before production activation, run:

- migrations from an empty database and the supported upgrade fixture;
- hermetic accounting contract and request-path tests;
- real PostgreSQL tests for hot-window concurrency, replay, recovery,
  reconciliation, and reset rollover;
- Helm schema, lint, and capacity tests;
- shared realtime, batch, and selector adapters with recovery tests;
- the 50/100/200/500 RPS workload with zero accounting 500/503 errors, no overspend,
  bounded queues, recovered projection lag, and p95/p99 within the release SLO.

PR 10 load qualification remains separate from this architectural implementation.

CI runs this configuration on kind for billing, batch and database changes, and
every night under batch and API load. See
[Accounting v2 acceptance and nightly soak](accounting-v2-acceptance.md).

## Rollback

Set `accounting_grants_enabled: false` and roll API pods to return new reservations to
the direct-window v2 implementation. This is a safe performance rollback, not a move to
eventual accounting. Continue running the accounting worker until old grants are closed
and projection backlog is zero. To disable accounting v2 entirely, follow the drain and
generation-fencing sequence in the architecture decision instead of removing the
worker or schema first.
