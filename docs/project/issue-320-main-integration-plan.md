# Issue 320 main integration plan

Status: active

Base: `origin/main` at `f5ffd80d`

Source implementation: `feat/issue-320-accounting-v2` at `534ef828`

Accepted performance code: `cc3113bd`

## Current status

Whole slices 1 through 8 are complete. Slices 9 and 10 have substantial verified
foundations, but their runtime integration is not complete. Slices 11 through 14,
the current-main Realtime, batch, and selector billing adapters, and final gateway
qualification remain unfinished. The experimental branch's 500 RPS result is not
evidence for this clean replay. This branch is not ready to merge.

The migration-131 checkpoint passed all 7,829 tests and all three migration paths.
The first PostgreSQL run failed two compatibility cases. Their unchanged modules
then passed all 15 cases, and a full confirmation passed all 705 PostgreSQL cases.
Keep the original failures: the passing confirmation does not establish their
causes or a production fix. The next step is the supervised journal worker.

## Integration rules

- PostgreSQL remains the durable economic source of truth.
- Redis and process memory remain bounded, disposable coordination layers.
- Budget and accounting failures stay fail closed before provider dispatch.
- Every replay keeps stable operation, event, owner, fence, and request identities.
- No slice adds an unmeasured SQL or Redis request-path round trip.
- Migrations remain append-only. Each database slice must pass fresh, last-release,
  and shared-feature upgrade checks.
- A slice is complete only after focused tests, required real-dependency tests, Ruff,
  formatting, and `git diff --check` pass.
- Load qualification starts only after all code slices are integrated on one clean
  image.

## Progress

- [x] Create a clean managed worktree from the latest remote `main`.
- [x] Create branch `codex/issue-320-main-integration`.
- [x] Slice 1: port concurrency observability, durable telemetry acceptance, and the
  reproducible load harness.
- [x] Preserve current `main` realtime startup while adding the runtime metrics owner.
- [x] Preserve the reorganized documentation navigation and register the concurrency
  measurement guide in it.
- [x] Retain the historical baseline samples because a regression recomputes their
  summary and validates their field allowlist.
- [x] Verify slice 1 with focused hermetic, startup-lifecycle, and real-PostgreSQL
  tests.
- [x] Commit slice 1 as one reviewable integration change.
- [x] Slice 2: port ingress isolation and bounded authentication fallback.
- [x] Slice 3: port dependency capacity ownership and startup arithmetic.
- [x] Slice 4: port capacity schema and durable admission foundations.
- [x] Slice 5: port budget and prompt hot-path reductions.
- [x] Slice 6: apply the spend-recovery schema, fixtures, and implementation.
- [x] Slice 6: preserve realtime recovery and the shared billing transaction owner.
- [x] Slice 6: apply request deadlines and bounded callback and guardrail work.
- [x] Slice 6: verify fresh, last-release, and shared-feature migrations.
- [x] Slice 6: port spend recovery, deadlines, and bounded work.
- [x] Slice 7: apply the managed lifecycle and deployment capacity implementation.
- [x] Slice 7: retain current main's realtime, asset-link, and authorization checks.
- [x] Slice 7: begin realtime cleanup before generic request cancellation.
- [x] Slice 7: verify the shared lifecycle, deployment, and real-dependency gates.
- [x] Slice 7: port readiness, drain, and Kubernetes capacity contracts.
- [x] Slice 8: port accounting protocol v2 and atomic grant admission.
- [x] Slice 8: check current main's realtime accounting against the new authority.
- [x] Slice 8: use the validated, transformed request for cost bounds, including
  multiple outputs. Do not parse the original request body again.
- [x] Slice 8: keep one accounting bootstrap owner and remove new policy from
  large composition modules.
- [x] Slice 8: put charged cache hits through the same admission and terminal owner.
- [x] Slice 8: probe the actual accounting pool and active generation for readiness.
- [x] Slice 8: reject preparation and activation while legacy billing work is pending.
- [x] Slice 8: reject unmigrated realtime, batch, and selector writers when v2 is on.
- [ ] Before merge: add typed realtime, batch, and selector adapters to the shared
  v2 budget and recovery authority. Prove mixed-feature recovery before removing
  the temporary checks. Legacy mode must retain every current main feature.
- [ ] Slice 9: port pre-issued permits and local lease dispatch.
- [x] Slice 9: add the inactive fenced-permit schema and prove current grant parity.
- [x] Slice 9: batch refill and claim work across subjects with a fixed database-call
  bound. Do not copy the source branch's sequential subject loop.
- [x] Slice 9: put permit persistence in a small typed repository owner.
- [x] Slice 9: add the bounded permit bank and test partial grants, cancellation,
  expiry, shutdown, and the warm-path call bound before bootstrap can select it.
- [x] Slice 9: profile all SQL inside the grant allocator before permit activation.
- [x] Slice 9: verify the retained cursor byte budget with the required gates.
- [x] Slice 9: add inactive immutable issued-receipt storage with both entry and
  byte limits. The local runtime must use this owner before activation.
- [ ] Slice 9: prove local-lease funding, unused-suffix return, expiry, and conservative
  owner-loss recovery before local dispatch can run.
- [x] Slice 9: separate the local dispatch deadline from the terminal recovery
  deadline in the inactive schema and issue owner. Keep dispatch within the funded
  budget period and refill TTL. Runtime and transport selection are still pending.
- [ ] Slice 9: retain typed cost bounds, the shared cache admission owner, and
  missing-owner rejection when adding local or remote accounting clients.
- [ ] Slice 9: retain the legacy reporting default while accounting v2 is disabled.
- [ ] Slice 9: keep protocol construction in the small accounting bootstrap owner.
- [x] Slice 9: verify immutable financial queue payloads, byte-bounded collection,
  and retained-byte limits across the required gates.
- [ ] Slice 10: port the terminal journal and compact acknowledgement path.
- [ ] Slice 11: split accounting transport and projection roles.
- [ ] Slice 12: port bounded worker runtimes, economic settlement, and recovery limits.
- [ ] Slice 12: bound expiry transitions as well as reconciliation. The source
  settlement SQL and the inactive lease foundation update every expired active
  grant before the limited reconciliation selection. Replace this in a new
  migration with a bounded indexed selection. Prove the limit with more expired
  grants than one worker slice, retained-history plans, and concurrent foreground
  admission. Do not edit an applied migration.
- [ ] Slice 13: port terminal/read-model lanes and rollup sharding.
- [ ] Slice 14: port settled-receipt and narrow streamed projection fast paths.
- [ ] Run fresh and upgrade migration verification for the complete integrated chain.
- [ ] Port the reproducible kind harness and bounded 500 RPS runner from the
  source worktree. The current main runner is capped at 200 RPS. Verify generator
  accounting, output limits, mock controls, and complete metric coverage before
  qualification. Keep throughput, economic correctness, and latency gates separate
  in the report.
- [ ] Run the 50, 100, 200, and short 500 RPS ladder on one clean kind image.
- [ ] Run the ten-minute 500 RPS qualification only after the short ladder passes.
- [ ] Complete the requested final 50, 100, 200, and 500 RPS tests on reproducible
  kind. Record each result separately. Do not treat native SQL probes as gateway
  qualification.

## Final qualification request

The user requested the complete 50, 100, 200, and 500 RPS series after integration.
Use one clean commit and image, the pinned kind tools, and the fixed local provider
mock. Do not use Rancher. Keep replicas, resources, pool limits, workload, and
pass/fail limits the same across rates. First run the short ladder to detect unsafe
capacity or accounting failures. Then run the ten-minute measurement at each rate.
Any pause between rates must be recorded and must wait for accounting to drain.
There is no cooling pause inside a measurement window.

Keep raw allowed samples and a source/image/configuration manifest in a new
evidence directory. Report offered and received throughput, successes, status and
error counts, p50/p95/p99, gateway and provider time, queue and in-flight trends,
dependency calls, and accounting reconciliation. A successful HTTP response alone
does not prove a correct charge. Record a failure as a failure. A short run does
not replace the ten-minute qualification. If a run reveals unsafe economic state,
stop the remaining load and report the reason; do not change code within a series.

## Slice 9e: retained cursor byte budget

Implementation and required lane verification are complete.
The inactive bank now limits both subjects and retained cursor bytes. Its default
budget is 8 MiB per lane. Cold work that exceeds the budget is rejected before a
database refill. Rejection keeps live grants, and expiry or retirement removes
the byte charge once. The batch call bound remains unchanged. Audit and pricing
bodies are not retained by a cursor. The budget includes Unicode character space
and fixed bounded object and grant metadata space; it is not an RSS measurement.

Focused checks: 66 passed. Native bank and byte-limit checks: 7 passed. The first
new native test incorrectly expected close to settle an unexpired grant. The
corrected test proves that close keeps the escrow, then expiry permits exact
settlement and unused-capacity release. No production recovery rule changed.
Object-graph tests then found that the first fixed object charge was too small for
the largest Unicode identifiers. The fixed cursor and window charges were
increased, without increasing the 8 MiB bank budget. All six object-graph cases
now pass. The 1,000-subject pruning fixture explicitly uses 16 MiB so that its
unchanged 256-scan assertion tests pruning, not byte rejection. Earlier full
runs were interrupted and are not passing gates. Final logs use the
`issue320-slice9e-*-complete.log` names.

Final verification:

- Focused memory, bank, profile, and small-owner checks: 66 passed.
- Native memory and bank checks: 7 passed.
- Full component and Helm lanes: 4,901 passed.
- Full application lane: 1,645 passed.
- Full PostgreSQL lane: 554 passed, with no required-service skips.
- Full Redis lane: 105 passed, with no required-service skips. One original
  server-clock expiry case failed in the first full run. It passed unchanged when
  isolated and in the second full lane. A read-only clock probe measured a
  107.5 ms host/server progression gap; the original test had a 10 ms host-wait
  margin. This is consistent with that timing failure. Keep the first log, and
  make the test observe the Redis expiry deadline in a separate test-only change.
- Collection: all 7,205 tests belong to one lane each: 4,664 hermetic, 1,645 app,
  554 PostgreSQL, 105 Redis, and 237 Helm.
- Ruff, format checks, and `git diff --check`: passed.

The final Redis result is in
`/private/tmp/issue320-slice9e-redis-confirmed.log`. The clock observation is in
`/private/tmp/issue320-slice9e-redis-clock-probe.log`. No migration, runtime
selection, pool, queue, fallback, or production deadline changes in this step.
The applied 115-migration chain is unchanged. Local dispatch, the remaining
integration slices, and the requested final gateway RPS tests are unfinished.

The next local-dispatch step must not copy a source lifetime error. In
`0ac46791`, `deltallm_accounting_allocate_local_permit_grant` extends the grant's
`expires_at` to the reservation lifetime. `_cursor_is_usable` then uses that same
field to allow new dispatch. The original allocator had capped it at the earlier
budget-window end or refill TTL. Recovery must retain funded money for late
receipts, but that must not extend the period for new provider work. Add separate
deadlines, a terminal-lifetime check before local issue, and native period-boundary
tests before activation. The clean runtime still uses assigned admission and
does not contain this source local-dispatch behavior.

### Redis server-clock test correction

The lease-expiry test now waits until Redis reports its declared deadline, within
a fixed two-second host deadline. Each observation waits at most 100 ms before
checking again. The one-second lease, reconnect, new-owner, and old-owner release
assertions are unchanged. No production script, timeout, or capacity changed.

Focused Redis checks: 5 passed. Full Redis lane: 105 passed, without required
skips. Ruff, formatting, and `git diff --check` passed. Logs are in
`/private/tmp/issue320-redis-server-clock-focused.log` and
`/private/tmp/issue320-redis-server-clock-full.log`. The original failure and
clock measurements remain available. The wait uses the declared server TTL;
it does not retry a failed acquisition or increase the lease.

## Slice 9f: local lease integration order

The inactive foundation is implemented in two new migrations. The first keeps
the short dispatch deadline separate from recovery. It uses the existing bulk
claim owner instead of the source's per-grant terminal loop, and prevents claims
from using a returned suffix. The second gives later issues recovery headroom
without extending stored deadlines on replay. The already-applied first migration
was not edited. All 17 new native cases pass, including assigned parity, period
boundaries, wrong identity, zero-cost capacity, duplicate settlement, and owner
loss. Bootstrap selection and the Python local lease lifecycle remain unfinished.

