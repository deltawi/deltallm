# PR 6: bounded per-item Batch selection

## Decision and ownership

Selector-enabled chat items, including groups that can reach a selector through
normal, context or content-policy fallback, execute individually in the existing
bounded Batch worker pool. Selector-free microbatching is unchanged. There is no
grouped selector context, regrouping queue, new scheduler or admin workflow.
This is the compromise approved after PR 5: prefer simpler Batch execution over
recovering maximum provider-side batching throughput.

The canonical selector operation still owns eligibility, one classifier attempt,
parsing, safe defaults, provider capacity and selector billing. Batch owns the
item claim/heartbeat, its normalized input, and completion outbox. Caller
authentication, final-model authorization, budget and rate/concurrency admission
precede paid selection. Caller RPM is admitted once per Batch execution attempt,
not again for the hidden classifier hop; existing Batch retry admission remains.
Costs are soft-budget checked, not hard spending reservations. Customers pay the
actual classifier provider cost without markup; answer usage and Batch pricing
retain their existing contracts.

## Durable replay

One nullable versioned JSON checkpoint belongs to the existing Batch item. It
contains only a stable operation UUID, hash of normalized input/frozen principal,
source policy identity and, once available, the typed selector decision. It is
bounded to 4 KiB and never contains prompt text, raw output, credentials or a
second copy of authoritative pricing. Claim queries already return the item,
so ordinary items and restored decisions need no additional lookup.

This refines the original plan's placement on the billing journal: an item
checkpoint survives policy removal and journal settlement/retention and remains
visible on every reclaim. Journal-only discovery could miss an earlier paid
decision after the policy was removed. The billing journal remains the sole
owner of reserve/dispatch/receipt/settlement; there is no new economic lifecycle.

The active worker writes a pending marker after hard eligibility and successful
canonical billing admission, before selector dispatch, and saves the decision
before dispatching the answer. A rejected/rolled-back admission writes no
checkpoint and can retry after the dependency recovers. If the reservation
committed but its response was lost, the stable journal identity and frozen owner
still reject unsafe replay; exceptions never trigger checkpoint deletion. Both writes
compare-and-set on the primary, checking item/job identity, creator key, worker,
claim epoch, active lease, cancellation, job status and expiry. No lock or
transaction spans a provider call. A duplicate pending writer cannot start work.
Each transaction, including pool/lock/statement wait, is bounded to 250 ms and the
remaining item deadline.

A reclaimed item revalidates current authorization, input and policy, then
rehydrates the canonical request state. Incompatible/removed policy, malformed
checkpoint or uncertain pending selection fails closed with
`batch_selector_checkpoint_unavailable`. It never reclassifies. Existing bounded
Batch backoff, maximum attempts and job expiry own retries. A crash before a
provider call after successful admission can therefore conservatively fail that item; we accept this rare
loss of availability to avoid another recovery state machine or duplicate charge.
Unknown incurred usage remains pending in the existing billing recovery owner,
never fabricated as zero.

The answer outbox producer uses the stable operation UUID as the row's completion
ID for these items, linking the selector component to its answer in protected reports.
Both old and new consumers use that same row ID; a mixed-version outbox drain
cannot introduce another economic identity. Legacy rows retain their old IDs. Public output ordering,
`custom_id`, per-item errors and answer-only `usage` are unchanged.

## Capacity and dependency budget

No extra clients, pools, settings or background tasks are added. Preparation does
no paid selection; selection and answer share an existing worker execution slot,
caller lease, item heartbeat and one attempt deadline capped by job expiry.
Classifier capacity uses the same distributed provider RPM/TPM/concurrency owner
as realtime traffic. No HTTP response cache is consulted by internal Batch.

- Ordinary selector-free item (successful microbatch or standalone): zero added
  SQL/Redis/provider calls. Unsupported-microbatch split dispatch is the narrow
  exception described below.
- New selection: two bounded item checkpoint transactions, one fenced item lease
  renewal, existing selector reservation/dispatch/receipt and provider capacity.
