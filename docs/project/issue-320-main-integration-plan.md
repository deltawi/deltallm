# Issue 320 main integration plan

Status: active

Base: `origin/main` at `f5ffd80d`

Source implementation: `feat/issue-320-accounting-v2` at `534ef828`

Accepted performance code: `cc3113bd`

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
- [ ] Slice 9: cap issued-lease state by bytes as well as entries before activation.
- [ ] Slice 9: prove local-lease funding, unused-suffix return, expiry, and conservative
  owner-loss recovery before local dispatch can run.
- [ ] Slice 9: separate the local dispatch deadline from the terminal recovery
  deadline. Keep dispatch within the funded budget period and refill TTL.
- [ ] Slice 9: retain typed cost bounds, the shared cache admission owner, and
  missing-owner rejection when adding local or remote accounting clients.
- [ ] Slice 9: retain the legacy reporting default while accounting v2 is disabled.
- [ ] Slice 9: keep protocol construction in the small accounting bootstrap owner.
- [ ] Slice 10: port the terminal journal and compact acknowledgement path.
- [ ] Slice 11: split accounting transport and projection roles.
- [ ] Slice 12: port bounded worker runtimes, economic settlement, and recovery limits.
- [ ] Slice 13: port terminal/read-model lanes and rollup sharding.
- [ ] Slice 14: port settled-receipt and narrow streamed projection fast paths.
- [ ] Run fresh and upgrade migration verification for the complete integrated chain.
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
- [ ] Retain the allocator's short dispatch deadline. Store a separate bounded
  receipt-recovery deadline. Reject a new local issue if its terminal lifetime
  does not fit the funded lease.
- [x] Add typed bulk refill, unused-suffix return, and terminal persistence owners.
  Keep a fixed call bound across subjects. Never await one call per subject.
- [ ] Bound issued receipts and retiring cursors by both entries and bytes.
  Preserve exact operation, request, owner, generation, grant, and ordinal identity
  after an uncertain transport acknowledgement.
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
