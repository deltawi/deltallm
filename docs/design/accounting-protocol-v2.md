# Accounting protocol v2 architecture decision

Status: clean-main integration in progress. Tracking: issue 320. HTTP, cache,
Realtime, selector, and batch now share accounting authority. Native reporting
and isolated roles are connected. Final regression, container, and gateway load
checks remain open. This branch is not ready for production activation or merge.

Sections named "Inactive" record earlier gated implementation steps. They are
development history, not the current runtime selector. The current native mode
is the startup-only `accounting_execution_mode: local_journal` setting.

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

The immutable accounting event stream is the write authority. In native mode, a
fenced worker projects it to native usage facts, audit facts, and sharded rollups.
Reporting reads old and new retained records without writing new native charges
to the legacy spend ledger. Assigned mode retains its compatibility projector.

## Current integration boundary

The clean replay preserves all main features when accounting v2 is disabled. With v2
enabled, HTTP provider calls and charged cache hits use one admission authority.
Their cost bounds come from the final validated request, not the original JSON body.
Multiple outputs and multiple embedding inputs are included in the bound. A charged
cache hit reserves its known charge and records a terminal result before success.
It reserves one accounting slot, but no extra provider-attempt allowance. It does
not call a provider.

Realtime, batch completion, and selector billing now use typed adapters to the
same authority. Real-database mixed-feature checks cover exact charges, all five
budget scopes, replay, uncertain results, and unused funding. The temporary
startup and Helm checks were removed after these proofs passed. Legacy mode
retains main's previous behavior.

Batch persists an immutable checkpoint before provider dispatch. Item claim
epochs fence that write. The existing completion outbox stores terminal results
and delivers them through shared accounting. Its native transitions also require
the live completion lease and attempt number. Reclaim never repeats a paid
provider call. Unknown work stays charged as provisional until valid evidence
settles it. No new batch ledger, pool, queue, or feature worker is added.

## Ownership and invariants

- API processes own bounded local issue and terminal queues. They never keep a database
  connection open during provider I/O.
- PostgreSQL functions own lock ordering, budget arithmetic, idempotency, protocol
  generation checks, grant lifecycle, and the dispatch transition.
- The request role owns signed funding, unused-suffix return, and durable terminal
  acceptance. The projection role owns canonical processing, expiry recovery,
  renewable-window rollover, and native reporting. Leases fence duplicate workers.
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

Native mode now has a signed request role. It owns bounded durable calls and
does not load provider adapters or the API bootstrap. Warm local issue needs no
SQL call. Cold funding and terminal acceptance use one bounded batch each.
This separates API scheduling from database work without moving money authority
out of PostgreSQL.

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

In assigned mode, to roll back only grant allocation, set `accounting_grants_enabled: false` and roll API
processes. New requests then use the original direct-window v2 functions; keep the
accounting worker running until existing grants have closed. Stop traffic before a
full accounting rollback. Keep the active authority and compatible workers available
until reservations, expiry recovery, and reporting drain. In assigned mode, verify
that every legacy scope counter includes the projected charges before disabling v2.
Do not drop accounting tables or downgrade the worker while provisional debits remain.
A generation can be fenced only after its retained money and work are reconciled.

Native mode does not update legacy spend counters. An empty queue does not make
those counters current. After native charges exist, rollback must keep native
accounting enabled and use a native-compatible image. Do not switch to assigned
mode, disable v2, or restart legacy writers as a rollback. A reverse balance
migration is not supplied or verified by this change. Such a migration needs
separate exact-scope reconciliation and writer fences before legacy admission
can resume. Retain native facts, budget windows, receipts, and checkpoints.

### Budget edits, admin reads, and alerts