- Decided replay: zero classifier calls/charges/checkpoint writes; one fenced item
  renewal plus normal answer execution and accounting.
- Rejected financial admission: zero checkpoint writes and zero provider calls.
- One slice-owned Batch limiter preserves the pinned deployment's existing
  `chat_batching.max_in_flight`, bounded by worker concurrency. Ordinary single,
  selected and microbatch answers share slots by concrete deployment ID, not by
  selector group or runtime generation. Conflicting captured caps use the smaller
  value. Classifier distributed limits remain with the canonical selector owner.
- Slots are acquired before provider permits/timing and released before fallback.
  Waits share the attempt deadline; overload stays a local gateway failure and
  never changes provider health. The limiter itself adds no dependency calls.
- Unsupported microbatches split serially inside their existing worker slot.
  Other worker slots remain parallel. This deliberately forgoes nested fallback
  fan-out, keeping admitted pipelines and waiters within worker concurrency;
  supported ordinary microbatch packing is unchanged.

### Split-fallback lease ownership

The chunk owns cleanup from before caller admission until all outcomes are
handled. It releases every still-owned caller lease and stops its refresher on
success, error or cancellation, including items not yet entered by serial
fallback. Release failures use the existing bounded Redis release-retry queue.
Handoff does not repeat caller RPM/TPM admission.

Each item retains its original heartbeat while waiting in the split chunk. The
single-item executor borrows that watch and stops it before terminal persistence;
otherwise a completed row could falsely signal shared lease loss. No second
heartbeat or nested execution pool is created. Shared lease loss cancels active
work and stops remaining dispatch; existing fenced recovery owns the claims.

After local deployment-capacity waiting, each split provider attempt atomically
renews the existing item fence (item ID, active state, worker ID and exact claim
epoch). A stale epoch, unavailable database or timeout fails locally, before
provider permits, latency sampling or cooldown. This reuses the canonical renewal:
an expired but unreclaimed claim can still renew; a reclaimed claim cannot.
The initial microbatch and its split calls share one runtime deadline capped by
job expiry. The check never holds a database transaction over provider I/O.

Each check adds **one transaction**, containing one timeout-configuration SELECT
and one primary-key UPDATE: two SQL statements plus transaction begin/commit
(rollback on failure). Pool/transaction/statement/lock waiting is capped at
250 ms and the remaining request deadline. Retries/fallback attempts each check
again. Normal standalone, selector-only, realtime and successful microbatch paths
do not use this added transaction. This supersedes the earlier blanket
zero-extra-SQL claim for selector-free split fallback.

Retained heartbeat load is bounded by the existing admitted chunk. With eight
replicas, four worker slots, eight items per chunk and a 15-second heartbeat,
there are at most 256 item watches and approximately 17.1 item-renewal statements
per second while all chunks are waiting, plus existing job/outbox work. This is
an illustrative configured bound, not a new default or global provider limit;
larger configured chunks increase that bound. No pool sizes are increased.

The existing production profile allows 8 worker replicas × 1 process × 4 slots =
32 item pipelines (selection and answer are serial within each slot). This is an
example, not a capacity certificate: replica surge and API traffic also consume
the existing deployment/provider/DB budgets. Operators must size the existing
Batch capacity allocation and provider limits with realtime headroom. This change
does not increase those pool sizes or defaults.

## Migration and rollback

The nullable-column migration installs its CHECK with `NOT VALID`; a separate
migration validates it after the installation transaction commits. The history
scan therefore does not retain the installation's ACCESS EXCLUSIVE lock.
New/updated rows must satisfy the constraint even while validation is pending.
No backfill or new index is needed.
Updates target the item primary key and join one parent primary key; the
checkpoint has two writes per new selected item and follows Batch item metadata
retention and deletion, not a new append-heavy table. Existing vacuum/analyze and
Batch cleanup remain the owners. Migration lock and statement waits are bounded.

