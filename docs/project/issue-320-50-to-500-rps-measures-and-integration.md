# Issue 320: measures from 50 RPS to 500 RPS

Status: source integration record; not a clean-main qualification

This record describes the earlier experimental branch and its 2026-10-04 main
comparison. Its 500 RPS results, latency limits, and drain limits do not apply to
the clean replay. Read [the latest RPS report](issue-320-rps-report.md) for current
verification results and [the accounting deployment guide](../deployment/accounting-v2.md)
for the installed architecture and migration procedure.

This document records the concrete measures that moved the issue 320 candidate from
the first reliable 50 RPS result to the accepted 500 RPS qualification. It also records
how to integrate the work with the current `main` branch.

## Result and scope

The starting candidate passed 50 and 100 RPS but failed at 200 RPS. At 200 RPS, only
6,360 of 24,000 target requests succeeded. The run also returned 14,474 HTTP 503
responses, 378 HTTP 429 responses, and 155 HTTP 500 responses.

The accepted candidate at commit `cc3113bd` passed a clean ten-minute run at 500 RPS:

- 300,000 scheduled requests started and completed.
- All 300,000 responses were HTTP 200.
- There were no generator drops or dependency errors.
- Mean latency was 126 ms, p95 was 234 ms, and p99 was 448 ms.
- Redis used 6.0004 round trips per request.
- All durable work drained in 272.6 seconds, inside the 360-second limit.
- The final state had no unsettled operations, open grants, backlogs, or overspent
  windows.

This is a gateway and accounting qualification. It used a reproducible kind cluster,
four API pods, and a fixed one-token local provider. It proves that the gateway can
accept, account for, and project 500 requests per second in this profile. It does not
prove that a remote model provider can generate 500 real responses per second.

## Concrete measures that were retained

The measures below are in dependency order. They should be kept in this order during
integration.

### 1. Make the load test reproducible

- Create a new two-node kind cluster and database for each qualification stage.
- Build one immutable image from the exact tested commit.
- Record the source hash, image digest, topology, settings, and workload with the
  result.
- Use a constant-arrival workload. Do not use a closed-loop client that slows the
  offered load when the server slows down.
- Collect API, worker, PostgreSQL, Redis, queue, and drain evidence throughout the
  run.

This did not make the product faster. It removed invalid comparisons and made the
next bottleneck measurable.

### 2. Remove test transport bottlenecks

- Stop using a constrained port-forward or single-thread proxy as the product
  capacity path.
- Use a stable shared NodePort or a direct Kubernetes Service.
- Run four generator processes for the shared endpoint, without multiplying the total
  scheduled rate.
- Set the reference edge connection allowance at or above the declared gateway
  admission envelope.

The early proxy path looked like a gateway limit. The direct Service result showed
that the proxy was the main cause of the first 100 RPS ceiling.

### 3. Move Prometheus encoding off the API event loop

- Add one bounded snapshot owner per process.
- Generate immutable Prometheus bytes outside the request event loop.
- Make `/metrics` return the latest completed snapshot.
- Stagger normal product scrapes in the diagnostic harness.

This removed synchronized event-loop pauses caused by metrics encoding.

### 4. Add bounded failure attribution

- Classify queue-full, deadline, pool, lock, transaction, connection, and invalid-result
  failures.
- Preserve the public `spend_persistence_unavailable` response.
- Record bounded internal reasons without tenant or request identifiers.
- Fail a diagnostic when an HTTP 500 is unclassified or required evidence is missing.

This made overload causes visible and stopped unknown failures from being treated as
capacity results.

### 5. Reduce Redis coordination work

- Combine routing prerequisite reads in one pipeline.
- Combine successful health, latency, usage, and release writes in one fenced and
  idempotent acknowledgement.
- Cache absent prompt bindings with bounded L1 and L2 caches.
- Align the negative L1 and L2 time-to-live values to avoid repeated misses.
- Add owner-attributed round-trip metrics and a six-round-trip regression budget.