- [x] Complete full database and migration verification for the inactive lease
  foundation. Focused checks passed: 47 native and 35 verifier/lane cases.
- [x] Add inactive local-lease fields and constraints in an append-only migration.
  Keep assigned and durable-claim behavior unchanged by default.
- [x] Retain the allocator's short dispatch deadline. Store a separate bounded
  receipt-recovery deadline. Reject a new local issue if its terminal lifetime
  does not fit the funded lease.
- [x] Add typed bulk refill, unused-suffix return, and terminal persistence owners.
  Keep a fixed call bound across subjects. Never await one call per subject.
- [x] Bound issued receipts and retiring cursors by both entries and bytes.
  Preserve exact operation, request, owner, generation, grant, and ordinal identity
  after an uncertain transport acknowledgement. The inactive owners are verified;
  runtime selection remains incomplete.
- [ ] Supervise one bounded return worker through the existing lifecycle. Close
  must stop new issues, drain terminal work, and return only the proven unused
  suffix. Process loss must keep uncertain money as provisional, not release it.
- [ ] Prove short TTL and budget-period boundaries, partial funding, duplicate
  receipt replay, wrong owner/fence rejection, return races, owner loss, and mixed
  assigned/local settlement against PostgreSQL. Then add runtime selection through
  the small bootstrap owner and every governed configuration surface.

The terminal journal follows this foundation. Its accepted payload must remain
durable and immutable. The materializer must retain funding while accepted work
is pending, and preserve exactly-once settlement after worker loss. The source
append and materializer SQL must retain the clean branch's bounded key probes;
copying an old JSON join must not restore retained-history scans.

The local runtime must carry a complete typed financial issue proof through the
request handle and terminal owner. A terminal retry after receipt-store removal
must replay durable accepted facts, not create another admission or depend on a
mutable process cache. Remote workers must derive their own clock anchors; they
must not use an API process's monotonic value to permit new dispatch.

Plan cold funding before committing a local issue batch. Keep new, never-issued
funding in bounded staging that shares the cursor budget. Complete local issue
without an intervening await after funding checks pass. A later funding failure
must not turn an undelivered warm receipt into unknown provider work. The same
admission owner must protect staging from the return worker until commit or abort.
Do not await a gate release or another cleanup step after the local issue commit.
The result and every proof must be ready before that commit starts.

Before activation, collection must also respect serialized batch bytes. A valid
large terminal payload can exceed the 1 MiB batch limit when joined with other
valid entries. Split collection at the byte limit, not in a per-subject awaited
loop. Bound retained queue bytes and keep accepted facts immutable. Do not reduce
the request payload contract or let a provider success lose its terminal record.

### Slice 9f verification

All 7,223 collected tests passed. Counts are 4,665 hermetic, 237 Helm,
1,645 application, 571 PostgreSQL, and 105 Redis. No required-service test was
skipped. The full suites ran one at a time. All 117 migrations passed fresh install,
upgrade from `v0.1.42`, and shared-feature upgrade. The verifier removed its
disposable databases. Prisma generation, changed-file Ruff checks, format checks,
and `git diff --check` passed.

Logs are `/private/tmp/issue320-slice9f-postgres-full.log`,
`/private/tmp/issue320-slice9f-components-full.log`,
`/private/tmp/issue320-slice9f-app-full.log`,
`/private/tmp/issue320-slice9f-redis-full.log`,
`/private/tmp/issue320-slice9f-collection.log`, and
`/private/tmp/issue320-slice9f-migrations.log`.
These are foundation checks, not gateway qualification results. The local issuer,
bulk return lifecycle, runtime selection, terminal journal, and later plan slices
remain unfinished. Do not activate local dispatch or mark slice 9 complete.

## Slice 9g: inactive bulk local persistence

The typed bulk repository is implemented but remains inactive. Funding, suffix
return, and terminal persistence each use one database call for up to 256 items.
Client payloads are limited to 1 MiB; SQL accepts at most 2 MiB. All phases share
the existing statement, cancellation, result-set, and ambiguity-recovery owner.
Each phase has at most three attempts and one recovery query per attempt within
the caller deadline. There is no extra retry layer or per-subject awaited loop.

Funding returns the database observation time. The caller anchors the dispatch
and recovery horizons to its monotonic clock before each database call. This
subtracts call latency and avoids extending dispatch through host/database clock
differences. The local anchor is process-owned, not a value to trust from a remote
transport. A future remote adapter must derive its own anchor before its request.

The first append-only migration adds two bulk functions. It also checks funded terminal
lifetimes and immutable terminal timestamps after the claim owner holds operation
locks. Exact suffix returns can replay after grant closure. A bad entry rolls back
the whole bulk effect. No setting, default, bootstrap selection, or pool changed.

Final review found SQL null comparisons in return identity checks. Three native
cases confirmed that a missing fence or generation could mark capacity unused.
A second append-only migration now uses null-safe comparisons and explicit
generation validation. It also rejects a missing suffix ordinal. All four
rejection cases preserve active grant metadata and escrow. The earlier applied
migrations remain unchanged. This path is not selected by the gateway.

- [x] Complete the full affected application and PostgreSQL checks and record the
  final results before marking the bulk persistence step complete.
- [x] Prove native partial funding across two replicas, lost acknowledgements in
  all three phases, exact closed replay, wrong return identity, and timestamp races.
- [x] Verify all 119 migrations on fresh, released-version, and shared-feature paths.
- [x] Complete representative nested-plan checks for windows, grants, operations,
  events, reservations, and grant-window records, including prepared warm calls.

The local issuer, entry and byte limits for issued state, supervised return worker,
runtime selection, terminal journal, and later slices are still required. This step
does not remove request-path claims until that runtime work is complete. The final
50/100/200/500 RPS kind qualification remains unchecked.

### Slice 9g verification

The expanded focused check passed 100 cases. Six native plan cases seed 50,000
retained budget windows and 10,000 each of grants, operations, events,
reservations, and grant-window records. They exercise six prepared calls in each
phase, with explicit and implicit window selection. The checks require indexed
financial-history access with bounded rows. Final redacted plans are in
`/private/tmp/issue320-slice9g-plans-final.log`. Migration checks passed all 118
migrations in `/private/tmp/issue320-slice9g-migrations.log`.

The first full PostgreSQL run passed 585 cases and failed the unchanged realtime
test `test_older_accepted_timestamps_are_not_rebuilt`: its immediate worker claim
returned no record. The same test then passed alone, all 19 cases in its family
passed, and 20 further unchanged repetitions passed with an aggregate-only,
read-only empty-claim probe. The cause was not established. No production code,
assertion, deadline, or retry policy was changed to pass this test.

The second, uninstrumented full PostgreSQL run passed all 586 cases. The full
component and Helm run passed all 4,969 cases. All 1,645 application and 105 Redis
cases passed. Those gates covered the typed bulk owner and migration 118. After
the null-identity guard, all 36 local native and nested-plan cases passed. Migration
119 passed all three upgrade paths in
`/private/tmp/issue320-slice9g-null-guard-migrations.log`. The guard changes only
invalid direct SQL inputs; no Python production code or valid caller changed.
The next full PostgreSQL run passed 589 cases and failed
`test_real_native_statement_deadline_and_connection_recovery`. The caller deadline
fired before the native statement-timeout response arrived. The request still
failed closed, but the test required a native error cause. This test and its owner
were unchanged by this slice. All nine cases in its family then passed alone.
The final full lane passed all 590 cases without instrumentation, changed limits,
or changed assertions. Keep the failure in
`/private/tmp/issue320-slice9g-postgres-null-guard-final.log` and the isolated result
in `/private/tmp/issue320-slice9g-allocation-isolated.log`.
Keep the original failure in
`/private/tmp/issue320-slice9g-postgres-full.log`; isolated and diagnostic evidence
is in `/private/tmp/issue320-slice9g-realtime-isolated.log`,
`/private/tmp/issue320-slice9g-realtime-family.log`, and
`/private/tmp/issue320-slice9g-realtime-diagnostic.log`.

Final collection covers 7,309 tests: 4,732 hermetic, 237 Helm, 1,645 application,
590 PostgreSQL, and 105 Redis. Each test belongs to exactly one lane. The full
component, application, and Redis gates passed before migration 119; the final
PostgreSQL gate checks its invalid-input guards. No required service was skipped.
All 12 changed Python files passed Ruff and format checks. `git diff --check`
passed. Final native and collection logs are
`/private/tmp/issue320-slice9g-postgres-null-guard-confirmed.log` and
`/private/tmp/issue320-slice9g-collection-null-guard-final.log`. The other final
gates are in `/private/tmp/issue320-slice9g-components-final.log`,
`/private/tmp/issue320-slice9g-app-final.log`, and
`/private/tmp/issue320-slice9g-redis-final.log`.

This inactive persistence step is complete. Slice 9 as a whole is not complete,
and these tests are not gateway RPS qualification results.

### Next: local issue and return lifecycle

The local issuer must retain an immutable copy of each reservation. Frozen model
fields do not freeze nested pricing and audit dictionaries. Use serialized bytes
for retained request facts and account for both those bytes and scalar proof
overhead. Prove that later dictionary changes cannot alter an issued receipt.
Do not evict an unacknowledged receipt to admit new work.

An expired short dispatch horizon does not prove that its funded escrow is gone.
Recovery must distinguish a live dispatch proof from a return-only funding proof.
Neither a recovered old horizon nor a remote process's monotonic anchor can
authorize new provider work. Prove this boundary before runtime selection.
The return worker must supervise bounded scans and bulk calls, stop issue before
drain, and retain uncertainty until the database terminal owner confirms it.

## Slice 9h: immutable local receipt retention

The inactive receipt store now keeps canonical reservation bytes and a copied,
scalar funding proof. New request work must fit both its entry limit and its byte
limit. The store has no eviction, expiry deletion, database client, task, or pool.
Only an exact operation and generation terminal acknowledgement can remove a
matching issued proof. Duplicate removal has no second effect. Recovery visits
at most 256 entries and rotates that slice without removing its proofs.

The retained-byte charge includes the serialized request plus a conservative
fixed allowance for the grant, wrapper, key, and map entry. Tests compare the
charge with the complete typed object graph for small, large, nested, and maximum
Unicode cases. A 300-entry check verifies the aggregate charge and exact removal.
This charge is a retained-state limit, not a process RSS limit.

Frozen model fields do not freeze nested pricing or audit dictionaries. The store
validates those dictionaries before serialization. Its first draft check found
that a serializer could turn NaN into null before validation. The final store
rejects NaN and oversized changes before it retains a proof. Caller mutations and
changes to a restored copy cannot change the retained request facts.

Funding recovery can now return a proof after its short dispatch horizon expires,
while its recovery horizon is still live. That proof is for return, not dispatch.
The original dispatch time remains unchanged and its monotonic deadline is in
the past. One native lost-response test waits on the database clock for this
boundary, recovers the proof, returns all four never-issued ordinals, and checks
zero economic drift. No production deadline or allocator SQL changed.

- [x] Complete focused, full application, full native PostgreSQL, component,
  Redis, lane, and style checks before committing this inactive step.
- [x] Record immutable-receipt and return-only recovery test results.
- [x] Add the inactive local issuer in slice 9l.
- [x] Complete entry/byte-bounded retiring cursor verification in slice 9i.
- [x] Add the supervised bulk return foundation in slice 9l.
- [ ] Add the shared terminal path and runtime selection after that.

The main gateway still selects assigned admission. Slice 9 and the final kind
50/100/200/500 RPS qualification remain incomplete. The applied 119-migration
chain is unchanged.

### Slice 9h verification

Focused checks passed all 135 cases. Full lanes passed all 7,346 collected cases:
4,767 hermetic, 237 Helm, 1,645 application, 592 PostgreSQL, and 105 Redis. Each
test belongs to one lane. The full suites ran serially and had no required-service
skips or failures. Ruff, format checks, the small typed-owner ratchet, and
`git diff --check` passed. No schema, migration, default, or runtime selection changed.

The first focused repository run misplaced an existing no-operation-insert
assertion in the new successful-settlement case. The assertion was restored to
its original rejection case without a change. The final focused run and all full
lanes passed. The earlier failure remains in
`/private/tmp/issue320-slice9h-focused-expanded.log`.