The original PR 6 migration was unpublished but applied to disposable test
databases. Its replacement was explicitly approved on 2026-09-10 and tested on
fresh databases. The remote feature branch was checked at PR 5 merge `4dae1349`;
the migration was absent. Existing applied test databases and their histories
were preserved. Once shared, these migrations are append-only.

If validation times out, retain the installed column/constraint and stop rollout.
Inspect the failed validation and blocking activity through the coordinated
migration workflow. After verifying its transaction rolled back, mark only that
failed migration rolled back using Prisma's migration recovery procedure and
retry deploy. Never mark incomplete validation applied or edit a shared migration.
A larger validation-only statement budget requires a measured release decision;
do not extend the strong-lock installation budget or run DDL from workers.

Apply the migration through the coordinated release workflow before workers.
Drain old workers before enabling Batch selector workloads: old workers explicitly
reject selectors and cannot honor a saved decision after policy removal.
Removing a selector affects fresh requests; incompatible checkpointed items fail
safely instead of switching routes. For binary rollback, drain selected work and
outboxes first, then roll back workers; leave the additive column in place.
No destructive down-migration or automatic deletion/reclassification is provided.

## Bounded extraction

Single-item execution and dispatch were extracted from the oversized chat worker
module before adding this behavior. Its existing mixin facade, injected execution
callbacks and legacy `Any` signatures are preserved to avoid a subsystem-wide
worker rewrite. New selector contracts/repository/operation adapters are typed.
Removal condition for this named compatibility debt: migrate the common chat and
embedding worker host contract together in a separately reviewed Batch refactor.
The new composition edge imports after bootstrap because legacy cache/HTTP package
facades eagerly import each other; it performs no import-time I/O.

The review repair extracts Batch capacity into `batch/chat_capacity.py` behind
`router/attempt_capacity.py`. The existing failover attempt method retains a
narrow optional admission wrapper; it does not parse Batch settings or own a
second limiter. This localized execution seam avoids rewriting unrelated
provider retry/streaming behavior in the oversized failover module. The six-line
admin query repair similarly leaves existing endpoint SQL ownership unchanged;
extracting the full admin Batch repository belongs to the existing bounded
control-plane cleanup, not this error projection fix.

The public error contract is an allowlist in `batch/public_errors.py`.
Persistence recognizes server-owned Batch errors (including canonical wrappers),
not arbitrary provider codes. JSONL artifacts, admin offset/cursor lists, detail
and terminal-error remediation retain the checkpoint error's fixed safe message.
Unknown codes and all raw messages stay redacted. The existing UI text/JSON
rendering consumes an optional typed code; there is no new operator action.

## Reproducible performance evidence

Measured 2026-09-09 UTC on a shared macOS arm64 development host, Python 3.11.13,
Node 22, PostgreSQL 15 and Redis 7. The implementation is the working diff on
PR 5 merge `4dae1349c17afcd9cb71e73ff17a37f9dac15b47`; realtime baseline tree
`f888aca` matches that merge. Frozen dev/docs dependencies were installed.
These are local adapter/ASGI profiles, not a deployed gateway capacity certificate,
a saturation limit, or validation of the repository's aspirational 50-RPS SLO.

```sh
uv run python -m tests.performance.batch_selector_profile --output-dir /tmp/pr6-batch
uv run python -m tests.performance.realtime_selector_profile --output-dir /tmp/pr6-realtime
```

The Batch profile reuses the existing constant-arrival runner: 2 eight-item slices
per second for 10 seconds, four worker slots, one in-flight slice maximum, fixed
5-ms local provider calls, fake SQL/Redis/billing. Durability and contention are
tested separately against PostgreSQL/Redis. Every case completed 160/160 items
at the offered 16 items/second, with no generator drops and zero sampled
in-flight slope. That offered rate is not measured maximum throughput.

