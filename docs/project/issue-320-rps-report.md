# Issue 320: RPS results and 1,000 RPS diagnostic

50–500 RPS results: 7 October 2026. Proof-copy comparison and 1,000 RPS diagnostics: 8 October 2026.
Future PR target: `feature/issue-320-concurrency`.

## Result

The latest proof-copy change passed a 30-second 500 RPS diagnostic, with all
15,000 requests successful. Its 30-second 1,000 RPS diagnostic failed, including
the accounting drain. See the proof-copy comparison below. These short tests do
not complete ten-minute qualification or replace the earlier results.

The earlier measured image (`7d4fbe71`) passed the 60-second stages at 50, 100, and 200 RPS.
It also served all requests in two 600-second tests at 500 RPS, with no errors
or dropped arrivals and correct accounting. Both long tests failed only the
active-request growth limit. **Full four-tier qualification is not complete.**

The new reviewed image failed a 60-second 1,000 RPS diagnostic. It returned
17,870 successful responses from 60,000 intended arrivals. Routing Redis access
reported pool-full and acquisition-timeout errors. One financial operation
remained provisional after the drain deadline. The complete result is below.

The 7 October index covers all 87 earlier saved gateway stages in this worktree.
The [complete stage index](evidence/issue-320-20261007/all-runs.md) includes every
pass and failure. Its [JSON file](evidence/issue-320-20261007/all-runs.json) records
source and image identities, duration, main gate decisions, and original checksums.
Setup failures with no arrival stage are not counted as RPS results.

## New reviewed-image 1,000 RPS diagnostic

The computer could run the diagnostic, but this deployment did not sustain
1,000 RPS. This was one 60-second gateway stage, not ten-minute qualification.
No lower tier was repeated. No application code, resource limit, or acceptance
gate was changed for this test. Only the test harness was extended to accept an
explicit, short-only 1,000 RPS diagnostic. Its 72 affected tests and Ruff checks
passed. The normal 50/100/200/500 schedule is unchanged.

| Measurement | Result |
| --- | ---: |
| Intended arrivals | 60,000 |
| Requests sent | 37,320 (622 RPS) |
| Successful responses | 17,870 (about 298 successful responses/second) |
| HTTP 503 responses | 19,448 |
| Client read errors | 2 |
| Dropped arrivals | 22,680 (37.8% of intended arrivals) |
| Successful / intended arrivals | 29.78% |
| Response latency p95 / p99 | 2,955 / 3,752 ms |
| Maximum client in-flight requests | 1,000, the unchanged generator limit |
| Accounting drain | Failed after 181.415 seconds; one provisional operation |
| Overall result | Failed diagnostic; not release-eligible |

Latency includes all completed attempts, including error responses. Successful
response rate is not a measured capacity ceiling: this was an overloaded,
partly dropped workload. The provider-only generator proof delivered all 10,000
requests at 1,000 RPS for ten seconds with zero errors or drops. That proof shows
the generator can produce the rate; it does not qualify the gateway.

### Failure evidence

- Of the 503 responses, 19,245 reported `no_healthy_deployments`, 77 reported
  `auth_fallback_unavailable`, and 126 were unclassified HTTP errors.
- Gateway logs show routing state entering unavailable mode after `Redis
  allocation is full` and `Redis acquisition deadline exceeded`. Sampled critical
  occupancy reached the per-API limit of 64; one API had 54 waiters. The captured
  metric delta counted 246 full-allocation and 34 acquisition-deadline events.
  This is the first clear failure point, not proof of the underlying cause of
  the slower Redis access. Mean measured client Redis round-trip time was
  27.32 ms; that includes client/network/server delay and is not Redis server
  execution time alone.
- The core Redis command-count budget passed at about 5.04 round trips per
  observed request, below the unchanged 6.01 limit. Reducing command count had
  helped, but does not by itself prevent pool and latency failures at this load.
- Recorded application and dependency counters showed no container CPU
  throttling. Sampled API memory peaked near 409 MiB against a 1 GiB limit;
  PostgreSQL peaked near 446 MiB. These observations do not prove that the host,
  network, or an individual process has spare capacity at sustained 1,000 RPS.
- The generator reached its 1,000 in-flight limit and dropped arrivals.
  Throughput, success, p95, and p99 checks failed. Some metrics scrapes also
  failed, so metric-derived totals cover only their observed intervals. Raw
  client counts above retain every completed attempt.
- The active-request slope was negative, so its isolated growth check passed.
  This is not a stability pass: arrivals were dropped and routing was rejecting
  work. The overall test failed.

### Accounting result

The native facts contain 17,871 charges: the 17,870 successful load requests plus
the successful precheck. Their total is exactly `0.125097`, and committed amounts
in all four budget scopes match those facts. No unsafe budget window, open
grant, pending terminal journal entry, pending report partition, or legacy spend
row remained.

