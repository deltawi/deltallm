# PR5 budget and prompt evidence — September 15, 2026

This directory records local, fixed-workload comparisons for issue #320. It proves
policy parity and bounded dependency work; it does not qualify a Kubernetes pod,
production p95 SLO, hard monetary cap, or sustainable multi-pod throughput.
The accompanying [runbook](../../../deployment/budget-prompt-performance.md)
describes rollout, counter maintenance, configuration, capacity and failure behavior.

## Environment and reproducibility

Native macOS, Python 3.11.13, PostgreSQL 15.19, Redis 7.2.5. No containers, pod CPU/
memory limits or throttling measurement. Use a disposable database named
`deltallm_concurrency`; the probes reject other names and non-loopback endpoints.
These runs used PostgreSQL port 55434 and Redis port 56379. The canonical fixed
provider used port 59441 and one API process used 59440. Fixture credentials are
the public test-only values documented in the concurrency measurement runbook.

HTTP manifests record server source SHA-256, base commit, exact Python/dependency
versions, effective settings and profile hash. The before server is clean feature
commit `9445da6c4ffe1a23c875289e269f088afd598769`; after is the PR5 working tree,
identified by its source hash, with the same base commit before committing PR5.
Later review tightened the team/model index lookup and shutdown/cache error paths;
the final service probes and regression suite cover those changes. HTTP fixtures
have no configured team/model cap, so their ordinary budget path is unchanged by
that later index fix. The final service reports also record source and harness hashes.

No local test suite ran concurrently with the recorded HTTP and initial service
comparisons. Use the checked-in profiles, real dependencies and existing runners:

```bash
export PYTHONPATH=.
# Start the fixed provider and a fresh gateway for each profile; see the runbook.
export DELTALLM_CONFIG_PATH=docs/project/benchmarks/budget-prompts-2026-09-15/http-before.yaml
uv run uvicorn src.main:app --host 127.0.0.1 --port 59440 --workers 1
uv run python -m tests.performance.gateway_concurrency_manifest \
  --api-processes 1 --output .load-results/manifest.json
uv run python -m tests.performance.run_gateway_concurrency \
  --metrics-url http://127.0.0.1:59440/metrics --label before \
  --rate 5 --duration 10 --server-manifest .load-results/manifest.json \
  --output-dir .load-results/http-before-5rps
# Repeat at 25 RPS; restart with http-after.yaml and label after for PR5.
uv run python -m tests.performance.measure_budget_dependencies \
  --entities 5000 --events 100000 --rate 50 --duration 10 \
  --output .load-results/budgets
uv run python -m tests.performance.measure_prompt_fills --output .load-results/prompts
```

Each HTTP server received 200 successful warmup requests paced at 5 RPS before
measurement. Response-cache bypass, required audit/spend outboxes and their workers,
authentication and policy checks stayed enabled. Both profiles deliberately use one
active inference slot and zero waiters; these are controlled comparison settings.
The unpaced setup warmup hit an ingress 503 and was stopped before measurement;
pacing corrected the setup. Every owned HTTP process completed graceful shutdown.

## HTTP comparison

[Comparison and metric bounds](http-comparison.json) link the four complete raw
sample/summary/metric sets. Each run lasted ten seconds with zero generator drops,
transport failures, HTTP 500s or metrics scrape errors. All errors below were
`gateway_ingress_full`.

| Offered rate | Before successes | PR5 successes | Before p95 | PR5 p95 |
| --- | ---: | ---: | ---: | ---: |
| 5 RPS | 50 / 50 | 50 / 50 | 43.72 ms | 33.57 ms |
| 25 RPS | 199 / 250 | 247 / 250 | 44.47 ms | 36.72 ms |