| Workload | Provider calls | Item p50 / p95 / p99 ms | Synthetic answer cost | Selector cost | Synthetic net saving |
| --- | --- | --- | --- | --- | --- |
| Always quality, concurrent | 160 answers | 19.57 / 22.93 / 38.41 | 0.036800 | 0 | 0 |
| Always quality, microbatch | 20 batches of 8 | 11.77 / 13.16 / 13.17 | 0.036800 | 0 | 0 |
| 70% economy / 30% quality | 160 selectors + 160 answers | 36.29 / 39.10 / 40.10 | 0.013616 | 0.003680 | 0.019504 |
| 50% economy / 50% quality | 160 selectors + 160 answers | 35.93 / 43.03 / 46.88 | 0.020240 | 0.003680 | 0.012880 |
| 10% economy / 90% quality | 160 selectors + 160 answers | 37.72 / 57.82 / 134.02 | 0.033488 | 0.003680 | -0.000368 |
| Malformed selector → quality | 160 selectors + 160 answers | 36.77 / 39.45 / 39.79 | 0.036800 | 0.003680 | -0.003680 |

All selected cases perform 320 checkpoint transactions and 160 calls each to
the existing reserve/dispatch/receipt methods. Selector-free cases perform none.
No selected item enters a microbatch. The quality-heavy p99 outlier is retained;
shared-host contention is not normalized away. Synthetic prices make quality
10× economy; net = always-quality answer cost − actual answer cost − selector
cost − measured mock penalty (zero). This explains the cost/throughput tradeoff;
it does **not** establish real quality or savings. Those evidence fields are null.
Both negative-saving cases are reported without clipping.

Sanitized raw slice samples, item timings, exact totals, provider timings and
Redis method counters are checked in under
`docs/project/benchmarks/pr6-batch-qualified/`. The first harness run exposed
a stale test-auth limit; the harness now invalidates/warms the existing key cache
after setting its workload limits. Only qualified run artifacts are retained.
The first three summaries precede the addition of lane/cause counters; their
fixed 70/30 mix and aggregate call counts are otherwise the same harness.

Realtime profiles use 10 requests/second for 20 seconds and a fixed 1-ms provider.
Each before/after case completed 200/200 requests, with no drops or queue growth.
Redis, billing and provider method counts are identical before/after for all four
cases. Cache-hit and Responses behavior is additionally covered by the full app
suite; this timing profile measures Chat only.

| Realtime path | Before p50 / p95 / p99 ms | After p50 / p95 / p99 ms | TTFT p95 before → after ms |
| --- | --- | --- | --- |
| Selector-free, non-streaming | 5.454 / 6.365 / 8.998 | 5.525 / 6.139 / 6.317 | n/a |
| Selected, non-streaming | 8.703 / 9.635 / 10.612 | 8.597 / 9.629 / 10.510 | n/a |
| Selector-free, streaming | 6.314 / 6.965 / 7.438 | 6.237 / 6.932 / 7.868 | 5.515 → 5.445 |
| Selected, streaming | 9.807 / 12.177 / 24.581 | 9.694 / 12.281 / 19.461 | 10.437 → 10.527 |

Raw before/after samples and summaries are in
`docs/project/benchmarks/pr6-realtime-before/` and
`docs/project/benchmarks/pr6-realtime/`. Shared-host measurements do not support
a statistically significant improvement/regression claim.

## Verification and remaining acceptance

Focused regressions cover independent decisions, lazy normal/context/content
fallbacks, hard eligibility, authorization, immutable replay, equivalent policy
republication, malformed/pending checkpoints, duplicate workers, lease loss,
admission cleanup, selector timeout, job deadline, answer failure, regular
selector versus Batch answer prices, and microbatch opt-out/concurrency.
The public Files/Batch API → durable create → worker → ordered output integration
passes with real PostgreSQL. Public output remains answer-only. Real billing
journal/recovery/report tests link one selector event and one answer event,
including duplicate outbox delivery and mixed-version completion IDs.

