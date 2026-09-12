---
title: Measure gateway concurrency
description: Reproduce a local one-token workload and diagnose durable acceptance under load.
status: experimental
audience: developers
---

# Measure gateway concurrency

Use this workload to compare gateway changes with authentication, tenant policy,
required audit, and durable spend enabled. The provider returns a fixed one-token
response. The runner bypasses the response cache while retaining normal policy
preflight. It uses the existing constant-arrival generator.

This is a local baseline, not a supported production capacity profile. The saved
[September 11 summary](../project/benchmarks/concurrency-2026-09-11/summary.json)
is historical mixed-provider, closed-loop evidence. Its
[manifest](../project/benchmarks/concurrency-2026-09-11/manifest.json) links seven
sanitized sample files. It does not establish per-pod capacity or identify the
database exception behind every audit failure.

**Prepare disposable dependencies**

Run from the repository root with the frozen development environment and a
generated Prisma client. These example containers use the same PostgreSQL 15 and
Redis 7 major versions as the repository's dependency tests. Their credentials
are fixtures for isolated loopback access.

```bash
uv sync --frozen --extra dev
uv run prisma generate --schema=./prisma/schema.prisma

docker run -d --rm --name deltallm-concurrency-db \
  -p 127.0.0.1:55432:5432 \
  -e POSTGRES_DB=deltallm_concurrency -e POSTGRES_PASSWORD=concurrency-test-only \
  postgres:15-alpine -c shared_preload_libraries=pg_stat_statements
docker run -d --rm --name deltallm-concurrency-redis \
  -p 127.0.0.1:56379:6379 redis:7-alpine

export DATABASE_URL=postgresql://postgres:concurrency-test-only@127.0.0.1:55432/deltallm_concurrency
export REDIS_URL=redis://127.0.0.1:56379/0
export DELTALLM_MASTER_KEY=sk-local-concurrency-master-20260912-example
export DELTALLM_SALT_KEY=concurrency-salt-for-local-testing-only
export DELTALLM_LOAD_API_KEY=sk-concurrency-local-fixture-key-20260912
export DELTALLM_CONFIG_PATH=tests/performance/gateway_concurrency_profile.yaml
```

Wait for `docker exec deltallm-concurrency-db pg_isready -U postgres` to succeed,
then migrate and seed the new database:

```bash
uv run prisma migrate deploy --schema=./prisma/schema.prisma
docker exec deltallm-concurrency-db psql -U postgres -d deltallm_concurrency \
  -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements'
uv run python -m tests.performance.gateway_concurrency_fixture
```

The fixture creates one organization, team, user, and scoped API key with budget
checks enabled. It uses the production key hashing implementation and rejects
the master key. It refuses to reseed an existing fixture. Reuse that fixture for
paired runs or start with a fresh disposable database; do not reset its economic
state during measurement.

**Start the fixed provider and gateway**

Use two terminals with the environment above, the same checkout, and the same
Python environment:

```bash
uv run uvicorn tests.performance.gateway_concurrency_mock:app \
  --host 127.0.0.1 --port 59441 --workers 1
```

```bash
uv run uvicorn src.main:app --host 127.0.0.1 --port 59440 --workers 1
```

Check `http://127.0.0.1:59440/health/readiness` before measuring. The profile
keeps main/telemetry pools at 20/5, legacy budget query mode, global/org preflight
at 100/50, Redis fail-closed, and both durable telemetry workers enabled.
These are controlled baseline settings, not tuning recommendations.

**Capture and compare**

Create the manifest from the same checkout and environment used to start the
gateway. It records the source hash, declared process count, selected nonsecret
settings, dependency versions, and profile hash. Inspect it against the running
process. Unknown CPU/memory limits remain null; populate actual limits when
using containers or pods. The manifest is operator-declared, not remote runtime
attestation.

```bash
uv run python -m tests.performance.gateway_concurrency_manifest \
  --api-processes 1 --output .load-results/server-manifest.json
uv run python -m tests.performance.run_gateway_concurrency \
  --label before --rate 5 --duration 10 \
  --metrics-url http://127.0.0.1:59440/metrics \
  --server-manifest .load-results/server-manifest.json \
  --output-dir .load-results/warmup
uv run python -m tests.performance.run_gateway_concurrency \
  --label before --rate 50 --duration 600 \
  --metrics-url http://127.0.0.1:59440/metrics \
  --server-manifest .load-results/server-manifest.json \
  --output-dir .load-results/before
```

Restart the gateway from the candidate revision, regenerate its manifest, repeat
the warmup, and run with `--label after`. Keep resources, dependency data shape,
worker settings, and provider behavior fixed. Compare cold-cache runs separately.
The runner accepts 5–600 seconds and up to 200 offered RPS, with 1,000 maximum
client requests in flight and a ten-second total timeout per request.

For multiple API processes, expose each process's metrics separately and repeat
`--metrics-url` for every process. A load-balanced Service URL does not scrape
every pod. Local port forwards can supply the distinct loopback endpoints.
Use one API process per pod for an unambiguous mapping.