Final logs are `/private/tmp/issue320-slice9h-focused-final.log`,
`/private/tmp/issue320-slice9h-components-full.log`,
`/private/tmp/issue320-slice9h-app-full.log`,
`/private/tmp/issue320-slice9h-postgres-full.log`,
`/private/tmp/issue320-slice9h-redis-full.log`, and
`/private/tmp/issue320-slice9h-lanes-final.log`.

This inactive retention step is complete. The local issuer, retiring-cursor owner,
supervised returns, runtime selection, terminal journal, later integration slices,
and final gateway RPS qualification remain required.

## Slice 9i: bounded return-only cursors

The inactive cursor owner now shares one entry and byte limit across active
grants and grants waiting for return. Expiry moves a cursor to return-only state.
It does not reduce the byte charge or claim that money was refunded. A matching
suffix acknowledgement removes the charge exactly once. Wrong fences, ordinals,
counts, and generations cannot overwrite or remove retained capacity.

Cursor ordinals are immutable. A borrowed old cursor cannot change the suffix
after retirement. Fully issued grants need no unused-suffix return; their issued
receipts remain with the separate receipt owner. Expiry, close selection, and
return selection each inspect at most 256 entries. One scan never walks the whole
configured bank. Memory tests cover zero, one, and five windows and maximum
Unicode identifiers. No client, worker, task, pool, or runtime default was added.

Review found a second exact-money boundary. Return recovery used the ambient
decimal precision for allowance multiplied by unused count. Four regressions
failed before correction: exact amounts were rejected and rounded amounts were
accepted. The comparison now uses a fixed 80-digit context. The failure remains
in `/private/tmp/issue320-slice9i-return-money-before.log`.

A full-precision native case then found that the generic query client could
convert PostgreSQL NUMERIC into a float. Funding and recovery now project monetary
scalars as text before transport. The same projection serves local and durable
pre-issued funding. No extra call or SQL-history scan was added. The native
failure remains in `/private/tmp/issue320-slice9i-focused-final.log`. The six
retained-history plan cases pass after the projection change.

Focused lease, cursor, receipt, permit, and ratchet checks passed 226 cases before
the last pre-issued full-precision case was added. All 32 final native money,
plan, and focused ratchet cases passed. Full verification passed all 7,381 tests:
4,798 hermetic, 237 Helm, 1,645 application, 596 PostgreSQL, and 105 Redis.
No required-service test was skipped. All nine changed Python files passed Ruff
and format checks. Collection assigned every test to one lane. `git diff --check`
passed.

The first full PostgreSQL run passed 595 cases and failed one unchanged realtime
cleanup case: `test_transient_release_failure_closes_socket_and_retries_cleanup[True]`.
The socket closed and routing release ran twice, but the runtime stayed unready.
The log shows cleanup cancellation, a transaction rollback failure, and a billing
recovery warning. It does not establish why cleanup exceeded its allowance.
The failure remains in `/private/tmp/issue320-slice9i-postgres-full.log`.
All 12 unchanged tests in the realtime failure family then passed in
`/private/tmp/issue320-slice9i-realtime-failures-diagnostic.log`. No assertion,
deadline, or production realtime rule was changed. The complete unchanged
PostgreSQL lane then passed in `/private/tmp/issue320-slice9i-postgres-confirmed.log`.
The earlier failure remains an intermittent signal, not a proven product fix.
The other full logs are `/private/tmp/issue320-slice9i-components-full.log`,
`/private/tmp/issue320-slice9i-app-full.log`,
`/private/tmp/issue320-slice9i-redis-full.log`, and
`/private/tmp/issue320-slice9i-lanes-final.log`.

- [x] Complete all five full lanes, lane collection, Ruff, formatting, and
  `git diff --check` before committing this inactive step.
- [x] Record exact native unused-suffix return, lost-ACK recovery, and full-money
  results with the complete lane counts.
- [ ] Use both bounded state owners in the local issuer. Keep warm issue at zero
  SQL calls and cold issue at no more than two bulk funding rounds.
- [ ] Add the supervised return worker and the shared typed terminal proof path.

The applied 119-migration chain is unchanged. The gateway still uses assigned
admission. Slice 9, later slices, current-main feature adapters, and all four final
kind RPS qualifications remain incomplete.

## Slice 9j: immutable byte-bounded financial queues

The assigned accounting runtime now freezes each validated financial record as
canonical JSON bytes before enqueue. One snapshot owner also serves local receipt
retention. It validates the raw nested graph before JSON conversion, so invalid
non-finite values cannot become JSON null. A caller cannot change queued pricing,
audit, or spend facts through a nested dictionary after submission.

The shared microbatch owner now collects at both an item limit and a 1 MiB JSON
list limit. It includes list delimiters and commas. It leaves the next item in
the queue when a batch is full. It does not split in an awaited subject loop.
Each accounting queue has a separate 8 MiB retained-byte budget by default,
including selected payloads and a fixed metadata charge. The startup settings,
environment example, YAML example, Helm values and schema, and generated 402-field
reference are synchronized. Accounting remains disabled by default. Existing
batch sizes, acknowledgement budgets, pools, and per-record size limits are unchanged.

Cancellation before collection removes one queued entry and its byte charge in
constant time. Cancellation after selection does not remove the charge or cancel
the persistence owner. Owner cancellation, observer failure, a wrong result count,
and shutdown fail affected waiters and release each charge once. A cancelled
handler now stops its worker instead of being swallowed by the per-batch exception
handler. Worker health keeps the failed task visible.

Before implementation, all 17 byte-collection regressions failed because the
queue had no byte owner. After queue support was added, all 10 financial snapshot
regressions still failed: large batches exceeded the transport limit and mutable
or invalid nested records could enter the queue. The original failures remain in
`/private/tmp/issue320-slice9j-byte-queue-before.log` and
`/private/tmp/issue320-slice9j-immutable-queue-before.log`.

Focused financial, cache, bootstrap, settings, and Helm checks passed 177 cases.
All 50 final byte, observer, snapshot, and size-ratchet checks passed. All four
native large-payload cases passed: direct and assigned admission, with and without
a lost terminal acknowledgement. Eight 264 KiB-class terminal records use three
bounded terminal calls. Lost acknowledgement recovery adds one call, not a second
charge. Each case keeps eight reserved and eight finalized events, exact spend
and audit payloads, and zero reserved/provisional balance after settlement.
The default assigned grant retains its 32 units before expiry; the test advances
only its private native expiry to prove exact release. No runtime TTL changed.

The first new native fixtures read audit from the wrong column, counted reserved
events as terminal events, and expected a default partially unused grant to close
before expiry. Those fixture errors were corrected against the schema. The first
observer test edit also misplaced a fake repository method; it was restored.
No existing assertion or deadline was weakened. The failed logs remain available.
The final focused logs are `/private/tmp/issue320-slice9j-settings-queue-focused.log`,
`/private/tmp/issue320-slice9j-observer-byte-confirmed.log`, and
`/private/tmp/issue320-slice9j-native-queue-final.log`.

- [x] Run all five full lanes, complete collection, changed-file Ruff, formatting,
  generated-reference verification, and `git diff --check` before commit.
- [ ] Extend serialized-byte measurement to the complete local financial proof
  envelope when the local terminal path is integrated. Do not measure only its
  finalization body.
- [ ] Complete bounded cold-funding staging, atomic local issue, supervised
  return, and typed proof/replay integration before local runtime selection.

No migration was added or changed. Slice 9 and the final kind qualification remain
incomplete. These native cases are correctness checks, not gateway RPS results.

The complete suite passed 7,429 tests with no service skips: 4,836 hermetic,
1,645 application, 600 PostgreSQL, 105 Redis, and 243 Helm tests. Every collected
test belongs to one lane. Changed-file Ruff, formatting, generated-reference
verification, and `git diff --check` passed. The full logs are
`/private/tmp/issue320-slice9j-components-full.log`,
`/private/tmp/issue320-slice9j-app-full.log`,
`/private/tmp/issue320-slice9j-postgres-full.log`,
`/private/tmp/issue320-slice9j-redis-full.log`, and
`/private/tmp/issue320-slice9j-lanes-final.log`.

## Slice 9k: bounded staging and atomic local issue

The cursor owner now retains active, staged, and return-only proofs under one
entry and byte budget. Staging is not dispatchable or visible to the return
worker. Abort moves a bounded slice to return-only state at ordinal zero, without
changing warm cursors or claiming a refund. Raw grant validation rejects invalid
clocks before JSON conversion.

One small local issue owner freezes all reservation facts, builds dispatch
results, and validates both stores before mutation. Its batch is bounded at 256
receipts and 1 MiB of complete serialized issue proofs. It rejects wrong fences,
subjects, ordinal gaps, duplicate operations, and skipped warm suffixes. It checks
receipt capacity and repeats deadline checks after preparation. The cursor and
receipt commits contain no await. Each complete issued proof remains retained
until an exact terminal acknowledgement.

The 15 staging regressions failed before staging existed. The initial atomic
owner check failed at collection because that owner did not exist. Logs are
`/private/tmp/issue320-slice9k-staging-before.log` and
`/private/tmp/issue320-slice9k-issue-before.log`. Initial focused checks passed
106 cases. All eight native issue and cursor cases passed in
`/private/tmp/issue320-slice9k-native-partial-grants.log`.

The new native fixture initially assumed three four-unit grants under a ten-unit
budget. It now explicitly proves grants of four, four, and two, with all ten
units reserved. The two cold grants follow ordered fences, not caller order.
The fixture selects the larger grant first to exercise a full prefix and a partial
tail. The original failure logs remain in
`/private/tmp/issue320-slice9k-native-issue.log` and
`/private/tmp/issue320-slice9k-native-issue-confirmed.log`. The new negative-clock
fixture also constructed a receipt before shifting its operation expiry; it was
corrected to shift the complete proof before validation. No existing assertion,
database budget, deadline, migration, or runtime default was changed.

- [x] Share entry and byte limits across active, staged, and return-only funding.
- [x] Keep staged funding hidden from dispatch and return scans until commit or abort.
- [x] Prepare immutable proofs and results, then commit both stores without an await.
- [x] Prove native abort, partial funding, exact settlement, and lost acknowledgement.
- [x] Reproduce retained-history scans in the complete allocator and window locks.
- [x] Add guarded, append-only primary-key lookup corrections. Preserve all
  economic calculations, replay decisions, and global lock order.
- [x] Verify all 123 migrations on fresh, released-version, and shared-feature paths.
- [x] Complete final clock, graph, full-lane, collection, style, and diff checks.
- [ ] Integrate bounded funding coordination with a synchronous gate release.
- [ ] Complete terminal proof transport, replay, and supervised return lifecycle.

Bootstrap does not select these owners. Slice 9 and final kind qualification
remain incomplete. These checks are not HTTP RPS results.

### Slice 9k allocator failure and correction

The first full component, application, and Redis gates passed 5,119, 1,645, and
105 cases. The strict PostgreSQL gate passed 603 cases and failed one unchanged
retained-history plan case. One node read 50,002 window rows instead of at most
45. The failure is retained in
`/private/tmp/issue320-slice9k-postgres-sdk-full.log`. The earlier PostgreSQL run
had four SDK skips and is not a complete gate.

The new probe puts the requested window after the seeded history. It covers
automatic, generic, custom, and alternate join plans. The complete allocator
still has the original bounds for windows, grants, and operations. Separate
checks use each funding owner's actual ordered window-lock SQL. No production
planner setting or test row bound was relaxed.

The probes exposed four flattenable lookup shapes: window membership, explicit
window references, missing-operation anti-joins, and funded-grant promotion.
New migrations 120 through 123 replace only those exact fragments. They reject an
unexpected function body before replacement. Money calculations, validation,
counters, and lock order do not change. Applied migrations 116 through 119
remain unchanged.

The original strengthened whole-function failure is retained in
`/private/tmp/issue320-slice9k-keyset-before.log`. All six window-entrypoint cases
failed before the reference correction in
`/private/tmp/issue320-slice9k-all-window-entrypoints-before.log`. The complete
alternate-plan cases then exposed the operation scan in
`/private/tmp/issue320-slice9k-operation-probe-before.log` and grant promotion in
`/private/tmp/issue320-slice9k-all-probes-native-final.log`. These are diagnostic
tests, not gateway load results. Final verification below includes these cases.