PostgreSQL checkpoint query-plan tests use 10,000 active items and assert a
one-row item-primary-key index scan. A held-row lock verifies bounded transaction
rollback. The migration verifier checks fresh install, latest release `v0.1.42`,
the PR 5 base and shared-feature ancestry. Legacy pending/completed items and an
old queued outbox are seeded before upgrade and verified unchanged afterward.

Original implementation gate results (2026-09-09, retained for history):

- `pytest -m 'hermetic or app' -q --tb=short --disable-warnings -ra`:
  4,507 passed, one sandbox-local socket skip. All 12 webhook delivery tests
  passed with socket access. The nine later-added app tests passed in the
  final 58-test lifetime/profile/structure/migration-verifier run.
- `pytest -m postgres`: 294 passed, no skips; `pytest -m redis`: 46 passed;
  `pytest -m helm`: 59 passed. PostgreSQL and Redis used isolated local containers.
- `pytest --collect-only -qq --dependency-lane-report`: 4,916 tests,
  exclusively classified as hermetic 3,251; app 1,266; PostgreSQL 294;
  Redis 46; Helm 59. No CI filename allowlist was added.
- `prisma generate --schema=./prisma/schema.prisma` and
  `python scripts/verify_migration_paths.py` passed. The verifier also passed
  with `--base-ref 4dae1349c17afcd9cb71e73ff17a37f9dac15b47`.
  Use `MIGRATION_TEST_ADMIN_DATABASE_URL` for disposable verification databases.
- Changed-path `ruff check` and `ruff format --check`: passed (32 Python files).
- UI `test:unit`: 243 passed; `build`: passed, 375.21 kB initial JS gzip.
  No UI source/dependency changed. Full UI lint remains at the pre-existing
  118 errors/four warnings; no touched-file lint exception is introduced.
- `python scripts/docs/export_openapi.py --check`: current, 229 paths /
  297 operations. `mkdocs build --strict` and `git diff --check`: passed.

Run through `uv run` with frozen dev/docs dependencies, or the prepared `.venv`.
Set `DATABASE_URL` for the PostgreSQL lane, and `REDIS_URL` plus
`DELTALLM_TEST_REDIS_URL` for Redis. Use Node 22 for `npm --prefix ui run ...`.
UI bundle replacement must not run concurrently with app tests.

## Review-fix qualification (2026-09-10)

All four review findings have regression coverage and are repaired. The final
focused command was:

```sh
.venv/bin/pytest tests/batch tests/router/selection tests/test_batch_worker.py tests/test_batch_worker_microbatch.py tests/test_batch_completion_outbox.py tests/test_batch_error_remediation.py tests/test_migration_verifier.py tests/test_failover.py tests/test_ui_authorization.py tests/test_batch_selector_profile.py -m 'hermetic or app' -q --tb=short
```

- Focused scope: **766 passed**, 25 PostgreSQL tests deselected and run separately.
- `pytest -q -m hermetic --durations=25`: **3,273 passed**, no skips.
- `pytest -q -m app --durations=25`: **1,277 passed**, no skips. The five
  subsequently added app cases (cap-one profile, two microbatch cancellation
  cases and two partial-admission cleanup cases) also passed in the final focused run.
- `pytest -q -m postgres --durations=25`: **299 passed**; final
  `pytest tests/batch -m postgres -q --tb=short`: **25 passed**.
- `pytest -q -m redis --durations=25`: **46 passed**;
  `pytest -q -m helm --durations=25`: **59 passed**.
- Final `pytest --collect-only -qq --dependency-lane-report`: **4,959 tests**,
  exactly one lane each: hermetic 3,273; app 1,282; PostgreSQL 299; Redis 46;
  Helm 59. Existing classifier and CI ownership remain unchanged.
- Prisma generation passed. The migration verifier passed fresh/release/shared
  checks using `v0.1.42`, then again with
  `--base-ref 4dae1349c17afcd9cb71e73ff17a37f9dac15b47`. Its staged upgrade
  proves the unvalidated CHECK rejects new bad writes before validation.
  Real PostgreSQL tests prove compatible writes during validation and bounded
  rollback of conflicting installation DDL.
