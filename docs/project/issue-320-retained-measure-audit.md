# Issue 320: retained-measure audit of the clean replay

Status: restored implementation and full regression complete; finalization CPU remediation and load qualification active.

The final CPU remediation regression contains 8,674 cases: 6,059 component/chart,
1,649 application, 854 PostgreSQL, and 112 Redis. All required confirmations
passed. Migration 143 passed fresh, last-release, and shared-feature paths. Its
three function-local execution settings remove the repeatable funding timeout
without changing money checks, deadlines, or database-wide reporting policy.
Commit `476904b4` passed all five exact-image checks. Its isolated short 50,
100, and 200 RPS stages passed. Short 500 RPS failed, so no ten-minute stage
started. Host isolation used an approved temporary Rancher shutdown and restart.
The unchanged first run and isolated rerun are both preserved.

The clean replay adds six measured costs and corrections to the original
26-measure record: scalar configuration reads, stable dependency logging,
the frozen async detector, matched terminal aggregation, function-local SQL
execution policy, and immutable terminal handoffs. The last is now implemented
and passed 249 focused checks. Its full confirmation passed all 8,704 cases.
Source, lock, image-export, configuration, and capacity checks passed again.
Commit `5a28ca26` passed all five new image checks and generator proof.
Its isolated short 50/100/200 RPS stages passed. Short 500 RPS failed, including
four unsettled operations after the drain bound. Budget state stayed safe.
Rancher and both original contexts are restored. No ten-minute stage started.
The remaining measured-cost work and final load gates remain open.

Two further measured-cost changes are implemented but not load-qualified.
Local handles reuse their two fully validated canonical input documents for
exact comparison. Quiet spend/audit consumers use one bounded idle-wait helper
through their existing lifecycle. Local enqueue remains prompt; remote enqueue
is found within the one-second idle cap plus database time. All 140 financial
and 113 worker/handle/structure focused cases passed. The 8,728-case collection
is complete. Component/chart, application, and Redis passed 6,113, 1,649, and
112 cases. Full PostgreSQL passed 852 cases and failed two; both unchanged
cases passed together in 1.45 seconds. Targeted confirmation and one Realtime
due-time setup repair retain all money, conflict, and deadline checks. The
80-case confirmation passed 79 and failed one existing statement-timeout case;
that case passed unchanged alone in 1.59 seconds. These are not clean full-gate
passes. Preserve the warmup timeout as unexplained, without reducing its cold
concurrency coverage. No new exact-image result exists for this slice yet.

The source record is the earlier 26-measure extraction. The first clean image,
`22f62c8c`, omitted request-path and worker measures even though its accounting
slices passed their tests. Its short ladder failed. Keep those failures as
evidence. They are not a completed qualification.

## Measure-by-measure result

