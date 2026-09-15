# PR6 spend recovery evidence — September 15, 2026

This comparison measures the cost of recording an ordinary provider operation before
execution and accepting its receipt through reserved settlement capacity. It demonstrates
bounded work, recovery and predictable local rejection. It does not qualify Kubernetes
pod throughput or hard monetary budgets. See the [runbook](../../../deployment/spend-recovery.md).

## Workload and reproduction

Native macOS, Python 3.11.13, PostgreSQL 15.19 and Redis 7.2.5; one API process, one
fixed one-token provider, no container CPU/memory limits. PostgreSQL used port 55434
with `pg_stat_statements` preloaded, Redis 56379, provider 59441 and gateway 59440.
The existing disposable `deltallm_concurrency` fixture was reused without resetting
its economic state. The manifest records exact source, commit, dependency versions
and effective settings; resource limits remain unknown, not inferred.

Both servers ran commit `19fd00237810fd1a147049df0e5ff960764e75f3`. Before disables
operation intents; after enables them. This isolates protocol activation on the same
source rather than comparing different feature-branch revisions. A subsequent
review fix preserves blocked-worker diagnostics on identical receipt replay; the
measured first-acceptance path and SQL call budget are unchanged. The final SQL
report and PostgreSQL regression cover that fix. The checked-in
[before](http-before.yaml) and [after](http-after.yaml) profiles retain combined
budgets, required audit/spend outboxes and workers, response-cache bypass, identical
20/8/5/5 control/foreground/telemetry/worker connection totals and one active inference
slot with zero waiters. After carves telemetry into admission 4 and settlement 1.

Use the [canonical fixture instructions](../../../deployment/concurrency-measurement.md)
to provision dependencies and start the provider, then run each profile from this
checkout with the same documented test-only environment:

```bash
export PYTHONPATH=.
export DELTALLM_CONFIG_PATH=docs/project/benchmarks/spend-recovery-2026-09-15/http-before.yaml
uv run uvicorn src.main:app --host 127.0.0.1 --port 59440 --workers 1
# In a second terminal, after readiness and 200 successful warmup requests at 5 RPS:
uv run python -m tests.performance.gateway_concurrency_manifest \
  --api-processes 1 --output .load-results/before-manifest.json
uv run python -m tests.performance.run_gateway_concurrency \
  --metrics-url http://127.0.0.1:59440/metrics --label before \
  --rate 5 --duration 10 --server-manifest .load-results/before-manifest.json \
  --output-dir .load-results/before-5rps
# Repeat at 25 RPS. Gracefully stop the API, switch to http-after.yaml,
# warm the replacement identically, generate its manifest, and repeat with label after.
uv run python -m tests.performance.measure_spend_operation_sql \
  --output .load-results/sql-report.json
```

No local test suite ran during measurements. Each API received 200 paced warmup
requests before the runner's own excluded precheck. The [process observations](process-observations.json)
record successful graceful shutdown for both APIs and the provider. The initial
[setup manifest](setup-precheck/http-before-manifest.json) belongs to an excluded
precheck: PostgreSQL had not preloaded its query-count extension, so the runner
stopped before recording requests; the owned servers shut down, the idle database
was restarted with the extension, and the complete comparison below started fresh.

## HTTP results and limits

[Comparison](http-comparison.json) links every raw request, summary and sampled
metric file. All four ten-second runs had zero generator drops, metrics scrape
failures, HTTP 500s and transport failures. Every rejection was `gateway_ingress_full`.
Percentiles include all responses; rejection counts are never hidden.

| Offered rate | Before success | After success | Before p50 / p95 / p99 | After p50 / p95 / p99 |
| --- | ---: | ---: | ---: | ---: |
| 5 RPS | 50 / 50 | 50 / 50 | 26.38 / 35.31 / 39.49 ms | 29.13 / 37.56 / 51.08 ms |
| 25 RPS | 246 / 250 | 194 / 250 | 30.24 / 36.65 / 48.87 ms | 35.66 / 44.04 / 46.73 ms |