Each run writes request samples, a summary, and periodic process metrics. The
collector exports only fixed metric names and allowlisted label values, replacing
endpoint addresses with numeric source indices. It bounds endpoints, response
bytes, samples, deadlines, snapshots, and total output bytes. Histogram buckets
are captured at the beginning and end; intermediate snapshots keep counts,
sums, and gauges. Scrape failures are explicit and invalidate a complete evidence
claim.

PostgreSQL and Redis call deltas include background workers. They are not isolated
per-request query counts. The SQL snapshot excludes its own aggregation query;
Redis command snapshots exclude `INFO`. Use the repository query-budget tests
alongside an idle/control run to attribute changes. Collect container CPU
throttling, RSS, actual DB pool use/waiters, and dependency resource limits from
their native exporters as companion evidence; the application gauges below do
not substitute for those measurements.

The proposed release target is 50 RPS for ten minutes with at least 99.9% success,
client p95 ≤150 ms, p99 ≤300 ms, and no sustained positive queue slope. The runner
records `qualification: baseline_only`; it does not certify this target. Review
failures, generator drops, scrape coverage, actual admitted work, durable backlog,
and resource evidence before making any capacity claim. Higher short-request
rates, 500 live streams, overload, and one-pod loss are separate workloads.

The separate [instrumentation regression sample](../project/benchmarks/concurrency-observability/summary.json)
uses an in-process ASGI fixture, fake Redis/provider, 10 RPS, and 200 requests
per case. Redis/cache/provider call counts are unchanged between the paired
runs. Miss p95 was 9.333 ms before and 9.316 ms after; hit p95 was 5.790 ms
and 5.957 ms. Corresponding p99 values rose from 10.720 to 11.951 ms and
6.481 to 7.159 ms. This small sample does not establish a capacity improvement
and does not exercise outbox acceptance or lifecycle sampling.

**Interpret the new measurements**

| Metric | Interpretation |
| --- | --- |
| `deltallm_telemetry_acceptance_phase_seconds` | Prepare, transaction acquisition, lock statement, enqueue SQL, commit/rollback, and total duration |
| `deltallm_telemetry_acceptance_in_flight` | Local enqueue calls in each phase; acquisition occupancy is not physical connection count |
| `deltallm_telemetry_acceptance_operations_total` | Accepted, duplicate, full, mixed, empty, error, or cancelled calls, separated by transaction ownership |
| `deltallm_telemetry_acceptance_failures_total` | Exceptions classified by queue, phase, and fixed reason |
| `deltallm_telemetry_acceptance_events_per_commit` | Actual new events per successfully committed owned transaction |
| `deltallm_telemetry_acceptance_serialized_bytes` | Serialized payload bytes retained by enqueue calls; excludes caller objects and driver buffers |
| `deltallm_http_requests_in_flight` | HTTP requests through final frame/disconnect, including work that has not passed admission |
| `deltallm_request_phase_latency_seconds` | Authentication/recheck, existing policy/provider phases, first body, response total, and application cleanup |
| `deltallm_event_loop_lag_seconds` | Scheduling delay sampled once a second by one lifecycle-owned timer |

The audit lock phase includes both capacity and organization policy locks in the
existing single statement, plus driver/network overhead. It cannot separate those
two lock waits. Acquisition includes Prisma transaction start; commit includes
the driver's commit operation. No diagnostic SQL is added to enqueue.

For `transaction_scope="external"`, the caller still owns commit/rollback.
An accepted repository result in that scope is not a durable acknowledgment;
no acquisition, commit, or committed-event histogram is fabricated for it.
Queue-full is represented by operation outcomes, independently of exceptions.
Do not add overlapping total/phase durations or subtract independently computed
percentiles.

Failure classification uses structured [Prisma codes](https://docs.prisma.io/docs/orm/reference/error-reference)
and [PostgreSQL SQLSTATE](https://www.postgresql.org/docs/15/errcodes-appendix.html).
`P2024` identifies database pool timeout; `P2028` remains a transaction error
unless the client provides a more specific typed exception. `57014` is
`statement_cancelled`, since it cannot distinguish statement timeout from
explicit cancellation by code alone. Unknown exceptions remain `unknown`.
Diagnostic records include queue, phase, reason, and a bounded event fingerprint
without raw exception text or payloads. The fingerprint is the first 16 hex
characters of SHA-256 of the event ID, or the first event ID in an audit bundle;
operators can correlate it with an event without exposing that identifier as a
metric label.

Sum per-process counters and in-flight gauges across pods. Shared audit/spend
backlog gauges describe one database queue and must not be summed per replica.
Use current queue observations and inspect staleness when workers stop.
`response_first_body` measures the first nonempty ASGI body, not token parsing.
`after_response` includes all application work after the final frame, not only
accounting. HTTP byte counters measure transferred bytes, not retained memory.

Recompute the historical summary without any private source files:

```bash
uv run python -m tests.performance.summarize_historical_concurrency \
  docs/project/benchmarks/concurrency-2026-09-11
```