One operation remained provisional, with an `uncertain` completed journal and
`service_unavailable` uncertainty class. Each applicable scope retained
`0.024582` provisionally. The strict financial and drain gates therefore failed;
this is not a claim of complete reconciliation. Successful-request charge checks
are intentionally unscored in the saved failed-workload report. No money or
operation state was manually changed to force a pass.

### Source, environment, and reproduction

- Image build checkpoint: `8fb9a035c75b89aa06279ce70406372648118c4c`.
- Clean harness checkpoint: `b6f3cab423fa13bcd7994790aa8db5225756dee1`.
  Only test files changed between these checkpoints; application source is identical.
- Image: `deltallm-native:issue320-reviewed-1000-8fb9a035`.
- Image index: `sha256:0f46ec1b8c8f1d4cbb22660cc15e138c89e6ab08a9ffae8d7ef4207b4bc2d8e4`.
- Linux arm64 manifest: `sha256:e3b4b0b059006bf271c4539f58fdf7e0373ccfb9d156ece52056f8b3159debb8`.
- Runtime source fingerprint: `2a60a7563c5b903a09066a5d4ae1d461ac49c9ded883216a5f079d70664e32aa`.
- Host: Apple M3 Max, 14 CPU cores, 36 GiB RAM.
- Dedicated Docker VM: 6 CPUs, 12 GiB declared RAM, Linux arm64, Docker 29.5.2.
  This differs from the earlier 8-CPU/6-GiB VM; do not treat the rows as a
  controlled old-versus-new performance comparison.
- Pinned kind v0.31.0, two nodes, four API processes, two accounting request
  processes, and one projection process. The workload and all Pod limits match
  the canonical native fixture. All reviewed fixes and additive migrations
  through the parent-lock migration are in the tested application image.

Local evidence is in
`artifacts/native-reviewed-1000rps-6cpu-12g-60s-20261008/`.
The actual gateway result is `run2/short-1000rps/qualification.json`, with
`run2/results.json`, compressed raw samples, metrics, resource counters, strict
financial snapshots, failure evidence, and final cluster logs alongside it.
The folder also contains complete source Git bundles and archives, image build
provenance, all five passed image checks, and `reproduce.md` with exact commands.
The [reproduction guide](issue-320-rps-reproduction.md) explains the new source
bundle and diagnostic command.

Setup failures are preserved separately: the first build failed on VM DNS; the
first cluster attempt passed the provider proof but stopped at an inner test
runner's old 500 RPS validation before gateway arrivals. Neither is counted as
a gateway RPS result. Both owned clusters were removed. The temporary resolver
file was restored, the VM's saved settings and the global Docker context were
unchanged, and no Rancher or unrelated workload was stopped.

## Earlier fixed-image 50–500 RPS results

All rows below use image revision `7d4fbe71`. Every row had zero request errors
and zero dropped arrivals. Every row passed accounting reconciliation and drain.
Values are rounded for display; the JSON index keeps the original numbers.

| Test | RPS | Seconds | Successful / scheduled | p95 ms | p99 ms | Active-request growth / s | Result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Normal short ladder | 50 | 60 | 3,000 / 3,000 | 22.37 | 28.84 | +0.003040 | PASS |
| Normal short ladder | 100 | 60 | 6,000 / 6,000 | 21.81 | 25.71 | +0.001411 | PASS |
| Normal short ladder | 200 | 60 | 12,000 / 12,000 | 24.40 | 34.84 | -0.006730 | PASS |
| Normal short ladder | 500 | 60 | 30,000 / 30,000 | 69.37 | 103.80 | +0.068335 | Growth limit exceeded |
| SQL-cost repeat 1 | 500 | 60 | 30,000 / 30,000 | 75.23 | 117.84 | +0.070506 | Growth limit exceeded |
| SQL-cost repeat 2 | 500 | 60 | 30,000 / 30,000 | 79.61 | 116.79 | +0.105515 | Growth limit exceeded |
| SQL-cost repeat 3 | 500 | 60 | 30,000 / 30,000 | 63.84 | 94.60 | -0.075391 | PASS: diagnostic |
| Standard storage, SQL sampler | 500 | 600 | 300,000 / 300,000 | 79.45 | 116.55 | +0.012308 | Growth limit exceeded |
| Explicit PostgreSQL data volume, SQL sampler | 500 | 600 | 300,000 / 300,000 | 87.18 | 139.12 | +0.012879 | Growth limit exceeded |

The normal runner stopped after short 500 failed. **No ten-minute 50, 100, or
200 RPS qualification stage ran on this image.** The later long 500 runs used
declared diagnostic schedules and a bounded host-side SQL sampler. The data-volume
run also changed PostgreSQL storage setup. These are useful measurements, not
substitutes for the unchanged normal qualification sequence.