The accepted 500 RPS run used 6.0004 Redis round trips per request. Commit
`8a488a31` added the main attribution and budget work.

### 6. Stop failure telemetry from amplifying an outage

- Separate required economic and audit persistence from optional operational
  telemetry.
- Keep required accounting fail closed.
- Let bounded optional diagnostics shed load instead of replacing the original
  response with a second persistence failure.
- Bound production request and exception logging.

This broke the feedback loop in which a request failure created more synchronous
database work. The retained work is anchored by commits `3779acbc` and `3414762c`.

### 7. Introduce accounting protocol v2

- Keep PostgreSQL as the durable economic authority.
- Append immutable accounting events with stable operation identities.
- Use explicit reservation and terminal acknowledgements with ambiguity recovery.
- Fence protocol generations and grant owners.
- Keep Redis and process memory as disposable coordination layers.

This supplied the correctness foundation for the later performance changes. The main
implementation starts at `ab4837e9`.

### 8. Make grant admission one database operation

- Combine grant assurance and reservation persistence in one bounded PostgreSQL
  operation.
- Use a dedicated bounded PostgreSQL adapter for the accounting hot path.
- Give accounting acknowledgements an isolated database allocation and deadline.

This reduced request-path SQL calls and prevented telemetry work from consuming the
accounting acknowledgement pool. The main commits are `062ab0c4` and `7986e1c7`.

### 9. Amortize budget-window locks with bounded grants

- Escrow a bounded amount and operation capacity in a short-lived subject-specific
  grant.
- Reuse that grant for a block of operations.
- Use an ordinary target of 32 operations and allow a measured hot-subject batch to
  grow to a maximum of 256.
- Reconcile unused or uncertain grant capacity conservatively.

This moved shared budget-window locking out of the ordinary per-request path. The
first adaptive-grant run raised successes from 12,529 to 21,487 of 30,000 and reduced
mean reservation queue wait from about 780 ms to 43 ms.

### 10. Pre-issue one-use permits

- Allocate a fenced grant to one API process.
- Issue unique permit ordinals from one bounded in-process permit bank.
- Allow only one active refill owner per subject.
- Return only a suffix that the owner can prove was never issued.
- Treat unproven capacity as provisional after owner loss.

This removed grant refill from most requests while preserving the no-overspend
invariant. The feature starts in `0ac46791` and its related migrations.

### 11. Use the escrowed grant as the dispatch proof

- Remove the per-request durable permit-claim call from the local-dispatch mode.
- Carry the grant fence and ordinal in the request-local operation handle.
- Combine the first durable operation record with terminal acceptance after the
  provider returns.
- Keep the older durable-claim path for rolling compatibility and rollback.

This removed the remaining pre-provider database acknowledgement for ordinary local
lease requests. Crash recovery remains conservative because possibly used ordinals
are never restored as free capacity.

### 12. Keep internal permit responses compact

- Return only the fenced grant and ordinal from the internal worker endpoint.
- Reconstruct and validate the full proof from the API's original reservation.
- Keep old endpoints and an old-worker fallback during the rolling window.
- Enforce the 64 KiB internal response limit.

This fixed responses that could exceed 64 KiB when a 32-item grant repeated the full
reservation in every permit.

### 13. Separate terminal acceptance from terminal materialization

- Append one small, durable, idempotent terminal journal record on the response path.
- Return its sequence as the terminal acknowledgement.
- Move operation, event, usage, audit, and compatibility materialization to a bounded
  worker.
- Keep pending journal rows in readiness, drain, expiry, and reconciliation checks.
- Continue draining accepted rows even when new journal appends are disabled.

This was the main architectural change that removed heavy accounting work from the
response latency. The terminal journal starts in migration
`20260930180000_accounting_terminal_journal`.

### 14. Isolate the large terminal payload

- Keep a compact typed validation envelope on the acknowledgement path.
- Store the already validated reservation and finalization documents as bounded opaque
  payloads.