The standalone lock probe first reused a no-bitmap assertion from the ordered
overlap helper. Six cases then failed on bounded indexed bitmap probes, not
history scans. Their indexed row counts were at most one, with at most five scope
loops. The lock probe now shares the complete allocator's original no-sequential-
scan and 45-row bounds. It also bounds bitmap index rows so a large bitmap cannot
hide behind a small heap result. The original overlap helper's no-bitmap assertion
is unchanged. The diagnostic failures remain in
`/private/tmp/issue320-slice9k-retained-plans-stdout.log`. The failed tee-capture
attempt in `/private/tmp/issue320-slice9k-retained-plans-final.log` could not start
the Prisma subprocess. Final plan evidence uses separate output streams.

Final focused verification passed 67 funding, plan, and lease cases in
`/private/tmp/issue320-slice9k-retained-plans-verified.log`. It records all 24
complete allocator cases: three owners, two window modes, and four planner modes.
Each executed window, grant, and operation node read at most one row, against
50,000 retained windows and 10,000 closed grants and operations. All six actual
window-lock queries also pass. Five component cases prove that sequential scans,
large results, filtered history, and large bitmap indexes still fail the bounds.
The final focused component checks passed 93 cases in
`/private/tmp/issue320-slice9k-final-focused-hermetic.log`.

All 123 migrations passed fresh install, upgrade from `v0.1.42`, and shared-feature
upgrade in `/private/tmp/issue320-slice9k-migrations-final.log`.
The complete suite passed all 7,509 tests with no service skips: 4,886 hermetic,
1,645 application, 630 PostgreSQL, 105 Redis, and 243 Helm tests. Every collected
test belongs to one lane. All 12 changed Python files passed Ruff and formatting.
The 402-field generated settings reference and `git diff --check` passed.
Full logs are `/private/tmp/issue320-slice9k-verified-components.log`,
`/private/tmp/issue320-slice9k-verified-app.log`,
`/private/tmp/issue320-slice9k-verified-postgres.log`,
`/private/tmp/issue320-slice9k-verified-redis.log`, and
`/private/tmp/issue320-slice9k-verified-lanes.log`.

This inactive staging and issue step is complete. Funding coordination, terminal
proof transport, replay, supervised returns, later integration slices, and the
final 50/100/200/500 RPS kind qualification remain required.

## Slice 9l: bounded admission, complete replies, and suffix returns

The inactive admission owner has one active caller and at most one waiter. Its
release is synchronous, so cancellation cannot enter between local issue and
release. Tests cover queue overflow, timeout, cancellation before and after wakeup,
waiter order, and release without ownership.

The atomic issue owner now prepares dispatch, replay, and denial results together
in the caller's original order. Only dispatch results retain financial proofs.
The 256-result bound covers both issued and denied entries. The 1 MiB bound covers
the complete reply, including scalar permits and full proofs. Result validation,
serialization, and allocation finish before either state owner is changed.

- [x] Add the bounded admission owner with synchronous release.
- [x] Prepare complete mixed results before atomic issue. Reject duplicate IDs,
  stale generations, invalid decisions, incorrect order, and oversized replies.
- [x] Coordinate cold funding in no more than two bulk rounds. Keep warm issue
  at zero SQL calls, and leave warm prefixes unchanged on a cold-funding failure.
- [x] Preflight retained proof and cursor capacity before funding. Retain known
  staged grants for exact return after failure; never invent a refund after an
  unknown acknowledgement.
- [x] Add one supervised return owner with bounded scans and bulk writes. Share
  the admission owner, validate the whole reply before removal, stop admission
  before drain, and keep proof charges on timeout or invalid acknowledgement.
- [ ] Complete the shared terminal proof/replay path before runtime selection.
- [x] Complete full required gates before this next integration step is committed.

Initial mixed-result checks failed ten cases in
`/private/tmp/issue320-slice9l-mixed-results-before.log`. The new size fixture first
used a model-copy method on a dataclass. It now uses the dataclass replacement
method. No existing assertion or monetary contract was changed.
Final focused admission, result, staging, issue, and ratchet checks passed 83 cases
in `/private/tmp/issue320-slice9l-complete-replies-focused.log`. All 19 native lease
and issue checks passed in `/private/tmp/issue320-slice9l-complete-replies-native.log`.
The new funding owner passed 106 focused checks. Three native cases prove zero
SQL calls for warm issue, two bulk calls for partial cold issue, exact balances,
lost funding or terminal acknowledgement recovery, and terminal replay after
grant closure. The log is `/private/tmp/issue320-slice9l-issuer-native.log`.

The return worker shares the issuer's cursor store and admission owner. It skips
a busy owner instead of adding another waiter. Each scan and bulk return covers
at most 256 grants. It validates all counts and proofs before the first removal.
Known unused suffixes keep their entry and byte charge until exact acknowledgement.
Issued proofs keep their separate terminal owner after return-worker close.
Dependency failure makes this worker unready. Failed startup stops admission and
cancels its task. Close uses the caller's deadline, and a failed drain keeps proofs.

Initial return checks had two startup fixture failures. The worker correctly
rejected initial dependency failure; the new fixtures now inject it after a ready
startup. Startup cleanup was added without changing that readiness rule. The log
is `/private/tmp/issue320-slice9l-returns-focused.log`. All 154 focused local-owner
checks passed in `/private/tmp/issue320-slice9l-all-focused.log`.

Two initial native return checks expected partition capacity to drop on suffix
return. The applied return function records unused ordinals but leaves partition
capacity charged until whole-grant settlement. The new tests now check the exact
three-ordinal return and the retained four-slot charge, then require zero charge
and exact cost after settlement. No applied migration or existing test was changed.
The initial log is `/private/tmp/issue320-slice9l-returns-native.log`.

All 24 native local-owner cases then passed in
`/private/tmp/issue320-slice9l-returns-native-fixed.log`. Those cases include lost
return acknowledgement, exact unused-ordinal facts, retained issued-proof charges,
terminal acceptance after return-worker close, and replay after grant settlement.

The first full gates passed 5,198 component and Helm cases and all 1,645 application
cases. PostgreSQL had 634 passes and one overlap-plan assertion failure. The full
failure log is `/private/tmp/issue320-slice9l-full-postgres.log`. Its assertion text
did not retain the complete plan. Do not infer the exact failing branch from that
truncated text. A single isolated check, four planner profiles, and seven planner
cost profiles then passed with the original assertions and unchanged production
settings. Their complete safe plans show a zero-work explicit-reference branch
and an indexed nine-window overlap result.

The window-plan guard inspected unused branches as if they had run. It now requires
zero rows, filtering, and buffer work for each unused window branch. Executed
branches keep the same row and filter bounds and the ban on sequential and bitmap
heap scans. They must also have no sort. All 26 focused plan-guard checks passed,
including tests that reject hidden work and 50,000-row scans. The log is
`/private/tmp/issue320-slice9l-actual-plan-guard-focused.log`. No SQL, deadline,
capacity, or executed-row limit changed. Final full gates must still pass with
the complete overlap plan retained.

The next full PostgreSQL run passed all plan checks but timed out waiting for a
process-death fixture's child commit marker. Its failure log is
`/private/tmp/issue320-slice9l-final-postgres.log`. All seven isolated spend recovery
cases passed with the same ten-second startup and 250 ms statement deadlines.
The diagnostic log is `/private/tmp/issue320-slice9l-spend-child-diagnostic.log`.
No runtime, test deadline, or process-death assertion was changed.

The final full PostgreSQL run passed all 635 cases without skips. It retained
complete safe overlap plans and bounded child diagnostics in
`/private/tmp/issue320-slice9l-verified-postgres.log`. One child spent 8.235 seconds
in imports, 0.349 seconds connecting, and 0.023 seconds committing its intent.
The host had about 17 GB of swap in use. This is evidence of slow test startup,
not proof of the earlier timeout's exact cause. No unrelated host process was
stopped. No Prisma engine remained after the isolated checks.

Final component and Helm checks passed all 5,209 cases in
`/private/tmp/issue320-slice9l-final-components.log`. The unchanged application
code passed all 1,645 cases in `/private/tmp/issue320-slice9l-full-app.log`.
Final collection covers 7,594 cases, each in one lane, in
`/private/tmp/issue320-slice9l-final-lanes.log`. Redis passed all 105 cases without
skips in `/private/tmp/issue320-slice9l-verified-redis.log`. All 123 unchanged
migrations passed fresh install, upgrade from `v0.1.42`, and shared-feature upgrade
in `/private/tmp/issue320-slice9l-verified-migrations.log`. The verifier removed
only its disposable databases. All 15 changed Python files passed Ruff and format
checks. The generated reference remains current at 402 fields. `git diff --check`
passed. No schema, configuration default, or runtime selection changed in 9l.

Runtime selection, the rest of slice 9, later integration slices, and the four final
kind RPS runs remain incomplete.

## Slice 9m: shared local proof and terminal owner

- [x] Add typed local permit and request-handle contracts. Keep the existing
  assigned contracts unchanged. Check the complete reservation, owner, generation,
  partition, grant fence, and ordinal before provider work.
- [x] Freeze local terminal input before its first await. Reject duplicate
  operations, stale generations, NaN, and entry or byte excess before persistence.
- [x] Add a typed bulk terminal owner with the caller's single deadline. Check
  every acknowledgement before removing any issued proof or retained-byte charge.
- [x] Prove cancellation, blocked transport, wrong acknowledgement, and replay
  after local receipt removal. Do not create another admission on terminal retry.
- [x] Add the shared service path and preserve local proofs through provider retry
  and charged cache admission. Missing proof must fail closed, not use assigned
  or legacy billing.
- [x] Add compact authenticated transport with process-local clock anchors. Keep
  financial proof identity separate from a process's monotonic clock value.
- [x] Prove the shared path against PostgreSQL, then run all required gates before
  runtime selection. Keep later slices and the final four-rate kind series pending.

The repository now contains the typed local handles, bulk terminal owner, shared
byte-queue service, compact wire contracts, and bounded signed HTTP transport.
Full proof checks also reject invalid scalar model copies. Each constructed handle
gets its own request dictionaries. Terminal acknowledgement prepares every removal,
then checks the caller's deadline before it changes retained entries or bytes.

The shared service cannot accept an assigned handle. The assigned service cannot
accept a local handle. Provider retries keep one proof. Charged cache requests use
that same terminal path. One HTTP regression passed the provider response followed
by a charged cache response with no legacy write. Compact replies omit the repeated
reservation and process-local monotonic clock. They retain only the remaining warm
dispatch lifetime. Terminal proof comparison excludes only clock observation fields,
not a durable grant field, ordinal, or reservation fact.

Focused local proof checks passed 132 cases in
`/private/tmp/issue320-slice9m-final-focused.log`. The native proof, return, issue,
and lease group passed 27 cases in
`/private/tmp/issue320-slice9m-native-proof-focused.log`. Six of those checks cover
the shared terminal owner and queued service with normal or lost funding and
terminal acknowledgements. They use different process clock anchors and replay
after local proof removal and grant closure. The focused HTTP provider/cache group
passed five cases in `/private/tmp/issue320-slice9m-cache-app-focused.log`.

Initial new fixtures had three errors: a call to a missing test method, a direct
repository's 2-second statement budget inside the service's unchanged 1-second
acknowledgement deadline, and a shifted grant expiry without its reservation expiry.
Two transport checks also used the wrong fixed error label. Correcting the fixtures
and using the existing error enum fixed these checks. No production deadline,
capacity limit, or SQL assertion changed. The first native failure remains in
`/private/tmp/issue320-slice9m-native-shared.log`; the first compact transport failure
remains in `/private/tmp/issue320-slice9m-transport-focused.log`.

Full gates passed all 7,701 tests: 5,069 hermetic, 243 Helm, 1,646 application,
638 PostgreSQL, and 105 Redis cases. No required-service case was skipped. Each
test belongs to one dependency lane. Logs are
`/private/tmp/issue320-slice9m-full-components.log`,
`/private/tmp/issue320-slice9m-full-app.log`,
`/private/tmp/issue320-slice9m-full-postgres.log`,
`/private/tmp/issue320-slice9m-full-redis.log`, and
`/private/tmp/issue320-slice9m-final-lanes.log`.