The extra durability transaction adds measurable latency and, in this deliberately
one-active-slot profile, reduces successful throughput at 25 offered RPS from 24.6
to 19.4 RPS. This is a reliability/correctness tradeoff, not a throughput improvement.
Do not use this profile as a production concurrency recommendation. Canary rollout
must qualify the extra transaction under its real admission and downstream budgets.
At 5 RPS p95 increased 2.25 ms; both protocol operations retain independent 250 ms
whole-operation deadlines. No pool size or request timeout was increased.

Sampled provider HTTP means ranged from 0.98 to 1.31 ms; their phase counts and means
are retained in the comparison. Do not subtract independent percentiles to estimate
gateway overhead. Inference occupancy peaked at one, waiters at zero and buffered
bytes at 144; each returned to zero. Audit and spend depths stayed within one sampled
event, with oldest sampled ages below 8 ms and 13 ms respectively. One queued event
could remain at the last scrape, so these short sampled traces do not prove long-run
queue stability. The [post-shutdown database check](drain-observation.json) records the durable drain
separately: 446 accepted operation receipts, no dispatched/unknown work, and zero
nonterminal audit/spend records or pending capacity counters.
Unknown-operation gauges stayed zero. Samples can miss short settlement occupancy;
zero sampled occupancy does not imply that allocation performed no work. After-run
transition deltas matched every successful request's dispatch and receipt.

Total SQL calls including background work were 3,088 / 3,494 before/after at 5 RPS,
and 11,030 / 10,289 at 25 RPS. The latter admitted different numbers of requests;
use the isolated budget below to assess per-operation amplification. Raw summaries
retain Redis command deltas and client in-flight series.

## Dependency budget and representative plans

The [SQL report](sql-report.json) records the implementation hash, counts and sanitized
`EXPLAIN (ANALYZE, BUFFERS)` plans against 100,000 historical spend events, 10,000
retained completed outbox rows and 1,000 expired operations in disposable migrated
schemas. No fixture money or production table was reset.

| Operation | Transactions | SQL statements |
| --- | ---: | ---: |
| Before: ordinary receipt enqueue | 1 | 2 |
| After: pre-provider intent | 1 | 3 |
| After: reserved receipt | 1 | 2 |
| After total, one provider attempt | 2 | 5 |

Counts include native deadline setup and the separate lock statement, and exclude
transaction start/commit RPCs. Including those, Prisma RPCs rise from 4 to 9. A retry
adds one bounded intent-append transaction; no transaction spans provider I/O.
Receipt acceptance neither takes the global admission/capacity lock nor acquires
another outbox slot. PostgreSQL tests hold both admission locks while accepting an
already-reserved receipt through the separate allocation.

Admission uses indexed event identities and the singleton capacity row. Recovery
locks at most 100 expired candidates through the partial expiry index and updates
their transaction-local tuple locations, avoiding a retained-history target-table
scan. The PostgreSQL test lane enforces these query plans and exact SQL budgets.
Review found and removed the previous full recovery target scan and unnecessary
admission round trips before capturing this evidence.

## Correctness and rollout evidence

Native tests cover shared-capacity races between independent clients, cancellation
before commit, process kill before/after receipt, unknown recovery, late receipts,
conflicting replay, stale-worker fencing, settled identity retention, tenant scope,
and atomic reconciliation with its required audit. HTTP regressions cover all ordinary
modalities, catalog/deployment pricing changes during I/O, pre-provider rejection,
secondary failure handling, all terminal stream aliases and a real early disconnect.

Fresh, last-release and shared-feature migration verification belongs to schema
child [#328](https://github.com/deltawi/deltallm/pull/328); application and complete
CI evidence belongs to [#329](https://github.com/deltawi/deltallm/pull/329). The migration
remains additive and requires the documented coordinated window plus a restored-size
rehearsal. No production database or Kubernetes cluster was tested or modified.