Migration `20261007150000_accounting_budget_policy_fence` closes the race between
a new hard-budget scope and permits granted before that scope existed. A policy
write takes an exclusive protocol-row lock. Refills and direct reservations hold
the shared lock on that same row. Migration
`20261007152000_accounting_scoped_budget_policy_fence` retains scope identity when
a grant is funded. If a new scope has no current window and matching grants or
direct reservations remain live, the policy write fails and its transaction rolls
back. The admin API returns `409` with code `budget_policy_requires_drain`.
Pause inference for the affected scope, drain its accepted work and unused permits,
then retry. Other tenants can continue. Older live grants without scope identity
require a one-time generation-wide drain. Existing windows still permit limit edits
that cover all committed, reserved, and provisional debits. The fence adds no cache
epoch or database read for a warm request. Accepted requests retain their original
financial attribution. No previously applied migration changes.

Migration `20261007160000_accounting_budget_policy_history` preserves current-period
charges when a hard budget is added or restored. The policy transaction reads
durable terminal events. It does not use delayed usage facts. Charges already
copied to the legacy spend ledger are excluded because the legacy counter includes
them. Hour, day, and month periods use UTC and the existing monthly anchor. A cap
below existing debits returns `409` with code `budget_policy_below_debits`.
New windows are rejected while matching operations have provisional debt or closed
local grants have unreported capacity. Reconcile that usage before the policy edit.
The existing protocol fence prevents concurrent funding during this check.

These history reads run only during a control-plane policy edit, after the scope
drains. They can scan retained history for that scope. Existing control-pool query
and transaction deadlines bound the edit; a timeout rolls back the edit. There is
no new history scan, counter write, index, pool, or worker on the inference path.
Retain source events for budget reconstruction under the existing financial
retention policy. Do not remove them based only on reporting progress.

Native selectors use shared accounting health. The legacy spend worker is required
only for legacy selector billing. No second selector recovery owner is created.

Migration `20261007153000_accounting_team_model_policy_identity` preserves the
existing colon-delimited team/model window identity, including IDs with delimiters.

Migration `20261007154000_accounting_grant_policy_scope_bytes` allows the existing
256-character identities to use Unicode and JSON escaping without rejection.
The additive grant column retains at most 8 KiB of scope data for each funded grant.
One partial index covers live grant scopes, not each provider request. Closing a
grant removes its live index entry; normal grant retention still owns the row.
There is no new per-request write or worker. Migration statements have a two-second
lock timeout and a 30-second statement timeout; retry deployment after draining
writers if those bounds prevent index creation. Existing grant row cleanup and
vacuum ownership do not change.

Native admin GET responses read committed balances from current hard-budget
windows. Unlimited and soft-only scopes read projected native facts plus retained
legacy period balances. Hour, day, and month periods use the existing reset policy
and monthly anchor. An overdue legacy period does not carry its old balance into
the current period. These are control-pool reads: at most 500 authorized identities
per page, one or two database calls, and a two-second page deadline. Legacy nested
team lists use bounded pages under one response deadline. A missing finite-budget
authority returns `503`, not zero. The JSON spend field remains numeric. Native
facts can lag until reporting completes, and window balances can lag until grant
settlement. Mutation receipts do not depend on a reporting refresh; use the GET
response to obtain the current balance.

The existing budget notification worker owns threshold discovery when accounting
v2 is enabled. It reads at most 32 active organizations per cycle on the control
pool, then enqueues crossed thresholds in one bounded batch. Its cursor wraps to
the first page. The existing intent table, dedupe window, delivery fences, and
email/Slack delivery owners do not change. A failed threshold read marks this
optional worker degraded but does not stop accepted intent delivery or inference.
Once the organization page is known, a failed balance read or enqueue advances
the cursor past that page. The next full pass retries it from durable policy and
balances. Later pages can proceed. A failed organization-list read keeps the
cursor because its next page boundary is unknown. Cancellation stops the scan.
No threshold query, recipient lookup, or notification send runs during admission.
The existing scope/time indexes serve balance reads; this change adds no usage-fact
index or per-request counter write. Large reporting reads can time out explicitly;
they must not use the inference pool as a fallback.

Deploy these additive migrations before the application update. Old application
versions also receive the database fence and funded-grant scope data. Rollback
keeps this safety fence and native accounting enabled. Do not restore legacy spend
reads as native authority.

