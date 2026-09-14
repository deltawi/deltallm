# PR 4 admission measurements — 2026-09-14

These are measurements of the **durable PostgreSQL admission boundary**, not gateway, provider, streaming, or Kubernetes capacity certification. The design and conditional decisions are in [the PR 4 decision record](../../design/pr4-durable-admission.md).

## Reproduction and workload

Use an isolated PostgreSQL **15.19** test database with the repository's 90 Prisma migrations applied, the frozen `uv.lock`, and generated Prisma client. The recorded local runtime is Python **3.13.12** on an Apple Silicon macOS development machine; each summary contains the exact Python/platform/server versions. CI separately exercises Python 3.11. This developer host is not an isolated load generator or a controlled production resource profile.

The before source is `ea77c231`; the after source is `4ca6abdd`. Each summary records hashes of the measured admission repositories, allocation adapter and timing probe, along with PostgreSQL settings. PostgreSQL used UTC, 100 maximum connections, 128 MiB shared buffers, and enabled `fsync`/synchronous commit. Admission uses PR 3's five-connection allocation, zero application waiters, 200 ms acquisition/lock deadlines, 1 s statement and 2 s transaction deadlines. A separate worker allocation owns polling. Each run clones migrated audit/spend/capacity/organization tables into a unique schema and removes that schema after draining clients; source table data is untouched. Pass a test URL explicitly. The probe is not intended for a production database.

Each of the 16 independent cases offers 50 or 200 calls/second for 10 seconds, without a warmup exclusion. The initial exploratory campaign was repeated after fixing the test-server timezone and adding source/config fingerprints; the table and committed samples are from that final campaign. There is one synthetic required event per call, 2 KiB of highly compressible synthetic content, content storage enabled, and either one organization or 13 organizations selected round-robin. One call's acceptance always waits for its commit. Both successful and rejected calls are retained, with scheduler lag, offered/completed rates, RPC counts and sampled in-flight admission work. The generator caps live tasks at 128 and total samples at 30,000. Consumers are intentionally disabled to isolate acceptance, so durable backlog grows by accepted events; it is **not** the admission queue slope.

Example after run (from this worktree):

```sh
export DATABASE_URL=postgresql://postgres@127.0.0.1:55434/deltallm_pr4_test
uv sync --frozen --extra dev
uv run --frozen prisma generate --schema=./prisma/schema.prisma
uv run --frozen prisma migrate deploy --schema=./prisma/schema.prisma
PYTHONPATH=. uv run --frozen python scripts/benchmarks/measure_admission.py \
  --queue audit --organizations 13 --rate 50 --seconds 10 \
  --revision pr4 --output /tmp/pr4-audit-13org-50rps
PYTHONPATH=. uv run --frozen python scripts/benchmarks/measure_capacity_writes.py \
  --revision pr4 --output /tmp/pr4-capacity-writes.json
PYTHONPATH=. uv run --frozen python scripts/benchmarks/explain_admission.py \
  --output /tmp/pr4-admission-plans.json
```

To reproduce the before side, export `src/` from `ea77c231` to a temporary directory using `git archive`, then put that directory before `.` in `PYTHONPATH` while invoking the current benchmark scripts. Repeat both queues, both organization counts and both rates. The lock timing probe substitutes an instrumented statement with the same transaction locks, keys and acquisition order; server timestamps separate queue-lock acquisition from policy-lock waiting without another RPC. The admission SQL and commit use the actual repositories and allocated client. Estimated hold time adds server policy wait to the interval from the lock response to commit acknowledgement, so it includes client/network overhead and must not be called exact server lock occupancy. No throughput gain is inferred solely from this estimate.

## Results

| Profile | Accepted / offered | Accepted p95 (ms) | Lock wait p95 (ms) | Mean estimated hold (ms) |
| --- | ---: | ---: | ---: | ---: |
| after-audit-13org-200rps | 1978 / 2000 | 24.02 | 14.343 | 4.69 |
| after-audit-13org-50rps | 500 / 500 | 12.72 | 0.010 | 3.89 |
| after-audit-1org-200rps | 1963 / 2000 | 25.47 | 15.333 | 4.72 |
| after-audit-1org-50rps | 500 / 500 | 11.16 | 0.008 | 4.16 |
| after-spend-13org-200rps | 1987 / 2000 | 10.17 | 0.363 | 3.20 |
| after-spend-13org-50rps | 500 / 500 | 12.96 | 0.010 | 3.66 |
| after-spend-1org-200rps | 1989 / 2000 | 11.43 | 1.684 | 3.51 |
| after-spend-1org-50rps | 500 / 500 | 12.62 | 0.010 | 3.67 |
| before-audit-13org-200rps | 1968 / 2000 | 24.72 | 14.934 | 4.46 |
| before-audit-13org-50rps | 500 / 500 | 11.82 | 0.011 | 3.97 |
| before-audit-1org-200rps | 1982 / 2000 | 22.54 | 12.377 | 4.28 |
| before-audit-1org-50rps | 500 / 500 | 11.42 | 0.010 | 4.14 |
| before-spend-13org-200rps | 1971 / 2000 | 21.97 | 11.417 | 4.41 |
| before-spend-13org-50rps | 500 / 500 | 11.88 | 0.010 | 3.92 |
| before-spend-1org-200rps | 1975 / 2000 | 23.20 | 12.562 | 4.27 |
| before-spend-1org-50rps | 500 / 500 | 12.21 | 0.007 | 3.79 |

