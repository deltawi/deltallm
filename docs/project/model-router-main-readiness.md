# Model router: feature-to-main integration readiness

Date: 2026-09-10. Integration parents: feature `46075ba5` (all six PRs)
and main `35ed9843` (provider expansion). This record does not approve or
perform a merge to main, deployment, or representative quality/savings acceptance.

Latest correction: see the [independent-selector implementation record](#independent-selector-implementation-2026-09-10)
and its [design/rollout decision](model-router-independent-selector.md).

## Integration decisions

- Keep `resolve_chat_upstream_from_registry` as the single upstream resolver
  for ordinary answers, streaming, Batch and selectors. Port the five named
  provider profiles' adapter selection, default endpoints, explicit endpoint
  overrides and Bearer authentication into that owner.
- Keep main's bootstrap-owned provider discovery/control transport and immutable
  adapter registry. Update selector and executor fixtures for that registry.
- Use response-cache namespace `v5`: main's reasoning-history semantics and
  the feature's routing-policy identity must not reuse either old `v3` or
  feature-only `v4` entries. Tests exclude `v2`, `v3` and `v4`.
  Existing entries expire normally; there is no wildcard deletion.
- Share the named providers' usage parser between public answer normalization
  and the selector's durable receipt. Preserve all three reported cached-input
  aliases, reject conflicting/invalid counters, distinguish missing from zero,
  and enforce the existing bounded selector receipt before pricing.
  New integration tests reproduced 37 failures before the receipt fix.
  A reported 10-input/4-output receipt with six cached inputs costs exactly
  USD 0.126 at the test's explicit 0.01/0.001/0.02 rates, without markup.
- Adapt the existing provider benchmark fixture to both old and shared resolver
  interfaces; retain one configured adapter and add six executor/stream/MCP
  call-count and cleanup regression cases.

The tenant principal, published policy, shared capacity and billing operation
remain owned by their existing services. This integration adds no awaited
SQL, Redis, provider or filesystem work and no extra retries. Missing economic
evidence still fails closed or remains explicitly unknown under the existing
receipt/recovery contract. Budgets remain soft; Batch retains the approved
per-item compromise. No new UI workflow, scheduler or infrastructure dependency
is introduced.

## CI audio-test failure

The merged feature's CI run 34470694021 failed at
`tests/test_elevenlabs_stt.py` reading `audio_seconds_pm`. The test read a
per-minute routing counter after a real-time sleep; that is not a lifetime
usage total. A controlled clock that advanced from 12:00:59.999 to the next
minute immediately after `increment_usage_counters` reproduced the identical
`KeyError`, with HTTP 200 and the correct spend event.

The two tests asserting minute counters now pin the router window. All four
spend assertions in that module wait for the recorder's event with a bounded
one-second deadline instead of sleeping. The identical injected clock-boundary
reproduction then passed. No production audio routing, counter or billing
behavior was changed; the historical CI runner's clock was not captured.

## Local verification

Full local integration lane results at `98abc414`:

| Lane | Passed |
| --- | ---: |
| Hermetic | 3,593 |
| Application | 1,393 |
| PostgreSQL | 305 |
| Redis | 48 |
| Helm | 68 |
| Total | 5,407 |

Commands use `UV_CACHE_DIR=/tmp/deltallm-readiness-uv uv run --no-sync`:

- `pytest -q -m <lane> --tb=short` for every lane above. Local PostgreSQL 15
  and Redis 7 containers are disposable, loopback-only test services; no
  production database, credentials or provider calls are used.
- `pytest --collect-only -qq --dependency-lane-report`: exactly one primary
  lane per test. Classifier, migration verifier and structure checks: 73 passed.
- `prisma generate --schema=./prisma/schema.prisma` and
  `prisma migrate deploy --schema=./prisma/schema.prisma`: successful;
  all 89 migrations applied to the isolated fresh database.
- `python scripts/verify_migration_paths.py`: fresh install, last release
  `v0.1.42` and shared-feature upgrades passed. No shared migration was edited.
- `ruff check .`: passed. `ruff format --check` on the 13 integration-fix
  Python paths: passed.
- `npm --prefix ui run test:unit`: 253 passed, no skips.
- `npm --prefix ui run build`: passed; initial JS gzip 370.21 kB versus the
  prior feature's 374.15 kB. Existing chunk-size guidance remains.
- ESLint on all feature-to-main changed UI source/test/script paths passed.
  Full `npm --prefix ui run lint` retains the recorded baseline:
  118 errors and four warnings; it is not claimed green.
- `python -m scripts.docs.export_openapi --check`: current, 229 paths and
  297 operations. `mkdocs build --strict` and `git diff --cached --check` pass.
- `uv lock --check --offline`: passed outside the sandbox after a macOS
  system-configuration access panic in the sandbox; no lock file changed.

An initial sandboxed hermetic run skipped three localhost socket checks;
the subsequent full runs with socket access executed all cases without skips.
An earlier successful hermetic run emitted the already-recorded unrelated
rate-limit retry-task cleanup warning; the final 3,593-case run did not.

## Remote CI follow-up: Batch profile regression

Integration [CI run 34478639197](https://github.com/deltawi/deltallm/actions/runs/34478639197)
passed seven jobs but the application lane finished with 1,392 passed and one
failure in `baseline_split_slow`. Its constant-arrival functional test scheduled
three slices at one per second, with one slice allowed in flight. The first
slice took 1.149 seconds, so the generator correctly dropped the next arrival:
two slices/16 items completed, one slice was dropped. The earlier audio test
passed. This run is not claimed green.

The Batch functional test now uses a finite three-request wave, serialized by
a test-only lock, so every scenario exercises all three slices regardless of
CI host speed. It retains all item, capacity, lease, checkpoint, cost and
provider-call assertions. Two fault-injection cases verify that failed or
dropped load still raises and remains visible in saved reports. The benchmark
implementation, real constant-arrival driver, production Batch code and CI
gates are unchanged. Functional-test wave artifacts are not performance evidence.

Focused profile/load-driver checks: 15 passed. Ruff check and format pass on
the changed test. Full collection is now 5,409 tests, exactly one lane each
(3,593 hermetic; 1,395 app; 305 PostgreSQL; 48 Redis; 68 Helm). The five full
local lane results above precede these two new regression cases; remote CI on
the follow-up commit remains required.

Separately reran the unchanged strict CLI profile for `baseline_split_slow`
at its default two slices/second for ten seconds: 20/20 slices, 160/160 items,
zero drops or sampled queue slope, serial answer peak one, 160 dispatch
renewals, and zero leaked caller leases/refreshers. Slice p50/p95/p99 was
344.78/355.71/360.18 ms. Raw samples, summary and item timings are retained under
`docs/project/benchmarks/model-router-main-readiness/batch-split-followup`.
This local mock result does not erase the CI-host saturation result or certify
production capacity.

## Remote CI follow-up: reservation contention test

The next [CI run 34480898409](https://github.com/deltawi/deltallm/actions/runs/34480898409)
at `77b4c5b0` exposed a different timing assumption: PostgreSQL had 304 passes
and one failure when eight identical reservations contended within the
production 250-ms transaction cap. The database log records a statement timeout
on the duplicate's `SELECT ... FOR UPDATE`; the repository correctly returned
`BillingOperationUnavailable`. The idempotency test required all eight callers
to succeed, combining a correctness invariant with shared-runner throughput.

Only that functional test now uses a two-second transaction cap, restored by
its monkeypatch fixture. Production retains its 250-ms cap and caller deadline,
and unit tests still assert that cap and interruption/no-retry behavior. A new
real-PostgreSQL test holds the operation row, expires a duplicate's caller
deadline, and verifies one hold across all five scopes and one capacity slot;
a subsequent replay still returns the original reservation.

Focused locking/repository/operation checks: 28 passed. The complete local
application lane after the Batch test fix also passed: 1,395 tests, no skips.
The full PostgreSQL lane and remote CI are being rerun; the issue's latest
readiness update records their completion status. No production code, migration,
budget semantics, retry behavior or CI gate was changed by either test follow-up.

## Performance evidence

The unchanged `tests.performance.realtime_selector_profile` runs at 10 RPS for
20 seconds per case with a fixed 1 ms mock provider, fake Redis and fake billing.
Baseline is PR 6 `d85df256`, whose tree equals feature parent `46075ba5`;
after is this integrated worktree. Raw summaries and samples are retained in
`docs/project/benchmarks/model-router-main-readiness/{before,after,paired-before,paired-after}`.
They are short shared-host comparisons, not a deployed saturation certificate.

All 16 cases completed 200/200 requests, with no generator drops and zero
sampled queue slope. Provider-call, fake Redis-method and billing-operation
counts match before/after exactly for each corresponding case. Selector-free
cases make zero classifier/billing-operation calls; selected cases make one
classifier call and one answer call per request.

The first sequential comparison was slower after integration. Repeating both
checkouts concurrently on the same host also slowed the unchanged baseline;
matched medians/p95 do not show the earlier broad slowdown. Retain all original
samples, including tail outliers. The matched results below are milliseconds:

| Case | Before p50 / p95 / p99 | After p50 / p95 / p99 |
| --- | ---: | ---: |
| Selector-free nonstream | 10.25 / 13.03 / 27.37 | 10.35 / 13.42 / 30.06 |
| Selected nonstream | 16.31 / 20.19 / 34.34 | 16.29 / 19.41 / 27.21 |
| Selector-free streaming | 12.17 / 15.41 / 18.54 | 11.36 / 15.49 / 33.86 |
| Selected streaming | 17.27 / 22.49 / 34.29 | 16.91 / 20.10 / 21.73 |

Matched p95 TTFT is 11.38 → 11.51 ms selector-free and 18.41 → 16.51 ms
selected. Selector-free streaming p99 remains noisy/worse in this short sample;
this is not statistical latency certification. The first before run overlapped
other local checks, and the matched runs intentionally share host capacity.
Python and the FastAPI/Starlette/Pydantic/httpx/AnyIO versions were identical.

## Remaining approval and qualification

- Remote CI must pass on the published integration head.
- Browser operator/mobile/desktop/keyboard smoke checks remain unverified.
  The current installed browser skill was read and its connection retried,
  but the runtime still imports a missing older `browser-service.mjs`.
  No browser installation or user profile was changed, and component tests
  are not treated as real-browser evidence.
- Representative held-out outputs/prices and deployment-specific mixed-traffic
  qualification remain operator acceptance work in issue #304.
- Before production activation, apply migrations through the coordinated
  release workflow, upgrade API/worker binaries and follow the existing
  selector publish/canary/rollback guidance. For binary rollback, disable and
  drain selected work first; retain durable receipts and checkpoints.

There is no new temporary plan file. This document is retained design and
verification evidence, not a claim that the open acceptance items are complete.

## Independent-selector implementation — 2026-09-10

Implemented locally on `fix/issue-304-independent-selector` in
`.worktrees/issue-304-independent-selector`, from freshly fetched remote feature
`d41b66afe43154181f1c21f02b1ac9b372c2714a`. No push, PR, remote-issue update,
main merge, paid provider call or user-demo modification was performed.

The classifier is now independent of answer membership across file configuration,
durable lifecycle, immutable runtime qualification, cache identity, Chat/Responses,
Batch and the simple guided editor. Existing explicitly configured dual roles remain.
Selector cost and Batch checkpoint ownership are unchanged. New physical-ID dependency
locks protect published references (including disabled groups) against deletion,
invalid metadata and concurrent selector switches. The temporary plan was removed;
lasting decisions and rollback instructions are in the linked design document.

### Automated gates

Local dependencies: frozen `uv.lock`, Python 3.11.13, generated Prisma client,
isolated PostgreSQL 15 on 55438 and Redis 7 on 16383, npm lockfile and Helm CLI.
The original feature worktree remains clean. Commands below use the installed
`.venv/bin/python` / `.venv/bin/ruff` equivalents of `uv run`.

| Gate | Final result |
| --- | --- |
| `pytest --collect-only -qq --dependency-lane-report` | 5,525 tests, exactly one primary lane each |
| `pytest -q -m hermetic --tb=short -rs` | 3,609 passed; local socket permissions used, no skips |
| `pytest -q -m app --tb=short` | 1,474 passed |
| `pytest -q -m postgres --tb=short` | 323 passed |
| `pytest -q -m redis --tb=short` | 51 passed |
| `pytest -q -m helm --tb=short` | 68 passed |
| `ruff check .`; `ruff format --check <touched Python paths>` | Passed |
| Touched-file ESLint; `npm --prefix ui run test:unit` | Zero findings; 256 tests passed |
| `npm --prefix ui run build` | Passed; initial 370.21 KB gzip unchanged, Model Group lazy chunk 26.31 KB |
| `npm --prefix ui run lint` | Existing 118 errors / 4 warnings, unchanged; not a clean full-lint gate |
| `python -m scripts.docs.export_openapi --check` | Current; 230 paths / 298 operations |
| `mkdocs build --strict`; `git diff --check` | Passed |

`uv sync --frozen --extra dev --extra docs --python 3.11` and
`prisma generate --schema=./prisma/schema.prisma` were run.
The canonical Docker build uses Node 20; host UI checks used Node 23.6.1 and emitted
the existing dependency-engine warning. No lockfile/package upgrades were made.

`scripts/verify_migration_paths.py` passed fresh installation, upgrade from
`v0.1.42`, and the shared-feature migration path, including all 90 migrations.
Its temporary databases were cleaned up. The new dependency-plan test uses the
production SQL with 5,000 groups and 20,000 policy revisions: PostgreSQL uses
`deltallm_routepolicy_selector_dependency_idx`, returning one affected group.
One observed execution was 0.97 ms (planning 0.687 ms). PostgreSQL chose to scan the
small group table for its final join, not policy history; this is control-plane
work, never inference admission.

The first broad app run overlapped a Vite rebuild and had seven fixture setup
errors while assets were being replaced. A later run exposed the performance
harness's direct fixture caller after fixture parameterization; that caller was
fixed through a shared harness. The clean complete app rerun above supersedes
both failures. No tests were removed or skipped to obtain that result.

### Reproducible mock performance

Raw samples/summaries are retained under
`docs/project/benchmarks/independent-selector/`. These are fixed local mocks
with synthetic pricing, not real-provider quality or net-savings evidence.

- Realtime: `python -m tests.performance.realtime_selector_profile --output-dir <dir>`,
  repeated with `--independent`, 10 RPS for 20 seconds per case. Initial and
  repeated baseline source runs use feature `d41b66af`; comparison runs use the
  correction. All cases completed 200/200 with zero drops and zero sampled
  in-flight slope. Redis and billing call counts match the baseline exactly.
  Ordinary cases make 200 answer calls; selected cases add exactly 200 classifiers.
- Batch: `python -m tests.performance.batch_selector_profile --cases baseline_microbatch
  selector_balanced selector_safe_default --output-dir <dir>`, repeated with
  `--independent`. Each case completes 20 eight-item slices / 160 items at 16 items/s,
  no drops or queue slope. Redis counts match; selected items retain 320 checkpoint
  writes and 160 receipts, ordinary microbatching retains zero selector/checkpoint
  work. Heartbeat renewals vary with elapsed time (not new per-item I/O).
- Selected nonstream p50/p95/p99: initial baseline 12.46/16.43/17.36 ms,
  correction dual-role 8.71/10.14/10.84 ms, external 8.66/9.72/10.52 ms.
  These short shared-host runs do **not** establish a speed improvement.
- Streaming tails remain noisy. A separate selected-stream pair measured
  10.84/17.27/23.11 ms before and 11.04/26.01/28.28 ms external; p95 TTFT
  14.97 → 21.75 ms. Both retained identical calls, 200/200 success and zero slope.
- Batch balanced p95 was 43.70 → 41.23 ms; safe-default 58.46 → 40.58 ms.
  Ordinary microbatch runs included cold-tail outliers (237.88 ms initially,
  299.76 ms in a repeat). A later unchanged-source control had a comparable
  median (25.17 ms versus correction 24.92 ms), but not that tail. Retain all
  samples; do not dismiss or hide these outliers. Controlled tail-latency
  qualification remains open before claiming production performance readiness.

No new inference SQL/Redis/provider lookup, pool, concurrency allocation or retry
was introduced. Physical inventory is prepared once per generation; external-selector
response identity is hashed off-path. The SQL addition is bounded publication/dependency
coordination, with its own lock deadline, not request routing.

### Final container acceptance and remaining gates

Canonical `docker build -t deltallm-independent-selector:local .` succeeded.
Final tested image:
`sha256:a991f0783ae2fe1a4b82b2b89cca5954e30f97f04b47794df89a1258d73fba5b`.
The unchanged canonical image still defaults to root: non-root release qualification
is a pre-existing gap, not a newly claimed pass.

The checked-in `tests/performance/independent_selector_profile.yaml`,
`independent_selector_mock.py` and `independent_selector_smoke.py` define the
isolated acceptance fixture. A separate migrated `independent_selector_smoke`
database and Redis DB 1 were used; fixture seeding refuses a non-loopback or
differently named database. Migrations ran before startup. The gateway ran on
127.0.0.1:4003, not the user's existing demo port.

Both readiness/liveness were 200. An expiring group-only fixture key exercised
economy, quality, streaming Chat and Responses successfully, with exactly four
classifier calls, two economy answers and two quality answers. Direct calls to
`tiny-selector` returned 403 without another provider call. Public usage was
answer-only; the first smoke's four selector operations were durably settled.
The final image repeated the four cases successfully, then completed graceful
shutdown. Mock/gateway containers are stopped; their logs/data remain recoverable.
The isolated PostgreSQL/Redis test services remain available.

Browser acceptance is still open. The Browser skill was read from the installed
newer plugin and connection attempted; its runtime imports a missing older
`browser-service.mjs`. No browser plugin, profile or user session was modified.
Component tests at 375/1024px and keyboard/focus tests are not a substitute for
full browser publication/history/import/rollback acceptance.

Remote CI, controlled tail-latency qualification, browser verification and the
existing deployment/representative-quality gates remain distinct from completed
implementation and functional test coverage. Do not enable external references
until every API and Batch worker runs this correction.

## Stream-completion accounting correction — 2026-09-10

The subsequent live test exposed a shared streaming bug: clients closing at
`[DONE]` could cancel answer accounting. In outbox mode, the focused correction
durably accepts the existing answer write and required audit before the terminal marker. Content
frames remain streamed immediately. Provider EOF is not required; the existing
response deadline and bounded cleanup owner remain authoritative. See the
[design and limitations](model-router-independent-selector.md#streaming-completion-correction).
No additional provider request, charge event, migration, pool or worker was added.

### Verification after the fix

Commands use the same frozen worktree environment and isolated test services
described above (`.venv/bin/python -m pytest` and `.venv/bin/ruff`).

| Command | Result |
| --- | --- |
| `pytest -q tests/test_stream_accounting_commit.py` | 20 passed, including all four wrappers with/without a selector over real loopback HTTP |
| `pytest -q tests/test_stream_accounting_commit.py tests/test_stream_usage.py tests/test_stream_response.py tests/test_chat.py tests/test_text_endpoints.py tests/router/selection/test_realtime.py` | 141 passed before the final six wrapper cases were added; the 20-case run includes those six |
| `pytest -q -m hermetic --durations=10` | 3,612 passed |
| `pytest -q -m app --durations=10` | 1,488 passed in 584.69 seconds; the final six new cases were separately covered above |
| `pytest -q -m postgres --durations=10` | 323 passed against PostgreSQL 15 |
| `pytest -q -m redis --durations=10` | 51 passed against Redis 7 |
| `pytest --collect-only -qq --dependency-lane-report` | 5,548 collected: 3,612 hermetic / 1,494 app / 323 PostgreSQL / 51 Redis / 68 Helm |
| `ruff check .`; touched Python `ruff format --check` | Passed |

The new regression failed on the original ordering (zero answer writes when
the terminal was sent). Tests also cover blocked/failed accounting, required-audit
failure, cancellation/deadline cleanup, no provider retry/cooldown for write failure,
one charge, and release before post-call hooks. An initial attempt held the
provider permit through post-call hooks; the unchanged existing regression caught
this, and the correction now preserves that ordering. No test was weakened.
Helm/UI were not changed by this follow-up; their preceding results remain dated
evidence, not reruns. Deprecation warnings remain in the Python suites.

Canonical Docker build:
`docker build --build-arg INSTALL_PRESIDIO=false -t deltallm-independent-selector:stream-commit .`.
Tested image: `sha256:8533abe06cf6c93c7a8b7608241af09eea0076f3ee53c99f131a18b97f8efdad`.
The optional Presidio build argument uses the canonical Dockerfile; no dependency
or image-hardening changes are part of this fix. Existing root-image debt remains.

The isolated Docker mock on port 4003 returned 200 for Chat, Responses,
Completions and Messages, closing each client immediately at `[DONE]` or
`message_stop`. PostgreSQL showed exactly one answer event per correlation ID
and a settled selector receipt: each fixture charge was $0.000023 for the answer
and $0.000023 for the selector, with 18 answer tokens. An initial startup failed
because the test dependencies were not attached to the gateway network; attaching
the existing isolated test services resolved it, without source/config changes.

The same image replaced only the local Groq gateway on port 4002; existing
database/Redis data and operator policy were preserved. One bounded live request
closed at `[DONE]` after 1,046 ms (first content 795 ms). Current policy version 9
classified it to MiniMax, not the test script's economy expectation. Both charges
persisted: selector $0.00001146 and answer $0.00016350 for 164 answer tokens,
using the user's illustrative prices. This confirms terminal-close accounting,
not routing-quality or savings acceptance. The test key was expired; expired and
missing credentials returned 401 and direct selector access returned 403.

Historical missing answer charges were **not** synthesized or backfilled.
The earlier timed-out selector remains unknown/pending. Earlier midstream
disconnects without final usage and production load qualification remain separate
work; this fix is not a claim to resolve every incomplete historical operation.
Changes remain local and unpushed.

Review follow-up P2: the public docs now scope durable charge acceptance to
`spend_ingestion_mode: outbox`. The default legacy writer is awaited but may log
and swallow database errors, so its terminal marker is not proof of persistence.
Eight additional application cases cover all four wrappers in both modes with
the real ingestion service and legacy writer/ledger, mocking only persistence
boundaries. They distinguish legacy failure-with-terminal from outbox
failure-without-terminal, and assert one provider call and upstream cleanup.
This documentation/test correction does not change billing execution, defaults,
tenant attribution, retry behavior, dependency counts or latency.

P2 verification: the focused six-file pytest command above now passes **156
tests** (two existing deprecation warnings); the eight new mode-specific cases
also passed separately. Full collection with `--dependency-lane-report` selects
5,556 tests exclusively: 3,612 hermetic / 1,502 app / 323 PostgreSQL / 51 Redis /
68 Helm. Touched-file Ruff checks/formatting, `mkdocs build --strict` and
`git diff --check` passed. No live calls, Docker restart or full dependency-lane
reruns were needed for this documentation/test-only follow-up.

### Streaming dependency and latency evidence

[Raw samples and reproduction instructions](benchmarks/stream-completion-commit/README.md)
retain initial, paired and final checked-harness runs. Every case offered and
completed 200 requests at 10 RPS for 20 seconds, with zero drops and zero sampled
in-flight slope. Provider and Redis command counts match exactly before/after.
Ordinary streams make one answer call; selected streams make one classifier plus
one answer and one each of the existing logical reserve/dispatch/receipt operations.
The fake billing harness does not measure durable SQL calls or commit latency;
the fix reorders/awaits the same writer, with no new query or transaction shape.
Docker checks above independently verify persisted results.

Final paired p50/p95/p99, in milliseconds:

| Stream | Before | After |
| --- | --- | --- |
| Ordinary | 6.27 / 7.42 / 7.75 | 6.52 / 7.48 / 10.47 |
| Independent selector | 9.50 / 10.77 / 11.66 | 9.70 / 10.94 / 11.65 |

Measured p95 TTFT was 5.68 → 6.27 ms ordinary and 9.07 → 9.93 ms selected.
Initial runs were noisier (selected p95 11.05 → 25.36 ms while other checks ran);
all observations are retained. These short shared-host measurements show no
dependency amplification but do not prove zero latency regression or qualify a
production SLO. Required durable commit latency now precedes terminal delivery
by design; it does not precede content delivery and remains under the existing
end-to-end deadline. No concurrency/pool/replica allocation was increased.

## PR #318 CI repair (2026-09-11)

[PR #318](https://github.com/deltawi/deltallm/pull/318) contains the feature and
independent-selector fixes described above. Its first application-lane run had
1,501 passes and one failure: the 150 ms Batch job deadline expired before the
expected provider stage, with zero selector calls. The aggregate `test` check
correctly failed because that lane failed. The documentation job separately
reported two public pages missing from navigation.

The Batch lifetime test now controls wall-clock deadline conversion and advances
the event-loop clock only after the selector or answer has started. It asserts
the exact job-deadline limit, real timer-driven cancellation, caller-lease release,
provider cleanup, no completed item, and retention of an already-paid selector
receipt. Scheduler turns are bounded and the test owns/drains its execution task.
Both previously unnavigated pages are now in the Project navigation.

This is a test/documentation-only correction. Runtime deadlines, billing, tenant
scope, retries, dependency calls, CI timeouts, lane selection and aggregate gates
are unchanged. Test-only fault injection confirmed failures when the job-deadline
limit was removed (all four cases) and when the deadline was not propagated to
answer execution (both answer cases); no injected code remains in the worktree.

Local commands use `UV_CACHE_DIR=/tmp/deltallm-readiness-uv uv run --no-sync`:

- `pytest -q tests/test_dependency_lanes.py tests/batch/test_selector_lifetime.py
  --tb=short`: 31 passed.
- `pytest --collect-only -qq --dependency-lane-report`: all 5,556 tests still have
  exactly one primary lane, with unchanged counts listed above.
- `pytest -q --confcutdir=tests/docs tests/docs`: 9 passed.
- All three generated-reference `--check` commands, `report_health.py --check`,
  `mkdocs build --strict`, and `verify_public_site.py`: passed. All 89 public
  Markdown pages are navigated; there are no missing H1s or images.
- `ruff check .`, `ruff format --check tests/batch/test_selector_lifetime.py`,
  and `git diff --check`: passed.

## PR #318 CI repair: capacity rollback regression (2026-09-11)

[CI run 34531046325](https://github.com/deltawi/deltallm/actions/runs/34531046325)
passed app/docs but failed entering the capacity test's first reservation transaction,
before insertion or capacity rejection. The aggregate `test` correctly failed too.

A diagnostic 300 ms startup delay reproduced this failure under the production 250 ms
cap; the sanitized CI exception cannot establish its exact underlying cause. The
functional test now uses a test-local two-second cap, as the idempotency test does.
It verifies all five temporary holds inside the second transaction and an empty
capacity-update result before checking rollback, excluding unrelated failures.
The same delayed-start diagnostic passes; no injection code is committed.

A new hermetic test expires the real startup timeout and verifies the unchanged
250 ms application/pool/transaction budgets, cancellation, no retry and no writes.
Runtime code, financial/tenant semantics, migrations and CI gates are unchanged.

Verification uses the frozen-environment prefix above and isolated PostgreSQL 15:

- `pytest -q -m postgres --durations=25 --durations-min=0.5`: 323 passed (four existing warnings).
- `pytest -q -m hermetic --durations=25 --durations-min=0.5`: 3,613 passed; focused billing checks: 29 passed.
- Classifier tests: 15 passed; full lane collection: 5,557 tests, exactly one lane each.
- Ruff, touched-file formatting, documentation health/strict build/containment and diff checks passed.
