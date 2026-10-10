# Issue 320: upgrade results and merge status

Historical results through 9 October 2026. Original PR target: `main`.
Original source branch: `codex/issue-320-main-integration`.

The upgrade and the request-identity fix are now merged. All required PR checks
passed for [PR #351](https://github.com/deltawi/deltallm/pull/351), merged at
`0c622b47`. Native qualification and the declared database fixture remain under
test. Use the complete qualification report to assess the corrected release.
The older results below are not a certificate for the current release image.

## Fixed-candidate checks on 10 October 2026

The corrected candidate uses source `ee15393d0731e8e9c76453dae5d7b24a3c16249c`
and image ID
`sha256:2bf9f8d025aa4f1c7b1a56c0b308c9cd560a706ed7305f61dad751aa26f28d1e`.
One unchanged image ran all four short stages and all four ten-minute stages.
The fixture used a six-CPU, 12-GiB Linux arm64 VM with two kind nodes, four API
processes, two accounting request processes, and one reporting process.
PostgreSQL, Redis, the immediate synthetic provider, monitoring, and the generator
shared the VM. Response-cache bypass remained enabled.

All four 30-second stages passed. The ten-minute results are:

| Rate | Successful / scheduled | p95 / p99 | Active-request growth per second | Exact accounting | Result |
| --- | ---: | ---: | ---: | --- | --- |
| 50 RPS | 30,000 / 30,000 | 21.09 / 23.88 ms | +0.000023 | Passed | Passed |
| 100 RPS | 60,000 / 60,000 | 21.37 / 24.09 ms | +0.000241 | Passed | Passed |
| 200 RPS | 120,000 / 120,000 | 25.98 / 36.60 ms | +0.000307 | Passed | Passed |
| 500 RPS | 300,000 / 300,000 | 168.85 / 333.44 ms | +0.045218 | Passed | Latency and growth limits exceeded |

Every stage had zero HTTP errors and zero dropped arrivals. Native facts and all
four budget scopes agreed, and financial work drained. The fixed candidate now
has sustained evidence at 50, 100, and 200 RPS. It does not yet have a passing
sustained 500 RPS result. The unchanged limits are p95 at most 150 ms, p99 at most
300 ms, and growth at most +0.01 active requests per second.

The runner stopped before process-loss and Helm-rollout recovery because the
500 RPS performance gate failed. A fresh eight-CPU diagnostic then passed with
the same application image and limits:

| Rate and duration | Successful / scheduled | p95 / p99 | Active-request growth per second | Exact accounting | Result |
| --- | ---: | ---: | ---: | --- | --- |
| 500 RPS, ten minutes | 300,000 / 300,000 | 65.42 / 91.27 ms | +0.001150 | Passed | Passed diagnostic |

The VM still used 12 GiB RAM. The only capacity change was six to eight CPUs.
There were no HTTP errors or dropped arrivals, and accounting drained. This
supports the CPU-headroom diagnosis. It is not an application optimization or
a release certificate. The database was fresh, and only the upper tier ran.
The first complete eight-CPU attempt stopped after its 30-second ladder. The
200 and 500 RPS stages failed the growth limit, although all requests succeeded,
latency passed, and accounting was exact. At 200 RPS, sampled live requests
varied from three to five. A small change over this short window produced a
slope of +0.038261/second. This is not evidence of a financial backlog.

The supported 60-second short-stage repeat also failed at 500 RPS. It had
30,000 successful requests and exact charges, but latency increased slightly
within that short window. That failed decision remains unchanged.

### Growth measurement correction

The old growth series sampled live requests at one instant each second. The
new series uses the exact time spent by every request in each one-second
interval. The slope still uses the middle 80 percent of the arrival window and
the same +0.01/second limit. Peak concurrency, latency, dropped-arrival checks,
financial checks, and stage stop rules remain unchanged.

Saved-record diagnosis found sampling errors in the first 30-second eight-CPU
attempt: 200 RPS gave +0.038261/second from instant samples but +0.003908 from
all lifetimes; 500 RPS gave +0.057391 but +0.006455. The 60-second failure still
fails with the new method: +0.068595/second. The six-CPU sustained failure also
still fails: +0.049627/second, with failed latency limits. Regression tests check
that the method rejects small real growth and does not create growth from
a sampling-phase change.

These calculations diagnose the measurement. They do not change past pass/fail
records or create a release certificate. The first new-method 30-second series
passed 50, 100, and 200 RPS, but failed 500 RPS growth at +0.089038/second.
All 15,000 requests succeeded, p95 was 54.05 ms, p99 was 82.12 ms, and accounting
was exact. That failed decision remains unchanged.

The next normal series has a fixed 60-second warm-up at the same rate before
each measured stage. Warm-up samples and exact financial checks are retained
separately. The initial 50, 100, and 200 RPS stages remain 30 seconds. The initial
500 RPS observation is extended to 600 seconds. The final four stages remain
600 seconds each. There is no cooling pause inside any measured stage. Between
stages, the runner saves samples and requires accounting to drain. All limits
and cold-start functional checks remain active.

This longer observation is necessary because short slices of the passing
eight-CPU ten-minute diagnostic gave both positive and negative slopes. Even
its last 60 seconds gave +0.052789/second, although the full time-weighted slope
was +0.001844/second. Warm-up alone does not prove stable capacity. The new
complete series and native recovery checks are still required.

The first full-window warm-up series failed its initial 500 RPS growth check.
It had 300,000/300,000 successes, exact charges, p95 115.03 ms, p99 270.27 ms,
and growth +0.034164/second. Accounting drained in 0.80 seconds. Mean latency
rose from 46.80 ms in the first minute to 101.88 ms in the last minute. The
final four stages and native recovery did not run. Full CI passed on that
harness, `d7ef36f8`. This is still a failed local qualification.

A separate larger-database trial went directly to 500 RPS after functional
checks. It failed during preparation: 22,333/30,000 successes, 2,601 HTTP 503
responses, 346 client timeouts, and 4,720 dropped arrivals. PostgreSQL recorded
lock and statement timeouts, followed by terminal replay conflicts. There were
306 unresolved operations after the drain deadline, but no unsafe budget
windows. The measured ten-minute stage and recovery did not run. The trial is
not an established fix and remains failed evidence.

The next comparison uses the normal gradual preparation and a declared native
database fixture: four-CPU and four-GiB limits, 512-MiB shared buffers, and a
four-GiB WAL limit. It keeps the 1,000-connection bound, `fsync`, synchronous
commit, full-page writes, and autovacuum enabled. The runner verifies and saves
those live settings before load. Application replicas, limits, deadlines,
financial controls, and performance gates remain unchanged. This test allocation
is not a production minimum or proof that the slowdown is fixed.

The normal preparation comparison passed all four initial stages. The initial
ten-minute 500 RPS check had 300,000/300,000 successes, p95 82.15 ms, p99 145.51 ms,
and growth +0.001469/second. Exact accounting and complete drain passed. It used
the unchanged verified image `88e5a59c` and the declared four-CPU, four-GiB
PostgreSQL fixture in the eight-CPU VM. This is a better steady-state result than
the six-CPU series, not an application optimization or a production minimum.
The final sequence on the same retained database did not pass:

| Rate | Successes / arrivals | p95 / p99 | Growth / second | Decision |
| --- | --- | --- | --- | --- |
| 50 RPS | 30,000 / 30,000 | 21.97 / 25.59 ms | -0.000015 | Passed |
| 100 RPS | 60,000 / 60,000 | 22.30 / 26.09 ms | +0.000028 | Passed |
| 200 RPS | 120,000 / 120,000 | 25.74 / 42.40 ms | -0.000161 | Passed |
| 500 RPS | 298,734 / 300,000 | 276.52 / 592.63 ms | +0.028375 | Success, latency, and growth limits exceeded |

The final 500 RPS stage had 1,265 HTTP 503 responses and one client read error.
Of the 503 responses, 1,262 reported required persistence as unavailable.
No arrivals were dropped. Accounting drained with no pending or unsafe state.
The observed fact count and charge delta agree with the successful requests and
the precheck; all four scopes agree. The official economic gate remains failed
because not all requests succeeded. Do not treat that observation as a pass.

The subsequent maintenance check failed because PostgreSQL could not enlarge a
shared-memory segment to 67,145,728 bytes. The test database had no declared
`/dev/shm` allocation. Native process-loss and rollout recovery were not reached.
The next fixture mounts and verifies a 256-MiB memory-backed `/dev/shm` volume
within the unchanged four-GiB database limit. This corrects a proven maintenance
resource fault. It is not yet a proven fix for the persistence errors. Bounded
database and gateway logs are retained before cluster cleanup on the next run.

The runtime image and performance code remained fixed throughout the sequence.
Only ordinary regression tests and documentation changed during the run. Stage
commit metadata differs; the saved source comparison confirms that the runtime,
migrations, chart, generator, and performance harness did not change.

### Functional readiness

RPS is only one release check. The fixed candidate must also preserve the
application's supported features and its financial and access controls.

| Area | Current evidence | Remaining release check |
| --- | --- | --- |
| Client request IDs, complete streams, and native reporting | 24 installed-image completions passed, including four complete streams and omitted or invalid headers; charges were exact and all seven processes were ready | Repeat after process loss and Helm rollout |
| Installed authentication, input validation, model visibility, and UI delivery | All 16 checks passed across the four API processes | Repeat after process loss and Helm rollout |
| Application behavior, including streaming and access checks | 1,696 application tests passed locally | Keep all final application CI checks passing |
| Database behavior, budgets, permissions, and reporting | 1,148 PostgreSQL tests passed; all five opt-in cases passed separately; both latest CI shards passed | Keep all final database CI checks passing |
| Realtime clients | All four pinned official SDK cases passed locally | Keep final Realtime CI checks passing |
| Redis limits and failure policies | 256 tests passed, including separate memory-pressure services | Keep all final Redis CI checks passing |
| Install and upgrade migrations | Clean install, last-release upgrade, shared-feature upgrade, and model-identity recovery passed | Apply the forward reporting migration before the corrected image |
| Container lifecycle and accepted-work recovery | Offline non-root startup and bounded exit passed; the lifecycle comparison recovered one accepted charge exactly once | Complete native process-loss and Helm-rollout recovery |
| Helm configuration and packaged UI | All 275 Helm tests and the UI CI job passed | Keep all final chart and UI CI checks passing |

The full local database group in CI order passed: 504 passed and one opt-in case
skipped. All 33 affected tests also passed with the smaller CI Prisma pool.
Earlier CI reporting-startup failures did not reproduce in these checks. The
latest complete CI run passed all required jobs, including capacity and recovery.
The cause of the earlier failures is not proved. Bounded test diagnostics remain
in place. Full CI also passed for the stronger installed-image checks, the
time-weighted measurement, and the warm-up change. Final CI for the declared
database fixture and complete eight-CPU qualification are still required
before release approval.

Main's post-merge CI run `38070825615` has one failed routing-cost query-plan
check. PostgreSQL selected a bitmap heap scan; the test requires a plain index
scan. The follow-up check retains the exact required indexes and row and loop
bounds. It also checks bounded blocks and rejects lossy scans, large index
probes, and history filtering. It must pass with both the default planner and
an explicit bitmap plan. All other required main CI jobs passed. This failure
still blocks release approval until the follow-up checks pass.

The first follow-up default-plan case passed. Its forced bitmap case returned
one visible row from three physical tuple versions left by reservation and
dispatch. The check now bounds physical probes separately while keeping the
one-visible-row, exact-index, loop, and block limits. PostgreSQL shard 0 also had
one external-customer permission check return unavailable (503), not denial
(403). No unauthorized change was accepted. Test-only diagnostics now preserve
the cause class, bounded Prisma code, and elapsed time without session tokens.
The expected denial and all application deadlines remain unchanged. These
failures remain in the saved evidence; the next complete CI result is required.

The synthetic provider does not validate a real provider's quotas, service
availability, model behavior, or network delay. Qualify the actual production
workload before making a production capacity claim. Do not promote the unchanged
`v0.3.1` image: it still has the missing-request-ID reporting defect.

For production sizing, provider quotas, role separation and required release
checks, use [Production requirements for RPS targets](../deployment/production-rps-requirements.md).

## Historical status before the release checks

The upgrade includes bounded admission and dependency capacity, native financial
accounting, recovery and reporting, deployment role separation, and provider
connection-pool improvements. Legacy execution remains available. The
[concrete measure list](issue-320-50-to-500-rps-measures-and-integration.md) describes
the implementation steps. The [accounting runbook](../deployment/accounting-v2.md)
defines migration, activation and rollback.

**Full four-tier qualification is not complete.** Earlier 50, 100 and 200 RPS
short stages passed. The latest provider-pool image passed a 30-second 500 RPS
confirmation. Its 1,000 RPS diagnostic failed. These results use different source
images and do not form one unchanged-image release certificate.

Main revision `14cf7871` is integrated. The merge keeps output-token limits,
external customer sign-in, model identity migration recovery and the newer admin
UI. It also keeps the upgrade's bounded admission, native accounting and owned
client lifecycles. Authentication uses the revocation-safe cache with one bounded
fallback owner. The shared capacity report includes the external-auth database
pool and engine. Applied migration files remain unchanged.
The PR remains a draft until the release checks and unchanged-image qualification
are complete. No RPS test was rerun for this merge.

## Current-main integration checks

Checks used Python 3.11.13, PostgreSQL 15 and Redis 7 on isolated test services.
The collection contains 9,940 tests in five dependency lanes.

- Isolated tests: 6,586 passed. Application tests: 1,685 passed.
- Redis: 255 passed in the full lane. The separate memory-isolation test passed
  with two dedicated Redis servers. Helm: 275 passed.
- PostgreSQL: the first full lane had 1,112 passes, 24 skips, one failed auth
  error-mapping check and one query-engine startup error. After the mapping fix,
  all affected allocation and checkpoint tests passed. All 24 skipped cases
  passed with Redis, the pinned official Realtime client and the opt-in external
  auth profile configured. All 1,138 PostgreSQL cases are covered across these runs.
- Fresh install, last-release upgrade, shared-feature upgrade and the known model
  identity recovery path passed. Migration history matches all 165 source migrations.
- UI: 357 unit tests and the production build passed. The changed UI files pass
  lint. Full UI lint reports 65 errors and three warnings in files that are
  byte-identical to main; those findings are not fixed by this merge.
- Ruff, changed-file formatting, configuration reference, structure checks,
  strict documentation build and public-artifact checks passed.

The container build could not complete because the local Docker VM could not
resolve the package download hosts. Non-root image startup, image health and image
termination checks remain pending. The merge diff also retains two whitespace
findings from unchanged main files, including one applied SQL migration. No applied
migration was rewritten to remove whitespace. CI and exact-image qualification
must pass before release.

## Earlier fixed-image 50–500 RPS results

These rows use application revision `7d4fbe713e4ae1fa295ce7af2ccd9e87a2ada62f`.
Each row had zero errors, zero dropped arrivals and correct accounting.
The source and image identities are retained in the archived complete stage index.
Use the [restore procedure](issue-320-rps-reproduction.md#restore-historical-records).

| Rate | Duration | Successful / scheduled | p95 / p99 | Result |
| --- | ---: | ---: | ---: | --- |
| 50 RPS | 60 s | 3,000 / 3,000 | 22.37 / 28.84 ms | Passed short stage |
| 100 RPS | 60 s | 6,000 / 6,000 | 21.81 / 25.71 ms | Passed short stage |
| 200 RPS | 60 s | 12,000 / 12,000 | 24.40 / 34.84 ms | Passed short stage |
| 500 RPS | 60 s | 30,000 / 30,000 | 69.37 / 103.80 ms | Active-request growth limit exceeded |
| 500 RPS, SQL sampler | 600 s | 300,000 / 300,000 | 79.45 / 116.55 ms | Active-request growth limit exceeded |
| 500 RPS, explicit data volume | 600 s | 300,000 / 300,000 | 87.18 / 139.12 ms | Active-request growth limit exceeded |

The two long 500 RPS slopes were +0.012308 and +0.012879 active requests/second.
The unchanged limit is +0.01. This is a small stability failure, not a throughput
collapse. It remains a failed gate. No ten-minute 50, 100 or 200 RPS stage ran on
this image because the normal runner stopped after its short-stage failure.

The archived index covers all 87 earlier stages, including failures. Its readable
index, source identities and checksums are preserved at the original Git revision.
The cleanup changes storage location, not any recorded result.

## Latest provider-pool diagnostics

These are 30-second tests on a dedicated six-CPU, 12-GiB Linux arm64 VM.
They used kind v0.31.0, four API processes, two accounting request owners and one
projection owner. The workload is immediate synthetic, one-token, non-streaming
provider replies with response-cache bypass. It is not a production or competitor
comparison.

| Run | Successful / intended | HTTP errors | Dropped arrivals | p95 / p99 | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Pre-change 500 RPS baseline | 15,000 / 15,000 | 0 | 0 | 96 / 133 ms | Passed short checks |
| First adapter, 500 RPS | 15,000 / 15,000 | 0 | 0 | 154 / 309 ms | Latency limits exceeded |
| Revised adapter, 500 RPS first run | 15,000 / 15,000 | 0 | 0 | 90 / 113 ms | Growth limit exceeded |
| Revised adapter, 500 RPS confirmation | 15,000 / 15,000 | 0 | 0 | 79 / 115 ms | Passed all short checks |
| Revised adapter, 1,000 RPS | 18,499 / 30,000 | 63 HTTP 503 | 11,438 | 1,927 / 2,086 ms | Failed diagnostic |

The 1,000 RPS latency numbers describe successful requests only. The generator
started 18,562 requests, reached its unchanged 1,000-in-flight bound and dropped
the remaining arrivals. Do not treat successful throughput in this overloaded
run as a measured capacity ceiling.

The first revised 500 RPS slope was +0.11696; the confirmation slope was -0.00565.
Both runs completed all requests and drained financial work in about 12.13 seconds.
No original failed check was changed. Two short runs do not establish ten-minute
stability or a statistical gain over the baseline.

### Remaining 1,000 RPS bottleneck

The shared VM was 99.11% busy over the captured 27.48-second interval. API processes
used about 3.46 CPU cores. PostgreSQL used 0.73, accounting roles 0.35 and Redis 0.08.
The rest includes the generator, Kubernetes and diagnostic work; generator CPU
was not measured reliably. No CPU profiler ran in this test.

Redis server EVAL execution averaged 39 microseconds, while successful client
round trips averaged 38.15 milliseconds. Redis recorded 165 full-allocation
outcomes, 11 acquisition deadlines and 140 cancellations. These are dependency
events, not one-to-one counts of the 63 HTTP errors. Provider health stayed healthy.
The exact source of each generic 503 was not retained.

CPU and event-loop saturation are the strongest evidence. The next diagnosis
must profile the revised API path and separate application cost from test-fixture
overhead. A larger Redis pool is not an established fix.

Financial work drained in 12.12 seconds. There were no unresolved operations,
open grants, pending projections or unsafe budget windows. Native facts and all
four committed scopes agree at `0.129500`, including the precheck. Full economic
qualification still failed: charge-count and amount gates are evaluated only for
an all-successful workload. This is not an observed charge mismatch.

### Tested source identity

- Snapshot commit: `90e72985a4d8115f0b602c72373716e17a0b44ed`.
- Runtime source hash: `2d6832d719666d22f4c49b09458f6f6a84bb5ba0848c9300bc35c72c5a491031`.
- Image: `deltallm:http-pool-thin-20261008`.
- Image ID: `sha256:35f49c222fc4b4a7e4b8c82ffe420ab84752336d3489f436da78f3ae61ecbdc5`.
- 1,000 RPS run: `bf8218635029443880182caefd92115c`.

The final adapter previously passed 583 affected provider/bootstrap/deadline tests
and all 1,650 application tests. The first adapter passed 7,677 combined application
and hermetic tests. Those counts describe their recorded source checkpoints,
not integration with current main.

## Evidence and cleanup

Fresh PR-preparation checks on the saved branch passed:

| Check | Result |
| --- | --- |
| Full hermetic lane | 6,029 passed; 3,037 deselected; 6 warnings |
| Provider transport regressions after formatting | 26 passed |
| Ruff and format checks for the saved commit's Python changes | Passed; 57 files |
| Frozen lock consistency | Passed; 105 packages |
| Generated configuration reference | Current; 413 fields |
| Revised source bundle and four source archive checksums | Passed |
| Runtime fingerprint | Matches the revised adapter's measured runtime |

The hermetic command was
`python -B -m pytest -p no:cacheprovider -m hermetic -q --tb=short --maxfail=1`.
It used the isolated Python 3.11 review environment and saved its complete log.
Application, PostgreSQL, Redis, Helm and migration checks have earlier evidence,
but were not repeated for this documentation cleanup. CI and combined-main checks
remain pending. The full base-to-head whitespace check reports only eleven known
extra EOF blank lines in applied SQL migrations; their bytes are preserved.

Large raw evidence remains local and is not part of the PR:

- `artifacts/issue320-evidence-20261007/`: original 87-stage history and checksums.
- `artifacts/native-reviewed-1000rps-6cpu-12g-60s-20261008/`: earlier reviewed-image failure.
- `artifacts/accounting-proof-20261008/`: before/after accounting-copy measurements,
  passing short 500 RPS stage and failed 1,000 RPS stage.
- `artifacts/http-pool-20261008/`: baseline, first-adapter and revised-adapter runs,
  raw samples, financial snapshots, source bundles, image identities and test logs.
- `artifacts/pr-main-preparation-20261009/`: pre-cleanup source archive, full earlier
  report and reproduction guide, unfinished merge patch and fresh PR checks.

The earlier full reports and source archive are recoverable locally and in Git
history. Obsolete upgrade plans and host-only wrappers were already removed.
Current tests, migrations, architecture decisions, deployment guides and reproduction
tools remain. Historical samples used by regression tests are stored unchanged in
one compressed test fixture. Older stage indexes and design notes are archived. The cleanup does not erase failed
results or change acceptance limits.

See the [reproduction guide](issue-320-rps-reproduction.md) for source restore,
image checks and load commands. New results must record their own exact source and
image identities. Before release, verify the combined auth and output-token
contracts, pass database and migration checks, then run all four rates
on one unchanged image under the normal qualification schedule.

## PR scope cleanup

The folder reorganizations are preserved on the separate package-layout branch.
This upgrade keeps the pre-reorganization import layout. Six stage-specific design
notes are consolidated in [Concurrency runtime design](../design/concurrency-runtime.md).
Test-only accounting adapters now live under tests, with their coverage unchanged.
Three unreferenced historical diagnostic commands and the old measurement files
are archived. Financial repair commands and all migration bytes remain unchanged.

The archive is recoverable from Git revision
`abd09e5ff33d102bcb65f78fd288617f260331cd`. The local copy is
`artifacts/pr-cleanup-20261009/historical-materials.tar.gz`.
No RPS test was rerun for this cleanup. Earlier results still do not qualify the
new source or its integration with current main.