- Parse the full documents in the asynchronous materializer.
- Keep operation, grant, ordinal, owner, and replay fences on the acknowledgement
  path.

Two matched 500 RPS diagnostics then completed all 15,000 requests with no errors. The
first had p95 407 ms and the repeat had p95 519 ms. The main schema work is in
`253aced6`.

### 15. Split request transport from projection ownership

- Run the internal accounting HTTP batch service as a request-transport role.
- Run terminal materialization, read-model projection, spend, audit, and maintenance
  in a singleton projection role.
- Do not start projection loops in every transport replica.
- Scale request transport without multiplying database maintenance work.

Adding a second combined worker had made the system worse because it doubled empty
polling and projection work. Commits `aed0029a` and `04596701` establish the retained
role boundary.

### 16. Give each worker role bounded runtime resources

- Give transport and projection separate bounded executors, queues, database pools,
  readiness, and shutdown behavior.
- Include every role in deployment-wide PostgreSQL, Redis, process, and file-descriptor
  arithmetic.
- Keep foreground API pods from opening the projection worker's telemetry pool.

This prevents background work from consuming request-transport resources. Commit
`41ca0729` contains the central runtime bounds.

### 17. Make backlog observation independent of history size

- Add narrow indexes for pending terminal and projection work.
- Bound backlog and oldest-item queries.
- Avoid scanning the growing accounting history from a request-facing dependency.

This stopped health and readiness observation from becoming slower as the journal
grew. The retained work is anchored by `08f6dfce`.

### 18. Reconcile permits across request workers

- Track accepted journal ordinals when a worker returns an unused suffix.
- Prevent grant closure while a terminal record is pending.
- Reconcile owner loss without reusing an uncertain ordinal.
- Preserve exact settlement when traffic moves across API and transport workers.

Commit `cdab174e` contains this cross-worker settlement work.

### 19. Settle economic counters before heavy projection

- Apply compact terminal economic values once, independently of usage and audit read
  projection.
- Keep economic settlement idempotent.
- Let compatibility read models catch up without holding budgets or operations open.

This made the economic state current during traffic even when reporting projection
had a cooling backlog. Commit `ed391a8b` is the main change.

### 20. Bound Redis recovery bursts

- Limit concurrent Redis waiters and reconnection work.
- Avoid a synchronized retry wave after a short Redis pause.
- Keep allocation-full and deadline outcomes observable.

The exact `be8d5a25` image completed a 180-second 500 RPS diagnostic with 90,000 HTTP
200 responses. Commit `be8d5a25` contains the accepted recovery bounds.

### 21. Parallelize terminal catch-up without multiplying workers

- Add two independently fenced terminal claim lanes inside the singleton projection
  worker.
- Use `SKIP LOCKED` claims and lane-local poison isolation.
- Keep one deterministic iteration mode for tests and manual repair.
- Include all lanes in pause and shutdown.

This used existing database and worker headroom without duplicating every background
loop. Commit `02dab9bd` contains the terminal-lane implementation.

### 22. Match read-model capacity to terminal capacity

- Add four bounded read-model lanes behind the two terminal lanes.
- Reserve two connections in the eight-connection worker pool for spend and audit
  duties.
- Keep the pool size, worker count, and pod resources fixed.
- Remove Redis RDB and AOF persistence from the disposable test fixture because Redis
  is not the durable accounting store.

This removed the downstream stage mismatch and a benchmark-only Redis pause. The main
commits are `199e012a`, `6de3b3cf`, `45a65a08`, and `d2674198`.

### 23. Shard reporting rollup hot rows

- Shard day and month usage rollups by the existing accounting partition.
- Let projection lanes update disjoint keys.
- Sum the bounded shards in readers to preserve the logical totals.
- Increase only the dedicated projection batch to 256 events.

This removed contention on one day row and one month row. The exact `bbffc756` image
served 90,000 of 90,000 requests and drained all durable events in 46.7 seconds.