All accepted events matched the durable capacity counter at the end of every run. Accepted admission used **two SQL RPCs**, plus transaction start and commit, before and after. Realized events per committed call stayed **1.0**. A successful bundle remains able to accept more than one event in the existing API; this campaign does not assume that arrivals form such a bundle.

Both revisions accepted all 500 calls in every final 50-RPS case. At 200 RPS, both revisions rejected calls and audit lock waits increased substantially; p95 queue-lock waits reached roughly 12–15 ms. The after audit cases accepted 98.15% and 98.9% of offered calls. Rejections occurred during initial connection use and later bursts, so they are not dismissed as warmup. Every recorded rejection occurred before the first SQL RPC; no rejected call is presented as a successful latency sample. These runs do **not** certify a 200-RPS success SLO. Read all-call latency, rejection counts, scheduler lag, in-flight first/last-quarter means and drain-inclusive throughput in each `summary.json`.

There is no repeatable normal-admission latency improvement to claim: the changed branches are empty polls and submissions that insert zero rows. Spend is an unchanged control. The direct write measurements below establish that narrower improvement.

## Physical capacity-row writes

| Case | Operations per revision | Before row rewrites | After row rewrites |
| --- | ---: | ---: | ---: |
| Empty audit claim poll | 200 | 200 | 0 |
| Duplicate audit submission | 200 | 200 | 0 |
| Full audit queue rejection | 200 | 200 | 0 |

[Before samples](before-capacity-writes.json) and [after samples](after-capacity-writes.json) include per-operation latency. The probe compares `ctid` immediately after each committed operation, with a separate observer; it introduces no triggers. The native regression cases additionally hold the capacity row locked and prove those operations finish without acquiring that row lock. Real capacity changes still update it atomically, and a blocked required record continues consuming its slot.

## Plans and conditional decisions

[The captured EXPLAIN ANALYZE plans](admission-plans.json) execute the real admission SQL over 50,000 terminal records per queue and 10,000 organizations. The capacity table has two rows. Existing-event lookup is indexed by event identity; organization policy lookup is indexed by organization identity. No admission-time history aggregation was introduced. This is a plan check for admission, not a throughput benchmark for historical reporting or consumer scans.

The [comparison](comparison.json) recomputes an optimistic 32-event / 2-ms coalescing opportunity from both scheduled and actual observed caller arrivals. Offered arrivals yield exactly 1.0 event per commit; actual arrivals yield 1.0 in all after profiles. Adding a timer would add latency without reducing committed calls for these profiles. Coalescing is therefore **not enabled**. No in-memory acceptance buffer, new task lifecycle or new configuration is introduced.

At 50 RPS, p95 lock waits are at most 0.011 ms. At 200 RPS the audit singleton is under pressure, with mean estimated hold times around 4.6–4.7 ms and significant queue-lock waiting. These higher-load results are a **limit to this change**, not proof that the bottleneck has disappeared. A partition/credit migration is not selected for the initial 50-RPS profile: coalescing provides no observed saving, the uncontrolled development host is not a deployment capacity model, and the series has not yet qualified a higher offered-rate target. Before selecting a partition cutover, reproduce the higher-load contention on the declared deployment profile and verify the throughput plateau; then apply the writer-fencing, quota and privacy requirements in the decision record. The 200-RPS results remain a required input to PR 10, and no 200-RPS reliability claim is made here.

Raw samples are gzip-compressed JSONL (`samples.jsonl.gz` and `inflight.jsonl.gz`) inside each profile directory. Recompute comparisons with:

```sh
PYTHONPATH=. uv run --frozen python scripts/benchmarks/summarize_admission.py \
  docs/benchmarks/pr4-admission
```
