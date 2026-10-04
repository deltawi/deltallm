# Accounting protocol v2 architecture decision

Status: clean-main integration in progress. Tracking: issue 320. The HTTP accounting
core is behind startup-only configuration. Main's other billing paths still need
shared adapters. This branch is not ready for production activation or merge.

## Decision

PostgreSQL remains the durable authority for hard budgets and billable provider work,
but the gateway no longer performs a chain of budget reads and telemetry writes per
request. It uses short-lived PostgreSQL budget grants and a generation-fenced protocol
with two acknowledgements:

1. One short atomic admission transaction reuses or allocates a subject-specific grant,
   consumes it for the microbatch, records immutable attribution and pricing, and
   returns one-use dispatch permits. A refill locks every applicable budget window,
   escrows a small expiring balance, and leases a bounded set of partition-capacity
   slots. Reusing a grant avoids mutating hot budget-window and partition-counter rows
   for every request.
2. An independent bounded microbatch records exact or provisional outcomes against the
   grant. A set-based reconciler periodically moves the aggregate into authoritative
   budget windows and releases unused escrow.

The immutable accounting event stream is the write authority. A dedicated, fenced
worker projects it to the existing spend ledger and required audit outbox. Existing
spend and audit stores remain compatibility read models until their consumers migrate.

## Current integration boundary

The clean replay preserves all main features when accounting v2 is disabled. With v2
enabled, HTTP provider calls and charged cache hits use one admission authority.
Their cost bounds come from the final validated request, not the original JSON body.
Multiple outputs and multiple embedding inputs are included in the bound. A charged
cache hit reserves its known charge and records a terminal result before success.
It does not reserve provider-attempt capacity or call a provider.

Realtime, batch completion, and selector billing still use main's legacy write owner.
They cannot run beside v2 grants until they use the same budget authority and recovery
owner. Startup and Helm reject v2 with realtime or batch enabled. Selector startup
and dynamic activation also reject v2. These checks are temporary safety limits,
not a complete compatibility implementation.

The clean integration must add typed adapters for these three paths before merge or
mixed-feature qualification. Remove each check only after real-dependency tests prove
shared admission, exactly-once settlement, retries, and recovery after process loss.

## Ownership and invariants

- API processes own reservation and finalization queues. They never keep a database
  connection open during provider I/O.
- PostgreSQL functions own lock ordering, budget arithmetic, idempotency, protocol
  generation checks, grant lifecycle, and the dispatch transition.
- The accounting worker owns expiry recovery, renewable-window rollover, and legacy
  projections. Per-partition leases fence duplicate workers.
- A replayed reservation never returns a dispatch token. Database uncertainty before
  the reservation acknowledgement means no provider call.
- A provider call with no proven terminal outcome retains its full unused allowance as
  provisional. Recovery and operator reconciliation never resend provider work.
- Control-plane updates to legacy hard-budget fields synchronize the active accounting
  window in the same transaction. Database fences close the activation-snapshot
  race, and a limit cannot be reduced below committed, reserved, or provisional debit.

## Alternatives considered

Keeping the existing per-request budget reads and separate spend/audit writes was
rejected because the number of database round trips and global queue contention grow
with request rate. Moving budget authority to process memory or Redis was rejected
because it would add reconciliation ambiguity for money and weaken the existing
PostgreSQL durability boundary. Asynchronous accounting after dispatch was rejected
because overload or process loss could make provider work unbillable. A single queue
for admission and terminal writes was rejected because an admission burst could
starve the finalizations needed to release capacity.

A separate network accounting ingestor was not added to the mandatory request path.
The database function already appends the durable journal in the same transaction as
the dispatch decision, while grants remove the repeated hot-window mutation. An extra
service hop is reserved as a measured follow-up only if the durable append benchmark
shows PostgreSQL journal I/O, rather than window contention, is the remaining limit.

## Failure and overload behavior