The standard long run drained in 0.366 seconds; the volume run drained in
0.314 seconds. This timer starts at the post-export drain check, not at the last
arrival. Each long run recorded 300,001 usage facts, including the successful
precheck. Charges matched all budget scopes; no unsettled work or unsafe balance
remained. Neither run wrote new legacy spend or audit rows.

## What the growth failure means

The test counts requests that have started but have not finished. This includes
requests being processed, not just requests waiting in a server queue. It fits
a straight-line slope to one-second snapshots over the middle 80% of the
arrival window. The permitted slope is at most **+0.01 active requests/second**.

Thus, the latest volume result is a small stability failure, not a throughput
collapse. Its slope implies about 6.2 additional active requests over the measured
eight minutes; the limit permits about 4.8. Low latency, no errors, and exact money
do not make this separate limit pass.

The latency limits remain p95 <= 150 ms and p99 <= 300 ms. The success-ratio
limit remains >= 99.9%; generator, resource, dependency, and strict financial
checks also apply. The saved `latency_passed` field combines latency and
active-request growth checks. It is false on these rows because growth failed,
even though their p95 and p99 limits passed.

Do not replace the saved result with an offline analysis or a higher limit.
The reason for the remaining trend is not yet proved. The explicit data volume
did not remove it. Missing Pod volume declarations also did not prove that the
earlier database used an overlay filesystem: the image declares a data volume,
and the captured container runtime permits image-defined volumes.

## Earlier iterations

| Rate | Saved stages | Passed stages | Failed stages |
| --- | ---: | ---: | ---: |
| 50 RPS | 10 | 7 | 3 |
| 100 RPS | 10 | 6 | 4 |
| 200 RPS | 14 | 7 | 7 |
| 500 RPS | 53 | 4 | 49 |
| Total | 87 | 24 | 63 |

These counts span different source commits, VM sizes, durations, and diagnostic
changes. They are an inventory, not a statistical pass rate for the latest image.
They must not be combined into a four-tier release pass.

The retained-history diagnosis found two concrete database costs: reporting
claims that grew with history and terminal commit key probes that selected
history scans. The repaired terminal commit stayed below 800 page hits and
4.17 ms per call in the latest three short cost repeats, compared with
43,659 hits and 18.22 ms in the earlier slow interval. The new load results
support that repair; they do not prove the cause of the remaining small trend.

The earlier experimental branch used different latency limits. Its 500 RPS
acceptance is recorded in the
[measure and integration record](issue-320-50-to-500-rps-measures-and-integration.md).
It is not a clean-integration qualification result.

## Source, image, and test scope

- Application and image revision:
  `7d4fbe713e4ae1fa295ce7af2ccd9e87a2ada62f`.
- Normal ladder and long diagnostic source checkpoint:
  `914693f1739277ff57504a8609c549ae2655d61d`.
  Only two project documents changed between these checkpoints.
- Image index:
  `sha256:90894d32f3a896b1727d3716bf98e2f96e5ffc5e55aa833c1585f4713c4ef249`.
- Linux arm64 image manifest:
  `sha256:f0b8f97c88352f41ccbd63f5c145e9ccff3e28554be81bf9cda628d09d79e927`.
- Runtime source fingerprint:
  `b616d199611105f533ad3f50b4a5baf3eb2dd522914d8e3ddec0154288ce475a`.
  This fingerprint includes Python runtime source, lock, dependency declarations,
  and Prisma schema. It does not include migrations or the Dockerfile.
- Migration 147 checksum:
  `9c941504f821ee14b98f4897e324d72b2033e7bb900c35c7ecb0fd7ef47e244d`.

The local Docker VM had 8 CPUs and a declared 6 GiB memory allocation, with
about 5.77 GiB reported by Docker. It ran Linux arm64, Docker 29.5.2, and cgroup v2.
Tests used a disposable two-node kind cluster, four API processes, two accounting
request processes, and one projection process. The bundle retains all resource
limits and effective fixture values.

The generator is the repository's open-loop HTTP load tool, with one synchronized
shard per API service. It first proves 1,000 RPS against the local provider.
The measured gateway workload is `fixed-one-token-nonstream-v1`: immediate local
mock replies, fixed token usage, native financial accounting, and no streaming.
It does not measure real provider latency, long streams, large payloads, external
ingress, or production hardware. It is not a direct competitor comparison.

## Verification and remaining work

This section records the review timeline relative to the earlier `7d4fbe71`
50–500 RPS image. Those later review fixes are included in the new 8 October
1,000 RPS image above. The short diagnostic does not qualify their lower-tier
performance or replace a complete normal four-tier qualification.

The later code-review fixes are not in the measured image. They add migration
`20261007160000_accounting_budget_policy_history`, preserve spend during cap
changes, remove the legacy worker dependency from native selectors, and prevent
a failed alert page from blocking later pages. The RPS evidence below remains
unchanged. It does not qualify these later source changes.