- Ruff check passes all 51 changed/new Python files. Format check passes 50;
  `src/api/admin/endpoints/batches.py` is a pre-existing exception, confirmed
  by running the same formatter on its HEAD version. The repair adds only six
  SQL projection lines there; unrelated whole-file formatting was not retained.
- UI unit tests: **244 passed**. Production build passed, initial JS
  **374.15 kB gzip** (previous qualification: 375.21 kB). Touched-file ESLint
  passes; full lint remains **118 errors/four warnings**, unchanged.
- OpenAPI check is current: **229 paths / 297 operations**. No endpoint response
  schema was expanded beyond the existing error object.
- `mkdocs build --strict` and tracked/new-file whitespace checks pass.

The billing recovery regression records two admission attempts with the same
operation ID, zero checkpoint/provider calls after the rejected attempt, then
one selector call/charge, one answer and two checkpoint writes after recovery.
A separate real-journal test simulates a committed reservation with lost response
and proves a new owner cannot dispatch it. Existing replay tests still perform
zero additional classifier calls or charges.

### Repeatable latency and dependency evidence

Quiet runs execute sequentially, without this task's other test/build processes:
PR 5 `f888aca` (tree matching merge `4dae1349`) realtime baseline, then the
repaired worktree realtime and seven Batch cases. Raw samples are retained under
`docs/project/benchmarks/pr6-fix-quiet-before/`,
`pr6-fix-quiet-after/` and `pr6-fix-quiet-batch/`.
The earlier runs that overlapped other test work are also retained under
`pr6-review-fixes-realtime/` and `pr6-review-fixes-batch/`; they are not
discarded or presented as isolated timing comparisons.

Every quiet realtime case completes 200/200 requests at 10 RPS, with zero
generator drops and zero sampled in-flight slope. Redis, billing and provider
method counts match exactly in all four before/after cases.

| Realtime path | Before p50 / p95 / p99 ms | After p50 / p95 / p99 ms | TTFT p95 before → after ms |
| --- | --- | --- | --- |
| Selector-free, non-streaming | 11.55 / 14.42 / 28.76 | 8.24 / 14.10 / 15.98 | n/a |
| Selected, non-streaming | 17.61 / 21.79 / 34.23 | 17.15 / 20.22 / 20.75 | n/a |
| Selector-free, streaming | 13.21 / 16.06 / 17.15 | 13.46 / 15.62 / 16.57 | 12.04 → 11.76 |
| Selected, streaming | 18.19 / 21.58 / 22.72 | 19.06 / 22.86 / 27.01 | 18.23 → 19.06 |

The selected-streaming increase is retained, not normalized away. Shared-host
samples do not establish a statistically significant improvement or regression;
this is not a deployed SLO or saturation certificate.

Every quiet Batch case completes 160/160 items at the offered 16 items/second,
without generator drops or sampled queue growth. Provider/billing/checkpoint
counts match the original six cases. Ordinary Redis counts match exactly; the
balanced selected case records one extra EVALSHA (160 → 161) over the entire run,
with all other method counts unchanged. No per-item round trip was added.

| Batch workload | Item p50 / p95 / p99 ms |
| --- | --- |
| Always quality, concurrent | 22.23 / 37.17 / 37.60 |
| Always quality, microbatch | 21.39 / 26.08 / 26.11 |
| 70% economy / 30% quality | 38.13 / 57.60 / 79.41 |
| 50% economy / 50% quality | 39.13 / 56.92 / 70.15 |
| 10% economy / 90% quality | 36.14 / 55.00 / 55.95 |
| Malformed selector → quality | 45.35 / 69.03 / 70.13 |
| Quality with configured cap one | 54.56 / 81.55 / 90.02 |

