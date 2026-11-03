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
| Accounting v2 acceptance | Every pull request or push to main that changes `src/billing`, `src/batch`, `src/db`, `prisma`, the Helm chart or `tests/performance`, and every night | `native-acceptance.yml` (from `ci.yml` and `nightly-soak.yml`) | `tests/performance/run_native_acceptance.py` |
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
   apart. The batch completes with no failed item, without a manual restart.

After each step, the run checks four things. A failed check fails the job
once the last step has run, so one red step does not hide the others:

- Every tracked in-memory gauge is back at or below its level before the step
  (see [Bounded state](#bounded-state)).
- No pod refused accounting because a store or queue was full.
- Accounting drains within 5 minutes: no reserved or provisional operation,
  open grant, pending terminal journal row, or unprojected partition is left.
  If it does not, the run saves the stuck rows to
  `<step>-unsettled-state.json` and keeps waiting up to 18 minutes, past
  reservation and grant expiry, to show whether recovery clears them
  (`recovered_after_target`). The step still fails. After pod loss, the killed
  pods' reservations can only clear by expiry, so that step allows the full
  18 minutes.
- The new usage facts equal the successful requests and items. No operation
  is charged twice, every budget scope's committed amount equals the facts,
  and no scope holds reserved or provisional money. After pod loss, the facts
  may also include answers that a killed pod charged but its client never
  received. They can never be more than the failed requests.

Batch volume stays under about 1,100 items per pod, one pod's default receipt
store. Volume is tested by the nightly soak.

The job is advisory for now. It runs and reports on each matching pull
request, but the required `test` check does not depend on it yet.

## Nightly soak

One kind node with 2 API pods, 3 batch pods, 2 accounting request pods and
1 accounting worker. Phases run in this order. Each phase reports on its own,
and the run fails if any phase fails.

| Phase | Load | Passes when |
|---|---|---|
| Batch | 12 jobs at once, 20,000 items, chat and embedding jobs alternating | Every job completes, no item fails, at least 50 items/s from first upload to last job done, no full-store refusal |
| After batch | Idle | Within 5 minutes every tracked gauge returns to its level before load, accounting drains, facts equal completed items, nothing charged twice |
| Probe | One new 500-item chat job | It completes with no failed item within 120 s, with no pod restarted. This catches state that only a restart would clear |
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

## Bounded state

`tests/performance/soak_checks.py` lists the gauges that must return to idle:

- `deltallm_accounting_queue_depth` and `deltallm_accounting_queue_retained_bytes`
  (reservation, finalization and RPC terminal queues).
- `deltallm_batch_model_in_flight_items`.
- Any `deltallm_accounting_*` gauge whose name contains `receipt` or `cursor`.
  A new fill gauge for the local receipt or cursor store is checked as soon
  as it ships.

If a pod replaced another during the run, its gauges must be back at zero.
`deltallm_accounting_failures_total` must not grow for the `queue_full`,
`capacity_full` or `receipts_full` reasons. All other reasons are reported
but do not fail the run.

To check a new store, add its gauge to `IDLE_GAUGES`, or give it a name the
pattern already matches.