| No. | Retained measure | Clean implementation and adaptation |
| --- | --- | --- |
| 1 | Reproducible load | Pinned disposable kind, clean source/image identity, fixed provider, constant arrivals, raw per-process and dependency evidence. One fresh cluster owns the whole fixed-image series; it retains history and drains between stages. |
| 2 | Test transport | In-cluster four-process generator uses direct Services. The total rate and connection budget are split, not multiplied. |
| 3 | Off-loop metrics | Restored one bounded snapshot encoder per process, immutable scrape bytes, GC measurement, and startup heap collection/freeze. Harness parsing and export have fixed executor owners. |
| 4 | Failure attribution | Required persistence retains finite failure classes and public errors. Unknown HTTP 500 and incomplete generator/economic evidence fail the run. |
| 5 | Less Redis work | Restored combined prerequisite reads and one success script. It keeps main's live-expiry, recovery-owner, and manual-cooldown fences. Negative prompt caches remain bounded and invalidated. Qualification again requires the six-call core budget and the original finite cache-maintenance and snapshot-edge allowances. |
| 6 | No failure feedback loop | Restored optional pre-dispatch diagnostic shedding and fixed production logging. Required policy audit and post-dispatch terminal accounting remain fail closed. Cache authentication failures retain a structured 503 response. |
| 7 | Accounting protocol v2 | PostgreSQL owns canonical operations, exact money, immutable events, replay identity and generation fences. |
| 8 | One grant database operation | Existing dedicated adapter and atomic funded-grant operations remain. RPC ownership does not add a database call to every warm local permit. |
| 9 | Bounded grants | Existing subject/window escrow, operation caps, TTL, refill collapse and conservative recovery remain. |
| 10 | One-use permits | Existing bounded local bank, unique ordinals, owner fences and unused-suffix returns remain. |
| 11 | Escrow dispatch proof | Native dispatch uses local funded permits. The durable-claim mode remains available for compatibility. Batch and Realtime keep their main admission contracts. |
| 12 | Compact transport | Existing compact grant/permit replies and bounded retained cursor bytes remain. |
| 13 | Durable terminal journal | Response acknowledgement appends a bounded journal document. Fenced processing remains asynchronous and is included in drain checks. |
| 14 | Payload isolation | Compact acknowledgement proof is separate from wide documents. SQL processing and reporting retain byte/page limits. |
| 15 | Role split | Native request transport and native projection have separate minimal process graphs. Request replicas do not run projection loops. |
| 16 | Role resource bounds | Each native role owns its declared pool, queues, tasks, deadlines and readiness. Native projection now uses the planned eight-connection pool. |
| 17 | History-independent health | Existing fixed-cell/head observations and indexed bounded work queries remain. Required readiness is not an unbounded accounting-history scan. |
| 18 | Cross-worker settlement | Existing journal/ordinal fences prevent closure with accepted work and preserve uncertain owner loss. |
| 19 | Economic settlement first | Canonical exact counters are settled independently of heavy native read-model projection. Compatibility mode remains separate. |
| 20 | Redis recovery bounds | Restored 64 critical waiters, zero cache/bulk waiters, one acquisition deadline and socket readiness outside the driver bookkeeping lock. Connection ceilings remain 64/16/16. |
| 21 | Two terminal lanes | Native projection now owns two existing independently fenced journal workers. Each retains one claim. All tasks stop and drain through the existing process lifecycle. |
| 22 | Four reporting lanes | Native projection now owns four independently fenced reporting workers. They share one progress observation owner. Six work lanes leave two connections in the eight-connection pool for control/recovery. Only disposable Redis fixtures disable RDB/AOF. |
| 23 | Sharded rollups | Existing partitioned native rollups and bounded summing readers remain. The dedicated native projection batch is now 256; request batches and role count do not increase. |
| 24 | Matched admission guards | Native qualification now records 256 ingress slots, no ingress waiters, and 150/150 preflight. Other production/PR9 profiles are unchanged. |
| 25 | Settled receipt fast path | Existing native settled-receipt operation/event writes omit redundant reservation rows. Uncertain and compatibility receipts keep their conservative path. |
| 26 | Narrow streamed projection | Existing migration/query shapes retain bounded set-oriented narrow-key processing and avoid repeated wide JSON materialization. |

All source measures have an implementation owner. This is not proof that the
clean replay meets the load limits. Full route, database, Redis, chart, container
and qualification gates remain required.

## Verification and call budgets

- Metrics checks: 23 passed. Snapshot, GC, disconnect and phase checks: 20 passed.
- Bounded Redis recovery checks: 40 passed, including actual Redis.
- Failure telemetry and startup logging checks: 21 passed.
- Routing, cache and fixed-profile checks: 262 passed.
- Harness and prompt-cache checks: 72 passed.
- Redis report, budget, harness and size checks: 57 passed.
- Existing chat, failover and routing checks: 261 passed.
- Native lane, role, profile and chart checks: 93 passed.
- Actual PostgreSQL role graphs: 7 passed, including both one-lane compatibility
  and two/four-lane native processing, exact drain and projection loss.