### 24. Align admission guards with measured latency

- Keep the global and organization preflight limit at 150.
- Size each API ingress guard to 256 active requests for the measured four-pod 500 RPS
  profile.
- Keep ingress waiters at zero and reject overload before dependency fan-out.
- Add arithmetic tests so the edge and pod guards cannot fall below the declared
  workload envelope.

This was a bounded capacity correction. Raising guards alone did not create throughput;
it was accepted only after the request and worker bottlenecks were removed.

### 25. Remove redundant writes for settled receipts

- Keep the canonical billing operation and immutable accounting event.
- For a new, economically complete receipt, do not create redundant per-window
  reservation rows.
- Keep the reservation-backed path for old writers, uncertain outcomes, and recovery
  cases.
- Preserve exact replay and rolling compatibility.

Commit `21f79760` cut terminal materializer time from 108.9 to 54.7 database-seconds
in the 90,000-request diagnostic. It also reduced terminal WAL from 1.321 GB to
917 MB.

### 26. Stream settled receipt projection in narrow stages

- Stream the payload into separate set-oriented operation and event inserts.
- Pass only narrow keys through the completion stage.
- Do not force the wide JSON payload through repeated materialized query stages.

This removed 3.69 GB of PostgreSQL temporary-file spill from the first fast-path
query shape. The exact `cc3113bd` image produced no temporary files and drained the
90,000-request diagnostic in 16.9 seconds. This is the last code commit in the accepted
500 RPS candidate.

## Additional measures from the clean replay

These measures follow the original 26. They are clean-main changes, not part of
the earlier accepted experimental image. Read the current integration plan for
their qualification state. Do not combine results from different images.

### 27. Read the current master key without copying all configuration

- Add a typed scalar read on the existing dynamic configuration owner.
- Keep current-generation selection and constant-time key comparison.
- Keep the control-plane copy API and failed-reload behavior unchanged.
- Add no SQL or Redis call.

The diagnostic found a full configuration copy on each inference request.
Commit `476904b4` removes it and tests generation changes and copy isolation.

### 28. Avoid repeated global logging-cache resets

- Set all six HTTPcore trace levels through the existing startup owner.
- Keep the reference-counted privacy guard and its concurrent-operation lock.
- Do not call `setLevel` when the level is already correct.
- Retain DEBUG suppression during success, failure, and cancellation.

Commit `476904b4` tests zero level writes during normal operations. The change
removes repeated global cache clearing without permitting sensitive traces.

### 29. Ship the async detector used by the HTTP stack

- Pin `sniffio==1.3.1` in the canonical frozen runtime lock.
- Regenerate the container export without upgrading other packages.
- Prove the package is present in the exact non-root image.

The earlier profile showed repeated failed optional imports. Commit `476904b4`
adds the detector without adding a client, pool, task, or configuration path.

### 30. Match the two terminal aggregation stages

- Use a native qualification maximum of 32 entries for both API and request roles.
- Keep the production default of eight entries unchanged.
- Keep the entry, byte, queue, dwell, deadline, pool, and resource limits.
- Record the profile difference when comparing with an earlier image.

Commit `476904b4` removes the API-8/request-32 stage mismatch. Matching maximums
does not remove the need to measure actual batches and finalization queue wait.

### 31. Keep short accounting SQL functions out of JIT compilation

- Apply function-local `jit=off` to bulk local funding, backlog snapshot, and
  read-model projection through append-only migration 143.
- Keep query bodies, indexes, financial checks, and deadlines unchanged.
- Restore the caller's JIT policy after both success and error.
- Keep reporting and other database operations on their existing policy.

Commit `476904b4` includes fail-first and controlled funding evidence, nested
plan tests, and all three migration paths. Do not use a database-wide or pool
override. Rollback resets only the three function settings.

### 32. Reuse immutable terminal proofs inside each process