The new cap-one profile observes a maximum of **one simultaneous quality answer**,
160 selector calls, 160 answers and 320 checkpoint writes at four worker slots.
Its extra local queue time is the intended configured limit. Synthetic cost and
quality caveats from the original profile still apply.

## Split-fallback lifecycle qualification (2026-09-10)

The subsequent two lifecycle findings are repaired: chunk-scoped cleanup covers
unstarted items, and serial fallback retains existing watches and fences dispatch
after capacity waiting. No scheduler, regrouping, configuration, migration, UI
workflow or provider policy was added for this repair. The preceding qualification
sections are historical; the following checks were rerun for these changes.

- Focused PR 6 scope: **788 passed**, 33 real-dependency cases deselected and
  covered by their respective lanes.
- Full affected lanes: **3,283 hermetic**, **1,294 app**, **305 PostgreSQL** and
  **48 Redis** tests passed, with no skips (**4,930** total).
- The final standalone lifecycle/guard/profile run passed **28** tests. All six
  PostgreSQL dispatch tests also passed after strengthening the two-worker test
  to reclaim the row while the old attempt waits for capacity. Old completion
  and failure writes remain fenced, including reuse of a worker ID with a new
  epoch. The 10,000-active-item query plan is a one-row primary-key index scan.
- Redis regressions preserve an unrelated caller lease while cancelling the
  entire split chunk, including a failed release recovered by the existing queue.
  New lifecycle regressions leave no caller refresher or heartbeat task running.
- Collection: **4,989 tests**, exactly one lane each: hermetic 3,283; app 1,294;
  PostgreSQL 305; Redis 48; Helm 59. All **15** classifier regressions pass.
  Helm, migration upgrade, UI/build and OpenAPI evidence above was not rerun for
  this lifecycle-only repair; those surfaces did not change.
- Ruff check passes all **57** changed/new Python paths. Format check passes
  **55**. The existing admin Batch endpoint and newly touched Batch repository
  facade both fail the same formatter at HEAD; their unrelated formatting was
  preserved. The facade repair adds only its two optional-deadline forwarding
  lines. All other Python paths changed by this repair pass format checking.
- Strict docs build and tracked/untracked whitespace checks pass.

The first application pass caught an over-specific benchmark assertion that
counted periodic heartbeats as dispatch checks. The harness now records dispatch
checks separately; the full application lane above was rerun after correction.
The hermetic run logs an unrelated pending retry-task warning after passing.
Task-creation tracing reproduces it in the unchanged
`test_request_rate_limit_release_keeps_retrying_pending_slots`, which starts
`RateLimitReleaseRetryQueue` without stopping it. The test, middleware and retry
queue are unchanged from HEAD; no unrelated lifecycle rewrite was included.

### Split-fallback measurement

```sh
.venv/bin/python -m tests.performance.batch_selector_profile --output-dir docs/project/benchmarks/pr6-fallback-after --cases baseline_split baseline_split_slow baseline_concurrent baseline_microbatch selector_individual
```

Before samples were captured before implementation in
`benchmarks/pr6-fallback-before/`; repaired samples and raw item/slice timings
are in `benchmarks/pr6-fallback-after/`. Repaired profiles ran sequentially after
all this task's test processes finished. Both use 2 eight-item slices/second for
10 seconds, four worker slots and fixed local provider mocks. The normal mock
takes 5 ms; the slow split case takes 40 ms per single answer.

| Split workload | Before item p50 / p95 / p99 ms | After item p50 / p95 / p99 ms | Renewal method calls before → after |
| --- | --- | --- | --- |
| 5-ms answer | 52.30 / 84.17 / 90.73 | 44.99 / 73.50 / 77.72 | 160 → 847 |
| 40-ms answer | 220.98 / 374.28 / 384.69 | 218.99 / 360.96 / 367.35 | 730 → 3,208 |