The next review round also fixed automatic recurring-budget renewal, stale reset
dates during policy edits, and expired spend during cap restoration. It added
`20261007170000_accounting_budget_period_sync` and the corrective migration
`20261007171000_accounting_monthly_reset_metadata`. Renewal now shares the policy
fence. A removed cap cannot return on a later cycle. Unlimited admin balances do
not count compatibility-projected charges twice. These fixes are not in the
measured image either. No RPS run was done for this review round.

### Earlier code-review fix verification

Those earlier source checks passed:

| Check | Result |
| --- | --- |
| Hermetic lane | 5,940 passed; 2,982 deselected; 6 warnings |
| Application lane | 1,650 passed; 7,272 deselected; 147 warnings |
| Affected PostgreSQL tests | 95 passed; no skipped tests |
| Migration paths | Fresh install, v0.1.42 upgrade, and shared-feature upgrade passed |
| Prisma client generation | Passed |
| Ruff checks and format check | Passed for all nine affected Python files |
| Whitespace check | Passed |

Commands used for the main gates are below. These paths identify the isolated
Python environments and local PostgreSQL service used for this review.
The PostgreSQL service was stopped after the checks. No load test, merge, or
PR creation was part of this verification.

```bash
PYTHONDONTWRITEBYTECODE=1 DELTALLM_REALTIME_SDK_PYTHON=/private/tmp/deltallm-review.gh0K18/sdk/bin/python /private/tmp/deltallm-review.gh0K18/venv/bin/python -B -m pytest -p no:cacheprovider -m hermetic -q --durations=5

PYTHONDONTWRITEBYTECODE=1 DELTALLM_REALTIME_SDK_PYTHON=/private/tmp/deltallm-review.gh0K18/sdk/bin/python /private/tmp/deltallm-review.gh0K18/venv/bin/python -B -m pytest -p no:cacheprovider -m app -q --durations=5

DATABASE_URL=postgresql://mehditantaoui@127.0.0.1:55439/deltallm_policyfixes PYTHONDONTWRITEBYTECODE=1 /private/tmp/deltallm-review.gh0K18/venv/bin/python -B -m pytest -p no:cacheprovider tests/test_accounting_budget_policy_history_postgres.py tests/test_accounting_budget_regressions_postgres.py tests/test_accounting_protocol_postgres.py tests/test_accounting_local_leases_postgres.py tests/test_native_selector_postgres.py tests/test_realtime_native_postgres.py tests/test_migration_status_postgres.py -q

MIGRATION_TEST_ADMIN_DATABASE_URL=postgresql://mehditantaoui@127.0.0.1:55439/postgres PYTHONDONTWRITEBYTECODE=1 PATH=/private/tmp/deltallm-review.gh0K18/venv/bin:/opt/homebrew/opt/postgresql@15/bin:/opt/homebrew/bin:/usr/bin:/bin /private/tmp/deltallm-review.gh0K18/venv/bin/python -B scripts/verify_migration_paths.py --prisma /private/tmp/deltallm-review.gh0K18/venv/bin/prisma

PYTHONDONTWRITEBYTECODE=1 PATH=/private/tmp/deltallm-review.gh0K18/venv/bin:/opt/homebrew/bin:/usr/bin:/bin /private/tmp/deltallm-review.gh0K18/venv/bin/prisma generate --schema=prisma/schema.prisma

/private/tmp/deltallm-review.gh0K18/venv/bin/python -m ruff check --no-cache src/bootstrap/selector.py src/db/budget_notifications.py src/billing/budget_notifications.py src/middleware/errors.py tests/bootstrap/test_selector.py tests/test_budget_notification_worker.py tests/test_budget_threshold_scan.py tests/test_error_middleware.py tests/test_accounting_budget_policy_history_postgres.py

/private/tmp/deltallm-review.gh0K18/venv/bin/python -m ruff format --check --no-cache src/bootstrap/selector.py src/db/budget_notifications.py src/billing/budget_notifications.py src/middleware/errors.py tests/bootstrap/test_selector.py tests/test_budget_notification_worker.py tests/test_budget_threshold_scan.py tests/test_error_middleware.py tests/test_accounting_budget_policy_history_postgres.py

git diff --check
```

### Recurring-budget fix verification

All three reported defects are fixed. Tests also cover the approved reset/edit
safeguard, month-end metadata, and balances while a cap is removed. Applied
migrations were not changed. The second new migration corrects a metadata edge
case found after the first migration was applied to the disposable test database.