## Compatibility removal

The compatibility projector is intentionally one-way. Remove it only after reporting,
audit queries, notifications, and operator tools read the accounting event/window
models directly, and after a release proves no consumer depends on legacy counters or
outboxes. That removal needs its own migration and parity evidence; this change does
not silently create a second long-term ledger.

### Native batch schema and rollback

Migrations 141 and 142 add the nullable `accounting_checkpoint` item field and
its bounded envelope checks. Old rows stay null. New and changed rows must meet
both constraints. The checks use `NOT VALID` to avoid scanning retained batch
history during expansion. No data backfill is required for old null rows.

Apply these migrations through the existing release migration job. They use a
two-second lock limit and a thirty-second statement limit. If the job cannot
obtain the lock, stop rollout and schedule the bounded migration again. Do not
edit an applied migration or increase a request-path timeout.

Constraint validation is optional maintenance after rollout. Use the same
coordinated release owner and a measured maintenance window:

```sql
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';
ALTER TABLE deltallm_batch_item VALIDATE CONSTRAINT batch_accounting_checkpoint_bound;
ALTER TABLE deltallm_batch_item VALIDATE CONSTRAINT batch_accounting_checkpoint_envelope;
COMMIT;
```

If validation exceeds its bound, the transaction rolls back. New writes remain
protected by the existing checks. Do not treat an unvalidated historical check
as a reason to bypass the native checkpoint owner.

Before rolling native accounting back, stop new admissions and batch creation.
Keep native API, batch, request, and projection owners available until item
proofs and completion receipts settle, unused suffixes return, grants close,
and reporting reaches its final checkpoints. Uncertain debits need evidence;
they are not unused money. Retain the additive schema and compatible closer.
An old writer must not resume a stored native item or completion receipt.
Draining batch work does not permit a switch to legacy budget counters. Follow
the native-compatible rollback requirement above.

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

## Inactive terminal journal acceptance

Journal acceptance and canonical accounting have different receipts. A journal
receipt has a `journal_sequence`. It does not claim an `event_sequence` before
the worker creates an accounting event. Bootstrap does not select this repository.
The existing assigned and local terminal owners keep their behavior.

The repository freezes the full reservation and terminal before its first await.
It sends one compact identity batch and two immutable document arrays. The compact
identity includes exact allowance, subject, generation, grant, owner, fence, and
ordinal. SHA-256 hashes cover both complete documents, including terminal time.
The client permits at most 256 records and 1 MiB for the complete encoded call.
SQL permits at most 2 MiB, including array encoding. Missing fields, repeated
operations or ordinals, a changed hash, and an unfunded subject fail atomically.

One primary database call accepts a normal batch. An uncertain acknowledgement
uses exact-key recovery with the original hashes and caller deadline. Recovery
returns the first journal identity, not another provider admission. Each partition
counter changes once per batch. Journal and payload inserts are bulk statements
in the same transaction. No application loop awaits a write per request or subject.

Pending documents have two durable limits. Each partition has at most its funded
outstanding-operation limit in pending entries, and at most 64 MiB of pending
document bytes. The byte count covers both UTF-8 documents; it is not a database
disk or RSS measurement. Separate entry limits bound compact rows and index
metadata. A full queue rejects new acceptance. It retains existing documents and
permits exact replay. Worker failure must retain both capacity charges.

An accepted ordinal cannot be returned as unused. An unresolved journal entry
prevents grant closure. Its money remains reserved. Expiry moves at most the
requested 256 grants to draining in one pass; it does not update all expired
history first. The inactive worker repository claims and commits accepted work.
Its runtime lifecycle, local receipt integration, and failed-record operations
remain required before activation.

### Inactive canonical worker

Claims carry the generation, worker identity, lease nonce, and positive journal
keys. A claim selects at most 256 entries and 1 MiB of documents. Pending and
expired branches each inspect at most the requested entry limit. Claims expire
after a bounded lease. A stale owner cannot commit or fail another owner's work.

