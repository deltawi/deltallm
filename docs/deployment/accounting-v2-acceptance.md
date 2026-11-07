---
description: CI acceptance and the nightly soak that run the production accounting v2 configuration on kind.
---

# Accounting v2 acceptance and nightly soak

Production runs accounting v2 (`accounting_execution_mode: local_journal`, the
`values-accounting-native.yaml` bundle). The older capacity and lifecycle jobs
test the legacy spend outbox path. Two more runs test the configuration
production uses. Both run on a disposable kind cluster with a fixed mock
provider, so provider limits never affect the result.

| Run | When | Workflow | Runner |
|---|---|---|---|
| Accounting v2 acceptance | Every pull request or push to main that changes `src/billing`, `src/batch`, `src/db`, `src/metrics`, `src/bootstrap`, the accounting RPC, the config loader, `prisma`, the Helm chart, the `Dockerfile`, `uv.lock` or `tests/performance`, and every night | `native-acceptance.yml` (from `ci.yml` and `nightly-soak.yml`) | `tests/performance/run_native_acceptance.py` |
| Nightly soak | 02:30 UTC on main, on demand, and in short form on pull requests that change the soak | `nightly-soak.yml` | `tests/performance/run_accounting_soak.py` |

Both start from the strict production values, the capacity fixture, and then
`values-accounting-native.yaml`. Batch is on, with the production scheduler
shape (`smart_v1`, work slices, model capacity, 20 runners per pod, 50-item
claims, 5 attempts). Strict model capacity stays on, so each fixture model
declares `batch_capacity.max_in_flight` (60, three pods of 20 runners), as a
production model must. The receipt store and other bounded stores keep their
shipped defaults, so a leak shows at the same point it would in production.

## Accounting v2 acceptance

One kind node with 2 API pods, 1 batch pod, 2 accounting request pods and
1 accounting worker. It checks three things in order:

1. **Fixed replicas.** With 2 and then 3 API pods, 200 chat requests through a
   bounded edge all succeed, and every API pod takes traffic.
2. **Rollout.** A Helm rollout of every role starts while a 300-item batch and
   API traffic are in flight. The batch completes with no failed item.
3. **Pod loss.** During another 300-item batch and API traffic, the run sends
   SIGKILL to one API pod, one accounting request pod and the batch pod, 15 s
   apart. The batch completes without a manual restart. An item whose billing
   proof was lost in the kill may fail with "Batch billing proof is
   unavailable", because it may already have reached the provider and must not
   run twice. Any other failed item fails the step.

After each step, the run checks four things. A failed check fails the job
once the last step has run, so one red step does not hide the others:

- Every tracked in-memory gauge is back at or below its level before the step
  (see [Bounded state](#bounded-state)).
- No pod refused accounting because a store or queue was full.
- Accounting drains within 5 minutes: no reserved or provisional operation
  (after pod loss, no reserved operation),
  open grant, pending terminal journal row, or unprojected partition is left.
  If it does not, the run saves the stuck rows to
  `<step>-unsettled-state.json` and keeps waiting up to 18 minutes, past
  reservation and grant expiry, to show whether recovery clears them
  (`recovered_after_target`). The step still fails. The fixed replica steps
  stop no pod, so they do not wait for expiry. After pod loss, the killed
  pods' reservations can only clear by expiry, so that step allows the full
  18 minutes. Unsafe budget state is reported as a failed drain, not raised.
- The new usage facts equal the successful requests and items. No operation
  is charged twice, every budget scope's committed amount equals the facts,
  and no scope holds reserved or provisional money. After pod loss, the facts
  may also include answers that a killed pod charged but its client never
  received. They can never be more than the failed requests.
- After pod loss only, a killed pod cannot prove whether the provider answered
  its in-flight work, so that money stays provisional by design. The run
  checks it is bounded and exact (`killed_work` in the report): no reservation
  stays open, no more operations are held than were in flight (two batch pods'
  worth plus the failed requests), every failed batch item holds its money,
  only the killed pods' own grants keep an unreturned remainder, and each
  scope's provisional amount equals those held operations and remainders.
  A graceful rollout must still leave nothing provisional.

Batch volume stays under about 1,100 items per pod, one pod's default receipt
store. Volume is tested by the nightly soak.

The `accounting-v2-acceptance` artifact keeps a live log of every API and
accounting pod during the rollout and pod-loss steps under `follow/`, because
a rollout deletes pods before their logs can be read. A step that leaves
money provisional also saves the operations, grants and terminal events
that hold it, and each traffic summary lists its rejection codes.

The job is advisory for now. It runs and reports on each matching pull
request, but the required `test` check does not depend on it yet. On pull
requests and pushes to main it runs with `continue-on-error`: a failure shows
as a red `accounting-v2-acceptance / native-cluster` job, but the CI run itself
stays green, so a red run on main always means a required check failed. Open the
job when it runs. The nightly run is not advisory and fails the workflow.

Pull requests skip step 3. After the kill, the run waits about 15 minutes for
the killed pods' reservations to expire, which would double the job. Pushes to
main and the nightly run all three steps.

## Nightly soak

One kind node with 2 API pods, 3 batch pods, 2 accounting request pods and
1 accounting worker. Phases run in this order. Each phase reports on its own,
and the run fails if any phase fails.

| Phase | Load | Passes when |
|---|---|---|
| Batch | 12 jobs at once, 20,000 items, chat and embedding jobs alternating | Every job completes, no item fails, at least 50 items/s from first upload to last job done, no full-store refusal |
| After batch | Idle | Within 5 minutes every tracked gauge returns to its level before load, accounting drains, facts equal completed items, nothing charged twice |
| Probe | One new 500-item chat job | It completes with no failed item within 120 s, with no pod restarted. This catches state that only a restart would clear. A probe that times out is cancelled, so its retries do not load the API phase |
| API | 300 chat RPS on 2 API pods for 30 minutes, in 10-minute windows, from an in-cluster generator, plus 10 embedding RPS from the runner | In each window: no 5xx, no transport error, success at least 99.9 %, chat p95 at most 50 ms, and the generator kept its rate |
| After API | Idle | The same checks as after batch |

The mock provider answers in about a millisecond, so chat p95 is close to the
gateway overhead.

The job summary shows a table of every check. The `accounting-v2-soak`
artifact keeps `soak-report.json`, the API window reports, and bounded pod
logs for 30 days.

### Run it by hand

From the Actions tab, run **Nightly accounting v2 soak** with other numbers,
for example `batch-items` 5000 and `api-seconds` 0 for a batch-only run.
Locally, from the repository root, with Docker, kind v0.31.0, kubectl and Helm:

```bash
docker build --build-arg INSTALL_PRESIDIO=false -t deltallm-soak:candidate .
uv run python -m tests.performance.run_accounting_soak \
  --image deltallm-soak:candidate --output /tmp/soak-evidence \
  --batch-items 5000 --api-rate 100 --api-seconds 300
```

Every target is a command-line option (`--min-batch-items-per-second`,
`--max-p95-seconds`, `--idle-seconds`, `--probe-seconds`, and more). See
`--help`.

### Runner size

The defaults are the targets in the robustness plan. A standard 4-vCPU
GitHub runner runs every role, Postgres and the load generator on one node,
so 300 RPS may be the runner's limit rather than the gateway's. If the API
phase fails only because the generator fell behind
(`load_generator_fell_behind`), set the `SOAK_RUNNER` repository variable to
a larger runner label. The workflow uses that label when it is set.

Each 10-minute API window starts a new in-cluster generator pod that requests one
CPU. The runner deletes the previous generator and waits until its pod is gone
before it starts the next one, and gives a new generator 180 s to become ready.
A terminating generator used to hold its CPU request, so the next window's pod
could not be scheduled in time ("in-cluster load generator did not become ready").

Without `SOAK_RUNNER`, the latency gates are 0.5 s for chat and 0.75 s for
embeddings instead of 50 ms and 0.25 s. On the 4-vCPU runner chat p95 measured
0.2 to 0.3 s at 100 RPS with no errors, so the strict targets there measure
the runner. The batch throughput floor is 30 items/s instead of 50, since it
measured 36 to 54 items/s between runs there. The pull request run sends 50
chat RPS instead of 100: at 100 RPS the runner's CPU was 96 % busy (p95) and
an API pod missed its liveness probe. The scheduled nightly run without
`SOAK_RUNNER` also sends 50 chat RPS, for the full 30 minutes and after the full
20,000-item batch: at 300 RPS the generator dropped about 30 % of its requests,
most of the rest were shed or timed out, and both API pods restarted, so the API
phase failed every night on the runner rather than on the gateway. A manual run
keeps the rates you enter. Every other check stays the same. Each
API window reports `host_cpu`, the busy share of the runner's CPUs, to show
how loaded it was.

After a run, `roles/<role>-previous.log` holds the last run of every container
that restarted, so a probe kill shows what the pod was doing.
The soak report also lists each failed batch item (`failed_items`): its error,
attempts, checkpoint outcome and the operation's accounting state, without
request or response bodies. Pod logs keep only their last lines, so a single
failed item in a long run would otherwise leave no trace.

## Bounded state

`tests/performance/soak_checks.py` lists the gauges that must return to idle:

- `deltallm_accounting_queue_depth` and `deltallm_accounting_queue_retained_bytes`
  (reservation, finalization and RPC terminal queues).
- `deltallm_batch_model_in_flight_items`.
- Any `deltallm_accounting_*` gauge whose name contains `receipt` or `cursor`,
  today the local receipt store's count, bytes and fill ratio. Counters such as
  `deltallm_accounting_local_receipt_releases_total` are left out.

If a pod replaced another during the run, its gauges must be back at zero.
`deltallm_accounting_failures_total` must not grow for the `queue_full`
reason. A batch pod whose local receipt store is full counts here. All other
reasons are reported but do not fail the run.

In the soak, no gateway container may restart or be replaced. A restart
clears the in-memory stores, so it would hide a leak. The summary lists any
restarted pod.

To check a new store, add its gauge to `IDLE_GAUGES`, or give it a name the
pattern already matches.