| Check | Result |
| --- | --- |
| Final hermetic lane | 5,944 passed; 3,020 deselected; 6 warnings; 88.02 seconds |
| Full application lane | 1,650 passed; 7,310 deselected; 147 warnings; 670.34 seconds |
| Final affected admin application modules | 326 passed; 29 deselected; 5 warnings; 118.45 seconds |
| Final affected PostgreSQL tests | 136 passed; no skipped tests; 67.64 seconds |
| Stronger expired-counter checks | 8 passed; 22 deselected; 4.21 seconds |
| Migration paths through migration 154 | Fresh install, v0.1.42 upgrade, and shared-feature upgrade passed |
| Prisma client generation | Passed; client version 0.15.0 |
| Ruff and format checks | Passed for all 11 affected Python files |
| Whitespace check | Passed |

The full application run covered the shared recovery changes. After the final
balance-read correction, all affected admin application modules were run again.
The final database suite covers the same correction. The eight cap-toggle cases
were then made stronger and passed again: an expired legacy counter must not hide
current native charges, including charges present in both projection sinks.

The tests prove renewal by the actual native role graph, one renewal with two
concurrent recovery owners, valid budget and metadata edits before and after
renewal, and exact current-period spend for keys, users, teams, and organizations.
They also check cap removal before renewal, a policy edit that blocks renewal,
hard-budget denial, preserved old provisional balances, UTC month-end and leap-year
rules, explicit invalid dates, and the unchanged legacy reset owner.

The first broad run found an old three-action test expectation and local socket
restrictions. The test expectation was corrected for four actions; disposable
HTTP servers were rerun with socket access. A test-server cleanup overlap caused
one later database run to lose its connection. That run was interrupted and rerun
under one server owner. The table lists the successful final checks, not those
setup failures. The database server is stopped. No production service, RPS
evidence, merge, push, or PR was changed by these checks.

The full-lane, migration-path, and Prisma commands above were run again. The
database checks used a new disposable database cloned from the earlier review
database, then upgraded through migration 154. The commands for the changed
test selections and final checks are below.

```bash
DATABASE_URL=postgresql://mehditantaoui@127.0.0.1:55439/deltallm_periodfixes PYTHONDONTWRITEBYTECODE=1 PATH=/private/tmp/deltallm-review.gh0K18/venv/bin:/opt/homebrew/bin:/usr/bin:/bin /private/tmp/deltallm-review.gh0K18/venv/bin/prisma migrate deploy --schema=prisma/schema.prisma

DATABASE_URL=postgresql://mehditantaoui@127.0.0.1:55439/deltallm_periodfixes PYTHONDONTWRITEBYTECODE=1 /private/tmp/deltallm-review.gh0K18/venv/bin/python -B -m pytest -p no:cacheprovider tests/test_accounting_budget_period_postgres.py tests/test_accounting_recovery_postgres.py tests/test_accounting_role_runtime_postgres.py tests/test_accounting_budget_policy_history_postgres.py tests/test_accounting_budget_regressions_postgres.py tests/test_accounting_protocol_postgres.py tests/test_accounting_local_leases_postgres.py tests/test_native_selector_postgres.py tests/test_realtime_native_postgres.py tests/test_migration_status_postgres.py -q --tb=short

DATABASE_URL=postgresql://mehditantaoui@127.0.0.1:55439/deltallm_periodfixes PYTHONDONTWRITEBYTECODE=1 /private/tmp/deltallm-review.gh0K18/venv/bin/python -B -m pytest -p no:cacheprovider tests/test_accounting_budget_period_postgres.py -k cap_toggle -q --tb=short

PYTHONDONTWRITEBYTECODE=1 DELTALLM_REALTIME_SDK_PYTHON=/private/tmp/deltallm-review.gh0K18/sdk/bin/python /private/tmp/deltallm-review.gh0K18/venv/bin/python -B -m pytest -p no:cacheprovider -m app tests/test_error_middleware.py tests/test_control_audit_mode.py tests/test_ui_key_notifications.py tests/test_ui_member_candidates.py tests/test_ui_asset_access.py tests/test_ui_tier_assignments_api.py tests/test_ui_legacy_models.py tests/test_ui_rate_limits.py tests/test_ui_self_service_keys.py tests/test_ui_authorization.py tests/test_ui_tiers_api.py tests/test_ui_tier_policy_preview_api.py tests/test_ui_scope_asset_visibility.py tests/test_ui_organization_member_count.py tests/test_ui_organizations_assets.py tests/test_ui_organization_deletion.py -q --durations=5

/private/tmp/deltallm-review.gh0K18/venv/bin/python -m ruff check --no-cache src/billing/accounting_recovery.py src/db/accounting_recovery.py src/db/accounting_budget_reads.py tests/test_accounting_recovery.py tests/test_accounting_recovery_postgres.py tests/test_accounting_budget_reads.py tests/test_accounting_budget_period_postgres.py tests/test_accounting_role_runtime_postgres.py tests/test_accounting_presence.py tests/performance/gateway_concurrency_metrics.py tests/test_gateway_concurrency_workload.py

/private/tmp/deltallm-review.gh0K18/venv/bin/python -m ruff format --check --no-cache src/billing/accounting_recovery.py src/db/accounting_recovery.py src/db/accounting_budget_reads.py tests/test_accounting_recovery.py tests/test_accounting_recovery_postgres.py tests/test_accounting_budget_reads.py tests/test_accounting_budget_period_postgres.py tests/test_accounting_role_runtime_postgres.py tests/test_accounting_presence.py tests/performance/gateway_concurrency_metrics.py tests/test_gateway_concurrency_workload.py

git diff --check
```

