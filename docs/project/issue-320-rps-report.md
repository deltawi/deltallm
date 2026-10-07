# Issue 320: results from 50 to 500 RPS

Date: 7 October 2026. Future PR target: `feature/issue-320-concurrency`.

## Result

The latest image passed the 60-second stages at 50, 100, and 200 RPS.
It also served all requests in two 600-second tests at 500 RPS, with no errors
or dropped arrivals and correct accounting. Both long tests failed only the
active-request growth limit. **Full four-tier qualification is not complete.**

The report covers all 87 saved gateway stages in this clean integration worktree.
The [complete stage index](evidence/issue-320-20261007/all-runs.md) includes every
pass and failure. Its [JSON file](evidence/issue-320-20261007/all-runs.json) records
source and image identities, duration, main gate decisions, and original checksums.
Setup failures with no arrival stage are not counted as RPS results.

## Latest fixed-image results

All rows below use image revision `7d4fbe71`. Every row had zero request errors
and zero dropped arrivals. Every row passed accounting reconciliation and drain.
Values are rounded for display; the JSON index keeps the original numbers.

| Test | RPS | Seconds | Successful / scheduled | p95 ms | p99 ms | Active-request growth / s | Result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Normal short ladder | 50 | 60 | 3,000 / 3,000 | 22.37 | 28.84 | +0.003040 | PASS |
| Normal short ladder | 100 | 60 | 6,000 / 6,000 | 21.81 | 25.71 | +0.001411 | PASS |
| Normal short ladder | 200 | 60 | 12,000 / 12,000 | 24.40 | 34.84 | -0.006730 | PASS |
| Normal short ladder | 500 | 60 | 30,000 / 30,000 | 69.37 | 103.80 | +0.068335 | FAIL: growth |
| SQL-cost repeat 1 | 500 | 60 | 30,000 / 30,000 | 75.23 | 117.84 | +0.070506 | FAIL: growth |
| SQL-cost repeat 2 | 500 | 60 | 30,000 / 30,000 | 79.61 | 116.79 | +0.105515 | FAIL: growth |
| SQL-cost repeat 3 | 500 | 60 | 30,000 / 30,000 | 63.84 | 94.60 | -0.075391 | PASS: diagnostic |
| Standard storage, SQL sampler | 500 | 600 | 300,000 / 300,000 | 79.45 | 116.55 | +0.012308 | FAIL: growth |
| Explicit PostgreSQL data volume, SQL sampler | 500 | 600 | 300,000 / 300,000 | 87.18 | 139.12 | +0.012879 | FAIL: growth |

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