Canonical processing checks complete document hashes and financial identities
before its bulk writes. Operations, per-window reservations, events, grant usage,
capacity release, and pending-document removal commit together. Accepted facts
may be processed after the original dispatch or grant expiry; this never grants
another provider admission. A completed record retains its canonical event key
for exact retry. The lease is checked again before transaction completion.

A normal claim or commit uses one database call. A lost claim reply is recovered
with the original nonce. A lost commit reply uses the original completed keys.
Neither recovery issues a second claim or a second financial write. Cancellation
can leave a committed result, which the same keys recover without another debit.

Five unsuccessful attempts move a record to failed state. Failed entries retain
their documents, reserved money, pending entry charge, and pending byte charge.
They remain visible in a separate failed-entry count. No automatic eviction or
retention job removes them to hide a backlog. Runtime selection is still off.

### Shared journal acceptance owner

The local terminal owner can explicitly select the journal receipt contract.
Canonical finalization remains its default. Each owner rejects the other receipt
kind, and validates the complete batch before removing any issued local proof.
Validation includes generation, operation, outcome, key bounds, and replay state.

A journal acknowledgement means the complete terminal is stored durably. It does
not claim that a canonical event exists. Its journal sequence is not an event key.
Once all acknowledgements are valid, local queue and proof bytes can be released.
Durable pending documents, pending capacity, and reserved money remain charged
until canonical processing commits. A lost or cancelled acknowledgement keeps
the local proofs for exact retry. Closed replay keeps the original journal keys
after the large pending documents have been removed.

Provider calls and paid cache hits use this same terminal owner and request queue.
The journal adapter delegates one bounded append, with no second database call or
legacy writer. Bootstrap does not yet select this runtime. Supervised processing,
unused-grant return, recovery, and transport ownership remain activation gates.

Admission effects must also use the current bounded keys. The grant-counter
update uses an explicit current-grant key set. Each reservation-reference lookup
depends on its inserted operation. This prevents a planner from scanning retained
grant or grant-window history during admission. Actual nested plans cover four
planner modes with 10,000 retained grant-window references and unchanged row
limits. These restrictions add no database round trip or financial authority.

### Supervised journal processing

One worker owns one task and at most one immutable claim. The claim has at most
256 sequence keys and a conservative 16 KiB retained-byte charge. It contains no
audit, spend, request, or pricing document. A concurrent tick is rejected without
a waiter or another database claim. PostgreSQL remains the financial authority.

Startup must complete a real claim and processing check before it reports ready.
An empty claim uses one database call; a normal nonempty tick uses one claim call
and one canonical-processing call. Exact ambiguity recovery stays in the existing
repository. A processing error can make one bounded failure-transition call.
If that acknowledgement is uncertain, the worker keeps its original claim.
Cancellation also keeps the claim. The next tick reuses the same keys before it
can claim other work. Lease loss returns no financial effect and permits recovery
through the database-owned claim path.

Temporary database failure produces degraded task health and bounded, jittered
backoff. Unexpected task exit produces failed health without exception text.
Fixed action and outcome labels record worker calls, errors, cancellation, and
duration. No worker, tenant, operation, or financial key enters a metric label.

Close stops new claims and waits for the one owned task within the caller's
deadline. A stopped task is not proof that the durable backlog has drained.
Accepted documents, pending capacity, reserved money, and failed records remain
charged in PostgreSQL. Task health describes execution, not settled balances.
Runtime selection still requires the later bootstrap, durable backlog checks,
transport roles, terminal drain, unused-suffix return, and recovery integration.

### Shared local startup and shutdown

`LocalAccountingRuntime` owns the local queues and their one return worker.
Construction rejects another issue owner or a worker that is already running.
The return task starts before the admission queues. One real active-generation
probe must pass before the runtime exposes its accounting service. Concurrent
probes cannot create another task or database call. Queue failure or an unready
return task makes the runtime unready with a fixed, safe detail code.

