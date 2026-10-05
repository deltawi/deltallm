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

Financial queues retain canonical, validated JSON bytes rather than shallow-frozen
models with mutable dictionaries. Each queue defaults to a separate 8 MiB byte
budget for queued and selected payloads, plus a fixed metadata charge. These
startup limits have a 1 MiB minimum and a 64 MiB maximum. They do not replace item
limits or change per-record payload limits. They are not RSS measurements.

Collection also stops at 1 MiB of serialized JSON, including list delimiters and
commas. Valid large records use separate batches instead of failing as one oversized
commit. Cancelled queued work releases its charge immediately. Selected work keeps
its charge until persistence finishes or the worker fails. Constant-time queued
removal does not scan other tenants. Queue byte metrics use only the two fixed
reservation/finalization labels. Future local terminal queues must measure the
whole typed financial envelope, including the issue proof, not only terminal facts.

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

The bank caps subjects, retained cursor bytes, and grant size. Capacity rejection
does not evict a live grant. Expiry cleanup checks at most 256 subjects per refill;
it does not scan the whole configured bank on the request event loop. Subject and
available-permit gauges use fixed lane labels. Action counters contain no tenant,
owner, or fence
values. Grant representations also hide the grantee because it contains a fence.

The inactive bank has an 8 MiB retained-state budget per lane. The constructor
accepts a budget from one byte to 64 MiB; this is not a new runtime setting. Each
cursor uses a conservative charge: 8,192 bytes for its bounded objects and grant
metadata, four bytes per financial-subject character, and 2,048 bytes plus four
bytes per scope-ID character for each window reference. Four bytes covers Unicode
characters. The grant's two identifiers each have a 256-character contract bound.
The fixed charge covers those identifiers and the cursor's model and number state.
This is a retained-state capacity limit, not a process RSS measurement.

A cold batch checks both entry and byte capacity before database allocation. A
large subject can be rejected while a smaller subject in the same batch succeeds.
The byte check does not add a database call or serialize audit and pricing bodies.
The bank retains only the financial subject and funded grant. Expiry, exhausted
grants, uncertain acknowledgements, and close remove each byte charge once. The
byte gauge uses the same fixed lane label. With `L` lanes and `P` processes that
own banks, the default retained-state budget is `L * P * 8 MiB`, in addition to
queues, issued leases, clients, and other process memory.

Subject identity includes the generation, budget references, full budget scope,
model, and allowance text. For example, `1` and `1.0` are numerically equal, but
the database subject contract hashes their different text. The bank must not reuse
a grant across those contracts.

The isolated native profile now supports `--mode all` to compare direct, assigned,
and pre-issued admission on the same pool and terminal owner. Its source manifest
records the commit, file hashes, and dirty-worktree state. It counts refill, claim,
recovery, and terminal calls. This probe excludes HTTP, Redis, provider latency,
and compatibility projection. It is not gateway RPS qualification.

Activation still requires a separate entry and byte budget for issued leases,
the supervised local-dispatch and recovery owners, and clean-image gateway
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

## Inactive local lease foundation

The database foundation adds local-lease fields with `local_dispatch=false` by
default. Bootstrap does not select local dispatch. Assigned grants and durable
pre-issued claims keep their current owners and behavior.

Local funding keeps two deadlines. `dispatch_expires_at` retains the allocator's
earlier budget-period end or short refill TTL. The existing `expires_at` retains
escrow for terminal receipt recovery. Its first value covers the first operation
lifetime plus the remaining short dispatch horizon. The operation lifetime is at
most 15 minutes and the refill TTL is at most five minutes, so recovery is at most
20 minutes from allocation. Exact allocation replay does not extend either stored
deadline. This gives later local issues room to finish, without extending the
period for new provider work. Runtime issue must check both deadlines.

One terminal statement calls the existing bounded cross-grant claim owner and
terminal owner in the same transaction. It retains global operation and grant
lock order, full receipt identity, and one economic effect. Closed receipts can
replay without a new provider dispatch. Batches are limited to 256 entries and
2 MiB. No pool or normal assigned-path database call is added.

