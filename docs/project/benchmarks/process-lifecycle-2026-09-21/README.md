# Process lifecycle qualification — September 21, 2026

This records PR8's readiness, termination, migration and recovery experiments for
[issue #320](https://github.com/deltawi/deltallm/issues/320). The
[operator contract](../../../deployment/process-lifecycle.md) defines the supported
process model, budgets and release procedure. These short experiments do not
certify production throughput or supported stream concurrency; PR9 owns resource
and autoscaling profiles, and PR10 owns capacity qualification.

## Reproducible comparison

The baseline is merged PR7, `4d856670e54e689ea4fc1ceead787730e5edcf5c`, packaged
with the candidate Dockerfile but its own application and frozen dependency lock.
It uses raw Uvicorn because that revision has no managed launcher. The candidate
runtime is `be4016de` and uses the image's managed command; see the
[source and lockfile provenance](provenance.json). Both run sequentially on the same ARM64 Docker
VM with the same profile, dependencies and offered load.

Each API container has one process, a two-CPU limit and 2 GiB memory limit.
PostgreSQL 15.19 and Redis 7.4.11 each have two CPUs and 1 GiB limits. The profile
enables combined budgets, audit/spend outboxes, spend operation intents and
fail-closed Redis. Response caching is bypassed. The fixed provider returns five
input tokens and one output token with zero artificial delay. The load is ten
requests per second for 20 seconds, followed by one stream first-byte sample.

Raw request/metrics samples, image/source/profile hashes, dependency allocations
and accepted economic records are retained alongside this page. Report paths are
normalized to these artifact filenames; measurements are unchanged.

| Measurement | PR7 baseline | PR8 candidate |
| --- | ---: | ---: |
| Successful / started requests | 200 / 200 | 200 / 200 |
| Generator drops | 0 | 0 |
| Mean latency | 32.15 ms | 31.69 ms |
| All-response p50 | 30.88 ms | 29.21 ms |
| All-response p95 | 41.49 ms | 42.75 ms |
| All-response p99 | 58.43 ms | 70.16 ms |
| Single stream TTFT | 26.50 ms | 23.48 ms |
| Observed maximum in-flight requests | 1 | 1 |
| Managed/raw process exit code | 0 | 0 |

The sample shows similar latency at this offered load. One TTFT observation and
200 responses are insufficient to establish a performance improvement. The
`baseline_only` qualification field in the runner's output intentionally does not
grant a production capacity qualification.

Nonzero Redis command deltas match, including 1,000 EVAL, 400 MGET and zero PING calls
during the measured interval. Foreground database operation counters increase by 213 on each, and control
operations by 208 on each. Total PostgreSQL calls, including background polling,
are 11,468 and 10,872. The candidate removes three receipt round trips per request;
background activity also contributes to these shared totals, so they are not per-request
SQL counts. Startup migration verification and readiness prechecks precede the
sample; every candidate readiness-probe counter has a zero interval delta.
The receipt allocation's completed-query counter increases by 200 on the
candidate; the baseline uses interactive transactions, which this query counter
does not count. This is a change in operation type, not 200 additional queries.

The sampled audit queue stays between zero and one. The maximum sampled
last-lag gauge is 2 ms on the baseline and zero on the candidate. The short
interval and one-second scrape cadence do not establish sustained queue stability or a peak-lag bound. See
[metric deltas and gauge observations](metric-summary.json),
[before/after reports](comparison.json), [baseline requests](before-http.jsonl),
[candidate requests](after-http.jsonl), [baseline metrics](before-metrics.jsonl)
and [candidate metrics](after-metrics.jsonl).

The candidate also accepts a request while its ledger and audit sink are locked.
After releasing the fixture lock, its spend and audit records complete, with
exactly one ledger entry of `0.000007`. The full comparison export contains 403
completed spend records and 403 persisted audit records, including prechecks and
the recovery request. The two intentionally interrupted TTFT streams remain
blocked with dispatched intents: their final usage is unknown, so no charge is
invented. See [accepted records](accepted-records.json) and the
[unknown-work procedure](../../../deployment/spend-recovery.md#investigating-unknown-work).

## Exact images and Kubernetes

Both ARM64 image variants pass a non-root, read-only-root, no-network runtime
check and two real blocked-callback watchdog checks. The runtime exits zero;
the normal and cancelled-cleanup watchdog scenarios exit 70. The Presidio check
also asserts that the engine attempts no HTTP suffix-list download. See the
[default image identity](default-image/image.json) and
[Presidio image identity](presidio-image/image.json).
The Presidio image also passes full managed startup against real PostgreSQL and
Redis, ten successful requests, accepted spend/audit recovery and SIGTERM with
exit code zero. Its analyzer is checked separately in the offline test; this
small startup/shutdown run does not measure guardrail throughput. See the
[managed result](presidio-image/managed-summary.json),
[requests](presidio-image/managed-http.jsonl),
[metrics](presidio-image/managed-metrics.jsonl),
[durable records](presidio-image/managed-records.json) and
[shutdown phases](presidio-image/managed-shutdown.log).

The reusable CI job builds and checks both variants on AMD64. Its default-image
scenario uses an owned kind 0.31.0 cluster with a pinned Kubernetes 1.34.3 node,
two API pods, one batch worker, real PostgreSQL/Redis and shared test artifacts.
It retains exact-image identities, rendered fixture settings, HTTP/metrics
samples, readiness and shutdown timelines, migration outcomes, accepted records,
batch accounting and upstream-close observations as `lifecycle-false` artifacts.
The single-node shared PVC is a fixture exception; production split workers use
shared object storage.

The batch scenario keeps the chart's two execution slots and ten-item claim
limit. It holds provider responses until the old worker is observed draining,
then permits ten-second attempts. Work that exceeds the response-drain deadline
retains its 360-second item lease; the test observes that expiry and waits for
the surviving production worker to reclaim it. It requires all 20 items and
their economic records to complete. Only the fixture's lease-sweeper polling
interval is shortened to one second; the item lease and production defaults
remain unchanged.

The abrupt-loss scenario signals the selected application container through
the owned kind node's container runtime and verifies Kubernetes exit code 137.
It does not signal PID 1 from a peer inside the same container; Linux
[restricts signals to a PID namespace's init process](https://www.man7.org/linux/man-pages/man7/pid_namespaces.7.html).

The complete local Kubernetes gate passed on the same `be4016de` application
source and default image as the comparison. Helm 3.19.0, kubectl 1.34.3 and the
pinned kind node ran in the ARM64 Docker VM with Bunyan temporarily stopped and
restored afterward. The fixture completed in 724.40 seconds.

| Kubernetes check | Observed result |
| --- | --- |
| Before / after rollout load | 100/100 successful requests in each 10 RPS, ten-second sample; zero drops |
| Before / after p95 | 61.59 / 49.25 ms |
| Redis pause | Liveness stayed 200; active-pod readiness withdrew by 15.11 s and fully recovered at 30.30 s |
| Migration ordering | Fresh and concurrent/retried migrations passed; invalid-schema and wall-time failures left Deployment templates unchanged |
| Stream rollout | New work rejected with `gateway_draining`; interrupted stream emitted no terminal success |
| Accepted work during rollout | Spend and audit recovered; exactly one ledger entry |
| Worker rollout | 20/20 items completed and 20 unique ledger effects, including natural recovery of interrupted 360-second leases |
| Abrupt container loss | Runtime SIGKILL verified as Kubernetes exit 137; survivor served; accepted work recovered with one ledger effect and rejected stale acknowledgement |
| Upstream cleanup | All four test streams closed upstream within the bounded observation window |

The rollout stream disconnected after 31.21 seconds total, close to observed
drain entry; this port-forwarded sample does not measure the full 50-second
response cutoff. The real-HTTP managed-server regressions separately exercise
that deadline and repeated-signal behavior. Four interrupted operation intents
remain explicitly unknown and uncharged; only their fixture expiry is accelerated
for the reconciliation check. The batch item leases are not shortened.

See the [event timeline](kubernetes/events.json),
[readiness samples](kubernetes/readiness-recovery.json),
[before](kubernetes/before-rollout-summary.json) and
[after](kubernetes/after-rollout-summary.json) load reports,
[batch accounting](kubernetes/batch-accounting.json),
[accepted records](kubernetes/accepted-records.json),
[upstream closures](kubernetes/upstream-closures.json),
[shutdown phases](kubernetes/shutdown-phases.json) and
[environment provenance](kubernetes/provenance.json). Raw HTTP and metrics files
are retained beside the reports. The final export includes 225 successful ledger
records, 205 completed spend records, 500 persisted audit records, and the four
unknown uncharged streams.

## Counterexamples and limits

- The first local rollout after the atomic receipt fix returned 91/100 successes.
  Per-pod counters show seven operation-admission failures, two audit failures and
  nine acceptance-allocation overflow events, with no receipt failures. All 20
  batch items and their ledger records completed. The four acceptance connections
  had no waiting capacity. The follow-up permits one waiting operation per
  acceptance connection, using the existing acquisition deadline and allocation
  owner; settlement retains its separate bounded queue. No connection or timeout
  was increased. See the [91/100 summary](acceptance-contention-summary.json),
  [requests](acceptance-contention-http.jsonl), [metrics](acceptance-contention-metrics.jsonl)
  and [per-source counter deltas](acceptance-contention-deltas.json).

- A later post-rollout run still completed only 97 of 100 requests. Its three
  rejected receipts retained dispatched intents. The follow-up removes the
  interactive receipt transaction's start, timeout-setup and commit calls:
  one owner-fenced SQL UPDATE commits the receipt atomically through the same
  settlement allocation. The 250 ms caller deadline, immutable replay checks,
  connection limit and finite queue remain unchanged. See the
  [97/100 summary](post-rollout-contention-summary.json),
  [requests](post-rollout-contention-http.jsonl) and
  [metrics](post-rollout-contention-metrics.jsonl).
  The [new SQL measurement](spend-sql.json) verifies one statement and an indexed
  one-row receipt update with 100,000 ledger rows and 10,000 retained outbox rows.
  The [earlier measurement](../spend-recovery-2026-09-15/sql-report.json) records
  two statements inside an interactive transaction; its repository source hash
  matches the PR7 baseline. Admission still uses its separate lock statement
  and fresh transaction snapshot. The successful rollout above includes this fix
  and the subsequent bounded acceptance queue.
- The first completed Kubernetes run saved only two upstream-close events in an
  immediate snapshot after cancelling the survivor clients. A direct HTTP probe
  through the earlier `66d977d9` managed image closed both cancelled upstreams in 0.19 seconds.
  The runtime's [port-forward implementation](https://github.com/containerd/containerd/blob/v2.2.0/internal/cri/server/sandbox_portforward_linux.go)
  includes a one-second close grace, consistent with a delayed observation.
  Acceptance now waits at most ten seconds and requires exactly four upstream
  closures, rejecting duplicates. See the [early snapshot](early-closure-observation.json),
  [direct probe log](direct-disconnect.log) and [direct closures](direct-disconnect-closures.json).
- A Kubernetes sample returned 98 successful responses and two
  `spend_persistence_unavailable` responses at 10 RPS; metrics recorded five
  full-settlement-allocation outcomes. Review reproduced contention with readiness
  probes, and a subsequent run exposed overlapping business settlements too.
  The fixes permit a bounded waiter behind a probe and one queued receipt per
  settlement connection, using the existing acquisition deadline and connection
  ceiling. The strict 100-success acceptance
  assertion remains in place. See the [failure summary](probe-contention-summary.json),
  [requests](probe-contention-http.jsonl), [metrics](probe-contention-metrics.jsonl)
  and [durable-record export](probe-contention-records.json), plus the subsequent
  [99/100 overlap sample](settlement-overlap-summary.json), its
  [requests](settlement-overlap-http.jsonl) and [metrics](settlement-overlap-metrics.jsonl).
- A 50 RPS, 20-second baseline experiment completed only 560 of 1,000 requests:
  375 returned spend-persistence-unavailable and 65 audit-persistence-unavailable.
  It stopped before measuring the candidate. This demonstrates saturation in
  this local profile, without establishing a candidate comparison. See its
  [summary](saturation-summary.json), [requests](saturation-http.jsonl) and
  [metrics](saturation-metrics.jsonl). This early run predates failure-path database
  export, so its accepted-record export is unavailable.
- A migration container with a 512 MiB limit was killed with exit 137; the same
  image succeeded with a 1 GiB limit. The Job now requests 512 MiB and limits 1 GiB.
- The local Docker VM could not fit the Kubernetes fixture alongside existing
  workloads. The successful repeat temporarily stopped the approved Bunyan development
  containers and restored them afterward. This is a fixture resource requirement,
  not a measured production resource profile.
- An early compound experiment held the organization ledger through a rollout
  while batch accounting retried. Some batch completion records exhausted their
  finite attempts even though the batch API reported successful items. The
  harness now separates the ledger-outage and active-batch-termination scenarios
  and requires a persisted ledger record for every successful batch item. PR8
  does not promise automatic recovery from arbitrary-duration dependency outages
  or add a batch dead-letter replay service. Preserve failed completion records
  and investigate their accounting before any repair; do not rerun provider work
  merely to repair its ledger.
- A batch recovery check originally stopped after 240 seconds, before the
  default 360-second leases could expire. Its failure export retained 18
  completed items and two interrupted, still-claimed items. Acceptance now
  derives its finite recovery window from the observed remaining lease and
  retains the snapshot immediately after the old worker exits.

## Repeat the experiments

From the PR8 checkout with the frozen development environment and Docker:

```bash
uv sync --frozen --extra dev
uv run prisma generate --schema=./prisma/schema.prisma
docker build -t deltallm-pr8:candidate .
uv run python scripts/check_lifecycle_image.py \
  --image deltallm-pr8:candidate --output /tmp/pr8-image
uv run python scripts/build_lifecycle_baseline.py \
  --ref 4d856670e54e689ea4fc1ceead787730e5edcf5c \
  --image deltallm-pr8:baseline --manifest /tmp/pr8-baseline.json
uv run python -m tests.performance.run_lifecycle_comparison \
  --image deltallm-pr8:candidate --baseline-image deltallm-pr8:baseline \
  --baseline-manifest /tmp/pr8-baseline.json --output /tmp/pr8-comparison \
  --rate 10 --duration 20
uv run python -m tests.performance.run_lifecycle_acceptance \
  --image deltallm-pr8:candidate --output /tmp/pr8-kubernetes
```

The last command also requires Helm, kubectl and pinned kind 0.31.0. Each harness
creates and removes only its own dependencies. To check the optional variant,
build with `--build-arg INSTALL_PRESIDIO=true` and pass `--presidio` to the image
checker. Release CI stages multi-platform images by digest and promotes release
tags only after both variants complete the acceptance job.