- The real allocated-client routing budget is one prerequisite pipeline and two
  scripts: admission and completion. Duplicate or lost completion replies cannot
  increment usage twice. Complete gateway Redis counts must still be measured.
- Readiness extraction and restored-owner structure checks: 21 passed. All 153
  new backend modules since the main base meet the 500-logical-line and
  80-logical-line function limits. The size check first found one oversized
  readiness inventory. It is now split into service and policy helpers with the
  same order, required checks, and failure behavior.

The first full application lane passed 1,648 cases and failed one simulation
read-count assertion. Its retry and fallback results were correct. The assertion
still observed the two old readers. It now requires one combined read for both
deployments and zero separate reads. All 24 focused simulation/readiness checks
passed. The full application confirmation then passed all 1,649 cases in 519.20
seconds. Keep the original failed gate with its confirmation.

The first full PostgreSQL lane passed 846 cases and failed four local-permit
replay/shared-queue cases. They observed extra recovery calls or admission
deadlines. Both unchanged modules then passed all ten cases in isolation in 5.11
seconds. That confirmation does not establish the cause or a product fix. The
unchanged full confirmation passed those four cases, but passed 844 cases and
failed six reporting startup cases. Keep both failed full runs. Do not weaken
the original call-count, money, replay, or deadline assertions.

### Reporting startup recovery

The second full run recorded database calls at the 250 ms deadline. It does not
establish why those calls were slow. An unchanged focused run passed all 14
affected role, selector, and Realtime cases. Source review found that reporting
startup stopped on its first temporary database failure. The existing worker
recovery loop had time left, but its startup signal meant "first loop finished",
not "a real readiness check finished".

Startup now waits for a completed readiness check through the existing bounded
loop and backoff. Every startup call uses the original caller deadline. No
statement, readiness, freshness, or startup limit was increased. A persistent
failure still stops startup and cancels its owned task. Only the progress owner
can replace or withdraw the shared progress observation; each failed secondary
lane still withdraws group readiness. A failed progress observation is checked
again on recovery, not reused as healthy.

The first focused check passed 95 cases and failed two new deadline assertions.
The repaired confirmation passed all 97 cases. Actual PostgreSQL confirmation
passed all 14 affected cases. All five full dependency lanes are now running
again in sequence. The complete PostgreSQL lane then passed all 850 cases in
748.20 seconds. The Redis lane passed all 112 cases in 16.44 seconds. Final
component/chart confirmation passed all 6,040 cases in 98.84 seconds. The final
application confirmation remains pending. These results do not establish a
gateway RPS result.

### Metrics exception ownership

Final review found that the metrics encoder's asyncio wrapper did not observe
its exception, although its thread future did. A small diagnostic reproduced
"Future exception was never retrieved" while the last good snapshot stayed
available. Two new regression cases reproduced immediate and late failures.

Refresh now reads the result through its asyncio wrapper and cancels that wrapper
when its caller stops waiting. A running thread still retains the only encoder
slot until it finishes. No additional thread, queued generation, deadline, byte
limit, or scrape policy was added. All 39 focused observation, bootstrap,
structure, and role checks passed. The incomplete application gate was stopped
before this source change, after 588 passing cases; keep its log as interrupted,
not passed. The affected real-role, component/chart, and full application checks
are running again. The complete 850-case PostgreSQL and 112-case Redis results
remain valid for the unchanged database and Redis paths.

The real-role confirmation passed all six cases. Component/chart confirmation
passed all 6,042 cases in 92.50 seconds, including both wrapper failures. The
final full application gate remains pending.