The inference allocation peaked at one sampled active request, zero waiters and
144 buffered bytes, returning to zero. The separate health allocation is excluded
from inference occupancy. Sampled audit depth never exceeded one, audit oldest age
stayed below 10 ms and spend oldest age stayed zero. At most one audit event remained
at the last scrape; these short runs and sampled gauges are not a long-run drain proof.
Raw summaries retain client in-flight series and all dependency-call deltas.
At 5 RPS, total SQL calls including background work were 3,264 before and 3,101 after.
At 25 RPS they were 10,028 and 11,110 because PR5 admitted more requests; compare
per-decision counts below rather than interpreting that total as amplification.

## Budget dependency budget

All five scopes are evaluated in key/user/team/organization/team-model order.
Normal reads fall from six SQL calls in legacy mode to one combined snapshot with
a configured model cap (five to one for the HTTP fixture without that cap). Both
modes now avoid history scans; verified counters are required and unavailable
budgeted counters fail closed. Real PostgreSQL tests cover each scope's denial,
precision just below a limit, missing/recreated counters, repair CAS, and concurrent
calendar resets. Reset work has a fixed four-scope/nine-RPC bound.

The service probe seeds 5,000 rows per entity table and 10,000 additional models on
one hot team, then adds 100,000 zero-cost historical events without changing policy.
It records four constant-arrival cases of 500 decisions, 50 RPS for ten seconds.
The [final report](qualified-budget/budget-report.json) identifies the final source
and links all raw samples. Every case completed all 500 decisions with no drops. Legacy used 3,000 SQL calls
per case; combined used 500. No budget query references the historical event table.
The probe asserts indexed five-row plans and a complete `(team_id, model)` counter
lookup even for the last model among 10,001 counters. Review found that the old
join could stop at the first matching counter using a team-only index; the final
lateral lookup and regression eliminate dependence on that favorable row order.

| History added | Legacy p95 | Combined p95 | Legacy / combined calls per decision |
| --- | ---: | ---: | ---: |
| 0 events | 11.95 ms | 5.56 ms | 6 / 1 |
| 100,000 events | 12.18 ms | 6.60 ms | 6 / 1 |

These final service comparisons also ran with no other local test workload.
The unchanged call count and full-key indexed plan establish bounded work; the
small latency variation does not establish a production SLO.

## Prompt fills and cold starts

The prompt probe records a real PostgreSQL/Redis comparison with 10,000 equal-priority
bindings on one scope. The partial index supplies at most one enabled binding per
canonical/legacy alias; the SQL ranks only that bounded candidate set. The serial
baseline is a harness subclass retaining sequential SETEX writes with the same
current SQL, deliberately isolating cache fill batching rather than emulating an
entire older checkout.

The [final prompt report](final-prompts/prompt-report.json) records serial p95
13.69 ms and pipelined p95 9.36 ms. For each variant, 50 cold five-scope chains at 10 RPS perform 50 SQL queries and 50
MGETs. Serial fill makes 250 SETEX round trips; PR5 makes 50 pipelines of five
commands each. The following 100 L1 hits add zero SQL/Redis calls. A wave of 25
independent L1/single-flight owners completes all 25 fills using 25 SQL queries;
a later owner reuses L2 with no SQL. This is one process sharing bounded pools of
32, not 25 Kubernetes pods. It did not demonstrate a need for a distributed fill
lock; larger real-pod cold starts remain a PR10 qualification case.

## Artifact history and validation

`dependencies/` is the initial 5,000-scope probe; `hot-team-dependencies/` adds
10,000 model counters. `final-budget/` includes the exact-decimal legacy read fix,
but predates the stronger last-model index test. `qualified-budget/` exercises the final indexed lookup against the last model.
`prompts/` and `final-prompts/`
retain the successful prompt comparisons before/after cache error hardening.
`prompts-expired-warm-probe/` retains an initial cold comparison whose following
warm assertion selected an entry after its five-second negative TTL expired;
that warm setup is invalid and excluded. No failed measurement was overwritten.

Full test-lane, migration, documentation and review results are recorded in the PR.
The user-facing runbook remains the source for deployment limitations and recovery.
