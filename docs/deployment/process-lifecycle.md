# Readiness, termination and release ordering

Use the image's `python -m src.server` command for API and batch-worker pods.
It runs one process, withdraws admission when SIGTERM or SIGINT arrives, and
uses one monotonic shutdown deadline. PostgreSQL remains the durable source of
truth for accepted audit, spend, batch and business delivery.

## Readiness and admission

The process moves through `starting`, `serving`, `draining`, `stopping` and
`stopped`. Drain is irreversible; repeated signals cannot extend its deadline.
Readiness requires the configured workers for that pod's role, including startup
acknowledgement and current task health. Missing configured workers fail readiness.
Routing reconciliation, organization authorization freshness, config refresh and
required governance/tier refresh are checked on every sample.

Database and critical Redis probes share one refresh, cached for one second.
There are at most six concurrent dependency operations and a bounded number of
health callers. Timed-out work retains its allocation until it actually stops.
Each database owner allows one business operation to wait behind its single
readiness probe, within the existing acquisition deadline. This prevents a probe
from immediately rejecting settlement on a one-connection allocation. There are
no extra connections. Telemetry acceptance and settlement each reserve one waiting
position per configured connection for overlapping required writes; other business
saturation still has zero waiters. All waits consume the same acquisition deadline.
No provider request or new client pool is created for a probe. Optional callback,
notification and cache failures do not withdraw otherwise usable inference pods;
inspect their feature metrics for degradation.

The chart samples readiness every five seconds, requires three failures to remove
a pod and two successes to restore it. A single failed application sample returns
503 immediately. Drain overrides cached success immediately. Liveness performs no
dependency I/O. Detailed health and metrics remain internal operational endpoints.

After drain starts, new inference and control requests return HTTP 503 with
`error.code=gateway_draining` and `Retry-After: 1`, before reading their bodies or
using auth, Redis, SQL or provider capacity. The messages API retains its error
envelope. Queued admission checks drain again after obtaining a permit. Existing
requests retain their leases until response cleanup completes.

## Shutdown budget

The initial managed profile reserves the following time from the first signal:

| Phase | Allocation | Latest end |
| --- | ---: | ---: |
| Endpoint withdrawal; stop new business/batch claims | 5 s | 5 s |
| Existing responses and owned batch attempts | 45 s | 50 s |
| Cancellation and final durable acceptance | 5 s | 55 s |
| Worker drain and optional cleanup | 20 s | 75 s |
| Client and pool close | 5 s | 80 s |
| Kubernetes exit margin | 10 s | 90 s |

Phases can finish early. Smaller existing service timeouts still apply. One slow
closer cannot reset the budget or prevent later owners from attempting cleanup.
Required consumers remain available while request and batch producers settle.
Stopping one replica does not require a shared outbox backlog to become empty.
Committed pending records remain available for another replica to reclaim.
Recovery is not necessarily immediate when a pod exits: an interrupted batch
item retains its existing fenced lease until expiry. The default item lease is
360 seconds; the surviving worker then needs its sweep/poll and execution time.
Monitor pending claims and completion accounting separately from pod readiness.

A stream that exceeds the response allocation is interrupted without a terminal
success marker. A 600-second request timeout does not promise survival through a
shorter pod shutdown. Clients must treat interruption after response bytes as an
ambiguous operation; the gateway does not retry provider work to repair accounting.

Python threads cannot safely be killed. A launcher-owned watchdog forces exit 70
at the total deadline, including blocked interpreter thread joins. Its fixed stderr
message identifies forced exit and required durable recovery. A native extension
holding the GIL can block this watchdog; Kubernetes SIGKILL is the final bound.
Cleanup failure also produces a nonzero managed exit. Neither is reported as a
successful graceful drain.

`deltallm_process_state`, `deltallm_readiness_probes_total`,
`deltallm_readiness_refresh_seconds`, `deltallm_shutdown_cleanup_seconds` and
`deltallm_shutdown_cleanup_total` expose bounded state, component and phase labels.
`deltallm_shutdown_phase_seconds` records elapsed phase time.
`deltallm_shutdown_forced_exit_intent_total` records retained work at a cleanup cutoff;
the final pod status and watchdog log distinguish an actual forced exit.
Admission rejection metrics include `gateway_draining`. Collect pod termination
status and logs as well: an exited process cannot provide a final metrics scrape.

## Configuration and supported process model

Lifecycle settings are startup-only. Explicit YAML values take precedence over
their `DELTALLM_` environment defaults. DB-loaded effective values must match the
startup snapshot; incompatible reloads return `restart_required` before persistence.
See the [complete settings reference](../configuration/general-settings-reference.md).

The Helm chart validates the sum of all five phases, total plus exit margin,
probe timeout, one process per pod, and managed entrypoint for both roles. It adds
no `preStop` sleep. Production requires the managed lifecycle and an explicit
release image. Use the same tested digest for API, worker, dependency waiter and
migration Job. Custom commands, reload and multiple Uvicorn workers are outside
this contract. Direct `uvicorn src.main:app` remains a development/embedding path;
it does not receive the managed signal before Uvicorn's connection drain.