Only a proven never-issued suffix can be returned. Returned ordinals cannot be
claimed later, and suffix returns cannot reduce previously claimed capacity.
Return updates grant metadata; the existing settlement owner releases money and
slots once. If the issuer dies, unreturned capacity without a proven receipt
becomes a conservative provisional debit. It is not assumed unused. Late valid
receipts settle against the original funded budget period, not a new period.

The migrations add five scalar fields per funded grant and no new table or index.
They replace the existing settlement checks with local-aware checks. Existing
records use false, zero, and null defaults that retain their previous constraints.
Apply through the coordinated migration role, with a two-second lock limit and
a thirty-second statement limit. Constraint validation reads retained grants;
use a maintenance window if it cannot finish within those limits. Retention,
archival, and vacuum remain under the existing financial-grant policy.

The Python lease owner, byte-bounded issued receipts, supervised bulk return worker,
and terminal journal are still required before activation. This database step is
not a new gateway RPS result.

## Inactive bulk local persistence

One typed repository owns funding, unused-suffix return, and local terminal
persistence. Each normal phase uses one statement for up to 256 items across
subjects. All phases share the existing deadline, cancellation, result-set, and
ambiguity recovery helpers with durable pre-issued permits. Three attempts and
one recovery query per attempt are the fixed maximum. Recovery uses indexed fence,
grant, operation, and event keys and checks the complete owned proof. An invalid
result cannot authorize dispatch or enter another retry layer.

The client accepts at most 1 MiB of serialized batch data. SQL accepts at most
2 MiB and takes grants in the same global order as claims. One bad return entry
rolls back every return in that statement. A closed grant can replay its exact
never-issued suffix without releasing more money. Terminal persistence checks
that the operation lifetime fits its funded recovery horizon. It checks the
original terminal time after operation locks are held, so concurrent retries
cannot accept two different terminal facts.

Single and bulk suffix returns reject missing generations. Single returns also
reject a missing fence or ordinal. Null-safe identity checks prevent an absent
value from marking funded capacity unused. Each rejection leaves grant metadata
and escrow unchanged. These checks are additive; applied migrations are unchanged.

Funding includes PostgreSQL's observation time. The caller records its monotonic
clock before the call. It derives local deadlines from that anchor and the
database-relative horizons. Call latency reduces remaining validity instead of
extending it. Host/database wall-clock differences do not increase local dispatch
validity. A monotonic anchor belongs to one process; a remote adapter must derive
its own anchor and must not trust a received anchor.

This persistence owner opens no pool and starts no task. Bootstrap still uses
assigned grants. Runtime issue, byte-bounded receipt retention, and supervised
bulk return must be integrated before selection of this path. The inactive receipt
store below provides retention, not runtime selection.

## Inactive immutable local receipts

The local receipt store retains canonical reservation bytes, not mutable pricing
and audit graphs. It copies the scalar grant proof and checks nested facts before
serialization. Each restored receipt has separate request dictionaries. Retained
bytes and entry counts have fixed limits. The conservative byte charge covers the
serialized request and scalar/map overhead; it is not a process RSS estimate.

Capacity exhaustion rejects new retention without evicting an existing proof.
Expiry does not prove terminal settlement and does not remove a receipt. Only a
matching operation and generation terminal acknowledgement can remove its exact
issued proof. A recovery slice contains at most 256 entries and only rotates them.
The store starts no worker and performs no dependency call.

A recovered funding proof can have an expired dispatch horizon and a live recovery
horizon. Its stored short deadline is unchanged. Its derived monotonic dispatch
deadline remains in the past. Such a proof can return a known unused suffix but
must never authorize provider dispatch. The local issuer must enforce this check
before runtime selection. The issued store and this proof contract remain inactive.

## Inactive return-only cursor retention

Active, staged, and retiring cursors share one bounded entry and byte budget. Retiring a
cursor removes its dispatch capacity but keeps its immutable first-unused ordinal
and its byte charge. Expiry is not a refund. Only an exact native suffix return
acknowledgement removes a retiring proof, once. An exhausted cursor has no unused
suffix; the separate issued-receipt owner still retains unsettled operations.
Selection and expiry scans inspect at most 256 entries and rotate those entries.
The cursor owner performs no dependency call and starts no task.