All 123 unchanged migrations passed fresh install, upgrade from `v0.1.42`, and
shared-feature upgrade in `/private/tmp/issue320-slice9m-full-migrations.log`.
The verifier removed its disposable databases. All 20 changed Python files passed
Ruff and format checks. The generated configuration reference is current at
402 fields, and `git diff --check` passed. No schema or default changed in 9m.

This is an inactive integration checkpoint, not gateway RPS qualification. Worker
endpoint construction, remote runtime selection, the terminal journal, remaining
integration slices, and all four final kind rates remain unfinished.

### Slice 9m transport destination follow-up

Review after the checkpoint found a missing transport safeguard. The inactive
signed HTTP primitive did not yet use the shared outbound destination policy.
It now checks scheme, port, DNS results, and private-network permission before
connecting. It pins the checked address and retains the original Host and TLS
server name. Metadata targets remain blocked. DNS and body read share the caller's
deadline. No new URL policy or retry owner is added.

Focused accounting, shared webhook destination, and small-owner checks passed
67 cases in `/private/tmp/issue320-slice9m-egress-focused.log` before adding the
explicit plain-HTTP rejection case. Full component and Helm verification then
passed all 5,316 cases in
`/private/tmp/issue320-slice9m-egress-components.log`. This follow-up changes only
the unselected transport; the application, database,
schema, settings, and reporting paths from the checkpoint remain unchanged.

## Next slice 10: inactive durable terminal journal

- [x] Add the journal and pending-payload tables in a new migration. Add matching
  Prisma models. Keep compact accepted identities separate from large documents.
- [x] Use a typed journal acknowledgement. Do not present its journal sequence as
  a canonical accounting event that later processing has not yet created.
- [x] Append one bounded terminal batch with complete immutable payload hashes,
  exact money, generation, owner, grant, fence, and ordinal checks. Use indexed key
  probes. Reject missing fields and conflicting operations or ordinals atomically.
- [x] Keep accepted journal entries charged until canonical processing succeeds.
  Prevent an unused-suffix return or grant close from releasing accepted work.
  Preserve exact retry after grant closure and local proof removal.
- [x] Add bounded, fenced claim and canonical-processing calls. Retain one durable
  outcome through lease loss and retry. A failed record must remain visible and
  charged; it cannot be removed to make a queue look empty.
- [x] Prove normal and lost acknowledgements, changed-payload rejection, duplicate
  concurrent workers, return races, expiry, cancellation, and exact reconciliation
  with PostgreSQL. Check actual nested plans with substantial retained history.
- [ ] Run the required test and migration gates before selecting the journal.
  Do not copy the source's full-history expiry update. Its bounded replacement,
  worker roles, reporting lanes, current-main adapters, and final kind series
  remain tracked in the later integration slices above.

Source review covers the original journal migration and its payload-isolation
update. Their large repository and bootstrap methods, absent Prisma journal
models, nullable-field checks, and history-wide joins must not enter the clean
replay without the typed, bounded boundaries stated here. Slice 10a adds the
inactive acceptance foundation below. The worker and runtime selection remain
unfinished.

### Slice 10a: bounded journal acceptance

- [x] Add immutable compact identities, full document hashes, and a distinct
  journal receipt. Bound the complete encoded batch, not just its metadata.
- [x] Add primary-database bulk acceptance and exact lost-acknowledgement recovery.
  Update each partition counter once per batch and insert documents in bulk.
- [x] Add durable entry and payload-byte limits. Queue-full rejects new work and
  keeps prior documents. Replay can succeed while capacity is full.
- [x] Keep pending work funded through expiry. Block return of an accepted ordinal
  and grant closure with unresolved journal work. Bound the expiry transition.
- [x] Check native acceptance, lost replies, changed facts, concurrent append,
  missing fields, and eight retained-history plans. Worker reconciliation remains
  a later gate; these checks do not prove canonical processing.
- [x] Complete overload, return-race, expiry-limit, full lane, and migration gates.
- [x] Add fenced worker claims, canonical processing, and complete replay after
  closure. Then connect journal acknowledgements to the local terminal owner.

The initial schema check found PostgreSQL's shortened automatic constraint name.
The Prisma map now matches the actual name. One new snapshot test used a sync
fixture that needs an event loop, then assumed an allowance of 1 instead of the
fixture's 1.25. Both fixtures are corrected without changing production limits.
The initial native acceptance and plan runs passed 16 and eight cases.

Bulk commit review then found an ambiguous payload result column. A direct owned
database probe confirmed the SQL error. A new migration qualifies that result;
applied migrations stay unchanged. All 55 focused journal and small-owner checks
passed in `/private/tmp/issue320-slice10a-result-focused.log`. The failed bulk run
remains in `/private/tmp/issue320-slice10a-bulk-focused.log`. Extra overload checks
passed 27 cases before the final expiry-limit test was added.

This is inactive foundation work. Slice 10, worker roles, economic settlement,
reporting lanes, current-main adapters, and the four final kind rates are not
complete. Do not treat these SQL checks as RPS qualification.

Full 10a verification passed all 7,746 tests: 5,086 hermetic, 243 Helm,
1,646 application, 666 PostgreSQL, and 105 Redis cases. No required-service case
was skipped. Collection confirms one lane per test. All 126 migrations passed
fresh install, upgrade from `v0.1.42`, and shared-feature upgrade. Prisma generation,
changed-file Ruff and format checks, and `git diff --check` passed. The generated
configuration reference stays current at 402 fields. The verifier removed only
its owned disposable databases.

Logs are `/private/tmp/issue320-slice10a-full-components.log`,
`/private/tmp/issue320-slice10a-full-app.log`,
`/private/tmp/issue320-slice10a-full-postgres.log`,
`/private/tmp/issue320-slice10a-full-redis.log`,
`/private/tmp/issue320-slice10a-full-migrations.log`, and
`/private/tmp/issue320-slice10a-final-lanes.log`.
The expiry-limit case proves that each limit-one pass changes only one of three
expired grants. Entry and byte overload preserve accepted documents and counters.
The return race has one winner and cannot release an accepted ordinal.

### Slice 10b: fenced canonical journal processing

- [x] Add typed claim handles with generation, worker, lease nonce, and bounded
  positive sequence keys. Revalidate raw fields so copied booleans cannot become
  integer keys through serialization.
- [x] Claim at most 256 entries and 1 MiB of documents. Each pending and expired
  index branch inspects at most the requested entry limit.
- [x] Commit canonical operations, exact reservations, events, grant usage,
  capacity release, and document removal in one transaction. Bulk writes update
  each grant and partition once, not once per subject.
- [x] Recover a lost claim reply by its original nonce. Recover a lost commit
  reply by its original keys. Do not issue another claim or financial write.
- [x] Prove stale-worker rejection, concurrent disjoint claims, expiry, exact
  closed replay, payload corruption rollback, cancellation after commit, payload
  limits, zero and fractional money, and worker crash exhaustion with PostgreSQL.
- [x] Complete actual nested query-plan checks with retained journal, payload,
  grant, window-reference, reservation, event, and operation history.
- [x] Complete full required lanes, migration paths, collection, and style gates.
- [x] Connect the distinct journal receipt to the shared local terminal owner.
  Runtime selection remains off until this path and worker lifecycle are complete.

The first focused run found a PostgreSQL restriction on row-locking queries
inside a set operation. Migration 128 moves the two separately bounded claims
into materialized query blocks. Applied migration 127 remains unchanged.
The corrected focused run passed 45 cases. Expanded financial cases pass,
including capacity retained after five worker crashes and exact provisional
money for zero or fractional charges.

The first plan fixture did not retain enough payload or grant-window rows to
measure those lookups. The expanded fixture retains 10,000 journal, grant,
operation, event, reservation, and window-reference rows. The final fixture also
retains 10,000 charged dead-letter documents. Its owned diagnostic capacity is
20,000 entries; production defaults are unchanged. The alternate-join planner exposed a real grant
update scan of 10,001 rows for one result. Migration 129 restricts that update
and payload deletion to the current claimed keys. Migration 130 adds bounded
key-dependent document probes and transaction-local row-location deletion. It
also rejects duplicate, null, or nonpositive failure keys before any write.
The 500-document fixture exposed a small-table scan: PostgreSQL estimated ten
row locations and chose the four-page table scan. The final 10,000-document
fixture provides substantial payload history without changing planner settings.
All four planner modes, with normal and lost replies, pass the unchanged
bounded-row assertions. Keep the failed logs as diagnostic history.

The latest focused logs are `/private/tmp/issue320-slice10b-focused128.log`,
`/private/tmp/issue320-slice10b-complete-focused.log`, and
`/private/tmp/issue320-slice10b-key-focused.log`.
Final focused verification passed 71 cases in
`/private/tmp/issue320-slice10b-final-focused.log`. Raw SQL rejects malformed
materialization and failure keys. An observed grant-lock wait lets a claim lease
expire during processing; all financial effects roll back and capacity stays
charged. The required lanes and the 130-migration chain passed final verification.
The component and Helm lanes passed 5,347 cases. The first full application lane
passed 1,645 and failed one selected `/v1/messages` stream-close case: it observed
only the answer call instead of the expected classifier and answer calls. Its
stream charge and terminal-delivery assertions passed before that call-count
assertion. The complete stream-accounting module passed all 28 cases unchanged.
Keep `/private/tmp/issue320-slice10b-full-app.log` and
`/private/tmp/issue320-slice10b-stream-confirmation.log`. A full application retry
will record bounded selector cause and latency diagnostics without changing
production execution, deadlines, or assertions. The cause of the first mismatch
is not yet confirmed.
The first full PostgreSQL lane passed 700 cases and failed one Realtime receipt
recovery case: its first claim returned no rows. The unchanged Realtime module
then passed all 19 cases, and a complete PostgreSQL confirmation passed all 701.
The cause of that first mismatch remains unconfirmed. Preserve both runs and the
bounded read-only claim diagnostic; do not treat a passing retry as a cause fix.
The real-Redis lane passed all 105 cases. Fresh install, `v0.1.42`, and shared-feature
upgrade checks passed with all 130 migrations. Collection assigns all 7,799 tests
to exactly one dependency lane, and the 402-field configuration reference is current.

The first selector diagnostic used a fixture from an early pytest setup hook. That
temporary diagnostic caused a setup error and left later test patches active.
The contaminated application retry was interrupted and is invalid evidence. The
diagnostic now uses a normal autouse fixture, with pytest-owned cleanup. Neither
production code nor assertions or deadlines changed. Its focused check and a clean
full application retry must pass before the full-lane checkbox can be completed.
Keep `/private/tmp/issue320-slice10b-full-app-confirmed.log` as the invalid run,
and `/private/tmp/issue320-slice10b-full-app-valid.log` as the new confirmation.
The corrected diagnostic passed all 28 stream-accounting cases. The clean full
application confirmation passed all 1,646 cases. Final required counts are 5,104
hermetic, 243 Helm, 1,646 application, 701 PostgreSQL, and 105 Redis: 7,799 tests,
with no required skips. Changed Python files passed Ruff check and format, and
the diff check passed. The original stream and Realtime mismatch causes remain
unconfirmed; their passing confirmations do not establish a production fix.
No gateway RPS test has run on this clean replay yet. The four requested kind
rates remain pending after integration.

### Slice 10c: shared journal acceptance receipts

- [x] Keep journal acceptance and canonical event receipts as distinct typed
  contracts. Neither receipt can stand in for the other owner's result.
- [x] Use the existing local terminal owner and complete batch validation for
  journal acceptance. Keep every local proof until all acknowledgements are valid.
- [x] Use the same shared request queues for provider calls and paid cache hits.
  No legacy spend writer or second financial authority handles either path.
- [x] Prove lost acceptance replies, lost canonical replies, cancellation after
  acceptance, exact replay after grant closure, and exact balances with PostgreSQL.
- [x] Verify malformed or mismatched receipt kind, identity, outcome, sequence,
  generation, replay flag, and batch length cannot release a local proof.
- [x] Complete required full lanes, collection, migration checks, and style gates.
- [ ] Select this runtime only after supervised processing, unused-grant returns,
  recovery, and transport ownership are complete in the following slices.

