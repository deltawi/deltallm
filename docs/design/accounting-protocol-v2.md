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
It reserves one accounting slot, but no extra provider-attempt allowance. It does
not call a provider.

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

## Inactive permit foundation

The clean replay adds the fenced permit schema before it adds a permit runtime.
Existing grants keep `dispatch_mode='assigned'`. Existing operations have no permit
ordinal or fence. No setting selects the new functions yet. The current admission,
finalization, readiness, reporting, and recovery owners remain unchanged.

The new allocation function uses the existing budget allocator. It reserves money
and partition slots for one exact subject and one owner. A claim must match its
generation, owner, grant, fence, ordinal, allowance, and frozen request. PostgreSQL
enforces the unique grant ordinal. A replay does not return a dispatch token.
Claimed work uses the current terminal acknowledgement and recovery path. Expiry
releases unclaimed capacity only after claimed operations have settled.

This schema does not permit local provider dispatch. Each claim still needs a
durable acknowledgement. Redis and process memory cannot create money or slots.
The migration adds columns, constraints, and functions without a data backfill.
Apply it through the normal migration role during a bounded maintenance window;
adding constraints takes table locks. Rollback leaves the additive schema in place
and keeps the existing assigned-grant writer. Do not drop retained economic records.

The next runtime step must batch refill and claim work across subjects. The source
branch's sequential database call per subject is not copied. One request batch must
have a fixed database-call bound, stable cross-subject lock order, bounded local
state, and exact recovery identities. Typed permit persistence belongs in a separate
repository owner, not in the growing protocol repository. Bootstrap must select one
admission owner; a failed permit must never fall through to assigned admission.

The permit schema stays inactive until these safety checks pass. Removing assigned
admission also requires new gateway load evidence and a reviewed rollback window.

## Permit batch persistence

`AccountingPermitRepository` owns typed refill and claim batches. It uses the same
accounting pool and the same deadline, error, cancellation, and metric owner as
assigned admission. It does not add a pool, worker, setting, or admission fallback.
Bootstrap does not select it yet. The API and cache paths keep assigned admission.

A refill batch needs one database call for up to 256 subjects. A claim batch needs
one call for up to 256 operations across grants. Both have a one-MiB client payload
limit. PostgreSQL also bounds the expanded JSON representation at two MiB. An invalid
batch is rejected before a provider can run. Exact lost-ACK recovery adds one indexed
query per attempt. At most three attempts fit inside the existing ACK budget; they
use the same fences and frozen requests.

PostgreSQL locks refill fences, existing grants, and all applicable windows in stable
order before it invokes the reviewed allocator. Claim batches lock operation
identities and grants in stable order before they append any operation. They never
mutate budget windows. An identity error rolls back the whole claim batch. Existing
terminal or exhausted claims return replay without a dispatch token. Recovery checks
the complete request snapshot as well as owner, generation, grant, fence, ordinal,
and partition. A different claim cannot authorize provider work.

The window-lock lookup uses primary-key reads for explicit references and an indexed,
row-bounded lookup for each implicit scope. It rejects more than eight matching
windows. The scope/time index must be built through the migration role with the
declared lock and statement limits. If the bounded migration cannot finish, schedule
a maintenance window; do not increase request timeouts. Rollback retains the additive
functions and index and selects the existing assigned writer.

This persistence step is not the process-local permit bank or local dispatch. It
does not remove the claim acknowledgement. Before activation, the bank must batch
refills, bound subject state, handle partial grants, and retire unused permits safely.
The complete allocator and bank still need constant-arrival evidence. The indexed
window-lock check alone is not evidence for all SQL inside the allocator.

The new index adds one entry per budget-window creation or renewal, not per ledger
event. Its columns do not change when money counters change. The plan check uses
25,000 expired windows for the same subject and 25,000 live windows for other
subjects. It verifies the index and the one-row result. Retain normal window-table
vacuum and analyze work. Recheck index size and write cost in the full qualification.

An additional migration corrects zero-cost permit grants. Zero reserved money does
not mean that all operation slots are used. Such a grant stays active until its
operation limit is reached. The regression failed against the copied source function
and passed after the appended correction. The applied source migration is unchanged.

## Inactive permit bank