### Review loop: terminal ordering and ingress

The October 7–8 review reproduced and fixed four defects:

1. **P1: reporting could skip a committed terminal event.** Event numbers are
   allocated before commit. A higher number could commit first and move the
   checkpoint past an earlier uncommitted record. Migration
   `20261007173000_accounting_event_publication_order` orders terminal publication
   within each generation and partition. Financial locks come first. Locks are
   bounded and released on commit or rollback; independent partitions remain
   independent. Checkpoint replay preserves existing facts, audit rows, and rollups.
2. **P1: direct recovery could select grant-backed operations.** The same forward
   migration excludes grant-backed reservations from direct expiry. Grant recovery
   and settlement remain their only money owner.
3. **P2: an early capacity rejection could lose its response.** Closing an HTTP/1.1
   connection during a valid upload could reset the socket before the client read
   the JSON response. Retryable 503 responses now use the server's existing body
   discard and idle deadline. Invalid framing and HTTP/1.0 still close. Real tests
   cover both HTTP parsers, incomplete uploads, and safe connection reuse.
4. **P1: journal processing and reconciliation could deadlock.** The follow-up
   review reproduced a conflict between publication and an implicit parent-window
   foreign-key lock. Migration
   `20261008001000_accounting_event_publication_parent_locks` takes the required
   shared parent locks before publication. Fully settled compact receipts retain
   their existing fast path, without parent-window reads.

All four defects failed regression tests before their fixes. No applied migration
was edited. No inference database call, task, queue, pool, or client was added.
The deployment guide includes stopped-writer upgrades and controlled native
checkpoint replay. The load evidence remains unchanged: these source fixes are
not qualified by the earlier measured image.

| Check | Result |
| --- | --- |
| Final round-1 hermetic lane | 5,958 passed; 3,036 deselected; 6 warnings; 71.86 seconds |
| Full application and hermetic run after the HTTP fix | 7,596 passed; 1,381 deselected; 151 warnings; 542.03 seconds |
| Final HTTP transport, ingress, and managed shutdown checks | 73 passed; 2 warnings; 11.38 seconds |
| Final publication and journal-plan database checks | 36 passed; no skipped tests; 71.42 seconds |
| Full PostgreSQL lane through migration 156 | 1,009 passed; 7,985 deselected; 26 warnings; 757.54 seconds |
| Migration paths through migration 156 | Fresh install, v0.1.42 upgrade, and shared-feature upgrade passed |
| Redis and Helm lanes | 376 passed; one dedicated-Redis case skipped; 6 warnings; 40.18 seconds |
| Dedicated critical/cache Redis case, with two empty servers | 1 passed; 0.27 seconds |
| Prisma client generation | Passed; client version 0.15.0 |

The application run preceded twelve additional hermetic transport assertions and
the corrective SQL migration; the final hermetic, focused transport, and full
PostgreSQL runs cover those changes. The dedicated Redis case passed separately
after its required two-server fixture was supplied. No skipped case is left
unverified in those lanes.

The first combined application run exposed the HTTP defect. Earlier database
attempts lacked the required JIT build, Redis, or Realtime SDK, lost the local VM,
or exhausted a small temporary data volume. Those attempts are not pass evidence.
The successful full PostgreSQL run used a disk-backed disposable PostgreSQL 15.19
server with JIT enabled, Redis 7, and the SDK environment. Test services use
loopback ports only; no production or Rancher workload was changed.

Commands for the successful full and focused gates:

```bash
export PYTHONDONTWRITEBYTECODE=1
export DELTALLM_REALTIME_SDK_PYTHON=/private/tmp/deltallm-review.gh0K18/sdk/bin/python
export PATH=/private/tmp/deltallm-review.gh0K18/venv/bin:/opt/homebrew/opt/postgresql@15/bin:/opt/homebrew/bin:/usr/bin:/bin

python -B -m pytest -p no:cacheprovider -m hermetic -q --durations=5
python -B -m pytest -p no:cacheprovider -m 'hermetic or app' -q --tb=short --durations=5

export DATABASE_URL=postgresql://postgres:review-loop-local@127.0.0.1:55440/deltallm_reviewloop
export REDIS_URL=redis://127.0.0.1:56379/0
export DELTALLM_TEST_REDIS_URL=redis://127.0.0.1:56379/0
python -B -m pytest -p no:cacheprovider -m postgres -q --tb=short --durations=5 -rs --maxfail=1
MIGRATION_TEST_ADMIN_DATABASE_URL=postgresql://postgres:review-loop-local@127.0.0.1:55440/postgres python -B scripts/verify_migration_paths.py --prisma /private/tmp/deltallm-review.gh0K18/venv/bin/prisma

python -B -m pytest -p no:cacheprovider -m 'redis or helm' -q --tb=short -rs
```