Reservation timeout, stale generation, database uncertainty, or a full reservation
queue fails closed with 503 before dispatch. Hard-budget exhaustion returns 429.
Finalization has a larger independent queue and retries the same idempotent commit
within a bounded acknowledgement envelope; a terminal acknowledgement is required
before a non-stream response or terminal stream frame is released. Expired dispatched
reservations become provisional. Expired grants close only after all consumed
operations settle, then release unused escrow. Reviewed provider evidence is applied
through an idempotent reconciliation function and emits an operator audit event.

Queue depth, batch size/latency, reservation decisions, projection actions, and event
lag use bounded-label metrics. Readiness includes both microbatch owners and the
projection worker when configured. It also checks the active generation through the
actual accounting pool. A healthy Prisma pool does not prove that this pool is ready.

## Capacity impact

Each request adds one reservation item and one terminal item. A collected reservation
batch uses one atomic grant-admission and persistence transaction; a finalization batch
uses one transaction. Persistence batches are capped at 8 by default with a
two-millisecond dwell and a 250-millisecond per-statement reservation budget. A grant
still targets 32 operations and expires after 30 seconds, decoupling
the measured persistence-batch limit from hot-window refill frequency. Grant subjects
include attribution, authoritative window contracts, and allowance, so capacity cannot
cross economic contracts. Cross-replica budget exclusion remains exact while normal
reservations contend on process-scoped grant rows instead of shared budget rows.
Finalization updates only its operation, reservations, and immutable event; it does not
mutate the active grant row. The background closer derives grant completion from those
operation rows and applies the aggregate to budget windows once. The queues are
separately bounded at 4,096 and 8,192 by default.

Partition capacity is conservative: a grant leases its operation limit from one
partition, operations share that event partition, and grant closure returns the full
lease. This avoids the overlap where large random microbatches previously locked most
partition counters at once. Capacity may remain held until a partial grant expires,
but can never be oversubscribed.

The compatibility projector claims several independent partitions in one operation,
projects spend and audit records in batches, and runs recovery/rollover maintenance on
a paced interval rather than once per event batch. Backlog count and oldest-event age
are explicit metrics and can drive the accounting-worker HPA.

The dedicated Helm role is included in PostgreSQL, Redis, file-descriptor, process,
surge, and retiring-generation capacity arithmetic. API pods do not run legacy spend,
audit, or projection consumers when that role is enabled, and therefore do not open a
telemetry-worker database pool. The worker uses smaller control and foreground pools
because gateway traffic cannot select it.

## Migration and rollback

The Prisma migration is additive and inactive. The preparation tool takes an exact
legacy snapshot under bounded table fences, refuses unresolved legacy holds or
invalid balances, creates partition/window state, and optionally activates one
generation. It is safe to rerun after an activation acknowledgement is lost. Fresh,
last-release, and shared-feature migration verification checks the tables, functions,
columns, and policy triggers.

The clean integration also checks unresolved realtime intents, spend outbox rows,
selector operations, batch jobs, and batch completion receipts before preparation
and activation. One PostgreSQL function owns this check. Operators must stop all
legacy writers first, then let accepted work settle. The check does not fence every
legacy request and does not make a mixed-version online cutover safe.

To roll back only grant allocation, set `accounting_grants_enabled: false` and roll API
processes. New requests then use the original direct-window v2 functions; keep the
accounting worker running until existing grants have closed. To stop all v2 admissions,
disable the protocol startup flag and roll API processes. Keep the accounting worker
and active generation available until reserved operations finalize, expiry recovery
completes, and projection lag reaches zero. Do not drop accounting tables or downgrade
the worker while provisional debits remain. A generation can then be fenced; retained
events and windows remain an auditable record.

## Compatibility removal

The compatibility projector is intentionally one-way. Remove it only after reporting,
audit queries, notifications, and operator tools read the accounting event/window
models directly, and after a release proves no consumer depends on legacy counters or
outboxes. That removal needs its own migration and parity evidence; this change does
not silently create a second long-term ledger.
