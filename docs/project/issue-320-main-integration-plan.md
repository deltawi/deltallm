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
- [ ] Slice 7: port readiness, drain, and Kubernetes capacity contracts.
- [ ] Slice 8: port accounting protocol v2 and atomic grant admission.
- [ ] Slice 9: port pre-issued permits and local lease dispatch.
- [ ] Slice 10: port the terminal journal and compact acknowledgement path.
- [ ] Slice 11: split accounting transport and projection roles.
- [ ] Slice 12: port bounded worker runtimes, economic settlement, and recovery limits.
- [ ] Slice 13: port terminal/read-model lanes and rollup sharding.
- [ ] Slice 14: port settled-receipt and narrow streamed projection fast paths.
- [ ] Run fresh and upgrade migration verification for the complete integrated chain.
- [ ] Run the 50, 100, 200, and short 500 RPS ladder on one clean kind image.
- [ ] Run the ten-minute 500 RPS qualification only after the short ladder passes.

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