The receipt contracts now live in a small leaf module. This keeps the journal and
shared terminal owner free of a circular import. The journal persistence adapter
delegates one bounded append call; it adds no database call, fallback, or writer.
Canonical finalization remains the default receipt contract. A journal owner must
select the journal contract explicitly, and rejects a canonical receipt.

Focused verification passed all 191 cases in
`/private/tmp/issue320-slice10c-focused.log`. Native shared-queue tests prove that
four accepted operations release local queue and proof bytes but retain all four
durable documents and their reserved money. Canonical processing then commits
four outcomes once, releases pending capacity, and permits exact grant closure.
Closed replay returns the original journal keys after large documents are removed.
Cancellation after a committed append retains all local proofs until exact retry.
The provider and paid-cache checks prove both paths share this acceptance owner.
The full component and Helm checks passed 5,365 cases, and the full application
lane passed 1,646. The first PostgreSQL lane passed 704 cases but failed one
Realtime transient-release case, with a teardown error. Its initial WebSocket
admission returned HTTP 503 after the local Prisma query-engine connection was
lost; later cleanup could not reconnect. The journal runtime was not selected.
The unchanged Realtime failure module passed all 12 cases with bounded engine
process diagnostics. This does not establish why the first connection was lost.
Preserve `/private/tmp/issue320-slice10c-full-postgres.log` and
`/private/tmp/issue320-slice10c-realtime-engine-focused.log`. The next full PostgreSQL
run passed all Realtime cases but found four admission planner failures. A focused
repeat confirmed that the grant-counter update could scan 10,000 retained grants.
The reservation-reference join could also lose its dependent key lookup. These
are real history-dependent work defects, not reasons to relax the row limits.

Append-only migration 131 restricts the counter update to the current grant keys
and makes each reservation-reference lookup depend on its inserted operation.
It changes no amount, ownership check, capacity limit, lock order, or driver-call
count. The stronger regression retains 10,000 grant-window references as well as
the existing grant, window, and operation history. All four planner modes keep
the original row limits. The allocator, journal, and shared-terminal focused run
passed all 108 cases in `/private/tmp/issue320-slice10c-key-focused.log`.

The pre-correction real-Redis lane passed all 105 cases, and all three migration
paths passed through migration 130. The migration-131 full run passed 5,373
component and Helm cases and all 1,646 application cases. PostgreSQL passed 703
and failed two: the official-SDK two-turn transcription case received a terminal
event that did not match its assertion, and the intent-phase process-death test
timed out waiting for its child's committed signal. The child diagnostic recorded
9.61 seconds in imports before connection began; the underlying reason for the
slow startup is not established. The SDK terminal type was not recorded, so its
cause is also not established. Neither failure is a passing gate or a fixed issue.

Preserve `/private/tmp/issue320-slice10c-final-components.log`,
`/private/tmp/issue320-slice10c-final-app.log`, and
`/private/tmp/issue320-slice10c-final-postgres.log`. The serial chain stopped at
PostgreSQL. The current-source Redis, migration, collection, and configuration
checks did not run in that first chain. Ruff check and format passed for all 12
changed Python files, and `git diff --check` passed.

Both unchanged compatibility modules passed all 15 cases with bounded SDK and
child-startup diagnostics. The full PostgreSQL confirmation passed all 705 cases
in `/private/tmp/issue320-slice10c-postgres-diagnostic-confirmation.log`. No assertion,
production budget, deadline, or financial policy changed to obtain that result.
The original failure causes remain unconfirmed. The final real-Redis lane passed
105 cases. Fresh install, `v0.1.42`, and shared-feature upgrades passed all 131
migrations. Collection assigns 7,829 tests to exactly one lane: 5,130 hermetic,
243 Helm, 1,646 application, 705 PostgreSQL, and 105 Redis. The configuration
reference remains current at 402 fields. Final logs use the same
`issue320-slice10c-final-` prefix for Redis, migrations, lanes, and configuration.
Runtime selection remains off. The final four-rate kind series remains pending.

### Slice 10d: supervised journal processing

- [ ] Add one owned worker task with startup, liveness, bounded backoff, and
  cancellation-safe shutdown through the existing lifecycle helpers.
- [ ] Retain at most one immutable claim with 256 keys and a fixed byte charge.
  Reject another tick without a waiter or a second claim.
- [ ] Retry the same claim after an uncertain materialization or failure reply.
  Never release durable documents, funding, or capacity on process cancellation.
- [ ] Add fixed action/outcome metrics and safe failure health details. Task
  shutdown must not claim that the durable accounting backlog has drained.
- [ ] Prove lost claim and commit replies, cancellation after commit, owner loss,
  lease recovery, exact money, and complete document removal with PostgreSQL.
- [ ] Complete focused checks, the affected real-dependency gates, collection,
  style checks, and the small typed-owner regression before closing this step.
- [ ] Connect processing, terminal drain, unused-suffix return, and transport
  ownership through the accounting bootstrap and deployment roles in later steps.

## Slice 9a: inactive permit foundation

This step copies the fenced-permit migration from `0ac46791` without changes. It
adds the matching Prisma fields and migration checks. The runtime does not select
permit allocation yet. Assigned grants, HTTP and cache admission, terminal writes,
reporting, and current main's legacy features keep their existing owners.

Seven new real-PostgreSQL cases prove assigned-grant parity, unique fenced claims,
duplicate settlement, owner and ordinal rejection, two-replica hard-budget limits,
and release of unused grants. The source branch's sequential database call per
subject is not copied. The next step must give refill and claim batches a fixed
database-call bound and put permit persistence in a small typed repository.

Verification:

- Focused permit and native accounting checks: 37 passed.
- Full real-PostgreSQL lane: 522 passed, with no required-service skips.
- Fresh install, upgrade from `v0.1.42`, and shared-feature migration checks: all
  passed with 109 migrations. The verifier removed its disposable databases.
- Migration-verifier and dependency-lane regressions: 34 passed.
- Full collection: 7,076 tests in one lane each: 4,567 hermetic, 1,645 app, 522
  PostgreSQL, 105 Redis, and 237 Helm.
- Prisma client generation, Ruff, format checks, and `git diff --check`: passed.

Logs are in `/private/tmp/issue320-slice9a-postgres.log`,
`/private/tmp/issue320-slice9a-postgres-full.log`,
`/private/tmp/issue320-slice9a-migrations.log`, and
`/private/tmp/issue320-slice9a-collection.log`.

Slice 9 is not complete. This result is not gateway load qualification.

## Slice 9b: permit batch persistence

The clean replay adds a small typed permit repository. The assigned and permit
repositories use one deadline, metric, error, and cancellation owner. The new
repository is not selected by bootstrap yet. No pool, queue, setting, admission
fallback, or reporting default changes.

One refill call covers up to 256 subjects. One claim call covers up to 256
operations across grants. The tests verify this bound for batches of 1, 8, 32,
and 256 items. Stable window and grant lock order protects concurrent batches.
Recovery verifies the complete request snapshot and every fence and owner field.
Exact exhausted or closed claims replay without a provider dispatch token.

The new window-lock plan check uses 25,000 expired same-subject windows and
25,000 live other-subject windows. It uses the scope/time index and returns one
window. This check does not prove the plans for all SQL inside the allocator.

An extra regression found that the copied source function marked a zero-cost
grant as draining after its first ordinal. The test failed before the fix. A new
append-only migration keeps it active until its operation limit is reached. The
applied source migration remains unchanged.

Verification:

- Final focused permit and accounting checks: 123 passed.
- Full component and Helm lanes: 4,848 passed.
- Full application lane: 1,645 passed.
- Full real-PostgreSQL lane: 535 passed, with no required-service skips.
- Full real-Redis lane: 105 passed, with no required-service skips. The first run
  omitted the dedicated memory-service variables and skipped one case; the final
  configured run has no skips.
- All five lanes cover 7,133 tests: 4,611 hermetic, 1,645 app, 535 PostgreSQL,
  105 Redis, and 237 Helm. Collection is exhaustive and does not overlap.
- Fresh install, upgrade from `v0.1.42`, and shared-feature checks: passed with
  111 migrations. The verifier removed its disposable databases.
- Prisma generation, Ruff, formatting, the small-owner ratchet, and
  `git diff --check`: passed.

Final logs are in `/private/tmp/issue320-slice9b-focused-final.log`,
`/private/tmp/issue320-slice9b-components-final.log`,
`/private/tmp/issue320-slice9b-app-full.log`,
`/private/tmp/issue320-slice9b-postgres-full.log`,
`/private/tmp/issue320-slice9b-redis-final.log`,
`/private/tmp/issue320-slice9b-migrations-final.log`, and
`/private/tmp/issue320-slice9b-collection-final.log`.

The permit bank, local dispatch, and remaining integration slices are unfinished.
This verification is not a new 50/100/200/500 RPS gateway result.

## Slice 9c: inactive bounded permit bank

The bank batches subjects and grants through the typed permit repository. A warm
batch uses one claim call. A cold batch uses at most two refill rounds and one
claim call, including partial grants. The call bound does not grow with subject
count. Remaining unfunded work receives a capacity rejection, not another
admission path. The existing per-call recovery limits still apply.

Subject capacity is fixed. Expiry cleanup checks at most 256 subjects, not the
whole bank. An uncertain claim retires its touched cursors; it cannot reuse an
ordinal. Close rejects new work and leaves durable claims for recovery. The tests
cover shared hard budgets, partial grants, expiry, cancellation, and crash recovery.
Subject identity also retains the allowance text required by the database hash.
Metrics use fixed labels, and representations hide grantees that contain fences.

The native comparison harness now supports direct, assigned, and pre-issued
admission through the same production pool and terminal owner. It records exact
database calls and source hashes. The initial 50 RPS, ten-second development probe
completed 500 operations in each mode with no drops or economic drift. It is a
dirty-tree development result, not a gateway qualification or evidence of 500 RPS.
Fewer refills did not remove each request's claim and terminal acknowledgement.

Verification:

- Final bank, privacy, profile, small-owner, and lane checks: 61 passed.
- Focused native bank and accounting checks: 134 passed.
- Full component and Helm lanes, run without overlapping lanes: 4,874 passed.
- Full application lane: 1,645 passed.
- Full real-PostgreSQL lane: 540 passed, with no required-service skips.
- Full real-Redis lane: 105 passed, with no required-service skips.
- Collection assigns all 7,164 tests to one lane each: 4,637 hermetic, 1,645 app,
  540 PostgreSQL, 105 Redis, and 237 Helm.
- Ruff, formatting, the typed-owner ratchet, and `git diff --check`: passed.
- No migration or Prisma schema changed in this step. The 111-migration chain
  retains slice 9b's verified fresh, last-release, and shared-feature paths.

Two overlapping component runs each failed one existing lifecycle timing case:
a cold child-process shutdown and an HTTP rejection during withdrawal. Both passed
separately, and the full serial lane passed with their original deadlines and
unchanged source. No test was removed, skipped, retried inside its assertion, or
given a larger deadline. Keep these failures as a test-isolation signal.

Final logs are in `/private/tmp/issue320-slice9c-bank-final.log`,
`/private/tmp/issue320-slice9c-focused-rechecked.log`,
`/private/tmp/issue320-slice9c-components-serial.log`,
`/private/tmp/issue320-slice9c-app-full.log`,
`/private/tmp/issue320-slice9c-postgres-full.log`,
`/private/tmp/issue320-slice9c-redis-final.log`, and
`/private/tmp/issue320-slice9c-lanes-final.log`.

Bootstrap still cannot select the bank. Full allocator plan checks, retained-state
byte limits, local dispatch, transport, journal, reporting, and current-main feature
adapters remain unfinished. Slice 9 and final load qualification remain unchecked.

### Clean slice 9c database comparison

The repeat used clean commit `76bcb8aa`, Python 3.11, PostgreSQL 16, two API-like
processes, the same two-connection accounting pool per process, an 8-item batch,
2 ms dwell, 32-operation grants, and 50 offered RPS for ten seconds. Each mode
completed 500 of 500 operations with no generator drops or errors. Each ended with
300 exact units committed, zero reserved, and zero provisional.

| Mode | Request-path database calls | Caller p95 | Caller p99 |
| --- | ---: | ---: | ---: |
| Direct | 998 | 27.86 ms | 33.43 ms |
| Assigned grants | 999 | 27.42 ms | 46.07 ms |
| Pre-issued permits | 1,013 | 28.52 ms | 42.22 ms |

