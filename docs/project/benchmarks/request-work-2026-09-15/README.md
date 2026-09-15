# PR7 deadline and bounded-work evidence — September 15, 2026

This comparison checks inference overhead, optional callback retention and CPU
scheduling. It does not qualify Kubernetes pod throughput. See the
[operator contract](../../../deployment/request-deadlines.md) and
[implementation plan](../../../design/pr7-deadlines-bounded-work.md).

## Real HTTP comparison

The [profile](http.yaml) keeps required audit/spend outboxes and workers, operation
intents, combined budgets, policy enforcement and response-cache bypass enabled.
It deliberately admits one inference request and zero waiters, retaining PR6's
20/8/5/5 control/foreground/telemetry/worker connection budgets and one reserved
settlement connection. Optional callbacks and Presidio are disabled in this HTTP
profile. Their scheduling is measured separately below.

Native macOS, Python 3.11.13, PostgreSQL 15.19 and Redis 7.2.5; one API process and
the fixed one-token provider. CPU/memory/container limits are unknown and remain
null in the manifests. The new, isolated PostgreSQL cluster uses port 55437 and
UTC; Redis uses 56379, provider 59441 and gateway 59440. No production or existing
shared benchmark database was changed. The fixture's accepted economic records
were retained throughout the experiment.

Before is feature-branch commit `4d75b560`; after is the PR7 source. Both manifests
record the base Git HEAD because after was measured before committing. Their
different source SHA-256 values identify the exact implementations; the after
hash was checked against the application source committed in `59d72ec4`. Later Python 3.11 annotation and
guardrail registry reload/length-validation fixes change neither the measured
request execution nor the standalone probe scheduling. Both use the same frozen
Python environment and profile hash. No tests ran during measurement.

Each API receives 100 successful warmup requests paced at 5 RPS, followed by the
runner's excluded one-request precheck. Each recorded stage lasts ten seconds.
[Comparison JSON](http-comparison.json) links every raw request, summary and sampled
metric file. Percentiles include all responses and all rejections are reported.

| Offered rate | Before success | After success | Before p50 / p95 / p99 | After p50 / p95 / p99 |
| --- | ---: | ---: | ---: | ---: |
| 5 RPS | 50 / 50 | 50 / 50 | 28.78 / 32.03 / 51.89 ms | 36.50 / 42.01 / 62.18 ms |
| 25 RPS | 192 / 250 | 176 / 250 | 36.20 / 44.45 / 53.05 ms | 35.53 / 50.38 / 57.86 ms |

There were zero HTTP 500s, transport failures, generator drops or failed metrics
scrapes. All 58 before and 74 after rejections at 25 RPS were
`gateway_ingress_full`. These samples do **not** demonstrate improved ordinary
HTTP tail latency: p95 increased by 9.98 ms at 5 RPS and 5.93 ms at 25 RPS, and the
one-slot profile admitted fewer requests in the latter stage. Do not attribute
that difference solely to deadline bookkeeping or infer a production limit from
these short, unthrottled host measurements. PR10 must qualify the final profile.

At 5 RPS, both runs executed exactly 3,503 PostgreSQL statements including
background work, and every Redis command delta matched. Allocated database
operation deltas also match. Deadline propagation adds no SQL, Redis or network
operation. The existing PostgreSQL tests retain the PR6 intent/receipt SQL budget
and representative query-plan assertions. At 25 RPS total SQL calls were
10,135 / 9,233 before/after; those stages admitted different request counts and
must not be used as an isolated per-request call comparison.

Inference occupancy never exceeded one, waiters stayed zero, and buffered request
bytes peaked at 144 and returned to zero. Sampled audit/spend backlog stayed at
most one event; the final scrape can still contain an event awaiting its worker.
The separate [post-shutdown observation](drain-observation.json) records no
nonterminal outbox records, unknown/dispatched operations or pending capacity
counters. It includes warmups and excluded setup runs as well as the comparison.
[Process observations](process-observations.json) confirm application shutdown
completed; Uvicorn re-raises SIGTERM after its graceful shutdown on this runtime.

The [excluded setup samples](setup-precheck/note.json) preserve an initial harness
check that treated that normal signal exit as failure. A second
[excluded probe](setup-precheck/keyword-contract/note.json) did not accept the
baseline callback keyword signature. Review restored keyword invocation in PR7,
added a regression, and repeated the final after HTTP sample and both component
probes. The comparison above contains only the final applicable samples.

