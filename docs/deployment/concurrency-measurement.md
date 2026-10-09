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

The runner requires a successful one-token warmup before recording load; warmup
is excluded from request and dependency deltas. The fixture includes the explicit
organization model grant required by enforced callable-target policy. The
manifest reads `DELTALLM_CONFIG_PATH` and includes effective ingress, auth fallback,
and all four database allocation budgets.

This is a local baseline, not a supported production capacity profile. Use the
[current RPS report](../project/issue-320-rps-report.md) for results and the
[evidence restore procedure](../project/issue-320-rps-reproduction.md#restore-historical-records)
for earlier comparisons. Historical measurements are not a release certificate.

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
The runner accepts 5–600 seconds and up to 500 offered RPS, with 1,000 maximum
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

An export failure stops the active load instead of continuing without samples.
Dependency clients and output files are closed after startup failure, cancellation,
or a failed close of another client.

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

## Native accounting qualification on kind

Use `tests.performance.run_native_qualification` for the complete native
accounting profile. This is not the legacy baseline above. First pass the
regression, migration, chart, UI, and container checks. Commit the tested source,
then build that commit with the repository Dockerfile. The runner rejects a dirty
checkout, a reused output directory, or an image whose source hash differs from
the checkout. Use kind v0.31.0. The cluster owner pins Kubernetes v1.34.3 by digest
and creates a separate kubeconfig. It does not use the current cluster or Rancher.

Fixture imports retain their repository tags. For metrics-server, the runner
checks the selected platform manifest and uses its immutable digest reference
on every owned node. The evidence also records the original registry digest.
The application source hash includes Python sources, the frozen lock, the
dependency manifest, and the Prisma schema. Generated-client inputs cannot
change without changing this identity.
The canonical build uses recursive client types and precompiles that client.
The image smoke imports the actual API and native-role modules at a 1 GiB memory
limit. This checks startup memory before the full cluster run.

```bash
export DOCKER_CONTEXT=your-isolated-test-runtime
docker build -t deltallm-native:qualification .
uv run python -m scripts.check_lifecycle_image --image deltallm-native:qualification \
  --output artifacts/qualification/native-image-smoke
uv run python -m tests.performance.run_native_qualification \
  --kind /path/to/kind-v0.31.0 --image deltallm-native:qualification \
  --output artifacts/qualification/native-qualification-fresh
```

The owned two-node cluster has four API processes, two minimal accounting request
processes, and one native projection process. Each API and accounting process has
a two-core CPU limit and a 1 GiB memory limit. The generator has four synchronized
processes, a four-core CPU limit, a 1 GiB memory limit, and at most 1,000 active
requests. Record the Docker VM's physical CPU and memory as well: these are
container limits, not dedicated physical cores. HPA is off during this series.

Traffic uses four direct in-cluster per-pod services, with one generator shard per
API process. This isolates the gateway from ingress or load-balancer behavior.
It does not certify an external edge. The same generator must first pass a direct
1,000 RPS provider check. It then runs 30-second checks at 50, 100, 200, and 500
RPS. All four short checks must pass before the ten-minute series starts at those
same rates. No cooldown occurs inside an arrival window. Between stages, the
runner records and waits for grants, terminal work, reservations, and native
reporting to drain. It does not clear the ledger or restart dependencies.

Each stage has three independent results:

- Throughput: at least 99.9 percent valid fixed responses and complete generator,
  metrics, dependency, and resource evidence. Missing observations are failures.
- Economics: every successful response has the exact fixture charge, all four
  budget scopes match native facts, reservations drain, and the legacy spend
  table receives no native charge. The separate warmup charge is included.
- Latency: client p95 at most 150 ms, p99 at most 300 ms, and client in-flight
  slope at most 0.01 requests per second over the middle 80 percent of arrivals.

Raw samples are compressed JSONL. Fixed error codes, client phases, source
metrics, resources, PostgreSQL/Redis deltas, and exact reconciliation are retained.
Bounded cgroup CPU counter reads run before and after each stage, outside arrivals.
Their window includes warmup and artifact transfer. Generator counters cover its
container lifetime. Missing counters and resets stay explicit; they are not zero.
Client in-flight slope is not a server queue measurement; inspect the recorded
ingress and accounting queue gauges too. Dependency deltas include background
work. Do not subtract independent percentile values to claim gateway overhead.
After the series, a bounded offline vacuum/analyze check records append-heavy
table and index sizes and vacuum state. It is not part of request latency.

Do not change code, resource limits, prices, or budgets during the series. If a
failure requires a code change, build a new clean image and start a new evidence
directory. Keep the failed evidence. These fixed short requests do not replace
streaming, batch, Realtime, provider-loss, or production-scale tenant tests.

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
explicit cancellation by code alone. The database allocation adapter preserves
these classifications through its typed availability wrapper. Missing, unknown,
cyclic, or more than eight nested wrapper causes produce `database_unavailable`;
unrelated exceptions remain `unknown`. Classification follows only the adapter's
explicit cause and never parses exception messages or implicit exception context.
Diagnostic records include queue, phase, reason, and a bounded event fingerprint
without raw exception text or payloads. The fingerprint is the first 16 hex
characters of SHA-256 of the event ID, or the first event ID in an audit bundle;
operators can correlate it with an event without exposing that identifier as a
metric label.

Sum per-process counters and in-flight gauges across pods. Shared audit/spend
backlog gauges describe one database queue and must not be summed per replica.
Use current queue observations and inspect staleness when workers stop.
`response_first_body` measures the first nonempty ASGI body, not token parsing.
An observed disconnect ends HTTP occupancy immediately, even while the application
is still cleaning up. Later discarded body frames do not count as transferred bytes.
`after_response` includes all application work after the final frame or observed
disconnect, not only accounting. HTTP byte counters measure transferred bytes,
not retained memory.

The original closed-loop samples remain in one compressed regression fixture.
The test checks their hashes, allowed fields and original summary. Restore other
historical records with the reproduction guide linked above.
