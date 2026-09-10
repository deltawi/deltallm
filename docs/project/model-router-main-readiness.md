# Model router: feature-to-main integration readiness

Date: 2026-09-10. Integration parents: feature `46075ba5` (all six PRs)
and main `35ed9843` (provider expansion). This record does not approve or
perform a merge to main, deployment, or representative quality/savings acceptance.

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
