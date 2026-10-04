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
- [ ] Slice 9: batch refill and claim work across subjects with a fixed database-call
  bound. Do not copy the source branch's sequential subject loop.
- [x] Slice 9: put permit persistence in a small typed repository owner.
- [ ] Slice 9: add the bounded permit bank and test partial grants, cancellation,
  expiry, shutdown, and the warm-path call bound before bootstrap can select it.
- [ ] Slice 9: profile all SQL inside the allocator before permit activation.
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