Money comparisons do not use the caller's decimal precision. Unused-count
multiplication uses an 80-digit context. Funding and recovery monetary scalar
columns cross the query-client boundary as text, not a float. Local and durable
pre-issued grants share this exact allocation projection. These changes add no
statement, retry layer, pool, or reporting default.

## Inactive staging and atomic local issue

New funding enters staging while cold funding is incomplete. Staging adds no
dispatch capacity and no return candidate. The admission owner must hold it until
commit or abort. An abort moves at most 256 grants to return-only state. It keeps
every byte charge and marks ordinal zero as the first unused ordinal. Warm cursors
keep their issued prefixes and unused capacity.

The local issue owner prepares complete immutable proofs, dispatch results, and
cursor changes before mutation. Each grant must use a contiguous ordinal prefix.
A staged grant cannot skip an active grant's unused suffix. Existing operation
identities cannot receive another dispatch. A batch has at most 256 receipts and
a 1 MiB serialized proof limit, including JSON list delimiters and commas.

Receipt entry and byte capacity, exact grant and subject identity, and both
deadlines are checked before commit. Deadline checks use the database clock anchor
and are repeated after preparation. The commit changes cursor prefixes and retains
issued proofs without an await or external callback. The admission owner releases
its gate synchronously after commit. Complete replies contain dispatch, replay,
and denial results in caller order. The 256-entry and 1 MiB limits also cover those
results, not only their proofs.

The inactive funding owner snapshots input before its first await. It checks
entry and byte capacity before funding. Warm issue needs no SQL call. Cold issue
uses at most two bulk rounds, with no awaited loop per subject. A funding failure
does not consume warm prefixes. Only known fenced grants can enter return-only
state; an unknown acknowledgement is not proof of a refund.

One return task shares the admission owner and cursor store. It skips a busy gate,
scans at most 256 cursors, and returns at most 256 unused suffixes per bulk call.
It checks the complete reply before it removes any retained proof or byte charge.
Return records unused ordinals; whole-grant settlement releases database capacity.
Issued proofs keep their terminal owner after an unused-suffix return.

Dependency failure makes the return worker unready. Failed startup stops admission
and cancels its task. Close first stops admission, then drains unused suffixes
within the caller's deadline. A failed drain keeps its proofs and charges. The
runtime does not select these owners yet: shared proof transport and replay after
terminal acceptance remain required.

## Inactive shared local terminal path

Local dispatch permits and request handles extend the assigned contracts. They
carry the complete issue proof. Assigned contracts remain unchanged. A provider
retry keeps the first proof and adds only an attempt. Charged cache requests use
the same admission and terminal owners. A missing local proof fails closed; it
cannot select assigned or legacy accounting.

The local service reuses the existing reservation and terminal queues. Each queue
keeps immutable bytes, separate capacity, and the 1 MiB batch limit. Large valid
records are split at collection, not by awaited calls per subject. The terminal
owner freezes complete input before its first await. It checks the full reply and
all stored proofs, then checks the caller's deadline before it removes any proof.
A terminal retry uses the original complete facts after local proof removal. It
does not create a new admission.

Compact transport omits the duplicate reservation in each admission reply. It
does not send a process's monotonic clock. A warm reply advances the observation
time by elapsed monotonic time in its owner process. The receiving process anchors
the remaining horizons before its HTTP call. Network time thus reduces dispatch
validity. Terminal proof identity excludes only observation clock anchors. It
still checks every durable grant field, ordinal, and complete reservation.

Authenticated transport has bounded request and response bytes, one caller
deadline, one persistent connection pool, no redirects, and no implicit proxy.
It uses the shared outbound destination policy. DNS resolution is inside the same
deadline. The HTTP call uses the checked address with the original Host and TLS
server name. Plain HTTP and private addresses require explicit policy permission.
Metadata addresses remain blocked even with a broad private-network allowlist.
An uncertain request does not trigger a new admission. Worker endpoints, remote
runtime selection, terminal journal, and final gateway qualification remain
separate unfinished steps. The flag-off path and reporting default do not change.