- Validate the full nested financial graph at mutable and received-wire boundaries.
- Retain canonical reservation, finalization, and proof identity as immutable bytes.
- Reuse the accepted snapshot through transport, journal, recovery, and reply checks.
- Release local proofs only after the complete reply batch passes validation.
- Include stored documents and object overhead in the existing queue byte charge.

The clean replay's signed in-process diagnostic fell from 5.20 to 1.84 seconds
for 3,200 terminal entries. All 249 focused checks passed. This is instrumented
conversion evidence, not a 500 RPS certificate. The wire and durable formats
stay unchanged. No data migration, new policy owner, or larger limit is required.

### 33. Compare local financial handles without duplicate encoding

- Fully validate both mutable reservation inputs and retain their canonical bytes.
- Compare those exact bytes, including canonical money representation.
- Give each handle its own validated copies; retain owner and partition checks.
- Remove only the second encoding of each already validated input.

The 10,000-handle instrumented check fell from 2.04 to 1.44 seconds. Reservation
encodes fell from 40,000 to 20,000. All 140 focused financial cases passed,
including forged scalar copies, nested mutation, and canonical money mismatch.
This is conversion evidence, not an RPS certificate.

### 34. Reduce checks of empty legacy outboxes

- Keep the existing spend/audit consumers for supported non-native work.
- Start at the configured flush interval; double quiet waits up to one second.
- Preserve a configured interval that is greater than one second.
- Clear the wake signal before a claim so local work received during SQL is visible.
- Reset on work, local wakeup, startup, and configuration changes.
- Retain durable acceptance, claims, leases, retries, and owned shutdown.

At the default 100 ms interval, the deterministic idle-minute check allows at
most 64 claims, rather than the old 600. Local enqueue wakes the consumer at once.
Another process's work waits at most one second plus database time. All 113
focused worker/handle/structure cases passed. Full regression and fixed-image
qualification remain open. No pool, resource, or request deadline increased.

### 35. Reduce checks of empty native processing queues

Reuse `src/telemetry/worker_idle.py` in the existing journal and native reporting
workers. The six native lanes previously used the profile's 20 ms interval for
every empty claim, or about 300 claim calls per second before other work.
Successful empty claims now double the idle wait up to the larger of the
configured interval and one second. Completed work resets the wait and starts
another claim immediately. Errors keep the original recovery backoff.

No financial fence, health check, query, pool, or resource limit changes.
Cross-process discovery can add the bounded idle wait plus database time.
Eight new deterministic cases prove call reduction, active reset, wake
preservation, and unchanged failure waiting for both worker types. All 264
affected component cases and 56 real PostgreSQL cases passed. This change is
not yet load-qualified.

### 36. Recheck reporting work after the checkpoint lock

Native reporting first selects candidate partitions, then locks one checkpoint.
Another worker can finish its work between those steps. The old query could
claim the advanced checkpoint and return no event keys. Strict validation then
reported an outage, although the database call succeeded, and left an empty
lease live for 30 seconds.

Lock the current checkpoint before the final indexed, single-row work lookup.
If all work is done, do not claim it. If some work remains, return only that
work. A controlled real-PostgreSQL test reproduced the old failure and verifies
both cases, exact money, reporting counts, and absence of abandoned leases.
Keep result validation, financial fences, lease bounds, and caller deadlines.
No migration is needed. Fresh upper-tier qualification remains required.

## Important measures that were tested and rejected

Do not replay these experiments as part of the integration:

- Do not raise pools or timeouts to hide repeated work.
- Do not use two admission lanes for one hot subject. They split useful batches and
  increased database calls.
- Do not add a second combined accounting worker. It duplicates projection and
  maintenance work and caused allocation timeouts.
- Do not overlap API finalization batches. Larger concurrent batches made PostgreSQL
  work grow nonlinearly.
- Do not replace the journal append with a lock-free insert plus a second validation
  query. It was slower.
- Do not run four terminal materializer lanes in the current database budget. They
  caused 628 MB of temporary-file spill and drained more slowly.
