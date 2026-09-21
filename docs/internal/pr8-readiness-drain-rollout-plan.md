# PR8 implementation plan: readiness, drain and release ordering

Status: implementation in progress; final review and acceptance gates remain open.
Prepared September 21, 2026 for [issue #320](https://github.com/deltawi/deltallm/issues/320).

- Base: latest fetched `origin/feature/issue-320-concurrency`,
  `4d856670e54e689ea4fc1ceead787730e5edcf5c` (includes merged PR7 and its cancellation fix).
- Branch: `fix/issue-320-readiness-drain-rollout`.
- Worktree: `.worktrees/issue-320-pr8-readiness-drain-rollout`.
- Future PR target: `feature/issue-320-concurrency`.
- Engineering contract: [RULES.md](../../RULES.md).

## 1. Outcome and scope

An API or batch-worker pod must become ready only after its required dependencies
and workers have actually started. On termination, it must stop admitting new work,
finish or safely interrupt existing work within one shutdown budget, and leave
accepted durable records recoverable. A release must finish its migration stage
before starting new API or worker replicas.

PR8 includes implementation, settings, Helm validation, operator documentation,
failure tests, exact-image startup/termination tests, a controlled rolling-update
and pod-loss experiment, and review/fix iterations. A subprocess test alone does
not satisfy the container/Kubernetes acceptance scope.

PR9 still owns broader HPA/resource/profile tuning. PR10 still owns sustained
throughput and supported stream-concurrency certification. PR8 must provide its
own before/after latency, dependency-call and shutdown evidence; it must not defer
all measurement or all pod-loss testing to those later slices.

## 2. Verified starting point

Paths below are relative to the repository root at the recorded base.

| Existing path | What exists | PR8 gap |
| --- | --- | --- |
| `src/routers/health.py` | Concurrent dependency probes, one-second deadlines, missing DB/Redis clients fail readiness, process-only liveness | Expectations for all configured workers, process startup/drain state, bounded probe refresh, recovery behavior and safe details |
| `src/telemetry/lifecycle.py` | `WorkerHealth`, startup waiting and `stop_tasks_before_deadline` | Reuse these contracts across process shutdown; do not create another worker supervisor |
| `src/bootstrap/selector.py` | Selectors require the durable spend worker and reuse its health | Preserve that requirement when building role-specific readiness |
| `src/main.py` | Bootstrap composition and reverse-order `AsyncExitStack` cleanup | Cleanup begins after Uvicorn's connection drain; no shared process deadline |
| `src/ingress.py`, `src/middleware/ingress.py` | Bounded ingress, separate health allocation, permit held through response cleanup | No drain state; disabled ingress bypasses its middleware checks |
| `src/bootstrap/batch_runtime/lifecycle.py` | Stops claim loops before concurrent drains; 30-second worker, five-second cancellation and one-second transport limits | Caps are independent of every other shutdown phase |
| `src/bootstrap/runtime_services.py` | Spend/notification drains overlap; other closers use an exit stack | Still independent of audit, email, auth, batch and server shutdown |
| `src/billing/spend_ingestion.py`, `src/services/audit_service.py`, `src/services/email_outbox_service.py` | Local worker shutdown deadlines and durable outboxes | Accept a caller deadline; stopping one replica must not wait for a cluster-wide backlog to become empty |
| `Dockerfile`, `src/prisma_bootstrap.py` | Every default container runs Prisma then raw Uvicorn | No managed early-drain entrypoint, no external-migration verification mode, no finite subprocess deadline |
| `deploy/kubernetes/helm/templates/migration-job.yaml` | Optional job with `post-install,post-upgrade` hooks; default organization-deletion coordinator | Migration is not ordered before rollout; job depends on regular release resources |
| Helm `deployment.yaml`, `batch-worker-deployment.yaml`, `_pod.tpl`, `configmap.yaml` | Both roles run the same API process with different config; shared 30-second pod grace; checks for individual telemetry/email limits | Validate the complete shutdown budget and production entrypoint, not each timeout in isolation |
| `docs/deployment/database-migrations.md` | Correct manual migration-before-rollout contract | Make the chart enforce an equivalent sequence and update stale raw-Uvicorn overrides |

The issue's original claim that dependency checks are sequential is obsolete.
Keep PR3/PR6's concurrent probes and allocation checks, extending their tests.
The locked Uvicorn version is `0.40.0`; its `Server.shutdown()` waits for
connections/tasks before sending lifespan shutdown. The image currently installs
range-based `requirements.txt`, so its Uvicorn version is not guaranteed to match.

## 3. Design decisions and invariants

### One process lifecycle, existing service owners

Introduce a small typed process-lifecycle object, constructed by bootstrap and
shared with the server adapter, ingress and readiness. Its states are
`starting -> serving -> draining -> stopping -> stopped`; failed startup never
becomes serving, and drain is irreversible. Repeated termination signals cannot
reset the deadline or reopen admission.

`src/main.py` remains composition-only. Put lifecycle policy in a focused module
and bootstrap wiring in `src/bootstrap/`. Keep each existing runtime responsible
for its workers, clients and cleanup. Extend `stop_tasks_before_deadline` where
needed rather than inventing another task supervisor. The lifecycle object holds
only finite process-local state; PostgreSQL remains the source of truth for
accepted audit, spend, batch and business delivery.

The lifecycle is process-scoped, not tenant-scoped. It must not change authorization,
pricing attribution, lease fencing, cache keys or provider retry boundaries.
Readiness and shutdown must never retry a provider operation to repair accounting.

### Required readiness is based on configuration and role

Construct an explicit, bounded inventory from the validated effective config and
bootstrap results. Presence checks alone are insufficient: a required worker that
failed to be constructed must make readiness fail. No runtime scan or plugin
discovery on a probe request.

| Component | Readiness contract |
| --- | --- |
| Main/control and foreground PostgreSQL; critical Redis | Required, using existing owned clients and allocations |
| Telemetry acceptance/worker PostgreSQL | Required when an outbox is configured |
| Settlement PostgreSQL | Required when spend operation intents are enabled |
| Audit/spend workers | Required when configured on this role; preserve selector and intent-recovery requirements |
| Organization lifecycle authorization and configured deletion/cache-invalidation workers | Require startup completion and live tasks; retain authoritative privacy/auth checks |
| Routing generation and required config/policy refresh owners | Preserve existing reconciliation/freshness rules; missing/dead expected owners fail |
| Batch executor, completion outbox, webhook and correctness-critical lease/session workers | Required when enabled on this role, including split batch-worker deployments |
| Email worker | Preserve current readiness gating when enabled; configuration failure cannot silently make it disappear |
| Optional cache Redis, callback integrations, budget notifications, audit policy wake-up listener, retention/observability tasks | Report bounded degraded state without withdrawing otherwise usable inference capacity |

Give enabled workers an actual startup acknowledgement, not just `create_task()`.
Reuse `WorkerHealth` and existing startup helpers. Derive worker criticality once
in bootstrap; health routes should not duplicate policy or infer it by reflection.
Dead critical tasks and stale authorization/routing state bypass any probe cache.

Keep dependency checks concurrent and at most the current six per refresh: one
Redis `PING`, plus two SQL probes in legacy mode, four with outboxes, and five
with settlement enabled. Add no provider/MCP calls and no new client pools.

Use one owned refresh per process with a short, explicit cache (proposed one
second). Concurrent health callers share that refresh within the existing bounded
health allocation; do not build an unbounded waiter list or background polling
system. A stale/missing result fails closed. Probe timeouts must retain ownership
of any cancellation-resistant work until completion, following PR7's rule.
Cancelling a refresh observes/cancels child probes and cannot create replacement
capacity while old work is still live.

#### Review decision: bounded readiness and required-write contention

The Kubernetes experiment found two spend-persistence rejections among 100
requests, with metrics showing a full settlement gate. Review reproduced the
health-traffic race on a single connection. A subsequent run exposed overlapping
business settlements as a second cause, returning 99/100 successes after the
probe-only fix. The zero-waiter policy shed already-admitted economic acceptance.

`DatabaseOwner` remains the single admission owner. It permits one waiter only
while its one readiness query owns an existing slot; query and transaction
acquisition retain their original absolute deadlines. A cancelled probe retains
ownership until native work finishes, and queued work rechecks owner closure.
Acceptance and settlement each reserve one waiting position per connection for
overlapping required writes. The first local rollout after the atomic receipt fix
returned 91/100 successes: seven operation-admission and two audit failures, with
nine acceptance-allocation overflow events and no receipt failures. This justified
applying the same finite queue to acceptance. Probes cannot enter these queues
ahead of business work. Other saturation retains zero waiters. This adds no SQL
call, connection, or pool on inference paths. With all allocations enabled the
waiter bound is three borrowed probe waiters plus `telemetry_db_pool_size`, shared
between acceptance and settlement: eight per process by default, or 352 at the
44-process illustrative ceiling, inside existing bounded request/worker allocations.

Alternatives considered were a separate health pool, which would add connection
capacity, and client-presence checks, which would not verify connectivity. The
borrowed-slot design preserves real bounded probes and the declared pool ceiling.
Hermetic saturation/deadline/cancellation tests and a real one-connection
transaction regression verify the contract. Both before/after measurements and
the strict 100-success Kubernetes sample remain required. This change needs no
schema/config migration or compatibility mode; binary rollback restores the old
health-contention behavior.

Use Kubernetes probe hysteresis as the single pod-membership policy: retain
`failureThreshold: 3`, expose `successThreshold: 2`, and retain a five-second
readiness period as the initial test profile. Do not duplicate those counters in
the application. An individual failed sample reports 503; brief failures need not
immediately remove the pod. Drain always overrides cached success immediately.
Do not make normal ingress saturation or optional callback drops a readiness failure.

Preserve `/health/liveliness` and its dependency-free response. Keep existing
readiness/combined-health payload keys and document additive process states.
Readiness and detailed diagnostics remain internal according to the current
network-access contract. Replace raw exception details with fixed safe reason
codes; never return database URLs, exception text or provider topology from new
public probe fields. This plan does not introduce a public drain endpoint.

### Drain starts before Uvicorn waits

Add a narrow managed server entrypoint, proposed `python -m src.server`, using
the locked Uvicorn implementation through a small tested adapter. On the first
SIGTERM/SIGINT, synchronously mark the shared lifecycle draining before Uvicorn
starts waiting for connections. Reuse Uvicorn's protocol handling; do not copy its
server loop or create an alternative HTTP stack.

Keep the listener available for a bounded withdrawal interval to answer health
and promptly reject newly arriving work. Then close listeners and use only the
remaining response-drain allocation for Uvicorn's graceful wait. The adapter must
also cover termination during startup, programmatic shutdown and repeated signals.

Check drain before body reads, auth, cache, DB or Redis, even when ordinary ingress
limiting is disabled. Reject new inference and new control-plane work with a safe
local 503 and `Retry-After`; preserve the messages API error envelope. Health and
existing internal diagnostics remain available during withdrawal. Requests already
waiting for an admission permit must recheck drain before expensive work and
release exactly the permit they acquired.

Requests admitted before drain keep their existing deadline and leases through
completion/disconnect. At the response-drain cutoff, cancel remaining requests
through existing PR7/PR6 cleanup. Do not emit a success terminal marker, replace a
started stream, or retry after response bytes. A 600-second request timeout does
not promise that a stream will survive a shorter pod shutdown window.

Managed production runs one API process per container for both chart roles. Reject
unsupported worker/reload overrides in that profile; document raw `uvicorn
src.main:app` as a development/embedding path without the pre-lifespan signal
guarantee. Tests must exercise the actual managed command, not only `create_app()`.

### One shutdown deadline, with reserved cleanup time

Proposed initial profile, to be verified by the PR8 lifecycle experiments:

| Phase | Maximum | Required order |
| --- | ---: | --- |
| Endpoint withdrawal | 5 s | Mark unready/reject new work; stop new batch/business claims |
| Existing responses and active batch execution | 45 s | Keep required accounting/audit consumers and dependency pools available |
| Cancellation and final acceptance | 5 s | Close upstreams, release acquired leases, finish or record ambiguous durable acceptance |
| Worker drain and optional cleanup | 20 s | Stop remaining claim loops; finish owned work; overlap independent drains |
| Client/pool close | 5 s | Close only after their users stop, within remaining time |
| **Application total** | **80 s** | One absolute monotonic deadline from the first drain signal |
| Kubernetes exit margin | 10 s | Initial `terminationGracePeriodSeconds: 90` |

Phases may finish early; no phase may consume the reserved time for later
required cleanup. Existing smaller service limits remain upper bounds:
each service receives `min(its own deadline, the applicable process phase deadline)`.
Do not raise existing service limits to make the sum fit.

Pass the absolute deadline into audit, spend, email, auth, routing, callback,
guardrail and batch cleanup. `AsyncExitStack` must still attempt each registered
closer when another fails; one outer `wait_for(stack.aclose())` is insufficient if
a closer resists cancellation. Observe failures, account for unfinished work and
preserve reverse dependency ordering. Partial-startup cleanup uses the same owner.

Stop new batch claims immediately but allow already-owned attempts to settle.
Keep durable consumers available until all request/batch producers have finished
or been cancelled. Stop workers before closing shared clients. Do not require a
global outbox count of zero: other replicas may keep appending. It is valid to
exit with committed pending records that another worker can reclaim; it is not
valid to acknowledge uncommitted acceptance or release an unowned/fenced lease.

PR7 threads cannot be safely killed. Plan one bounded, launcher-owned exit
watchdog, independent of the asyncio loop, to force a nonzero process exit when
the total deadline expires (including interpreter thread joins). Cancel it on
normal exit. Test it with a blocked synchronous callback and cancelled cleanup.
A native extension holding the GIL can defeat an in-process watchdog; the
container runtime's SIGKILL is the final bound. Record forced exits distinctly
and verify recovery of accepted records rather than claiming graceful completion.

The managed chart needs no `preStop` sleep: withdrawal happens inside the process
budget. Validate `preStop budget + application total + exit margin <= pod grace`
for any supported hook configuration; unknown custom hooks/entrypoints are outside
the managed guarantee and must not silently pass production validation.
Kubernetes starts the grace countdown before `preStop`; see its
[termination sequence](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination-flow).

### Migrations precede rollout, using the existing Helm job

Promote the existing migration job to the managed production release gate. Use
`pre-install,pre-upgrade` hooks and wait for successful completion before either
Deployment changes. Retain an explicit external-job mode for existing delivery
systems using the same command and schema verifier; do not introduce a second
migration engine or a new application-owned deployment service.

Pre-install jobs cannot depend on resources that Helm has not created yet. Make
the generic job require only its database connection and exact release image.
Remove its dependency on the application ConfigMap, application master/salt keys,
ordinary release ServiceAccount and Redis/S3 credentials. Use a pre-existing
database Secret, or the already supported database URL configuration, and an
explicit no-token job account. Production pre-hooks require an externally ready
database; bundled development databases retain the documented startup mode.
Special coordinators declare their additional prerequisites explicitly.

Use a bounded release/revision/image-derived Job identity. A prior completed Job
for another image/command cannot satisfy the gate. Failed Jobs preserve useful
redacted logs; retries are finite and only connectivity errors are retried by the
existing bootstrap classification. Add a subprocess wall-time limit and preserve
Prisma's migration locking. Concurrent release jobs must either serialize safely
under the existing lock or fail safely; never add automatic destructive repair.

Generic jobs invoke `src.prisma_bootstrap`; organization deletion/backfill and
router Redis cutover remain named release procedures with their existing gates.
Do not silently run a special coordinator on every release or weaken its checks.
[Helm's hook ordering](https://helm.sh/docs/topics/charts_hooks/) explains why a
post-upgrade hook cannot be the production migration gate and why hook resources
need explicit cleanup and prerequisite handling.

Add typed startup migration mode (`startup` for the explicit development path,
`external` for managed production). In external mode, each process performs a
bounded read-only check of the checked-in migration history/checksums and failed
or unfinished records before serving; it performs no DDL. A flag alone is not
proof that migration succeeded. The verifier allows documented additive schema
supersets for rollback but rejects changed/missing required history. Use a short
existing startup DB connection/allocation, close it, and include it in capacity
arithmetic; add no inference-path query or new long-lived pool.

Both API and worker use the same managed entrypoint and image digest. Extend the
existing image template to accept a digest consistently across both Deployments
and the migration Job. Reject inconsistent managed production combinations, while
preserving explicit external orchestration where the startup verifier still applies.

## 4. Implementation sequence and review checkpoints

Each slice includes its focused tests. Keep one PR8 implementation branch unless
an actual incompatible schema/cutover is discovered; no schema change is planned.

1. **Lifecycle contracts and settings.** Add typed lifecycle/state/deadline
   contracts and bootstrap registration. Extend existing startup resolution and
   reload rejection. Record the worker inventory and cleanup dependency ordering.
   Verify monotonic/idempotent transitions, invalid budgets, partial startup and
   configuration precedence before attaching signals.
2. **Readiness.** Extract checks from route handlers behind a typed bootstrap-owned
   interface. Add required-worker expectations, bounded refresh/cache, safe states
   and real startup acknowledgements. Preserve allocation and selector checks.
   Prove missing/dead/stale versus disabled, cancellation, recovery and no liveness I/O.
3. **Managed entrypoint and admission.** Add the small Uvicorn adapter; synchronously
   signal drain, enforce withdrawal/request cutoffs, and stop new work before
   dependencies. Test real sockets, keep-alive connections, queued admission,
   repeated signals, disabled ingress, startup interruption and every text stream format.
4. **Shared cleanup deadline.** Thread the same deadlines through the existing
   runtime owners, reusing telemetry and batch helpers. Stop claims, settle
   producers, drain workers and close pools in dependency order. Add the bounded
   exit watchdog and recovery tests. Include the previously observed rate-limit
   retry worker's shutdown/test ownership when touching that cleanup path.
5. **Migration gate and startup verification.** Extend `prisma_bootstrap`, introduce
   the read-only verifier through the DB boundary, and update the existing Helm
   Job's timing, identity and prerequisites. Test fresh/upgrade, failure, concurrent
   launch, interruption, idempotent retry and compatibility with named coordinators.
6. **Container and Helm wiring.** Use the managed command in both roles; synchronize
   image digests, phase settings, 90-second initial grace, probe recovery thresholds,
   production migration defaults and rendered validation. Freeze the image's Python
   dependencies from `pyproject.toml`/`uv.lock` so the tested Uvicorn adapter is the
   shipped one. Generate/verify any requirements export. Preserve both image variants;
   lock optional Presidio build inputs if retained, with manifest and lock together.
   Make the actual runtime environment accessible to a non-root UID and smoke-test it.
7. **Acceptance harness and documentation.** Extend existing HTTP/load fixtures
   with lifecycle events; add exact-image and disposable Kubernetes orchestration.
   Publish raw phase/latency/recovery evidence and operator rollout/rollback commands.
   Add required CI coverage without removing any existing test lane or aggregate gate.
8. **Review and final qualification.** Review the full diff and scope checklist;
   plan/fix findings and repeat until no actionable items remain. Run all affected
   gates on the final head, attach evidence to the PR, then update #320. Mark PR8
   complete only after merge and linked acceptance evidence.

## 5. Files and configuration surfaces

- Lifecycle/server modules: proposed `src/process_lifecycle.py`, `src/server.py`,
  `src/bootstrap/lifecycle.py`, `src/lifecycle_settings.py`; keep modules small and
  typed. Their names may change before coding; their ownership must not split.
- Composition/admission/readiness: `src/main.py`, `src/ingress.py`,
  `src/middleware/ingress.py`, `src/routers/health.py`, relevant bootstrap modules.
- Cleanup: `src/telemetry/lifecycle.py`, `src/bootstrap/batch_runtime/lifecycle.py`,
  `src/billing/spend_ingestion.py`, `src/services/audit_service.py`,
  `src/services/email_outbox_service.py`, `src/billing/budget_notifications.py`,
  callback delivery/resources, `src/blocking_work.py`, routing/auth refresh owners,
  and `src/rate_limit_release_retry.py` as required by the ownership trace.
- Migration: `src/prisma_bootstrap.py`, a focused DB migration-status reader,
  `scripts/verify_migration_paths.py`, existing special-coordinator tests.
- Settings: `src/config.py`, `src/config_runtime/models.py`,
  `src/config_runtime/dynamic.py`, `src/config_startup.py`, `.env.example`,
  `config.example.yaml`; all lifecycle and migration settings are startup-only.
- Deployment: `Dockerfile`, lock/export sources as needed, Helm values/schema,
  shared helpers, API/worker Deployments, migration Job, probes and config validation.
  Update documented Compose/development commands where the entrypoint changes.
- Docs: `docs/api/health.md`, `docs/deployment/kubernetes.md`,
  `database-migrations.md`, `upgrade-and-rollback.md`, `dependency-capacity.md`,
  `request-deadlines.md`, spend/audit rollout docs and a public PR8 lifecycle runbook.
  Add only public completed contracts to `mkdocs.yml`; this plan remains internal.

Proposed typed settings cover per-probe timeout/cache lifetime, withdrawal time,
request drain, cancellation grace, worker drain, close grace, total shutdown and
migration mode/verification timeout. Derive phase deadlines once; reject totals
that cannot reserve every phase. Resolve explicit file values before environment
defaults using the existing path, validate effective DB-loaded config against the
startup snapshot, and return `restart_required` before persisting incompatible reloads.
Extract/reuse the existing initial config loader for the server entrypoint and
infrastructure bootstrap, passing the resolved startup context onward. Do not
parse the YAML or resolve secrets independently in two competing startup paths.

No new steady-state DB/Redis/provider pools are planned. Keep migration/startup
connections within existing reserved deployment capacity. Recheck API + worker +
surge + retiring-pod arithmetic for the longer grace and enforce one process in
the managed profile. Do not assume a 90-second drain leaves the retiring allowance
valid without checking rolling-update and scale-down behavior.

Add bounded metrics for lifecycle state, readiness check outcome/duration,
shutdown phase duration/timeout, local drain rejections and forced exit intent.
Use fixed state/component/phase/reason labels; log no request bodies or secrets.

## 6. Acceptance tests and evidence

| Scenario | Required evidence |
| --- | --- |
| Slow or missing required dependency | Concurrent deadlines; bounded probe count/waiters; safe 503; liveness stays responsive; recovery restores readiness |
| Configured worker never starts or dies | Readiness fails even if the object is absent; disabled optional services are distinct; selector checks remain strict |
| Short saturation/optional callback outage | Local shedding works; optional failure does not remove inference pods; configured Kubernetes failure/recovery thresholds are exercised |
| SIGTERM while serving | Readiness changes before Uvicorn waits; fresh/queued requests fail before body/auth/dependencies; admitted requests finish within budget |
| Streaming/disconnect/slow upload | Lease held until final frame/cleanup, provider closed once, no retry or false success marker, bounded cancellation acceptance |
| Slow/failing/cancellation-resistant shutdown | Later required cleanup still runs; shared deadline is never restarted; watchdog/forced termination is classified; no false durable acknowledgement |
| Shared backlog remains nonzero | Stopping pod exits after its owned work; committed records remain claimable by surviving workers |
| Partial startup/termination during migration check | Nonzero exit/unready; constructed resources cleaned once; no app traffic before required checks |
| Failed or timed-out migration | No new API/worker rollout; old healthy replicas remain; no automatic history repair; logs retained without credentials |
| Fresh install with pre-hooks | Job succeeds without a not-yet-created app Secret/ConfigMap/ServiceAccount; unavailable prerequisites fail clearly |
| Concurrent/retried release jobs | Migration lock/identity prevents conflicting acceptance; wrong-image completed jobs cannot authorize startup |
| Production image | Frozen dependency versions, non-root start, real PID1 signal behavior, correct entrypoint for both roles and both supported image variants |
| Rolling update with long responses and outboxes | Finite withdrawal/termination timeline, no duplicate economic effects, accepted events durable/recovered, survivors remain usable |
| One pod killed without cleanup | Lost-pod streams may terminate; survivor success/rejection is reported separately; claims fenced/reclaimed and ambiguous operations remain explicit |

Use deterministic clocks/events for unit timing. Reuse the current real-HTTP
deadline fixtures and constant-arrival runner rather than creating a second load
generator. Add lifecycle orchestration around `tests/performance/` and a temporary
namespace/cluster with fixed local provider, isolated real PostgreSQL/Redis and
shared batch artifacts. Never run destructive tests against an existing user cluster.

Use identical offered rates, payloads, dependency settings and fixed-provider
latency for before/after samples. Record image digest, source/lock/profile hashes,
CPU/memory limits, generator drops, all-response p50/p95/p99, streaming TTFT,
queue/in-flight slope, loop lag, dependency calls and the termination timeline.
Show zero added inference-path DB/Redis/network calls; report health-check traffic
and startup verification calls separately. Preserve accepted economic fixture data.

Collect readiness changes, rejected new requests, upstream close times, last
durable commit, worker stop, pool close, process exit code and post-restart outbox/
operation reconciliation. Attach counterexamples and limitations, not only a
successful shutdown trace. A small two/three-pod survival test is evidence for
this lifecycle change, not a supported production RPS/concurrency certificate.

Required gates after implementation:

```bash
uv sync --frozen --extra dev --extra docs
uv run prisma generate --schema=./prisma/schema.prisma
uv run ruff check .
uv run ruff format --check <touched-python-paths>
uv run pytest -q tests/test_health.py tests/bootstrap tests/test_ingress_admission.py tests/test_ingress_application.py tests/test_ingress_deadline_lifecycle.py tests/test_bounded_work.py tests/callbacks
uv run pytest --collect-only -qq --dependency-lane-report
uv run pytest -q -m hermetic
uv run pytest -q -m app
uv run pytest -q -m postgres
uv run pytest -q -m redis
uv run pytest -q -m helm
uv run python scripts/verify_migration_paths.py
uv run python scripts/docs/export_openapi.py --check
uv run python scripts/docs/generate_config_reference.py --check
uv run python scripts/docs/generate_provider_reference.py --check
uv run python scripts/docs/report_health.py --check
uv run pytest -q --confcutdir=tests/docs tests/docs
uv run mkdocs build --strict --site-dir site
uv run python scripts/docs/verify_public_site.py site
git diff --check
```

Provision the real-service lanes using their documented isolated-service variables;
these are commands to run later, not results claimed by this planning change.
Add focused lifecycle/server/migration tests to the appropriate existing lanes.
Container/Kubernetes tests should use a dedicated CI job rather than silently
requiring Docker inside hermetic tests; keep the five lanes exhaustive and the
existing `test`, UI, lint, Helm and migration checks intact. Pin the new test tools
and wire their job into the required PR8/release gate. Run base/eval/production
Helm lint/render and validate every forbidden budget/migration combination.

The current machine has Docker, Helm and kubectl CLIs; `kind` was not found during
planning. Verify daemon access and provision an isolated cluster or use the CI
runner before execution. No image, cluster or lifecycle experiment was run while
writing this plan. Lack of a local cluster must not become an unreported omission
of PR8 acceptance; keep that gate open until equivalent isolated evidence exists.

## 7. Rollout, compatibility and completion

1. Validate the immutable release image and chart in the isolated environment.
2. Check schema compatibility and any named cutover before enabling the managed
   migration gate. Run the single migration job and its verification before rollout.
3. Roll a canary API and worker with the managed command and explicit lifecycle
   budgets. Observe readiness transitions, admission rejections, accepted-work
   recovery and actual shutdown duration before proceeding to more replicas.
4. Stop rollout on failed verification, unexpected forced exits, new latency/call
   regressions or unrecoverable records. Preserve logs and accepted durable state.
5. Roll application/configuration back only to a schema-compatible image. Keep the
   migration external and preserve sufficient pod grace for the rollback binary;
   never roll Prisma history backwards or restore per-pod production DDL races.

Compatibility changes requiring explicit release notes: new drain rejection,
bounded interruption of long streams, startup rejection of missing migration
history, managed production entrypoint, total shutdown budget and migration hook
ordering/prerequisites. Keep existing request error envelopes and health keys.
Document unsupported custom process managers rather than implying they inherit
the managed signal guarantee.

Completion checklist:

- [x] Read RULES.md completely and inspect the latest merged feature branch.
- [x] Create the isolated PR8 worktree and record its base revision.
- [x] Map all five PR8 issue requirements to implementation and validation above.
- [x] Implement slices 1–6 with focused regressions and synchronized configuration.
- [ ] Finish exact-image, rolling-update, pod-loss and failure/recovery evidence.
- [ ] Publish the operator contracts and before/after measurements.
- [ ] Complete review/fix iterations and all required checks on the final PR head.
- [ ] Merge into the feature branch, then update #320 with acceptance links.

The Uvicorn adapter must remain small and version-tested: its
[graceful shutdown setting](https://uvicorn.dev/settings/#timeouts) bounds the
server wait, while application cleanup needs the shared deadline described above.

### Implementation checkpoint — 2026-09-21

The managed launcher, readiness inventory, shared shutdown owner, read-only
migration verifier, pre-release Job, frozen non-root images and operator contract
are implemented. Review fixes cover partial bootstrap ownership, deferred config
publication, policy catch-up after Redis reconnect, cancellation-resistant callback
ownership, bounded Prisma process-group cleanup, and explicit HPA stabilization.
The static bundle routes now share their required owner in `src/ui/routes.py`.

Local validation has passed the full app/PostgreSQL/Helm lanes and focused
lifecycle tests. The 10 RPS comparison completed 200/200 requests on each image
with no generator drops and clean exits; its raw artifacts remain preliminary
until the final committed image is qualified. A 50 RPS baseline run saturated
accounting admission. Neither sample establishes production capacity.

The Kubernetes harness covers migration failure/concurrency, readiness recovery,
streams and accepted batch/outbox work during rollout, and a killed claim owner.
Its complete acceptance gate remains open: the local Docker VM exhausted memory
with the multi-pod fixture, so qualification continues on a clean CI runner.
The draft PR must remain unmerged until that gate and final review pass.

### Final review checkpoint

Review of the acceptance observer found that it retained upstream-close events
without requiring all four test streams to close. The gate now waits a bounded
ten seconds for exactly four provider-side closures and rejects duplicates; the
final CI run must exercise this assertion. No production setting or test load
was relaxed.

The final ARM64 comparison uses runtime `66d977d9`, the bounded settlement queue
fix, and the immutable PR7 baseline. Both images completed 200/200 requests at
10 RPS with zero generator drops and exit code zero. Mean latency was 32.84 ms
and 32.80 ms; the recorded raw samples retain dependency counters and durable
records. See the public lifecycle measurement report for provenance and limits.

### Receipt round-trip review fix

The post-rollout rerun on `a30d5d29` returned 97/100 successes. All three failures
left dispatched spend intents; the batch scenario still completed all 20 items
and economic records. One bounded settlement waiter fixes short overlap but does
not remove the receipt transaction's start, timeout-setup and commit round trips.

Plan: execute receipt acceptance and unknown-state marking as single atomic
PostgreSQL UPDATE statements through the same owned settlement allocation. Keep
owner/principal/model/call-type guards, immutable replay and the 250 ms absolute
caller deadline. Cancellation or lost acknowledgement remains ambiguous; the
allocation continues owning native work through its configured deadline, and
late accepted receipts remain recoverable. Admission keeps its existing
multi-statement transaction and post-lock snapshot. No extra pool, waiter,
provider retry or timeout increase is introduced. Verify fencing, concurrency,
late acknowledgement and exact SQL counts on real PostgreSQL; refresh image
measurements and rerun the unchanged strict lifecycle load assertions.

### Local verification before the next push

The user's latest direction requires local verification before another push.
Runtime `c9f8d480` remains local; remote PR #331 still points to `a30d5d29`.
The frozen local environment passes all five complete pytest lanes:
`-m hermetic` (4,142), `-m app` (1,554), `-m postgres` (445), `-m redis` (60),
and `-m helm` (170), totalling 6,371 tests. Ruff check and format check pass
for all 105 changed Python files. Real PostgreSQL tests cover conflicting
concurrent receipts, lost acknowledgements, cancellation ownership and recovery.

Both rebuilt image variants pass the offline non-root runtime and blocked-cleanup
watchdog checks. The optional Presidio image additionally passes managed startup,
10/10 requests, one-ledger-effect spend/audit recovery and SIGTERM exit zero.
The refreshed 10 RPS comparison completes 200/200 requests on each image with
zero generator drops and clean exits. Baseline/candidate mean latency is
33.87/31.42 ms and p95 is 44.15/38.54 ms. The query-plan artifact verifies a
single indexed receipt UPDATE; the historical two-statement receipt measurement
has the same repository source hash as the PR7 baseline.
Generated reference checks, documentation structure, all nine documentation tests,
strict MkDocs build and public-artifact containment also pass. The completed local
PostgreSQL/Redis test containers and their network have been removed; their raw
test and benchmark evidence is retained.

After explicit approval, all 11 running Bunyan containers were temporarily stopped
for the local Kubernetes experiment and restored to their original running/healthy
states afterward. Fresh and concurrent migrations, failed migration ordering,
dependency recovery, active-stream rollout and 20/20 batch accounting passed.
The post-rollout load failed at 91/100, so no push was made. Per-pod counters show
zero receipt failures; seven spend-admission and two audit failures exhausted the
shared telemetry acceptance allocation. Its four connections had no business waiters.

Remediation: permit one finite waiter per telemetry acceptance connection using
the existing DatabaseOwner and acquisition deadline, matching settlement's bounded
waiting contract. Keep native work owned through completion, preserve admission's
lock/snapshot transaction, and retain the existing connection and SQL-call budgets.
The total waiting bound becomes `3 + telemetry_db_pool_size` per process: eight
with the default five-connection telemetry total, or 352 at the 44-process rollout
ceiling. Test overlap, overflow, cancellation, shorter acquisition budgets, and
real PostgreSQL commits; then rebuild both images, refresh measurements and rerun
the unchanged strict load and upstream-close assertions before pushing.

Artifact review also found that readiness observations included terminating pods.
The observer now requires the expected active-pod count and ignores retiring pods.
Its new regression rejects a false recovery caused only by a retiring pod. Replay
of all 25 saved live HTTP/pod observations passes the corrected observer: active
pods withdraw by 15.01 seconds and recover at 30.35 seconds. The full hermetic
suite with this observer change passed 4,144 tests before the acceptance-queue fix.