The permit mode used 16 refills, 499 claim calls, and 498 terminal calls. This
confirms refill amortization, not a throughput improvement. The probe excludes
HTTP, Redis, provider latency, and projection. It is not a 500 RPS result.

Raw samples, summaries, source hashes, and the clean-worktree marker are in
`/private/tmp/issue320-slice9c-native-clean-76bcb8aa/`. The initial dirty-tree probe
remains separate. Neither output directory was overwritten.

## Slice 9d: bounded allocator SQL plans

The first failing regression showed that a nine-row window result still read
25,000 rows to sort tied window times. A new migration removes the unused ID tie
sort and replaces broad generation scans at every grant window stage with indexed
scope lookups. A partial renewal index and scalar active-window probes keep
expired policy checks independent of retained history.

Actual nested plans then found full retained-operation and grant scans in JSON
batch joins. Three further append-only migrations use bounded key arrays for
ordered locks and primary-key probes for identity, new-work, and replay checks.
They preserve atomic settlement, immutable identities, stable lock order, zero-cost
grants, and the existing request deadline. No pool, call, fallback, or runtime flag
is added. Already applied migrations remain unchanged.

The native plan recorder loads `auto_explain` on one owned diagnostic connection.
It changes no server setting or production pool. Its reports omit SQL text,
conditions, outputs, and parameter values. Count and byte limits bound its memory;
setup failure and cancellation close the connection.

Four cases each seed 50,000 windows, 10,000 closed grants, and 10,000 closed
operations. They capture every nested statement for six cold/warm calls, including
the prepared-statement threshold. Assigned and permit modes both pass with explicit
and implicit windows. The four reports contain 234, 240, 236, and 242 statement
plans. No retained-history scan runs; each observed history-table scan returns at
most one row in these normal-admission cases. The invalid-overlap lookup is bounded
at nine rows. This is plan evidence, not a new gateway load result.

Verification:

- Native bounds and nested-plan regressions: 12 passed. The original window-sort
  and retained-key scan regressions failed before their corrections.
- Final recorder, profile, ownership, and lane checks: 42 passed.
- Migration-verifier and lane regressions: 34 passed.
- Full component and Helm lanes: 4,880 passed.
- Full application lane: 1,645 passed.
- Full real-PostgreSQL lane: 552 passed, with no required-service skips.
- Full real-Redis lane: 105 passed, with no required-service skips.
- Fresh install, upgrade from `v0.1.42`, and shared-feature checks: passed with
  115 migrations. The verifier removed its disposable databases.
- Collection assigns all 7,182 tests to one lane each: 4,643 hermetic, 1,645 app,
  552 PostgreSQL, 105 Redis, and 237 Helm.
- Prisma generation, Ruff, formatting, and `git diff --check`: passed.

Logs are in `/private/tmp/issue320-slice9d-before.log`,
`/private/tmp/issue320-slice9d-nested-failure.log`,
`/private/tmp/issue320-slice9d-nested-rechecked.log`,
`/private/tmp/issue320-slice9d-plans-final.log`,
`/private/tmp/issue320-slice9d-component-final.log`,
`/private/tmp/issue320-slice9d-components-full.log`,
`/private/tmp/issue320-slice9d-app-full.log`,
`/private/tmp/issue320-slice9d-postgres-full.log`,
`/private/tmp/issue320-slice9d-redis-final.log`,
`/private/tmp/issue320-slice9d-migrations.log`, and
`/private/tmp/issue320-slice9d-lanes-final.log`.

The permit bank is still inactive. Next: byte limits, local-lease schema and recovery,
then the supervised local-dispatch owner. Keep the remaining slices and final kind
qualification unchecked until their own gates pass.

### Clean slice 9d database comparison

The repeat used clean commit `9d65f8af` with all 115 migrations. It kept the same
50 RPS, ten-second, two-process settings as the slice 9c comparison. All three
modes completed 500 of 500 operations with no errors or generator drops. Each
again ended with 300 exact units committed and no reserved or provisional balance.

| Mode | Request-path database calls | Caller p95 | Caller p99 |
| --- | ---: | ---: | ---: |
| Direct | 1,000 | 26.83 ms | 39.99 ms |
| Assigned grants | 998 | 28.34 ms | 39.08 ms |
| Pre-issued permits | 1,014 | 31.26 ms | 50.12 ms |

The permit mode still used 16 refills, 500 claim calls, and 498 terminal calls.
The query corrections add no request-path database call. This small, empty-history
probe does not establish a latency gain. The native seeded-plan cases establish
the removal of retained-history scans. Local dispatch and the terminal journal
remain necessary to remove the per-request claim and compact terminal work.

Raw samples, summaries, source hashes, and the clean-worktree marker are in
`/private/tmp/issue320-slice9d-native-clean-9d65f8af/`. The log is
`/private/tmp/issue320-slice9d-native-clean-9d65f8af.log`. This is an isolated
accounting probe, not 50/100/200/500 RPS kind gateway qualification.

## Slice 1 source decisions

The slice replays the behavior from `30f4b1e7`, `2efcf685`, and `e82f24c5`. It does
not replay `1527785d`, because that commit only added CI triggers for the old feature
branch names. Current main's normal CI gates will cover the integration branch.

The original conflict in `src/main.py` was resolved by starting both current main's
realtime runtime and the issue 320 bounded metrics runtime under the same ordered
lifecycle. The documentation conflict was resolved by retaining current main's new
navigation and adding the concurrency guide to its operations reference.

## Slice 1 verification

- `ruff check`: 23 touched Python files passed.
- `ruff format --check`: 23 touched Python files passed.
- Focused observability and harness suite: 71 tests passed; one evidence test initially
  failed because its raw historical samples had been omitted.
- Restored evidence regression: 1 test passed.
- Current-main startup lifecycle: 2 tests passed.
- Real PostgreSQL telemetry acceptance and spend recovery: 4 tests passed against a
  fresh PostgreSQL 16 database with all 99 main migrations.

The only reported warnings are the existing Prisma/Pydantic Python 3.14 compatibility
warning and pytest-asyncio deprecation warnings.

## Slice 4 source decisions

The durable admission behavior comes from `e39512db`, `4ca6abdd`, `04282772`, and
`154aeb81`, with the batch race/replay regressions from `073e2d3f`, `f976bcb4`, and
`bd291a51`. The inactive capacity-partition schema comes from `bbc2c597`.

The design contracts from `6c532178` were retained and registered in current main's
documentation structure. The large historical PR 4 raw benchmark archive from
`61a4081c` was not copied because it is neither a release qualification result nor a
regression input. The design records explicitly document that decision. The migration
remains inactive: it prepares bounded partition tables but does not enable a second
admission authority.

## Slice 4 verification

- `ruff check` and `ruff format --check`: all 13 touched Python files passed.
- Hermetic admission benchmark and batch selector regressions: 59 passed.
- Native PostgreSQL admission and schema tests: all 66 unique tests passed. One combined
  host-pressure run produced an allocation-full result before SQL in one strict race;
  the exact case and then the full 20-test durable-admission file passed unchanged.
- Prisma client generation: passed.
- Repository migration-path verifier: fresh install, upgrade from `v0.1.42`, and the
  shared route-policy feature path all passed with seeded compatibility records. The
  verifier removed all of its disposable databases.
- `git diff --check`: passed.

The only reported warnings are the existing Prisma/Pydantic Python 3.14 compatibility
warning and pytest-asyncio deprecation warnings.

## Slice 5 source decisions

The budget and prompt changes come from `024f2e71` and `44b70fda`. The integration
keeps current main's realtime settings, managed-asset relations, migration checks,
and documentation navigation.

Normal combined budget checks use one SQL call. Missing or invalid budget counters
cause an unavailable response. Operators must repair counters outside inference.
Optional budget alerts use durable, deduplicated intents and a bounded worker.
Cold prompt bindings use one Redis read, one bounded SQL lookup, and one cache-write
pipeline. PostgreSQL remains the source of truth.

The large historical PR 5 sample directory was not copied. It is not an input to a
regression test or a release qualification gate. The design and deployment pages
record this choice. The final integrated image still requires new load evidence.

## Slice 5 verification

- Focused budget, prompt, bootstrap, and configuration checks: 151 passed.
- Real Redis prompt-fill check: 1 passed.
- Real PostgreSQL budget concurrency checks: 15 passed.
- Resume checks for budget, alerts, configuration, and Helm: 79 passed.
- Prisma client generation: passed.
- Fresh install, upgrade from `v0.1.42`, and shared-feature migration verification:
  passed with all 103 migrations and the budget and realtime fixtures.
- Helm lint and template: base, evaluation, and production profiles passed.
- Ruff check and format: all 30 changed Python files passed.
- `git diff --check`: passed.

The interrupted verifier did not produce a final result. The complete rerun used
the installed Prisma 5.17.0 CLI directly through the verifier's `--prisma` option.
This uses the same pinned engine and avoids repeated Python-wrapper startup.
Its output is in `/private/tmp/issue320-slice5-native-migration-verification.log`.
Five disposable databases left by the interrupted and superseded checks were
removed after the complete rerun passed.

## Slice 6 source decisions

The spend schema comes from `6708948d`. Spend recovery comes from merge
`4d75b560`. Request deadlines and bounded work come from merge `4d856670`.
The replay uses the first parent of each merge.

The integration keeps current main's realtime recovery, settings, managed assets,
and documentation structure. Admission and settlement use main's shared billing
transaction helper. The production transaction budget remains 250 ms.

Python 3.14 exposed a cancelled-thread waiter that retained a payload through an
error log. The executor now waits for its owned future through `asyncio.wait`.
It reads the result only after completion. A cancelled caller does not cancel
the owned thread. The existing ownership and payload-release tests cover this
change. The SQL race fixture now patches the budget in its actual owner, the
shared transaction helper. Its two-second functional-test budget is unchanged.

The two large historical sample directories were not copied. They are not
regression inputs. The design and deployment pages record this decision.
New load evidence is still required for the complete integration.

## Slice 6 verification

- Python 3.11 matches CI and the production image.
- Focused application, component, configuration, and Helm checks: 1,417 passed.
- Real PostgreSQL spend and realtime checks: 78 passed, with no skips. This run
  includes the separate environment for the pinned official realtime SDK.
- Full Redis lane: 102 passed. The memory-isolation check first skipped because
  its service variables were absent. The configured check then passed.
- Python 3.14 bounded-work and transaction regressions: 19 passed after the fix.
- Prisma client generation: passed.
- Fresh install, upgrade from `v0.1.42`, and shared-feature migration checks:
  passed with all 104 migrations and the realtime compatibility fixtures.
- Full test collection: 6,714 tests in exactly one lane each. Counts are 4,348
  hermetic, 1,637 app, 475 PostgreSQL, 103 Redis, and 151 Helm.
- Settings reference: current, with all 368 fields.
- Helm lint and template: base, evaluation, and production profiles passed with
  an existing test secret. Rendering without a required secret failed as designed.
- Ruff check and format: all 91 changed Python files passed.
- `git diff HEAD --check`: passed.
- Full application lane: all 1,637 tests passed.
- SQL probe: passed with 100,000 history rows, 10,000 retained outbox rows,
  and 1,000 expired operations. Admission used one transaction and three SQL
  statements. Receipt acceptance used one transaction and two statements.
  Recovery used the expiry index and a row-bound update.
- Bounded callback and blocking-work probe: passed. Its raw result is in
  `/private/tmp/issue320-slice6-request-work.json`.

Logs are in `/private/tmp/issue320-slice6-python311-focused-tests.log`,
`/private/tmp/issue320-slice6-python311-postgres-tests.log`,
`/private/tmp/issue320-slice6-full-app-tests.log`,
`/private/tmp/issue320-slice6-full-redis-tests.log`, and
`/private/tmp/issue320-slice6-migration-verification.log`.

## Slice 7 source decisions

This slice replays `9a3f2cfc` and `5be17a63`. It retains current main's realtime
runtime, asset-link reconciliation, and four creator authorization owners.
These checks now use the shared readiness inventory. Fixed diagnostics retain
asset counts and timestamps without exposing raw exception messages.

Realtime cleanup starts at the first process drain signal. Bootstrap and Helm
require its cleanup and write budget to fit before generic response cancellation.
Deployment capacity includes realtime upstream and downstream sockets and provider
connection limits. No extra connection pool or inference query is added.