Close uses one deadline for every step and stays within the process worker-drain
deadline. It stops local issue before its first await, drains accepted terminal
queue work, then closes the return owner. Returns cover only a proven unused
suffix. Concurrent close calls share one cleanup task. Interrupted tasks remain
visible to the existing shutdown owner; cancellation must not hide a live task.

A successful local drain requires empty local proof and byte queues, acknowledged
unused suffixes, and stopped owned tasks. An issued operation without a terminal
reply makes the drain incomplete. Its immutable proof keeps its entry and byte
charge. A lost funding reply also keeps database funding charged, even if no local
cursor received that reply. A stopped process is not proof that money is free.

Journal processing remains a separate role. Local drain can succeed while accepted
documents and partition capacity remain charged in PostgreSQL. Canonical processing
must commit before document removal; grant settlement must commit before budget
and partition capacity release. The new owner adds one startup probe and no extra
request-path call. Bootstrap selection, transport roles, bounded expiration and
recovery, and durable backlog health remain activation requirements.

### Bounded grant recovery

One recovery call has two work limits. It moves at most 256 expired active grants
to draining state, then inspects at most 256 draining keys for closure. The caller
can set a smaller limit. Indexed expiry and drain ranges use generation, expiry,
and grant identity. The inspected-key limit applies before eligibility checks,
so pending terminal work cannot cause a full retained-history scan.

A disposable cursor records only scan position for the generation. It advances
past blocked keys and wraps through the range. Cursor loss causes reinspection,
not financial release. A locked cursor makes another recovery owner skip closure.
This metadata stores no balance, capacity, terminal document, or replay identity.

Pending journal work and reserved billing operations still prevent closure.
Budget-window and partition locks, exact balance effects, and provisional charges
for unknown owner loss are unchanged. Failed terminal work stays funded. This
database foundation does not yet select a maintenance task in bootstrap.

### Durable backlog health

Task execution and durable financial progress are separate checks. A bounded
snapshot reads maintained capacity counters for the generation. It checks all
64 possible partition keys and rejects missing, extra, or misnumbered coverage.
One partial index provides the oldest pending, processing, or failed record.
Completed receipts do not enter this work index. Health never counts event,
receipt, or spend history to rebuild a missing counter.

One probe owns one immutable scalar observation and no financial document.
Concurrent refresh is rejected without another task or waiter. A refresh uses one
native read within the caller deadline. The observation includes query time in
its age. Failed or cancelled reads retain the last observation but make health
unavailable. Unknown or stale state cannot become an invented zero backlog.
Failed records, excessive age, full capacity, and an inactive generation produce
fixed safe details. This observer does not admit requests or release money.

A healthy backlog can still have funded work outstanding. Journal completion
removes the pending document charge; grant settlement releases funding and
partition capacity. An observed empty snapshot is not a cluster-wide drain proof
while other replicas can still admit work. Runtime integration must connect this
required check to the existing readiness and lifecycle owners before selection.

### Storage and rollout policy

The compact journal has three unique key indexes and five partial work indexes.
A new pending receipt writes six of these indexes. The lease, expiry, and claim
indexes contain only processing work. The unsettled-grant index supports the
close guard. Pending documents have only their sequence primary key. Large audit
and spend documents do not enter the retained replay indexes.

Completed documents must be removed only in the transaction that commits their
canonical result and releases the pending capacity charge. Compact receipts are
not deleted in this slice. An archive must retain the operation, ordinal, hashes,
and canonical result before a later retention job removes a hot receipt. That job
and its replay proof are an activation gate, not an implemented feature. Financial
retention stays governed by the deployment's accounting retention policy.

The first rollout uses unpartitioned tables. Exact-key acceptance does not depend
on retained row count. A partition change needs a separate design that keeps
global operation and grant-ordinal uniqueness. Operators must monitor relation and
index size, dead tuples, vacuum age, and analyze age. Autovacuum and auto-analyze
remain enabled. Pending-document deletion and receipt status changes need a
post-load vacuum/analyze check in the final qualification evidence.

