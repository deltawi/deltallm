# PR9 implementation plan: Kubernetes capacity and autoscaling

Status: implementation and local acceptance complete; PR #332 is published and awaiting CI/review.
Prepared September 22, 2026 for [issue #320](https://github.com/deltawi/deltallm/issues/320).

- Base: fetched `origin/feature/issue-320-concurrency`, commit
  `9a3f2cfc9c13f4eba332b3e331e7003acba20b03`, including merged PR8 (#331).
- Branch: `fix/issue-320-kubernetes-capacity`.
- Worktree: `.worktrees/issue-320-pr9-kubernetes-capacity`.
- Future PR target: `feature/issue-320-concurrency`.
- Contract: [RULES.md](../../RULES.md), especially sections 3, 7, 13, 15, 17 and 18.

## 1. Outcome and boundaries

Make the production deployment's resource and downstream allocations explicit,
reject configurations whose declared peak exceeds those allocations, and prove
that admitted in-flight work can drive Kubernetes autoscaling. Publish repeatable
2/3/4-pod experiments and an operator runbook with the effective configuration.

PR9 includes runtime/configuration parity, chart validation, a working custom
metrics adapter configuration, actual scale/overload evidence, and documentation.
Rendering an HPA or writing a capacity spreadsheet alone does not finish PR9.

PR10 owns sustained workload qualification, the proposed 10-minute 50/100/200 RPS
ladder, supported admitted-stream counts, soak testing and N−1 operating capacity.
PR9's short experiments must not be presented as that qualification. Provider
admission work tracked by #210 and batch scheduling work tracked by #248 remain
separate; this PR must count their allocations and accurately describe existing
enforcement without inventing another limiter.

Implementation is isolated to this worktree. Test clusters are disposable; production
promotion and PR10 capacity qualification remain outside this PR.

## 2. Verified starting point

The issue's original production table predates several merged changes. Use this
base revision and rendered configuration when implementing, not that old table.

| Area | Current implementation | Remaining PR9 work |
| --- | --- | --- |
| Process/release lifecycle | `src.server`, `_lifecycle.tpl`: one managed process, external migrations, pre-release migration hook, readiness/drain validation | Preserve; explicitly cover its interaction with capacity and HPA |
| Shutdown | 80-second application budget, 90-second pod grace, 10-second exit margin; production reserves two retiring generations | Keep the complete phase sum and overlapping-pod accounting |
| API deployment | 3–12 pods; CPU target 65%, memory 75%; 500m/2 CPU and 1/2 GiB request/limit | Explicit saturation metric and separately labelled experiment resources |
| Ingress | Existing process-local gate, active/waiter/body bounds, separate control/health allocations | Enable explicitly in production; current inherited default is disabled |
| Preflight | Existing shared Redis global/organization gate | Enable explicitly; distinguish deployment-wide preflight from per-pod live responses |
| Economic durability | Combined budget reads, audit/spend outboxes, spend operation intents already enabled | Keep parity/recovery guarantees; enumerate required workers and allocations explicitly |
| Redis failure policy | General setting still inherits `fail_open` | Explicit fail-closed production choice for required controls, verified at every consumer |
| PostgreSQL | Control 20 + foreground 8 + total telemetry 5 + telemetry worker 5 = 38 connections/process | Itemize migration/other-client/headroom allowances; preserve settlement inside the telemetry 5 |
| Redis | Critical 64 + cache 16 + bulk 16 = 96 connections/process | Validate physical critical/cache domains and all roles rather than only one aggregate ceiling |
| Existing calculator | `templates/dependency-capacity.yaml`, schema and `test_dependency_capacity.py` already reject bad DB/Redis budgets, process counts and deadlines | Extend the existing owner to HTTP/proxies, provider allocations and file descriptors |
| Runtime parity | `DependencyAllocationSnapshot` rejects DB/Redis allocation drift between startup and effective durable config | Extend only for newly governed startup allocations; never silently hot-reload pool sizes |
| Metrics | `deltallm_ingress_active{allocation="inference"}` measures an admitted ASGI lifetime, including streams | Initialize known series to zero, verify lifecycle semantics, publish per-pod custom metric |
| HPA | `hpa.yaml` contains resource metrics only; ServiceMonitor already exists | Install/configure the adapter path and test scaling at low CPU with held requests |
| HTTP | Shared upstream pool 500/100 keepalive; control pool 100/0; environment proxy mounts can add pools | Inventory actual transport objects and optional clients; preserve proxy/TLS behavior |

Current rendered production arithmetic, before PR9 changes:

```text
API peak processes = 12 maximum + 1 surge + 2 × 12 retiring = 37
API PostgreSQL = 37 × 38 + 100 reserve = 1,506 / declared 2,000
API Redis = 37 × 96 + 128 reserve = 3,680 / declared 5,000

With the optional two-replica batch-worker deployment:
worker peak processes = 2 + 1 + 2 × 2 = 7
combined PostgreSQL = 44 × 38 + 100 = 1,772
combined Redis = 44 × 96 + 128 = 4,352
```

Those ceilings are example declarations, not provisioned database/Redis capacity.
The base and eval profiles currently render respectively 5/3 peak processes and
160/100 PostgreSQL connections including reserve. Keep the eval 100-connection
regression: enabling more roles or pools must require a compatible allocation.

## 3. Design decisions and invariants

### Ownership, failure and latency

- PostgreSQL remains the durable source of truth; Redis retains existing shared
  coordination semantics. Capacity declarations are an operator contract, not a
  new reservation ledger or live source of tenant policy.
- Helm owns deployment-wide arithmetic. The existing bootstrap/client factories
  own runtime allocations. Prometheus Adapter translates existing metrics; the
  Kubernetes HPA owns replica decisions. Add no application autoscaling loop.
- Invalid static production combinations fail schema/render validation. Allocation
  mismatch at startup fails before readiness with a sanitized reason. Missing
  live infrastructure evidence is reported as unknown, never zero or certified.
- No new SQL, Redis, filesystem, or remote call is added per inference request.
  Reuse the ingress gauge; collect FD/process diagnostics off the request path.
- Preserve authentication, final-model authorization, distributed leases, durable
  acceptance, cancellation cleanup and retry boundaries. Scaling never retries a
  provider call or repairs accounting by re-executing inference.
- All metric labels use fixed categories or bounded configured identifiers. Do
  not publish credentials, URLs, tenant IDs, request IDs or raw provider secrets.

Check in `docs/design/pr9-deployment-capacity.md` with these decisions, alternatives
and rollback. Reject a second Python implementation of Helm arithmetic, a second
process supervisor, CPU-only asynchronous scaling, and treating pools as quotas.

### Typed calculation and report

Extend `dependencyCapacity` with bounded schema-defined inputs and a versioned,
structured report emitted by the existing capacity ConfigMap. Keep the current
summary fields while adding per-role/per-domain detail. Put cohesive calculation
helpers in a dedicated chart partial if the existing template becomes unwieldy.

Use a typed Python report DTO in the operator/test tooling to parse the rendered
report and compare it with runtime evidence. It must consume Helm's results;
do not maintain a second set of role/surge arithmetic in a Python CLI.

```text
peak_processes(role) = (
  maximum_replicas(role)
  + surge(role)                       # ceil(percentage × replicas), or integer
  + retiring_generations × maximum_replicas(role)
) × processes_per_pod(role)

dependency_total(domain) = Σ peak_processes(role) × owned_pool_caps(role, domain)
                         + migration_clients + other_clients + operating_headroom
```

Use HPA maximum when enabled and fixed replicas otherwise. Count only enabled
roles, using their effective merged configuration and actual rollout strategy.
Treat `Recreate` separately from rolling surge. Keep production's two retiring
generations and PR8's guard against upgrading while pods are terminating. They
are bounded operational assumptions, not a Kubernetes guarantee against arbitrary
manual scale churn, stuck termination or repeated overlapping rollouts.

Retain existing reserved-connection fields as operating reserve; add explicit
migration and other-client allowances with no double counting. Update examples
deliberately if their new totals exceed current ceilings. Never solve a rejection
by silently increasing a downstream limit. Include steady, rollout and peak totals
so the operator can see what consumes headroom.

For Redis, map critical/cache/bulk pools to declared physical capacity domains.
If pools share a server, sum them against that server; different Redis database
numbers do not create isolation. Keep critical coordination in its protected
memory/eviction domain. A separate cache endpoint must have its own connection
and memory/headroom declaration; startup/preflight checks resolve the effective
mapping without printing secret URLs.

### HTTP, provider quotas and file descriptors

Inventory every enabled client in the candidate profile, recording owner, role,
number of instances, pool ceiling, proxy multiplicity, timeout and shutdown owner:

- Provider/control HTTPX factories in `src/upstream_http.py`.
- Batch webhook transport in `src/bootstrap/batch_runtime/workers.py` and
  notification webhook client in `src/notifications/webhook.py`.
- Auth/JWKS/SSO and guardrail HTTP clients; MCP, object-storage and optional
  callback/export SDK clients where enabled.
- Prisma engine transports/processes, DB/Redis sockets, accepted inbound sockets,
  listener, pipes/logs/files, and bounded staging activity.

Count distinct transport pools, including proxy mounts; a `NO_PROXY` exclusion is
not an additional pool. Count keepalive within the connection ceiling, not on top.
Verify `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, lowercase environment handling,
`NO_PROXY`, supplied transports and SSL certificate settings against actual
constructed clients. Do not disable proxy support to make arithmetic smaller.
Keep private HTTPX compatibility logic confined to its existing factory seam.

Preserve one lifecycle owner. An enabled client whose multiplicity is currently
unbounded must gain a bounded allocation at its existing owner or be explicitly
disabled in the governed profile; an unexplained zero is not an inventory entry.
Do not refactor unrelated optional integrations simply because they exist.

Provider declarations identify a bounded, non-secret quota-domain ID, the API/
worker/model consumers sharing it, RPM, TPM, simultaneous-attempt allocation,
retry/failover allowance and reserved external usage. Validate the declared
workload/role envelope against each independent quota dimension and report the
limiting resource. Include worst-case failover concentration and conservative
input/output token bounds; do not assume model aliases have independent quotas.

Important existing limitation: ordinary router `AttemptCapacityLimit` creation
does not reserve token/request consumption; generic pre-call checks are not proof
of hard provider quota enforcement. Mark enforcement separately from allocation.
Helm rejects over-budget declarations; runtime preflight checks the effective
model catalog and rejects a claimed hard guarantee when the required existing
enforcement is absent. Soft checks remain labelled soft. Do not expand PR9 into
#210, or claim that 500 transport connections permit 500 provider requests.

Report FD ceilings per OS process, per pod, and for the declared peak placement
on a node. Python and Prisma engine processes have separate `RLIMIT_NOFILE` limits;
node aggregate capacity and connection tracking are additional constraints. Use
actual transport/pool allocations plus inbound/listener/log/staging allowance and
explicit headroom. Compare process limits in the Linux image during preflight;
Kubernetes resource requests do not configure `ulimit`.

The ASGI ingress gate does not bound idle accepted TCP sockets. Require a finite,
documented edge-to-pod connection allocation, including keepalive/slow clients and
probe connections, for an FD-checked production profile. The runbook must provide
and exercise a concrete edge configuration, not just a declaration. Validate its
maximum independently of admitted request count, and restrict direct traffic to
the intended edge/monitoring/probe paths. Treat an unbounded or unverified edge
as an unmet FD preflight condition. Do not add an unreviewed Uvicorn concurrency
limit that makes health probes compete with inference capacity.

### Saturation autoscaling

Use `autoscaling/v2` with a `Pods` metric named
`deltallm_admitted_inflight`, mapped from
`deltallm_ingress_active{allocation="inference"}`. This counts admitted application
lifetimes before final policy/provider admission; document that distinction from
successful provider streams. Include CPU utilization; retain the existing memory
target unless the experiment gives a reason to change it.

Initialize the finite ingress allocation series at lifecycle construction. Hold
the inference value through the final response/disconnect cleanup. Do not include
health, control, batch-worker metrics or a deployment-wide Redis preflight counter
in the API per-pod metric. Existing API/worker service names already distinguish
roles; test that separation rather than changing immutable deployment selectors.

Provide a pinned, reproducible Prometheus Adapter installation/configuration under
`deploy/kubernetes/monitoring/`, owned by the cluster monitoring release. Reuse an
existing adapter if present; do not install a competing `custom.metrics.k8s.io`
APIService. Include Prometheus discovery/scrape labels, namespace/pod resource
mapping, rule naming/query, metrics freshness, APIService/RBAC/TLS, network access,
and metrics-server prerequisites. Protect `/metrics` from public ingress.

The adapter rule must filter the inference series both during discovery and query,
map `namespace`/`pod`, honor the HPA resource selector and deduplicate repeated
scrapes by pod without summing duplicates. Missing/stale samples stay missing;
never use a global `or vector(0)` fallback. Fix the API ServiceMonitor's namespace
selection when the monitor is installed outside the application namespace, and
test both same-namespace and separate-namespace monitoring.

Start the candidate with a target of 70 admitted lifetimes per pod against its
100-active gate. This is an experimental target, not a throughput recommendation;
retain or adjust it only with the controlled scaling evidence. Require a positive
target below the gate, a positive warm minimum, maximum within all declared
budgets, and scale-down stabilization of 300 seconds and at least pod grace.

Adapter failure must remain visible. Kubernetes can still scale up from another
valid metric, but a failed metric prevents a downscale recommended by remaining
metrics. Test this rather than removing the custom metric on failure. An external
dependency bottleneck can make every pod saturated: respect the replica ceiling,
shed overload locally and expose exhausted dependency headroom.

## 4. Ordered implementation slices

Each slice includes tests and docs. Review/fix each slice before continuing; finish
all slices and acceptance evidence before describing PR9 as complete.

### Slice 1 — Capacity contract and complete inventory

- [x] Add the design decision and typed report/input schema described above.
- [x] Trace all enabled clients, workers, pool overrides, environment inputs and
  startup/durable configuration precedence; record a finite inventory table.
- [x] Extend the existing Helm calculator for per-domain reservations, HTTP/proxy
  allocation, provider quota dimensions, FD/process/node ceilings and diagnostics.
- [x] Extend bootstrap allocation parity and existing factory tests where needed.
  All new runtime fields are startup-only and use existing typed configuration.
- [x] Add exact-boundary and one-over-budget tests, disabled-role tests, worker
  overrides, integer/% surge rounding, retiring generations, migration overlap,
  shared/split Redis, proxy multiplicity and missing/unknown allocation tests.

Primary files: `templates/dependency-capacity.yaml`, `values.schema.json`,
`src/bootstrap/dependency_capacity.py`, `src/upstream_http.py`, their existing
tests, plus a small typed report/preflight module and focused tests. Keep any
additional module capability-specific rather than extending oversized owners.

### Slice 2 — Explicit production and experiment configuration

- [x] Explicitly pin process count, ingress bounds, preflight, combined budgets,
  required Redis failure behavior, outboxes/intents and worker roles, all DB/Redis/
  HTTP deadlines and pools, resources, migrations, probes and complete drain phases.
- [x] Production enables ingress with the current bounded 100-active/zero-waiter
  starting allocation, and explicitly selects preflight global 300/org 100. These
  are initial deployment settings requiring workload validation, not a capacity SLO.
- [x] Preserve production's existing resource requests/limits initially. Use the
  larger resources only in the separately named experiment profile.
- [x] Reject fail-open required-control configurations and ambiguous production
  choices at governed boundaries. Keep unrelated optional tier policy disabled
  unless deliberately configured; its enablement is not a side effect of PR9.
- [x] Add `values-capacity-experiment.yaml` as a documented overlay on production:
  one process; CPU 1/2; memory 2/4 GiB; foreground DB 8, control 20, telemetry 5,
  telemetry worker 5; preflight global 100/org 50; required audit/spend workers;
  combined budget reads and spend intents; PR8's 80/90-second drain/grace.
- [x] Provide fixed 2/3/4 API pod variants through one overlay plus explicit replica
  overrides, and a separate HPA 2–4 variant. Use PDB minimum 1 for the two-pod
  experiment, fixed worker count when batch is enabled, and durable shared object
  storage for any multi-pod batch case. No production durability bypasses.
- [x] Update every applicable surface in the same slice: typed/startup/runtime
  models, config/environment examples, all relevant values, schema, docs and tests.
  Chart-only capacity declarations do not need duplicate application settings.

### Slice 3 — Custom metric, adapter and HPA

- [x] Initialize/verify the existing lifetime gauge; add success/error/cancellation/
  long-stream and multiple-pod metric tests without introducing a second counter.
- [x] Extend HPA/schema/values for the named Pods metric and stabilization behavior.
  Require ingress and the adapter prerequisites for saturation autoscaling.
- [x] Check in the monitoring integration, immutable dependency versions, secure
  scrape/adapter access and copyable installation/verification commands.
- [x] Test selectors, cross-namespace ServiceMonitors, duplicate scraping, fresh
  zero, missing/stale series, and adapter failures. Leave worker scaling separate.

### Slice 4 — Reproducible experiment and preflight tooling

- [x] Reuse `tests/performance/lifecycle_cluster.py` and the existing constant-arrival
  generator. Extract only reusable cluster ownership seams; do not copy a second
  cluster lifecycle. Use a new owned kubeconfig and clean up only owned resources.
- [x] Add a typed preflight report: exact commit/image/config hash, actual pools,
  Linux FD limits, provider declaration/enforcement status, DB/Redis headroom,
  ready nodes/resources, edge connection ceiling and custom metrics API health.
  Run inside the owned test deployment; provide the same read-only operator checks.
- [x] Provision real PostgreSQL/Redis, deterministic local provider, real scrape/
  adapter/metrics-server path, and shared object storage when batch is tested.
  Seed the production `db_only` model catalog through the existing fixture path.
- [x] Run fixed 2/3/4-pod comparisons with identical offered load, provider duration,
  token/body sizes, data, warmup, resources and worker counts; vary only replicas.
  Route through the Service/edge and verify all pod traffic shares so a persistent
  connection or port-forward does not accidentally measure only one pod.
- [x] Run a held-request/stream case that raises admitted saturation at low CPU;
  prove HPA 2→3/4, ceiling enforcement, local prompt overload rejection, recovery
  and eventual warm-minimum downscale after stabilization. Record real samples,
  not injected metric values as the sole evidence.
- [x] Exercise adapter outage, one missing pod series, rollout/retiring overlap,
  slow/idle edge connections, and a brief pod-loss case. Verify required accepted
  spend/audit work remains durable/recoverable and leases/HTTP responses close.
- [x] Capture per-pod active/waiter/bytes, offered/admitted/completed/rejected rates,
  latency/TTFT and provider duration, SQL/Redis calls/wait/timeouts, queue slope/age,
  CPU throttling/RSS, FD counts, HPA decisions and dependency headroom. Bound sample
  storage, redact fixtures and retain generator drops and all failed requests.

### Slice 5 — Public documentation and rollout

- [x] Extend `docs/deployment/dependency-capacity.md` and `kubernetes.md`; link a
  focused saturation-autoscaling runbook from the deployment docs/MkDocs navigation.
  Keep `process-lifecycle.md` authoritative for shutdown and migration ordering.
- [x] Publish a generated/verified effective-settings table for base, eval,
  production and experiment, distinguishing startup-only fields and required
  operator-provided secrets, downstream budgets, edge and monitoring prerequisites.
- [x] Document the fixed/HPA experiment commands, raw evidence locations, limit
  interpretation, alerts, diagnosing missing metrics, overloaded dependencies,
  stuck terminating pods, and rollout abort/rollback conditions.
- [x] Explain `mean admitted concurrency ≈ admitted RPS × mean duration`, including
  the issue's 500-live-request examples (10 seconds ≈50 RPS; 0.4 seconds ≈1,250 RPS).
  Separate ingress occupancy, preflight occupancy, provider streams and generator
  concurrency. State that no supported pod-to-concurrency number is qualified yet.

### Slice 6 — Acceptance, CI and final review

- [x] Run focused tests first, then the complete affected suites. Before pushing,
  run all five existing Python lanes locally with their required dependencies,
  Ruff, all chart profiles, docs build and the actual autoscaling experiment.
- [x] Extend the existing Helm CI job to lint/render the experiment and exercise
  invalid capacity/drain inputs. Keep base/eval/production checks intact.
- [x] Add a required autoscaling acceptance job using the same runner/operator
  commands exercised locally. Preserve PR8's lifecycle job and include any new
  required job in `test` dependencies and result checking; no optional-service skip
  or draft-PR bypass may turn incomplete evidence green.
- [x] Preserve five exhaustive, mutually exclusive Python lanes. Add marker/
  classifier coverage only if needed; real multi-service Kubernetes experiments
  remain explicit acceptance jobs rather than disguised hermetic tests.
- [x] Review the complete diff, build a fix list, fix it and repeat until there are
  no actionable findings. Re-run checks affected by fixes; retain failure evidence.
- [ ] Publish a PR to the feature branch with exact validation/evidence links only
  after implementation and local gates pass. Update #320's six PR9 checkboxes only
  as their scope is completed; keep PR10 unchecked.

## 5. Acceptance matrix and commands

| Gate | Required proof |
| --- | --- |
| Typed inputs | Invalid/unknown governed keys, nonfinite/unbounded allocations and bad cross-field combinations fail |
| Capacity | Exact totals for every enabled role; boundary passes, one over fails for each downstream dimension |
| Runtime parity | Effective startup/durable config, proxy transports, FD limits and model inventory match the report |
| Helm | Base/eval/production/experiment lint + template + schema tests, fixed 2/3/4 and HPA, workers on/off |
| Lifecycle | Bad phase sum/grace/probe timeout rejected; required workers/outboxes and migration-before-rollout preserved |
| Metrics/HPA | Each ready API pod exposes correct zero/nonzero; held requests cause actual scale-up within declared budgets |
| Degradation | Missing adapter/series is visible; no fabricated zero, unsafe downscale, unbounded queue or lost accepted event |
| Experiments | Comparable 2/3/4-pod runs with raw samples, live resource/pool/FD evidence, bounded overload/recovery |
| Compatibility | Tenant/auth, stream cleanup, provider failover and accounting regression suites pass |
| Operator docs | Commands execute against the pinned fixture; effective values are verified, qualification remains pending |

Run from this worktree, with isolated test services and Python 3.11 as in CI:

```bash
uv sync --frozen --extra dev
uv run prisma generate --schema=./prisma/schema.prisma
uv run pytest --collect-only -qq --dependency-lane-report
uv run pytest -q tests/test_dependency_lanes.py
uv run pytest -q -m hermetic --durations=25
uv run pytest -q -m app --durations=25
uv run pytest -q -m postgres --durations=25
uv run pytest -q -m redis --durations=25
uv run pytest -q -m helm --durations=25
uv run mkdocs build --strict
uv lock --check
git diff --check
```

Use CI's documented `DATABASE_URL`, `REDIS_URL` and `DELTALLM_TEST_REDIS_URL`
test-service setup; the commands above do not provision services. Run Ruff check
and format check over the actual touched Python paths. Run fresh/upgrade migration
verification if allocation work materially touches database initialization; no
schema migration is expected. Run image/non-root/read-only startup and PR8 lifecycle
acceptance for changed startup/transport paths. The new autoscaling command and
exact Helm override examples must be checked in by slice 4, not left as prose.

## 6. Rollout, rollback and completion evidence

1. Install/verify monitoring prerequisites, actual downstream/FD/edge allocations,
   and the immutable candidate image. Record the current deployment and report.
2. Run the disposable cluster acceptance and compare effective configuration.
   Do not use production as a load-test target.
3. For an operator rollout, establish a fixed warm replica count that fits the
   checked budgets, enable production gates/durability, verify readiness and metric
   samples, then enable saturation HPA with the checked maximum.
4. Abort on missing required workers, allocation mismatch, durable backlog growth,
   excessive local rejection at the tested load, missing custom metrics, FD/pool
   exhaustion, or prolonged termination beyond the declared overlap envelope.
5. Roll back autoscaling to the last verified fixed replica count/profile; keep
   admission and durable accounting enabled. Preserve PR8 drain/migration ordering,
   wait for retiring pods, and never reduce dependency allocations underneath live
   processes. Do not fall back to fail-open controls to make readiness green.

Attach exact commit/image/tool versions, effective rendered configuration and
capacity report, local command results, runtime inventory, adapter API queries,
HPA/rollout timeline, workload manifest, sanitized raw samples and failed cases.
No production promotion or supported concurrency claim follows automatically.

## 7. Implementation validation performed

- Read `RULES.md` completely and kept all work in the isolated PR9 worktree.
- All five dependency lanes passed locally before final review: application,
  hermetic, PostgreSQL, Redis and Helm. Final classification contains 6,422 tests;
  the focused timeline and saturation regressions passed after the review fix.
- Fresh and previous-release migration paths, container startup contract, Ruff,
  generated references, documentation tests, strict MkDocs and public-site
  containment passed. The final Helm lane passed 210 tests.
- The final two-node campaign is retained at
  `/private/tmp/pr9-capacity-trial-8`. Both owned nodes were schedulable; it
  completed 600/600 fixed-profile requests,
  scaled 2→3→4 from held admitted work at low CPU, bounded overload, kept four pods
  for 339 seconds with the adapter unavailable, restored the warm minimum, killed
  a real API container, and recovered the accepted spend/audit work exactly once.
- Candidate image ID `sha256:43427327cb5b449c0b5dc233b802becb8b1f96e7c4a6323068c2dc5efe6d5f7b`
  was compared with the PR8 baseline image recorded in the campaign manifest.
  This is functional PR9 evidence; no production throughput or concurrency
  qualification is claimed before PR10.

## 8. Primary design references

- [Kubernetes HPA](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/):
  custom metrics API prerequisites, multiple-metric recommendations, missing-metric
  behavior and stabilization underpin the chosen adapter/HPA contract.
- [Prometheus Adapter configuration](https://github.com/kubernetes-sigs/prometheus-adapter/blob/master/docs/config.md):
  discovery, resource association, metric naming and query mapping define the
  integration. Pin the implementation dependency and test the complete path.
- [Kubernetes Deployment termination](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/):
  terminating pods can temporarily consume resources beyond replicas plus surge;
  retain the explicit retiring allowance and operational guardrails.
- [HTTPX environment variables](https://www.python-httpx.org/environment_variables/):
  proxy and certificate environment behavior must survive allocation validation.
  DeltaLLM's pinned client factories/tests determine the actual number of pools.