- Do not remove journal indexes or safety checks without an isolated database result.
  The tested versions did not improve capacity.
- Do not qualify through long-lived `kubectl port-forward` streams.
- Do not disable normal metrics to claim a release result. No-scrape runs are causal
  diagnostics only.

## What remains transitional

The accepted result still has compatibility work:

- Accounting-v2 events are the economic source of truth.
- V2 usage and audit read models exist.
- A background projector still updates legacy spend and audit stores for consumers
  that have not moved to the v2 read models.
- The accepted ten-minute run began cooling with 217,007 terminal receipts and drained
  all terminal, read-model, spend, and audit work in 272.6 seconds.

The legacy compatibility projector is not part of provider dispatch, but it is still
part of the release drain gate. A later change should migrate the remaining readers
and remove this projector with parity and rollback evidence.

## Comparison with the current main branch

Comparison date: 2026-10-04

- Current feature head: `534ef828`.
- Accepted performance code: `cc3113bd`.
- Current remote `main`: `f5ffd80d`.
- Common ancestor: `0086cd6a`.
- The feature has 117 commits that are not in `main`.
- `main` has 27 commits that are not in the feature.
- The feature diff from the common ancestor changes 736 files, with 202,291 additions
  and 2,295 deletions.
- The accounting and 500 RPS slice after the PR 9 feature base changes 191 files, with
  34,732 additions and 589 deletions.
- That slice includes 78 application files, 56 test files, 20 deployment files, 14
  forward migrations, 9 documentation files, and 7 test or operations scripts.
- The two branches changed 61 of the same paths.
- A three-way merge simulation reports conflicts in 19 files.

The conflict set includes critical integration points:

- `prisma/schema.prisma` and `scripts/verify_migration_paths.py`
- `src/main.py`, `src/router/state.py`, and `src/bootstrap/infrastructure.py`
- `src/billing/spend/spend_ingestion.py` and `src/db/billing_operations.py`
- runtime configuration, health, Helm values, deployment docs, and `uv.lock`

The new `main` work includes provider failover, asset access, organization deletion,
and realtime audio changes. Some of those changes touch the same bootstrap, router,
provider, schema, migration, and billing boundaries as issue 320.

## Integration decision

Do not merge the current feature branch directly into `main`.

Also, do not rewrite the implementation from memory. The performance work contains
important durability fences, crash behavior, migrations, and measured query shapes
that are easy to lose in a clean rewrite.

Use a clean integration branch from the latest `origin/main`, then replay and adapt
the validated work in small dependency-ordered slices. Preserve the existing tests,
design decisions, and append-only migrations. Resolve each slice against the new main
behavior before moving to the next slice.

Recommended integration slices:

1. Port the issue 320 prerequisite stack that is still missing from `main`. Keep its
   migration and configuration contracts intact.
2. Port accounting protocol v2, atomic grant admission, the dedicated database path,
   and ambiguity recovery.
3. Port pre-issued permits, local lease dispatch, and compact permit transport.
4. Port the terminal journal, compact validation envelope, and payload isolation.
5. Port the request-transport and projection role split with bounded runtime and
   deployment capacity arithmetic.
6. Port economic settlement, cross-worker permit settlement, recovery bounds, and
   failure-amplification controls.
7. Port terminal and read-model lanes, rollup sharding, settled-receipt projection,
   and narrow streamed inserts.
8. Port the reproducible kind harness and only the evidence needed to preserve the
   performance ratchets and accepted result.
9. Run fresh install, last-release upgrade, and shared-feature migration checks after
   every schema slice. The current `main` has later and same-day migrations, so ordering
   and upgrade paths must be verified explicitly.
10. Run the 50, 100, 200, and 500 RPS ladder on one clean integrated image. Run the
    ten-minute 500 RPS gate only after the short 500 RPS diagnostic passes.

This approach is a clean replay, not a redesign. It keeps the proven architecture and
avoids one unreviewable 736-file merge. It also gives each conflict with current main a
specific test and migration gate.