The image runs as UID/GID 10001, using frozen dependencies from `uv.lock` and
prebuilt Prisma/Node assets. Production mounts writable `/tmp` while keeping the
root filesystem read-only. Configure shared artifact storage and any persistent
application storage explicitly; a pod-local directory is not shared storage.
The Presidio variant includes its NLP model and uses the locked `tldextract`
package's bundled suffix snapshot for email validation. Its recognizer disables
[HTTP suffix-list fetching](https://github.com/john-kurkowski/tldextract#how-to-disable-http-suffix-list-fetching-for-production)
and disk caching; upgrades to that data ship with a tested image.

## Release procedure

1. Select a tested immutable image and verify schema compatibility and any named
   coordinator or cutover required by the release.
2. Provision the database and its Secret before Helm. The production example uses
   Secret `deltallm-database`, key `database-url`. Change these names to match your
   deployment. An existing Secret takes precedence over `runtime.database.url`;
   clear the Secret name when intentionally supplying a URL.
3. Wait for this release's terminating pods to disappear. Budget and serialize
   deployments and autoscaling so at most the declared retiring generations overlap.
4. Render and inspect the chart, then upgrade with the same explicit image:

```bash
helm upgrade --install deltallm deploy/kubernetes/helm -n deltallm \
  -f deploy/kubernetes/helm/values-production.yaml \
  -f production-runtime.yaml \
  --set image.digest=sha256:<tested-release-digest> --wait --timeout 10m
```

`production-runtime.yaml` supplies your application Secret, critical/cache Redis,
shared object storage, actual dependency ceilings and workload-specific settings.
Keep secret values out of shell history and committed files.

The `pre-install,pre-upgrade` migration hook finishes before either Deployment
changes. It needs only the exact image and database connection, not application
ConfigMaps, master/salt keys, Redis, S3 or an ordinary release ServiceAccount.
Bundled development databases cannot satisfy this pre-install prerequisite.
The generic command is `python -m src.prisma_bootstrap --timeout-seconds 300`.
Only connectivity failures are retried; wall time, attempts and captured output
are bounded. The Job has no retry after failure and a 330-second active deadline.
The migration Job requests 512 MiB and is limited to 1 GiB; the exact-image test
observed an OOM at 512 MiB and successful execution at 1 GiB.
Failed Job logs remain available until the configured TTL (one day by default).
Its identity includes release revision, image and command; a completed Job for
another release cannot satisfy this gate.

Every API/worker starts with `migration_mode: external` and verifies the image's
required migration names and checksums using an existing database allocation.
Missing, modified, failed or unfinished history prevents startup. This check is
read-only and adds no inference-path query. Completed additional migrations are
allowed for application rollback only when the old binary remains schema-compatible.

If delivery owns the migration stage externally, use
`migrationJob.enabled: false`, `migrationJob.external: true`, keep
`migration_mode: external`, and retain the managed image command. Run and verify
the same exact-image migration Job before Helm. Named organization-deletion and
router-state coordinators retain their separate prerequisites and gates.

## Capacity, rollback and acceptance

The 90-second grace increases overlap. The example reserves two retiring
generations: with 12 maximum API replicas and one surge, that is 37 API processes;
two fixed worker replicas add seven. Read the
[dependency capacity arithmetic](dependency-capacity.md) and replace illustrative
server ceilings with real allocations. Live Helm upgrades reject a release with
terminating pods; the Helm identity needs permission to list namespace pods for
this check. Offline rendering cannot prove that live condition. Serialize external
rollout/scale operations and constrain their frequency to the declared overlap.
Both HPA roles explicitly retain a 300-second scale-down stabilization window;
production validation requires that window to cover the entire pod grace. This
keeps a recent higher replica recommendation in force while pods drain. The
two-generation allowance covers one serialized rollout and one autoscaling
retirement wave under that constraint; forced manual scaling or repeated external
rollouts need separate capacity reservation. See Kubernetes'
[stabilization behavior](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/#stabilization-window).

Release CI builds each multi-platform variant once and stages it by digest. The
acceptance job pulls that digest, and release tags are created only after both
variants pass. Tag promotion preserves the tested manifest without a second
build; it uses Docker's [manifest creation command](https://docs.docker.com/reference/cli/docker/buildx/imagetools/create/).

Stop rollout on failed migration verification, unexpected forced exits, readiness
that does not recover or accepted records that cannot reconcile. Preserve durable
records, logs and the release image. Roll back application/configuration only to
a schema-compatible image and keep sufficient grace for that binary. Never undo
Prisma history or return to per-replica production DDL races.

Run exact-image acceptance in a disposable cluster:

```bash
docker build -t deltallm-pr8:acceptance .
uv run python -m tests.performance.run_lifecycle_acceptance \
  --image deltallm-pr8:acceptance --output /tmp/deltallm-pr8-evidence
```

The harness uses kind 0.31.0 with a pinned Kubernetes node image, real isolated
PostgreSQL/Redis, a fixed provider and shared test artifacts. It creates its own
kubeconfig and deletes only its own cluster. The single-node test PVC is an
explicit fixture exception; production split workers use shared object storage.
Its samples and event timeline are lifecycle evidence, not a supported production
RPS or stream-concurrency certificate. Resource/HPA tuning and sustained capacity
qualification remain separate requirements.

See the [recorded lifecycle measurements](../project/benchmarks/process-lifecycle-2026-09-21/README.md)
for source/image identities, raw samples, recovery evidence and observed limits.