## Slow callbacks and CPU work

Run the same [component probe](https://github.com/deltawi/deltallm/blob/59d72ec441b06e0faa6b2cae96caa90fb086c4bf/tests/performance/measure_request_work.py)
against both checkouts using `PYTHONPATH`. It records raw samples and exact source
hashes in [before](component-before.json) and [after](component-after.json).
Its producer and sampler share the component event loop; arrival lateness is
included in the raw data. It is not an independent HTTP capacity test.

The callback probe offers 1,000 distinct 4-KiB events at 200/second to a deliberately
stalled async integration. PR7 uses an explicit 16-delivery limit with concurrency
two and the default byte/deadline bounds. Before retains 1,000 tasks and starts
all 1,000 integration calls. After retains 16 deliveries, starts two while stalled,
and drops excess optional work. Releasing the integration drains all admitted work
and returns task/byte occupancy to zero. The after plateau is 546,420 charged bytes.
Peak traced Python allocations, including the probe, fall from 13,193,760 to
577,903 bytes. These are traced allocations, not whole-process RSS or SDK memory.

The CPU probe injects a fixed native, GIL-releasing calculation of 60,000 PBKDF2
iterations at Presidio's inspection boundary: 240 inspections offered at 80/second,
with the same work in both versions. It measures scheduling, not Presidio NLP
model throughput or detection accuracy. All inspections complete in both runs.

| CPU probe metric | Before | After |
| --- | ---: | ---: |
| Event-loop lag p95 / p99 | 2.29 / 5.65 ms | 0.72 / 0.79 ms |
| Inspection latency from scheduled arrival p95 / p99 | 7.18 / 8.19 ms | 7.45 / 17.61 ms |
| Pending inspections / charged bytes after drain | 0 / 0 | 0 / 0 |

Offloading reduces loop interference in this probe, with thread scheduling adding
some operation tail latency. It does not promise parallel speedup for Python code
or preemption of arbitrary C extensions that hold the GIL. Barrier-based regression
tests separately prove finite task/byte admission, local overload, bounded shutdown,
and retained capacity after timeout/cancellation until the real work completes.

The actual fallback email regex is also measured on a long local-part string with
no address. At 5,000 / 10,000 / 20,000 characters, before takes
26.60 / 105.27 / 416.56 ms; after takes 0.06 / 0.12 / 0.26 ms. Starting only at a
local-part boundary removes repeated suffix scans while retaining email detection.

## Reproduction and validation

Use the [canonical fixture instructions](../../../deployment/concurrency-measurement.md)
to provision a new disposable database, migrate it, enable `pg_stat_statements`
and seed once. For the native setup above, use port 55437 and UTC. Reuse the same
fixture for both source revisions; never reset its accepted economic records.

```bash
export PYTHONPATH=.
export DELTALLM_CONFIG_PATH=/absolute/path/to/pr7/docs/project/benchmarks/request-work-2026-09-15/http.yaml
# Start the fixed provider and each API using the canonical instructions.
# Pace 100 successful warmup requests at 5 RPS, then capture each stage:
uv run python -m tests.performance.gateway_concurrency_manifest \
  --api-processes 1 --output .load-results/server-manifest.json
uv run python -m tests.performance.run_gateway_concurrency \
  --metrics-url http://127.0.0.1:59440/metrics --label before \
  --rate 5 --duration 10 --server-manifest .load-results/server-manifest.json \
  --output-dir .load-results/before-5rps
# Repeat at 25 RPS; stop the API gracefully, switch checkout, and repeat as after.
# Run the same component probe from each checkout, changing mode/output:
uv run python /absolute/path/to/pr7/tests/performance/measure_request_work.py \
  --mode before --output .load-results/component-before.json
```

Local validation covers the complete application, hermetic, PostgreSQL, Redis and
Helm lanes, with additional real-HTTP stream expiry across all four text formats.
Tests verify auth/hook/settlement expiry, preserved keyword callback compatibility,
payload snapshots, oversized work, SDK retirement, bounded shutdown, late thread
completion, restart-only settings and rendered profile parity. Required CI also
checks UI, migration paths, lint and strict public documentation before merge.