The final application confirmation passed all 1,649 cases in 522.49 seconds.
Final collection contains exactly 8,653 cases: 5,778 hermetic, 1,649 application,
850 PostgreSQL, 112 Redis, and 264 Helm. Every case has one dependency lane. All
five lanes passed without required skips. Final source lint and format checks
passed. UI sources did not change after their 274-case test/build and unchanged
119-finding full-lint comparison. All three migration paths through migration
142 and all six deployment-profile lint/render checks remain valid. No SQL or UI
source changed during the restored-measure repairs.

The first lane-group verification exposed a source syntax error. Its repaired
confirmation passed. The first routing call-count test passed a string instead
of a model-group list; its confirmation must select an actual deployment. Keep
failed logs with confirmations. Do not describe failed runs as passing gates.

### Native deployment startup repair

Commit `eed8af71` passed all five exact-image checks, but fresh kind setup failed
before generator proof or gateway load. Helm supplied the legacy worker's
four-connection control limit while native projection declared eight connections.
The native pool rejected the mismatch. Request and API roles then could not
start because projection was not ready. The setup was stopped, its logs were
saved, and its disposable cluster was removed. It has no RPS result.

The chart now derives each minimal native role's database startup limit from its
`accounting_hot_path_db_pool_size`. This matches the existing capacity report and
actual dedicated pool. API and legacy worker limits do not change. Tests now
check rendered configuration, environment, resolved settings, an overridden
native allocation, and actual eight- and two-connection PostgreSQL pools. Both
rendered regressions failed before the repair. All 18 native chart cases and
seven real-role PostgreSQL cases passed after it. Full confirmation then passed
all 265 chart tests and all six profile lint/render checks. Collection contains
8,654 cases in exactly one lane each. Source lint, changed-file format, generated
capacity reference, and diff checks passed. A new clean image and qualification
remain required. No backend runtime, SQL, or UI source changed in this repair;
the earlier full backend, migration, and UI gates remain valid.

## Fixed qualification differences

The earlier source accepted 500 RPS with p95 234 ms, p99 448 ms and a 272.6-second
drain under its 360-second drain limit. The current runner still has p95 150 ms,
p99 300 ms and a 180-second drain limit. These limits are stricter. Do not change
them during a series or relabel the old source result as a clean replay result.

Native qualification keeps four API processes, two request processes and one
projection process. PostgreSQL and Redis remain in the disposable kind cluster.
Normal metrics stay enabled. Record all queue, latency, throughput and economic
gates separately. No pause is allowed inside an arrival window.

## Completion checks

- [x] Map all 26 source measures to current implementation owners.
- [x] Restore omitted request-path, observation and worker-lane measures.
- [x] Preserve main's bounds, failover, cache charge, batch and Realtime contracts.
- [x] Complete full affected backend lanes and current chart/configuration gates.
  - [x] Full application confirmation: 1,649 passed.
  - [x] Source lint, changed-file format, generated references and container parity.
  - [x] Lint and render all six effective deployment profiles.
  - [x] Full PostgreSQL confirmation: 850 passed.
  - [x] Full Redis confirmation: 112 passed.
  - [x] Component/Helm confirmation before metrics cleanup: 6,040 passed.
  - [x] Final component/Helm confirmation after metrics cleanup: 6,042 passed.
  - [x] Final application confirmation after metrics cleanup: 1,649 passed.
- [x] Commit restored source and pass all five exact-image checks at `eed8af71`.
- [x] Reproduce and repair the native deployment database-limit mismatch.
- [x] Confirm all 265 chart tests and all six deployment profiles after the repair.
- [x] Pass all five exact-image checks on the repaired commit `f01ef9ff`.
- [x] Run its fresh short ladder and preserve failed latency, rate, and queue gates.
- [ ] Complete the [finalization CPU remediation](issue-320-finalization-cpu-remediation.md).
- [ ] Pass a fresh generator proof and 50/100/200/500 short ladder.
- [ ] Run all four ten-minute qualification stages with the same image/profile.
- [ ] Save results, update the plan, and prepare the local merge handoff.