`PreissuedPermitBank` owns a bounded set of funded grant cursors. It does not own
a client, pool, task, or queue. The existing durable microbatch owner can call it.
Bootstrap cannot select it yet. Each provider dispatch still needs a durable claim
acknowledgement; a local ordinal alone does not authorize a provider call.

A warm batch uses one claim call across all subjects. A cold batch uses at most two
refill rounds and one claim call, independent of subject count. The second round
handles a partial grant. Work still unfunded after that round receives a capacity
rejection. Each repository call keeps its existing bounded lost-ACK retries. These
bounds do not add an admission fallback or extend the caller deadline.

The bank advances each ordinal before it awaits a claim. A failed or cancelled call
retires every touched cursor. It cannot reuse an uncertain ordinal. The database
worker recovers claimed work and releases proven unused money and slots after
expiry. Closing the bank rejects new work, waits for its owned call, and clears
local state. It does not release money that might belong to a durable operation.

The bank caps subjects and grant size. Capacity rejection does not evict a live
grant. Expiry cleanup checks at most 256 subjects per refill; it does not scan the
whole configured bank on the request event loop. Subject and available-permit
gauges use fixed lane labels. Action counters contain no tenant, owner, or fence
values. Grant representations also hide the grantee because it contains a fence.

Subject identity includes the generation, budget references, full budget scope,
model, and allowance text. For example, `1` and `1.0` are numerically equal, but
the database subject contract hashes their different text. The bank must not reuse
a grant across those contracts.

The isolated native profile now supports `--mode all` to compare direct, assigned,
and pre-issued admission on the same pool and terminal owner. Its source manifest
records the commit, file hashes, and dirty-worktree state. It counts refill, claim,
recovery, and terminal calls. This probe excludes HTTP, Redis, provider latency,
and compatibility projection. It is not gateway RPS qualification.

Activation still requires full allocator plan checks, a byte budget for retained
state, the supervised local-dispatch and recovery owners, and clean-image gateway
qualification. Fewer refills alone do not remove the per-request claim or terminal
acknowledgement.

## Allocator plan bounds

The clean replay checks SQL inside the grant allocator, not only its outer call.
The first new regression returned nine windows but read 25,000 rows to sort tied
window times. The correction removes the unnecessary window-ID tie sort. The
parent still locks window IDs in stable order and rejects more than eight matches.

The allocator now uses the same indexed request-scope lookup at its window-lock,
balance, and grant-link stages. Expired renewal checks use a scope-specific partial
index and one scalar active-window probe per scope. This avoids an anti-join that
can scan other tenants' active windows. An expired renewing policy with no current
window still rejects admission; it cannot become an unlimited budget.

Actual nested-plan tests then found full scans of retained operations and grants
in JSON batch joins. New append-only migrations use bounded key arrays for ordered
locks and primary-key probes for identity, new-work, and replay checks. They keep
the same lock order, transaction, fence checks, payloads, and economic effects.
No database call, pool, fallback, setting, or request timeout is added.

Four native cases cover assigned and pre-issued admission, explicit and implicit
windows, and six calls through the prepared-statement threshold. Each case seeds
50,000 windows, 10,000 closed grants, and 10,000 closed operations. The test captures
every executed nested plan with PostgreSQL's session-local `auto_explain` module.
It checks that no retained-history scan runs. The fixture also verifies all five
budget scopes, renewal rejection, hard-window bounds, and exact settlement.

The recorder owns one diagnostic connection and closes it with a fixed deadline.
It caps plan count and bytes. Reports contain only node types, table and index
names, row and buffer counts, and a query hash. They omit query text, conditions,
outputs, and parameter values. No server-wide logging or preload change is needed.
This diagnostic connection is not a new production pool.

The renewal index adds one entry per renewing window, not per request or event.
Money-counter updates do not change its columns. Build it through the coordinated
migration role with the declared two-second lock and thirty-second statement
limits. Use a maintenance window if the bounded build cannot finish. Keep window
vacuum and analyze work, and include index size and write cost in final load
qualification. The applied migrations remain unchanged.

Rollback can select the existing assigned writer or disable v2 through the current
cutover procedure. Keep the additive index, functions, and retained economic
records. These bounds do not complete local dispatch, the terminal journal, or
gateway qualification.