The password above belongs only to the disposable review database. It is not a
production credential. Reproduction requires provisioned test services and the
frozen project dependencies, not these services still running after review.

### Review loop: compatibility recovery audits

The next round found **P2: compatibility audit projection rejected recovery event
keys**. Expiry and reconciliation use suffixed source keys, not UUID strings.
The fallback attempted to parse them as UUIDs and stopped checkpoint progress.
Four new hermetic cases failed before the fix; valid envelope UUIDs already worked.

The adapter now preserves valid audit UUIDs and the existing UUIDv5 fallback for
UUID source keys. Only non-UUID recovery keys use the native namespaced audit
identity. No financial owner, database call, background task, or migration changed.
Golden identifiers agree with PostgreSQL. A real database test produces expiry
and reconciliation events, projects both, repeats after sink acceptance, and
replays the checkpoint. It keeps exactly two required audit outbox records,
the same capacity count, zero spend records, and unchanged resolved balances.
All applied migrations remain unchanged.

| Final check after the last source fix | Result |
| --- | --- |
| Full hermetic lane | 5,964 passed; 3,037 deselected; 6 warnings; 67.52 seconds |
| Affected compatibility, protocol, telemetry-sink, worker, and bootstrap checks | 105 passed; no skipped tests; 17.28 seconds |
| Ruff checks and format check | Passed for all 36 changed or new Python files |
| Whitespace check | Passed |

The database replay test first needed an older creation timestamp to satisfy
the existing expiry constraint. The corrected fixture passed; no constraint
or product code was weakened. The full 1,009-test PostgreSQL lane and three
migration paths above preceded this final Python-only adapter change. The
105-case selection includes the added database regression and affected existing
sinks. The full application run above is also earlier; it was not rerun after
this pure identifier change.

The review-only PostgreSQL and Redis containers and their disposable data were
removed after verification. Their fixtures can be reproduced from the tests.
The local Docker VM remains available and the original `default` Docker context
is unchanged. Existing product data, other workloads, images, and RPS evidence
were not removed.

The temporary fix plans were deleted after verification, as requested. The final
review rechecked publication and financial lock order, expiry ownership, bounded
parent lookups, ingress rejection and cleanup, audit identity and replay,
budget-policy and reset fences, authorized admin reads, and deployment guidance.
No remaining P0, P1, or P2 finding was identified in the reviewed upgrade.
This does not replace fixed-image RPS qualification or integration against the
current remote PR base. Neither was performed in this review loop.

Final affected-test command, using the disposable services and SDK above:

```bash
python -B -m pytest -p no:cacheprovider tests/test_accounting_compatibility_audit_ids.py tests/test_accounting_protocol.py tests/test_accounting_compatibility_recovery_postgres.py tests/test_telemetry_ingestion_db_integration.py tests/test_accounting_protocol_postgres.py tests/test_accounting_projection_observation.py tests/bootstrap/test_runtime_services_bootstrap.py -q --tb=short --durations=5
```

### Earlier image verification and release work

The accepted image passed all five offline, read-only, non-root image checks.
Migration 147 passed 130 affected PostgreSQL tests, rollback/forward checks, and
all three migration paths. The final full PostgreSQL run passed **920 tests**
with 26 warnings in 767 seconds. That run included Redis and the required
official Realtime SDK environment. The earlier 916-pass/four-failure run is
preserved; it lacked that SDK environment.

Before a release claim, resolve or explicitly review the active-request stability
contract, then run the normal unchanged-image short ladder and all four
ten-minute stages. Do not bypass the existing stop rule.

Before the future PR, inspect the current feature-branch base and choose a safe
integration method. At source checkpoint `914693f1`, the locally recorded target
`5be17a63` had 78 commits absent from that checkpoint. The checkpoint had 109
commits absent from that target. This report
does not establish compatibility with the current remote branch. No rebase,
merge, push, or PR creation was done for this report.

## Evidence and cleanup

The [reproduction guide](issue-320-rps-reproduction.md) gives the commands,
pins, and diagnostic differences.

- Latest review bundle: `artifacts/issue320-evidence-20261007/`.
- Original records for all 87 stages:
  `artifacts/issue320-evidence-20261007/history-reports/`.
- Versioned file identities:
  [bundle manifest](evidence/issue-320-20261007/bundle-manifest.json).