The migrations create new inactive tables and indexes, with 2-second lock and
30-second statement limits. They do not rewrite existing financial history.
Deployment migration ownership is unchanged. Rollback keeps the old runtime
selector and journal data intact; it must not drop accepted financial work.

### Native recurring-budget reset fixes

The existing native recovery owner also renews expired budget windows. One cycle
uses four separate maintenance calls: expired grants, expired operations, grant
settlement, and window renewal. One backlog call follows these calls. Each call
has the existing statement limit and shares the cycle deadline. The existing
presence observer can add its normal observation calls. No new task, pool, queue,
or inference call is added. Each renewal call creates at most 256 windows, or the
smaller configured recovery batch limit.

Migration `20261007170000_accounting_budget_period_sync` keeps recurring policy
edits valid after a reset. Before an entity policy UPDATE, it advances an unchanged
expired reset date and removes the expired compatibility balance. It keeps exact
current-period charges that already reached the legacy spend sink. The existing
AFTER trigger remains the policy owner. When it creates a native window, it adds
only charges that did not reach that sink. Native windows remain the hard-budget
authority. The control-plane reads use durable terminal events, not delayed native
reporting. They can scan scoped history and remain inside the existing control
statement deadline; they do not run in inference or automatic recovery.

Unlimited admin and soft-budget reads exclude native facts already in a current
legacy counter. If that counter has expired, they include all native facts in the
current period. These reads still use at most two calls for 500 authorized entities.
They do not count a charge twice while a cap is removed.

The entity UPDATE already owns the row lock used by the compatibility spend sink.
The reset trigger does not lock native windows. Renewal takes the shared protocol
fence before window locks. A policy edit takes the existing exclusive protocol
fence before window locks. This order prevents an edit and renewal from creating
competing windows. Removing a cap also cancels its last pending renewal. A later
cycle cannot restore the removed cap. Concurrent renewal owners still use window
row locks, `SKIP LOCKED`, and the existing unique window identity.

Hour, day, and month periods use UTC. Month resets keep the anchor day, including
February and leap years. The corrective migration
`20261007171000_accounting_monthly_reset_metadata` keeps this rule when old reset
metadata is null or is not an object. Other metadata keys stay intact.
Explicit invalid reset dates still fail. Old financial
events and provisional balances stay intact. A failed renewal does not publish a
healthy recovery observation. Admission still fails closed until a valid window
exists. Legacy mode keeps its existing reset owner when no native protocol is active.

This control-plane correction is used instead of a second background reset owner
or an entity UPDATE from inside a window lock. A second owner would duplicate
policy. An entity UPDATE inside that lock would reverse the policy lock order.
Compatibility counters are retained only for the existing sink and migration
baseline; this change does not add a requirement to enable that sink. A future
removal must retain the legacy cutover baseline and its exact period attribution.

Apply both additive migrations before the new worker image. Keep all applied
migrations unchanged. No financial row is deleted or rewritten during deployment.
Keep these migrations and accepted financial data during a runtime rollback. An older
native recovery worker does not renew windows, so it is not a safe long-term
rollback target for recurring budgets. Use a forward fix or the documented drained
legacy cutover. Test fresh installation and both supported upgrade paths before release.

## Terminal publication order

The compatibility audit adapter accepts both UUID source keys and recovery keys
such as `operation-id:expired:v2` and `operation-id:reconciled:v2`. Valid envelope
UUIDs keep their identity. A UUID source keeps the original UUIDv5 fallback.
A non-UUID recovery key uses the same namespaced MD5-derived identifier as native
audit projection. This hash is a stable deduplication key, not a security primitive.
It prevents a recovery record from blocking the compatibility checkpoint without
changing existing sink identities. Real-database tests cover expiry, resolution,
lost sink acknowledgements, and checkpoint replay with unchanged money and audit
capacity. Native mode does not enable the compatibility worker.