The old policy-listener tests lacked current main's creator-model owner. The
fixtures now provide that owner. Two regressions also prove that missing or failed
creator policy refresh prevents readiness. The production check remains closed.

The historical lifecycle sample archive was not copied. The source plans retain
their history but now point to this plan for clean-replay progress.

The first kind run found a migration Job memory failure. The Python Prisma CLI
imports the full generated client and exits with code 137 at 1 GiB, even for its
version command. The bundled native Prisma 5.17.0 CLI succeeds at the same limit.
The canonical image now selects that native CLI at runtime. Build-time client
generation still uses the Python CLI. The Railway image is generated from the
canonical image. No memory, timeout, migration, or admission limit was increased.
The image check now verifies CLI selection and execution at 1 GiB.

## Slice 7 verification

- Focused current-main readiness, realtime drain, and capacity checks: 62 passed.
- Full component and Helm suite: 4,682 passed. The first run found four fixture
  failures; the final run has none.
- Full application lane: 1,641 passed.
- Full real-Redis lane: 105 passed, with no skips. The first run found the same
  missing creator-model fixture; the final run has no failures.
- Real PostgreSQL migration, allocation, spend, recovery, and realtime checks:
  63 passed, with no skips. This includes the pinned official realtime SDK.
- Full real-PostgreSQL lane: all 485 passed, with no skips.
- Fresh-install, `v0.1.42` upgrade, and shared-feature upgrade checks: passed
  with all 104 migrations. Image history and realtime fixtures also passed.
- Native CLI, container contract, migration, and managed-server regressions:
  16 passed.
- Base, evaluation, and production Helm lint and template: passed.
- Effective capacity-profile documentation check: passed.
- Frozen dependency install and lock check: passed.
- Container and generated-settings checks: passed after the native CLI fix.
- Image build, offline non-root startup, and blocked shutdown: passed before and
  after the native CLI fix. The new 1 GiB native CLI image check passed.
- Optional Presidio image variant: build and offline checks passed. The analyzer
  made no external network request. Migration and blocked shutdown checks passed.
- Ruff check and format: all 134 changed or new Python files passed.
- Full collection: 6,915 tests in exactly one lane each. Counts are 4,464
  hermetic, 1,641 app, 485 PostgreSQL, 105 Redis, and 220 Helm.
- Settings reference: current, with 380 fields.
- Representative SQL probe: passed with 100,000 ledger rows, 10,000 retained
  outbox rows, and 1,000 expired operations. Receipt acceptance now uses one
  fenced statement and one implicit transaction. It used two statements in the
  previous slice. Admission and indexed recovery retain their row bounds.
- `git diff HEAD --check`: passed.
- Fresh disposable kind lifecycle run after the native CLI fix: passed. Checks
  cover fresh and concurrent migrations, Redis readiness recovery, both
  failed-migration rollout gates, stream drain, repeated signals, and shared
  backlog recovery. All 20 batch items completed after rollout, with one ledger
  entry each. Forced pod loss preserved one spend charge and the required audit.
  All four interrupted streams closed upstream without a success marker.
- The harness removed its own cluster after completion. It did not change the
  user's Kubernetes context or stop unrelated containers.

The before- and after-rollout samples each completed 100 requests at 10 RPS.
They prove lifecycle behavior only. Fixed-replica and autoscaling comparison
remains in the capacity CI job and is not claimed as a local result here. The
final integration still requires the full qualification ladder.

Application logs are in `/private/tmp/issue320-slice7-full-app-tests.log`.
Component and Helm logs are in
`/private/tmp/issue320-slice7-hermetic-helm-tests-final.log`.
Redis logs are in `/private/tmp/issue320-slice7-redis-tests-final.log`.
PostgreSQL logs are in `/private/tmp/issue320-slice7-postgres-tests.log`.
Image results are in `/private/tmp/issue320-slice7-native-image-smoke`.
Kind results are in `/private/tmp/issue320-slice7-kind-native-lifecycle`.
These are slice checks, not the final 50/100/200/500 RPS certificate.

## Slice 8 integration checks

The source protocol predates current main's realtime journal and selector billing
owner. Those paths still write through the legacy spend owner. The v2 profile can
disable that worker and can use a separate budget-window authority. Before v2
activation, prove that each enabled writer shares the same authority and recovery
owner. Do not restore a legacy worker merely to make startup pass. That would not
prove shared hard-budget safety. Migration checks must include unresolved realtime
and selector work, not only legacy spending holds.

The source request path also reads the original JSON body to calculate cost bounds.
Current main can change the validated payload before provider dispatch. The clean
replay must calculate bounds from that final payload and cover multiple outputs.
This change must add no SQL, Redis, or network call.

Accounting construction must move to a small bootstrap owner. The existing spend
module is already above the size guard. New accounting audit and cost-bound policy
must use separate typed modules, not new concerns in that large file.

## Slice 8 source decisions

This slice replays `ab4837e9`, `718fac95`, `062ab0c4`, `7986e1c7`, and `4b204a37`.
Current main's realtime schema, provider library versions, capacity checks, and
documentation structure remain in place. The historical accounting plan was not
copied. This file owns clean-replay progress.

Accounting construction now has one small bootstrap owner. Cost-bound and terminal
audit policy also have separate typed owners. Cost bounds use the final validated
payload. They cover multiple chat outputs, embedding inputs, images, speech
characters, and rerank documents. Unbounded audio pricing fails closed in v2.
These calculations add no database, Redis, or network call.

The replay found a paid-cache bypass: a cache hit had no provider dispatch and thus
no v2 reservation. Charged cache hits now reserve their known charge and use the
existing terminal owner. Success requires both acknowledgements. Budget rejection
stays HTTP 429; local capacity rejection stays HTTP 503. Cache fees and provider
cost metadata keep their existing contracts.

Readiness now checks the accounting pool and active generation, not only Prisma.
The direct pool remains within the declared telemetry allocation. The isolated
accounting probe now uses the same direct pool with two connections per process.
It no longer measures the superseded Prisma request transport.

The additive cutover migration has one database-owned check for pending realtime,
spend, selector, and batch work. Preparation uses the same check and activation
owner. Operators must stop legacy writers first. This is not an online cutover
fence. A first attempt used an obsolete main batch status; the new, unshared
migration rolled back fully. The failed marker was cleared on the private test
database, and the corrected migration then passed all upgrade paths. No shared
or historical migration was changed.

Realtime, batch, and selector billing do not yet share v2 budget authority.
The temporary startup and Helm checks prevent those combinations. They do not
remove features from legacy mode. Shared adapters remain required before merge.
Capacity rendering now checks the accounting-worker role only when it is enabled.
The regression tests retain main's batch storage and realtime capacity assertions.

The final review found another fallback gap. A configured v2 service that is absent
or invalid must not select legacy provider execution or cache charging. One HTTP-edge
resolver now rejects both paths. One admission result mapper serves provider calls
and cache hits. Neither change adds a dependency call. The new accounting modules
use bounded typed contracts without `Any`. A regression also limits new functions
and modules to the repository's size targets.

## Slice 9 integration checks

The source change `0ac46791` includes permits, local dispatch, terminal journal,
HTTP accounting transport, read models, metrics snapshots, and routing reductions.
It must not overwrite the clean replay's accounting bootstrap, final cost bounds,
paid-cache admission, or missing-owner checks. These owners remain shared.

The source also selects the v2 reporting view by default. That view combines legacy
records with v2 facts and removes duplicate event IDs. Thus the switch alone does
not hide legacy charges. The clean replay must prove parity for main's tenant,
owner, cost, and deletion contracts. It must also retain all v2 history after a
rollback. A reporting default must not change until those checks pass.

## Slice 8 verification

- Full hermetic and Helm gates: 4,804 passed. Counts are 4,567 hermetic and 237 Helm.
- Full application gate: 1,645 passed.
- Full PostgreSQL gate: 515 passed, with no skips. Redis and the pinned official
  realtime SDK were present for the current-main compatibility tests.
- Full real-Redis gate: 105 passed, with no skips. Three separate memory domains
  were present for the cache-eviction test.
- Full collection: 7,069 tests, each in exactly one dependency lane.
- Native accounting tests: 30 passed. They cover grant concurrency, idempotency,
  uncertainty, native deadlines, readiness, and all pending legacy billing lanes.
- Fresh install, `v0.1.42` upgrade, and shared-feature upgrade: passed with 108
  migrations. The verifier removed its disposable databases.
- Paid-cache and missing-owner regressions: passed. Rejection occurs before a
  cached success or provider execution, with the existing error contract.
- All 77 changed Python files: Ruff check and format passed.
- Frozen lock, generated dependency export, 400-field settings reference, and
  effective capacity documentation: passed.
- Base, evaluation, production, and accounting evaluation Helm lint and template:
  passed. No secret was put in a rendered artifact.
- Base and optional Presidio images: build and offline, non-root, read-only smoke
  checks passed. The 1 GiB migration CLI check and blocked-shutdown checks passed.
- `git diff HEAD --check`: passed.

The final image IDs are
`sha256:46486b7e532e1d9ff5d5e08fa1b30602150426533f407a5b645ed14c3f18e284`
and `sha256:226cd6d75a24691a3a1e813d1883a0cedee8a6c7df7659450d289a57822681bd`.

The isolated native accounting probe ran each mode at 50 RPS for ten seconds with
two processes. Direct-window and grant modes each completed all 500 operations.
Each operation used one admission call and one terminal call. Both modes left exact
committed balances and zero reserved or provisional balance. Direct p95/p99 were
26.35/56.84 ms; grant p95/p99 were 28.78/44.00 ms. This is an accounting probe, not
a 500 RPS run or a gateway qualification. It excludes HTTP, Redis, providers, and
projection. Raw samples are in `/private/tmp/issue320-slice8-accounting-probe-final`.

Final logs are `/private/tmp/issue320-slice8-final-components.log`,
`/private/tmp/issue320-slice8-final-app.log`,
`/private/tmp/issue320-slice8-final-postgres.log`, and
`/private/tmp/issue320-slice8-final-redis.log`. Image smoke outputs are in
`/private/tmp/issue320-slice8-final-image-smoke` and
`/private/tmp/issue320-slice8-final-presidio-smoke`.

The first broader run found validation of a disabled accounting role and one
shutdown fixture timeout under concurrent build pressure. The final full run kept
the original assertions and timeouts and passed. All temporary v2 compatibility
checks remain until the shared adapters pass their own recovery tests.

## Slices 2 and 3 source decisions

These slices were integrated and verified together because the ingress/authentication
qualification harness imports the database allocation layer. The historical PR numbers
suggested the opposite order, but the source code dependency is unambiguous.

The ingress and authentication behavior comes from `034be4dc`, `5c3d5361`,
`df67f7a8`, and `42216598`. The dependency ownership behavior comes from `891a4a17`,
`7aa53ed2`, `261bfc89`, `b3c1257c`, `301ed1eb`, `8c13b3f9`, `9ca8c2e8`, and
`ea77c231`. The allocated telemetry failure classification from `c4ebe3ee` was applied
after its database allocation dependency existed. Old feature-branch CI trigger changes
were not replayed; the Redis memory-isolation services required by the current test lane
were retained.

Current main's realtime runtime, managed-asset authorization, reconciliation service,
and documentation structure were preserved. All startup resources now share one bounded
cleanup owner, including the newer reconciliation service. The legacy dynamic-config
regression was adapted to use the production partial-update contract so full default
serialization cannot accidentally pin environment-owned pool limits for a later restart.

## Slices 2 and 3 verification

- `ruff check`: all 70 touched Python files passed.
- `ruff format --check`: all touched Python files passed after formatting four replayed
  files to current main's canonical style.
- Focused hermetic suite: 436 passed and 1 Redis memory test skipped until dedicated
  servers were supplied.
- Real PostgreSQL and Redis suite: 19 passed against isolated PostgreSQL 16 and Redis
  7.2 containers.
- Dedicated Redis no-eviction/eviction isolation: 1 passed against two physically
  separate Redis 7.2 containers.
- `git diff --check`: passed.

The only reported warnings are the existing Prisma/Pydantic Python 3.14 compatibility
warning and pytest-asyncio deprecation warnings.
