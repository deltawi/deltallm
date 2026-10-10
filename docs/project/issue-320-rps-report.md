# Issue 320: upgrade results and merge status

Historical results through 9 October 2026. Original PR target: `main`.
Original source branch: `codex/issue-320-main-integration`.

The upgrade is now merged. The current release checks are in
[PR #351](https://github.com/deltawi/deltallm/pull/351). That PR records one fixed
candidate image, full CI, ordinary native client requests, the sustained four-tier
series, and recovery checks. Use its final report to assess the corrected release.
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
500 RPS performance gate failed. An eight-CPU capacity check is in progress with
the same image and limits. It is a diagnosis, not a release certificate.

### Functional readiness

RPS is only one release check. The fixed candidate must also preserve the
application's supported features and its financial and access controls.

| Area | Current evidence | Remaining release check |
| --- | --- | --- |
| Client request IDs and native reporting | 20 ordinary installed-image requests passed, including omitted and invalid headers; all seven processes were ready; charges were exact | Repeat after process loss and Helm rollout |
| Application behavior, including streaming and access checks | 1,696 application tests passed locally | Keep all final application CI checks passing |
| Database behavior, budgets, permissions, and reporting | 1,148 PostgreSQL tests passed; all five opt-in cases passed separately | Resolve the repeated reporting-startup failure in CI |
| Realtime clients | All four pinned official SDK cases passed locally | Keep final Realtime CI checks passing |
| Redis limits and failure policies | 256 tests passed, including separate memory-pressure services | Keep all final Redis CI checks passing |
| Install and upgrade migrations | Clean install, last-release upgrade, shared-feature upgrade, and model-identity recovery passed | Apply the forward reporting migration before the corrected image |
| Container lifecycle and accepted-work recovery | Offline non-root startup and bounded exit passed; the lifecycle comparison recovered one accepted charge exactly once | Complete native process-loss and Helm-rollout recovery |
| Helm configuration and packaged UI | All 275 Helm tests and the UI CI job passed | Keep all final chart and UI CI checks passing |

The full local database group in CI order passed: 504 passed and one opt-in case
skipped. All 33 affected tests also passed with the smaller CI Prisma pool.
These passes do not remove the repeated CI reporting-startup failure. That
failure and the sustained 500 RPS gate still block release readiness.

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