Both split cases complete **160/160 items** before and after: 20 unsupported
microbatch attempts and 160 single answers, peak single-answer concurrency one,
zero generator drops and zero sampled in-flight slope. All Redis method counts
match exactly for each before/after case. No second caller admission was added.
Each repaired case records 160 bounded dispatch renewals. The statement-budget
regression checks two application-issued SQL statements per new transaction
(timeout configuration plus UPDATE), with begin/commit outside provider I/O:
**320 added SQL statements and 160 short transactions** for this workload.

The harness uses an accelerated **10-ms heartbeat**, not the production example's
15 seconds. Its total repository-method counts include retained queued heartbeats
and final persistence renewal; they are not SQL-statement counts. Retaining these
watches intentionally increases renewal traffic. Their production budget is
given above. Real PostgreSQL separately verifies locking, deadlines and query
plans; these fake-DB timings do not measure the added database round trips.

Concurrent, supported-microbatch and selected-individual profiles also complete
160/160 items each and record **zero split-dispatch renewals**. They retain their
160 answers, 20 microbatches, or 160 selectors plus 160 answers, respectively.
Every repaired profile ends with zero retained caller leases/refreshers. Baseline
artifacts predate those explicit cleanup counters; failure/cancellation cleanup
is established by the new regressions, not inferred from successful profiles.
These shared-host samples are not a statistical performance improvement, deployed
capacity certificate, or representative model-quality/savings evaluation.

The temporary PR 6 implementation and review-fix plans were removed before PR
submission. This document and the benchmark artifacts retain the design decisions,
completed verification evidence and remaining qualification work.

## Migration-verifier future-base qualification (2026-09-10)

The verifier now runs the intermediate installed-but-unvalidated CHECK only
when the extracted upgrade base does not already contain the selector validation
migration. Deploy cannot undo an applied validation. Seeding, the full upgrade,
final validation/data-preservation assertions, fresh installation and the fixed
shared-feature ancestry check remain unconditional. This repair changes only
the verifier, its regressions and this evidence; no migration or Batch runtime
behavior changed.

- The already-validated-base regression failed before the fix; all **18** verifier
  tests now pass. Coverage includes pre-install, installed-only and validated
  bases, plus failure propagation and reverse-order disposable-database cleanup.
- The full hermetic lane passes **3,287** tests with no skips. The previously
  documented unrelated retry-task warning remains. All **15** classifier tests
  pass; collection assigns **4,993** tests to exactly one lane: hermetic 3,287;
  app 1,294; PostgreSQL 305; Redis 48; Helm 59.
- The canonical verifier passes against real isolated PostgreSQL using its
  default release base, **v0.1.42**, and again using the current PR 6 migration
  tree as an already-validated base. Because PR 6 was uncommitted during that check,
  the second run substituted only Git-base extraction with the worktree schema; migration
  deployment, fixtures, assertions and database cleanup are real and unchanged.
  Both runs also pass fresh-install and shared-feature checks.
- Both real PostgreSQL selector-migration regressions pass, retaining checks for
  concurrent compatible writes during validation and bounded installation-lock
  failure. The broader app, PostgreSQL, Redis, Helm and UI lanes were not rerun
  for this verifier-only fix; their earlier evidence is retained above.
- Ruff check/format for both touched Python files, strict docs build and whitespace
  checks pass. The existing CI migration job needs no selection or workflow change.

## Remaining whole-feature acceptance

- Browser operator/mobile/desktop/keyboard smoke checks remain unverified; the
  earlier attempt failed on the browser plugin's missing service module. This
  review fix changes a UI error type and its transport test, not interactive UX;
  no new browser interaction is claimed.
- Representative held-out prompts, measured model outputs and real pricing are
  still needed for quality-and-savings acceptance through the existing PR 5
  supplied-fixture evaluator. No customer prompts or paid provider calls were used.
- Production mixed-traffic/saturation qualification remains deployment-specific;
  no new concurrency default or enforced SLO is justified by these local profiles.
- Whole-feature acceptance remains tracked in issue #304.
  The issue's original regrouping checkbox is superseded by the approved
  per-item compromise, not implemented silently or claimed as regrouping.