The review loop found that an event number is not a commit-order guarantee.
A grant terminal or an operator reconciliation could allocate a lower number,
remain uncommitted, and appear after a reporting checkpoint had passed it. The
result was a permanent omission from spend and audit reports. Direct expiry
could also select a grant-backed reservation and change its funded window
outside the grant settlement owner.

Migration `20261007173000_accounting_event_publication_order` fixes both defects.
Terminal writers take one transaction-level publication lock for each affected
generation and partition. They take these locks before event-number allocation
and keep them until commit or rollback. They take all financial locks first,
then publication locks in partition order. Recovery selects and locks its whole
bounded page before publication. Direct expiry excludes grant-backed records.
The existing grant owner still settles those records.

Migration `20261008001000_accounting_event_publication_parent_locks` completes
this lock order for native journal inserts. Non-compact receipts need a
foreign-key lock on each parent window. The journal takes these shared key locks
in window order before publication. Without this step, a journal terminal and
an operator reconciliation for the same window could deadlock. Shared key locks
do not serialize independent journal readers. Fully settled compact receipts
skip this step and keep their existing query budget. The parent lookup uses
only the bounded prepared grant keys, not a scan of retained window history.

PostgreSQL remains the financial and event owner. The lock key contains only a
generation and partition, not a tenant identity. A page has at most 256 records
and 64 distinct partition locks. The locks add no database calls, pool, queue,
task, or provider work. Existing statement and lock deadlines still apply. A
timeout rolls back the complete statement; a retry keeps the existing event
identity and financial replay checks.

A global publication lock was rejected because it would serialize independent
partitions. A reporting delay was rejected because a delay cannot prove commit
order. An insert trigger alone was rejected because PostgreSQL evaluates the
sequence default before a `BEFORE INSERT` trigger. The fixed writer functions
are checked by a real-database test so a new terminal writer cannot omit the
publication lock without a test failure.

Stop the old writers before this migration. For a database that already used
native reporting, a controlled replay can restore omitted records. Replay only
the native reporting checkpoint; keep all source events, financial balances,
facts, and rollups. The deployment guide gives the required sequence. Tests
verify complete spend and audit effects, independent partitions, commit and
rollback, native journal versus reconciliation, and idempotent checkpoint replay.
Tests also use a three-connection barrier for uncertain terminals in the same
window and check parent lookup plans with four planner settings and retained history.
This review does not change the retained RPS qualification results.

## Request-local proof preparation

Proof preparation uses the existing reservation, receipt, and terminal owners.
It does not add a cache, a second financial path, or a trusted-model flag.
`reservation_snapshot` checks the complete mutable reservation and returns an
owned model with its canonical bytes. The synchronous prepare phase can use
that model without parsing the bytes it has just produced. Dispatch and handle
checks still compare owner, generation, partition, and exact proof bytes. Equal
money values with different canonical documents do not match.

`RetainedLocalReceipt.prepare` checks the grant and the complete reservation.
It returns the owned receipt for the issue prepare phase. The retained snapshot
stores only scalar grant facts, canonical reservation bytes, operation identity,
and generation. Its raw constructor checks the full document and canonical
form. The store can check capacity and duplicate identity without decoding.
Its fixed memory charge includes the new scalar metadata. No receipt is evicted.
All preparation still occurs before the cursor and receipt stores commit.

A provider retry keeps the accepted issue and checks only the new attempt.
The new attempt owns its pricing containers. The protocol's 128-attempt limit
still applies. A new typed terminal checks the complete receipt and finalization
through their normal validators, then checks their shared identity. Received
terminal documents still pass full document validation. Existing accepted
terminal snapshots stay unchanged through queue, wire, and journal retries.
The terminal and its retained receipt share the same reservation byte object.

The canonical encoder, money rules, wire documents, database schema, queue
limits, deadlines, and durable replay rules are unchanged. Roll back with the
previous application image; no database migration is needed. The focused proof
benchmark is `tests/performance/benchmark_accounting_proofs.py`. It measures
small and wide payloads without network or database work. Gateway diagnostics
use the unchanged disposable-kind fixture and record their own image identity.
