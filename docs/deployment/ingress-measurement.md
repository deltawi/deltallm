---
title: Ingress and authentication measurement
description: Local admission comparison and real dependency operation budgets for concurrency PR2.
status: experimental
audience: developers, operators
---

# Ingress and authentication measurement

The September 14, 2026 check demonstrates bounded admission on one local API
process. At 100 offered requests/second for 10 seconds, the one-active-request
profile completed 236 requests and rejected 764 at ingress. Rejection p95 was
2.32 ms, below the proposed 50 ms local target. This is an overload behavior
check, not a recommended production limit or a sustainable throughput rating.

Both profiles run the same candidate code, authentication policy, fixed one-token
provider, required audit and spend outboxes/workers, legacy budget checks, and
response-cache bypass. The comparison isolates the optional ingress switch.
The gateway and dependencies ran natively on macOS with Python 3.11.13,
PostgreSQL 15.19, and Redis 7.2.5. API processes: one; configured connection
ceilings: control 20, foreground 8, acceptance 5, worker 5, totaling 38.
No pod CPU/memory limit, CPU throttling, RSS ceiling, or multi-pod behavior was
qualified. The manifests include the exact source commit/hash, profile hash,
runtime versions, and effective ingress/auth/database settings.

## HTTP results

Each run offered and started 1,000 requests, with zero generator drops and zero
metrics scrape failures. Latency includes the complete client response.

| Result | Ingress disabled | One active, zero waiters |
| --- | ---: | ---: |
| Successful responses | 165 | 236 |
| HTTP 503 | 553 | 764 |
| HTTP 500 | 255 | 0 |
| Client transport failures | 27 | 0 |
| Overall p95 | 1,036.67 ms | 41.73 ms |
| Ingress-rejection p95 | Not applicable | 2.32 ms |

All 764 bounded-profile errors were `gateway_ingress_full`. Sampled inference
occupancy never exceeded one; waiters stayed zero, retained raw body bytes peaked
at 144, and all three returned to zero. Scrapes are samples, not a proof of every
instantaneous peak; barrier-based ownership tests cover those invariants.

The disabled baseline includes database/audit capacity failures and unclassified
responses. A captured HTTP 500 stack shows failure persistence itself exhausting
the telemetry allocation while handling an earlier request error. The bounded
profile avoids that path under this workload. Required-persistence admission and
failure-path handling remain part of the later spend/finalization work; these
results do not certify larger ingress values or make the disabled profile safe
under overload.

The [comparison](../project/benchmarks/ingress-auth-2026-09-14/comparison.json)
links the raw request samples, summary/manifests, per-process metrics, and complete
dependency-call deltas. The initial `disabled-with-local-tests` run is retained
as supplementary evidence and excluded from the comparison because a local
hermetic suite was running concurrently. The final disabled and bounded runs had
no other local test workload. A setup run that returned only permission denials
was invalid; the fixture now grants its organization explicit callable-target
access, and the runner requires a successful warmup before collecting load.

## Authentication operation budget

The [authentication report](../project/benchmarks/ingress-auth-2026-09-14/auth-counts.json)
uses the actual key repository through `AllocatedPrisma` and real Redis. Counts
describe `KeyService`, excluding fixture cache deletion and separate organization
lifecycle policy work. They are not complete gateway request counts.

| Case | Requests | Cache read attempts | Key SQL queries | Cache write attempts |
| --- | ---: | ---: | ---: | ---: |
| Warm key | 100 | 100 | 0 | 0 |
| Sequential cold key | 100 | 100 | 100 | 100 |
| Simultaneous cold key | 20 | 20 | 1 | 1 |
| Injected cache read/write outage | 100 | 100 | 100 | 100 |

Every case authenticated successfully and ended with zero owned auth tasks and
database slots. Cache-outage counts are attempted calls: the fault is injected
at the cache boundary while PostgreSQL remains real. Distinct-key floods,
revocation across real Redis clients, expiry, queue deadlines, shutdown, and
cancelled/overdue native queries have separate regression tests.

The report also contains the actual key query's redacted `EXPLAIN ANALYZE` plan.
It returns one row under `LIMIT 1`; at this tiny fixture cardinality PostgreSQL
chooses sequential scans for several one-row tables. This does not qualify
production-cardinality query latency. Conditions/output expressions are omitted
so the hashed credential cannot enter the artifact.

## Reproduce

Follow [Measure gateway concurrency](concurrency-measurement.md) to generate the
client, start isolated PostgreSQL/Redis, apply migrations, seed the organization,
and start the fixed provider. Use the documented fixture credentials and a fresh
database named `deltallm_concurrency`. The original measurements used PostgreSQL
port 55434 and Redis port 56379; the default documented ports also work.

For each profile, start a fresh gateway process with the selected configuration:

```bash
export DELTALLM_CONFIG_PATH=docs/project/benchmarks/ingress-auth-2026-09-14/ingress-disabled.yaml
# For the bounded run, select ingress-one-active.yaml instead.
uv run uvicorn src.main:app --host 127.0.0.1 --port 59440 --workers 1
```

From another terminal with the same environment and checkout, create the manifest
and measure. Use a different output directory and `--label after` for the bounded
run. Send SIGTERM to the API process and wait for graceful shutdown between runs;
signalling its entire process group also interrupts Prisma's child engines.

```bash
uv run python -m tests.performance.gateway_concurrency_manifest \
  --api-processes 1 --output .load-results/server-manifest.json
uv run python -m tests.performance.run_gateway_concurrency \
  --metrics-url http://127.0.0.1:59440/metrics --label before \
  --rate 100 --duration 10 --server-manifest .load-results/server-manifest.json \
  --output-dir .load-results/ingress-disabled
uv run python -m tests.performance.measure_auth_fallback \
  --output .load-results/auth-counts.json
```

Run the auth measurement with the gateway stopped: it deliberately deletes only
the fixture key's cache entry. Production qualification still needs the declared
pod resources, longer runs, realistic payloads and streams, multiple tenants and
replicas, dependency failure/recovery, and complete persistence outcomes.