The old diagnostic archive and nine host-specific shell wrappers were removed
from the worktree and moved to local Trash. Five obsolete progress plans were
removed; Git history keeps their previous content. The concrete improvement
list, current design and deployment guides, and regression fixtures remain.

The review bundle keeps all 87 original stage reports with their checksums and
the raw samples for the latest nine stages. Older raw streams, command scratch
files, and failed one-off prototypes are no longer part of the upgrade package.
The original archive can be recovered from Trash if those older files are needed.
No product code, regression test, migration, worktree, or image was removed.
Large raw evidence stays local and ignored by Git.

## Proof-copy comparison: 8 October 2026

The six-step accounting-work plan is complete. The implementation removes JSON
copies from reservation, permit, handle, issue, and terminal preparation. It
retains canonical bytes and checked operation identity in the bounded receipt
store. Provider retries keep the original issue and check only the new attempt.
Existing accepted terminal handoffs still reuse their bytes. Full input checks,
money rules, wire documents, queue limits, pools, and timeouts remain unchanged.
No new migration is needed for this change. A strict grant check also fixes an
existing copy path that accepted a forged boolean operation limit.

The proof-only benchmark used Python 3.11.13, 100 cycles per sample and five
samples per case. Small-payload median CPU time fell from 285.17 to 220.77 us
(22.6%). Wide-payload time fell from 15,849.45 to 12,725.52 us (19.7%). JSON model
parses fell from six per cycle to one. Python model-validation calls increased
from seven to ten; those full checks were kept, not removed to improve a score.
These are local proof-processing measurements, not whole-gateway CPU savings.

All three gateway stages used the same 6-CPU/12-GiB VM, pinned two-node kind
fixture, four API processes, two request owners, one projection owner, one-token
non-streaming workload, response-cache bypass, and unchanged acceptance gates.
Both 500 RPS stages had the same profile fingerprint. Profiling was off for each
stage. This is one before/after pair, not a repeated capacity qualification.

| 30-second stage | Success / intended | HTTP 503 | Dropped | p95 / p99 | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Before, 500 RPS | 14,836 / 15,000 | 164 | 0 | 737 / 1,062 ms | Failed |
| After, 500 RPS | 15,000 / 15,000 | 0 | 0 | 87 / 146 ms | Passed short diagnostic |
| After, 1,000 RPS | 4,988 / 30,000 | 21,180 | 3,832 | 1,985 / 2,543 ms | Failed |

The changed 500 RPS image had no Redis full-allocation or acquisition-deadline
events. Mean measured client Redis round-trip time was 1.28 ms, compared with
4.39 ms in the baseline. Accounting drained in 12.13 seconds. All 15,001 charges,
including the precheck, totaled exactly `0.105007`. All four scopes matched the
facts; no provisional operation, open grant, pending terminal, or pending report
partition remained. The strict workload and financial gates passed.

At 1,000 RPS, Redis recorded 112 full-allocation and five acquisition-deadline
events. There were 20,346 `no_healthy_deployments` responses, 805
`spend_persistence_unavailable` responses, and 29 unclassified HTTP errors.
Mean client Redis round-trip time was 11.81 ms. This shows the remaining
Redis/routing failure; it does not prove a new underlying cause without further
profiling. Fast error responses are not usable throughput or spare capacity.

The 1,000 RPS drain failed after 180.96 seconds. One completed `uncertain`
journal left its operation provisional, with `service_unavailable` as its
uncertainty class. Each applicable scope retained `0.024582` provisionally.
Committed charges matched the 4,989 native facts and totaled `0.034923`.
No unsafe window, open grant, pending terminal, or pending report partition
remained, but this is not complete reconciliation. No financial state was
changed to force a pass. The image is not qualified for 1,000 RPS.

Verification passed: 7,648 application/dependency-free tests, 1,650 application
tests, and 438 final-source affected tests. The PostgreSQL lane passed 1,006
tests; its four skipped official SDK cases then passed with the pinned SDK test
environment. The Redis lane passed 111 tests; its skipped isolated eviction case
then passed on two dedicated empty servers. Ruff and diff checks passed.
Temporary clusters and test database containers were removed after evidence
capture. Unrelated containers and VM settings were not changed.

Evidence is in `artifacts/accounting-proof-20261008/`. It includes both source
bundles, exact image and source identities, raw arrivals, resource and phase
metrics, financial snapshots, benchmark samples, test logs, and source checksums.
The before source checkpoint is `c1a759db17cf217f4d10ef9aeb21614af9d47704`; the
after checkpoint is `9cbd2a32dc36fe516c57da4621e01392a55e8f1f`. These are clean
local reproduction snapshots, not commits pushed to the feature branch. The
after runtime fingerprint is
`41181d55bfce602dda33115a6bb5eeabc93179e2ef72be0ef41516d5142b13de`.
