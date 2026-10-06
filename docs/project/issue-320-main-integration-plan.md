# Issue 320 main integration plan

Status: active

Base: `origin/main` at `f5ffd80d`

Source implementation: `feat/issue-320-accounting-v2` at `534ef828`

Accepted performance code: `cc3113bd`

## Current status

Latest checkpoint: the clean integration is connected, but final qualification
is still open. The sustained unchanged `d93237b6` 500 diagnostic failed latency,
queue growth, and the economic gate after seven HTTP 503s. Budget scopes match
durable facts, including the successful precheck; no unsafe balance or unsettled
work remained. PostgreSQL saturated, and reporting claims grew from about 3 ms
to 46 ms. A real-index retained-history probe reproduced growing index-page work
in the composite frontier. Keeping that frontier and adding an exact sequence
bound passed 51 affected PostgreSQL and 148 component/tool checks; the corrected
new regression failed all eight cold/analyzed planner cases with the original
query. Keep resource and gate
limits unchanged. The canonical `6194a168` image passed all five offline checks,
shipped-query bounds, and unchanged migration checksum checks. Selected upper
tiers and final qualification are now running but are still open.

The reporting correlation fix at `9c5fca15` passed its source
and exact-image checks, but selected 500 still failed only the queue-slope gate:
30,000/30,000 successes, zero errors/drops, p95 93.51 ms, p99 135.29 ms,
exact accounting and drain passed. A separate actual cold terminal-claim probe
then found completed-history scans in cached array-key statements, including
empty work. Migration 146 adds empty-work returns and a claim-function-local
custom-plan policy; it leaves reporting and caller/pool/database settings alone.
The 77 affected PostgreSQL checks, eighteen-case rollback-forward confirmation,
86 component/tool checks, and all three migration paths passed. The normal
`d93237b6` image also passed all five offline checks and packaged migration
checksum checks. Recheck 500 before 200 or the full series. Earlier failures and
unsuccessful private prototypes remain in the CPU remediation plan. Do not
describe source checks as a capacity pass or completed four-tier qualification.

Slices 1 through 8 are complete. The runtime code for slices 9 through 14 is now
connected, including native reporting and shared Realtime, batch, and selector
accounting. The generated-client repair checkpoint passed all 8,573 Python cases.
UI checks, migration paths, and chart checks also passed. Full UI lint retains
unchanged baseline findings. The exact image passed all five smoke checks. Its
short ladder failed, and the retained-measure audit found missing Redis, routing,
failure-telemetry, observation, and worker-lane work. Those measures are now
restored. Their full regression confirmation passed all 8,653 Python cases. Commit
`eed8af71` passed all five exact-image checks. Its fresh cluster found a native
database-limit mismatch before any load test started. The chart repair is now
verified at `f01ef9ff`, and all five image checks passed. Its generator proof and
short 50 RPS stage passed. The short 100 and 200 RPS stages failed latency, and
500 RPS failed rate, queue, and latency gates. No ten-minute stage started. The
[finalization CPU remediation plan](issue-320-finalization-cpu-remediation.md)
records the evidence and the next independently tested changes. The experimental branch's
500 RPS result is not evidence for this clean replay. This branch is not ready to
merge.

The measured CPU-cost fixes are now implemented. The latest full component/chart
confirmation passed 6,059 cases. Full application checks passed 1,649 cases.
Full database and Redis confirmations passed 854 and 112 cases. All three
migration paths passed through migration 143. Its function-local execution
policy fixed the repeatable funding timeout; 61 focused policy, RPC, and retained-
history plan checks passed. Commit `476904b4` passed all five image checks.
Its first load run failed queue gates and the 200 RPS accounting drain. An
approved isolated rerun stopped Rancher temporarily and used the same image,
resources, profile, and limits. Short 50, 100, and 200 RPS passed all gates.
Short 500 RPS still failed; no ten-minute stage started. Rancher and the user's
original contexts are restored. The CPU remediation checklist retains both runs.

The remaining immutable terminal boundary is now implemented through the same
proof, transport, queue, and journal owners. All 249 focused checks passed.
The instrumented conversion diagnostic fell from 5.20 to 1.84 seconds for
3,200 entries. This is not an RPS result. Full confirmation passed all 8,704
collected cases: 6,089 component/chart, 1,649 application, 854 PostgreSQL, and
112 Redis. Source, lock, image-export, configuration, and capacity checks passed.
Commit `5a28ca26` passed all five new exact-image checks and generator proof.
Its isolated short 50, 100, and 200 RPS stages passed all gates. Short 500 RPS
failed rate, latency, queue, diagnostics, and drain gates. Four operations stayed
unsettled with safe provisional capacity. No ten-minute stage started. Rancher
and both original contexts are restored. The CPU plan records this new result
and the remaining measured-cost work. This branch is not ready to merge.

### Restored-measure regression checkpoint

The latest measured-cost slice removes duplicate local-handle encodes and
extracts bounded idle waiting for the existing spend/audit consumers. All 140
focused financial cases and 113 focused worker/handle/structure cases passed.
The second set includes 20 idle-wait cases. No persistence, financial release,
request deadline, pool, resource, or qualification limit changed. The complete
8,728-case confirmation finished. Component/chart, application, and Redis
passed 6,113, 1,649, and 112 cases. The second full PostgreSQL run passed 852
cases and failed two. Both unchanged failed cases passed in a focused check.
The Realtime conflict case now sets its recovered row explicitly due, without
changing receipt or money checks. The 80-case affected-module and allocation
check passed 79 cases and failed one existing statement-timeout assertion.
That unchanged case passed alone. All failed logs remain preserved; the full
PostgreSQL gate is not a clean pass, and the warmup timeout cause remains open.
Keep simultaneous cold-pool coverage and all original bounds. Use targeted
checks for this iteration. Commit `861947c8` passed all five image checks and
two fresh generator proofs. Its first short ladder passed 50 and 100 RPS,
failed 200 RPS, then stopped on an offline Kubernetes read before 500 RPS.
Its second ladder passed 50 and 200 RPS, failed the 100 RPS queue slope, and
failed 500 RPS with 2,075/15,000 successes and 69 unsettled operations.
No ten-minute stage started. Keep both failed runs. The full PostgreSQL
limitation also remains open.

The next evidence slice now preserves stopped stages in the aggregate result
and captures at most 64 unsettled operations in a bounded read-only transaction.
All 47 focused tool/PostgreSQL checks passed, including unchanged exact money,
real truncation, cancellation, privacy, and failed-result retention. This changes
no runtime owner, resource, deadline, financial release, or pass limit. The CPU
remediation plan tracks the next fixed-image run and runtime diagnosis.

The sealed `d3ec62bc` image passed all five smoke checks and generator proof.
Its four short stages all failed at least one gate. The 500 RPS stage completed
5,163/15,000 requests and left seven open grants, but no unsettled operation
or queued processing work. The failed aggregate now retains the 500 RPS result.
Readiness failures and client connection timeouts were also observed. No
ten-minute stage started. The host was not free of unrelated load.

Per the user's request, new iterations will run selected 200/500 RPS short
diagnostics, without repeating lower tiers. Selected runs cannot claim release
eligibility. Bounded open-grant evidence and that distinction passed 61 focused
tool/PostgreSQL cases. Next, confirm the funding-acknowledgement and readiness
failure paths, fix their proven cause, then run complete qualification once.

The first selected 500 RPS diagnostic passed image checks and generator proof,
but stopped on an accounting-unavailable gateway precheck. No 500 RPS arrival
stage ran. Its bounded logs show failed accounting health, API database probe
timeouts, and projection connection-release timeouts. The next runtime slice
removes repeated empty native claims: six lanes at the profile's 20 ms interval
can schedule about 300 claim calls per second. Journal and reporting workers
now reuse the existing bounded idle-wait owner, while active processing, failure
backoff, financial fences, and health freshness remain unchanged. All 127
focused checks passed. Affected component confirmation passed 264 cases, and
real PostgreSQL confirmation passed 56 cases. Its sealed `c1b3ee9d` image passed
all five checks. Selected 200 RPS completed all 6,000 requests but failed the
queue-slope gate. Selected 500 RPS completed 4,725/15,000 requests and failed
with one unsettled operation. No lower tier or ten-minute stage ran.

Bounded 500 RPS diagnostics then traced billing rejection to reporting health.
The last diagnostic completed 13,812/15,000 requests, with 1,188 billing HTTP
503 responses; all work drained in 12.12 seconds. A controlled PostgreSQL test
reproduced a reporting claim race: another worker can advance the checkpoint
after candidate selection, leaving an invalid empty claim and a live lease.
The repair checks for remaining work after locking the current checkpoint.
It keeps strict result validation and an indexed single-row lookup. This proves
the race and its repair, not that all load failures are fixed. A new upper-tier
run remains required. Focused confirmation passed 109 component cases and 37
real PostgreSQL cases, including all four planner profiles. One existing role
startup case failed, then passed unchanged alone. Both results are preserved.
The earlier full PostgreSQL limitation remains open.

The reporting-race repair is sealed at `5b271273`. All five image checks and
generator proof passed. Selected 500 RPS still failed: 9,092/15,000 successes,
1,235 dropped arrivals, p95 3,158.82 ms, and one unused open grant after the
181.18-second failed drain. Host snapshots show about 2.44 GB of swap reads
and 2.65 GB of swap writes across setup, load, and post-arrival capture. A
bounded follow-up recorded database deadlines and pool waits across several
owners, then provider read errors and cooldown. It did not record the repaired
invalid-result claim failure. Both failed results are preserved. The user then
approved stopping the other Colima VM. The unchanged image started all 15,000
arrivals but completed only 11,462 successfully. It returned 3,508 no-healthy-
deployment errors and 30 other HTTP 503 responses. p95 was 441.00 ms and p99
was 1,040.59 ms. Drain failed with one uncertain provider operation; all grants
and queues were empty and all budget windows were safe. The VM and its eight
original running containers were restored, as were the original contexts.
Another test task started Redis in the qualification VM before gateway
arrivals, so this failed comparison cannot count as fully isolated. Next,
reserve that VM from concurrent tests and trace the first provider failure.
Do not relax safety, resources, deadlines, or pass limits. Keep 50 and 100 RPS
out of iteration runs. Final qualification remains open.

The user approved coordination with both other test chats. They cleared their
services, and the new diagnostic confirmed an exclusive qualification VM.
It still failed at 500 RPS with 2,118/15,000 successes and 7,038 dropped
arrivals. The trace recorded accounting and Redis deadline failures, not a
provider transport error. Host paging remained high: about 2.76 GB read and
1.84 GB written across setup, load, and drain. All original workloads and
contexts were restored. Next, compare a temporary 8-GiB test VM with the same
six CPUs, image, pod limits, and strict gates. This is an environment check,
not a code repair or release certificate.

Two temporary 8-GiB attempts stopped before arrivals because Docker's registry
DNS failed. Both interrupted attempts and restoration logs are preserved.
The host-side test loader now reuses cached fixtures only after exact pinned
digest verification; missing images still require a digest-pinned pull.
All 70 focused tool checks and the real three-image cache proof passed.
Gateway runtime source and image `5b271273` remain unchanged.

The third 8-GiB attempt completed the selected 30-second 500 RPS stage.
All 15,000 requests succeeded with no drops or errors. Exact accounting and
dependency checks passed; accounting drained in 12.09 seconds. The stage
still failed p95 latency (187.79 ms against 150 ms) and queue slope (+0.9213
against +0.01). p99 was 299.23 ms. Host paging fell to about 0.206 GB read
and no writes across setup, load, and drain. No 200 RPS or lower stage ran.
Both VM settings, all original workloads, and both contexts were restored.
Keep this failed evidence in `native-5b271273-500-8g-exclusive-20261006-3`.
Confirm this image and environment once without a runtime change. Preserve
both results. If 500 passes, run selected 200, then the full fixed-image
series. If it still fails, measure the missing request-path timing first.
Full qualification remains open; no gate has been waived.

The unchanged 8-GiB confirmation also failed: 14,866/15,000 successes,
72 dropped arrivals, 62 HTTP 503 responses, p95 1,187.54 ms, and p99
2,251.47 ms. Accounting drained safely in 12.12 seconds. Charges matched
all four scopes, but partial-run success identity remains unknown and its
economic gate failed closed. Host paging was only about 0.039 GB read
with no writes, so paging alone does not explain this result. Preserve
the `-4` evidence and the first comparison. All original resources were
restored. Next, run one bounded 8-GiB RPC timing diagnostic on the unchanged
image before a runtime change. Keep lower tiers out of the iteration loop.

The bounded timing check completed all 15,000 requests with safe exact
accounting, but missed latency and queue gates. Local finalization averaged
62.94 ms per request out of 114.35 ms total client latency. Its 3,373 signed
RPC batches averaged 30.43 ms; native journal SQL averaged 11.72 ms over
2,381 batches. These are nested measures, not additive stages. No transport
or native database error was captured. Next, test a private two-batch API
terminal pipeline with the original shared byte and pending queue limits,
and unchanged combined selected-entry capacity. Five bounded ownership,
cancellation, and shutdown self-check groups passed. No runtime source or
production image has changed. The probe cannot count as release qualification.

The two-batch probe worsened p95 to 419.39 ms and p99 to 528.82 ms despite
all 15,000 requests succeeding and exact accounting passing. Reject this
private scheduling change; production remains unchanged. API cgroup work
was about 98.74 CPU-seconds, versus 34.10 for PostgreSQL. Next, profile one
API process for three seconds under 500 RPS with original scheduling.
Use that bounded CPU evidence to select the next change, not another
increase in concurrency. Full qualification remains open.

The three-second CPU profile identified heavy JSON encoding and model
validation. Source shows a redundant API terminal snapshot construction:
the service freezes it, keeps only its bytes, then reconstructs it on drain.
The next private probe keeps the immutable snapshot in the original single
queue and charges its full retained memory. Five existing financial-path
checks and a direct one-snapshot ACK check passed. This prototype cannot
be shipped as-is: full-width wire and retained-byte contracts must be
separated and tested if it improves performance. Runtime source and the
sealed `5b271273` image remain unchanged; full qualification is still open.

The retained-snapshot diagnostic failed: 5,199/15,000 successes, 1,795
dropped arrivals, 8,006 HTTP 503 responses, p95 3,415.45 ms, and p99
4,116.57 ms. Native database deadlines preceded readiness failures and
a provider read error. Drain failed with one safely held uncertain
operation. Reject the private prototype; no runtime change is supported
by this result. All original resources were restored and evidence kept.
Next, verify a smaller private header-only ASGI wrapper and test it at
500 RPS with the original financial behavior. Keep admission, durable
acknowledgements, deadlines, resource limits, and all qualification gates.
Do not rerun lower tiers during this iteration.

The header-only probe also missed the strict gates despite all 15,000
requests succeeding with exact accounting: p95 173.11 ms, p99 240.86 ms,
and queue slope +0.9548. No runtime change is being shipped from these
experiments. Next, compare the original image without hooks on an
explicitly separate eight-CPU/eight-GiB VM, retaining the same pod limits,
topology, deadlines, and all pass gates. If selected 500 and 200 pass,
run the full series there once. Do not present this as a six-CPU pass.

The eight-CPU comparison passed throughput, p95 (109.00 ms), p99
(141.39 ms), diagnostics, exact accounting, and drain. It still failed
the short-window in-flight slope (+1.1104). Preserve this failure; the
full series did not start. Next, use the driver's supported 60-second
short duration with the same original image and environment to check
whether growth persists. No pass gate changes or arrival pauses.

The 60-second check also failed: all 30,000 requests succeeded with safe
exact accounting, but p99 was 533.29 ms and queue slope +0.09954.
p95 passed at 129.86 ms. In-flight count mostly settled with two brief
spikes. Existing records do not show long event-loop pauses or substantial
host paging. Next, use the existing bounded terminal/RPC timing trace
on this unchanged eight-CPU image to locate the remaining wait.
Full qualification remains open.

The eight-CPU wait trace captured a journal-append lock timeout. Source and
a controlled PostgreSQL 15 regression identified an unnecessary contention
point: normal background claims lock the same capacity row as foreground
append, despite not updating it. A guarded append-only migration now locks
that counter only for exhausted claims. The original ordinary-claim test
failed, then all 30 worker checks passed with the change. Monetary effects,
fences, limits, and pass gates remain unchanged. Retained-history plans,
migration paths, rollback, source gates, and a new image still need verification.
This is not a 500 RPS pass. Continue upper-tier testing before the full series.

The lock-scope change passed all affected checks: 30 worker, 54 PostgreSQL,
four additional retained-history plans, 93 component, and 1,649 complete
application-route cases. Fresh, supported-release, and shared-feature
upgrade migrations passed, as did rollback and forward reapplication.
Client generation, lint, formatting, and diff checks passed. Next, seal
the new SQL migration in a fresh canonical image and verify its exact
artifact before selected upper-tier testing. All load gates remain open.

The fresh canonical `7b24cc12` image passed all five offline non-root smoke
checks. The new migration was checked inside the immutable image and matches
the source SHA-256. Its index digest is
`sha256:fea18e870466af92ffb16f686c5ac119f677f2fa022e6f65101c9db63035e6e2`.
VM registry DNS blocked two builds; a temporary, allowlisted build-only TLS
relay completed the unchanged Dockerfile and was removed before load testing.
No proxy settings entered the runtime image. Next, run uninstrumented selected
500 RPS for 60 seconds on the separate eight-CPU/eight-GiB environment, followed
by selected 200 and then one full strict series only if preceding stages pass.
This is not yet a throughput pass and does not prove six-CPU capacity.

The new-image selected 500 check completed 30,000/30,000 requests with
zero drops/errors, p95 86.86 ms, p99 170.41 ms, exact accounting, and a
10.16-second safe drain. It failed only the strict in-flight slope gate
at +0.15214. Preserve the complete failed stage; 200 and full qualification
did not run. All original VM settings, workloads, and contexts were restored.
Next, make one unchanged confirmation, not another unproven code edit.
Keep the upper-tier and full qualification checkboxes open.

The unchanged 500 confirmation again completed every request and passed
latency, diagnostics, exact accounting, and drain, but failed queue slope.
No 200 or lower-tier rerun occurred. A controlled PostgreSQL probe proved an
unused checkpoint lease-expiry index prevented all heap-only updates and
created avoidable dead versions. All readers already scope by projection and
generation; the composite primary key remains. A guarded append-only migration
now removes only that secondary index, with an explicit recreation rollback.

The original churn regression failed, then all nine reporting-plan checks and
90 affected PostgreSQL behavior/race/parity/compatibility checks passed.
Fresh, supported-release, shared-feature, rollback, and forward reapplication
checks passed. Runtime Python, Prisma schema/client, leases, money calculations,
queries, deadlines, and load gates are unchanged. Seal a new canonical image
before upper-tier testing. This is verified write-churn reduction, not yet
proof that 500 RPS qualifies. Preserve both `7b24cc12` failed load results.

The fresh `1aaf2bda` image passed all five offline checks, and both packaged
migration hashes match source. Its index digest is
`sha256:c927258c258d910ffa9f79189c84323bb7180a8224568634af15728fefb296bd`.
The build-only relay and verification fixture are stopped before testing.
Proceed to uninstrumented selected 500, selected 200, and then the complete
series only if preceding strict stages pass. No lower-tier iteration reruns.

The `1aaf2bda` cold 500 run completed all 30,000 requests and passed latency
(p95 78.41 ms/p99 131.83 ms), diagnostics, exact accounting, and safe drain.
Only slope failed (+0.12560). Keep this failure. Next run the existing supported
200→500 selected ladder on one instance to check the increasing-load pattern
used by final qualification, without repeating 50/100. No thresholds, request
counts, arrival exclusions, or delays change. Only proceed to the canonical
full series if both selected stages pass. Cold-start capacity remains unproved.

The upper ladder passed 200 completely (12,000 successes, p95 24.93 ms,
p99 37.35 ms, slope 0, exact accounting/drain). Its 500 stage again passed
throughput, latency, diagnostics, accounting, and drain, but failed slope
at +0.05254. Full qualification did not run; preserve this result and the
cold failures. Both VMs/workloads/contexts were restored. Next, check the
private header-only ASGI forwarding on the current image, with no timing or
profiling hooks. Runtime source remains unchanged pending measured evidence.

The retained-measure audit maps all 26 accepted source measures to their clean
implementation owners. Native processing now has two terminal lanes, four
reporting lanes, one progress owner, and the declared eight-connection pool.
Reporting startup recovery uses its original deadline. Metrics encoding stays
off the event loop and observes immediate and late worker exceptions.

- [x] Full PostgreSQL: 850 passed in 748.20 seconds.
- [x] Full Redis: 112 passed in 16.44 seconds.
- [x] Full component and Helm: 6,042 passed in 92.50 seconds.
- [x] Full application: 1,649 passed in 522.49 seconds.
- [x] Exhaustive, non-overlapping collection: 8,653 cases.
- [x] Source lint, formatting, generated references, frozen lock, and container parity.
- [x] All six deployment-profile lint and render checks.
- [x] Preserve failed and interrupted runs with their confirmations.
- [x] Commit the restored source and pass all five exact-image checks at `eed8af71`.
- [x] Reproduce and repair the native role's rendered database-limit mismatch.
- [x] Confirm the repair: 265 chart tests and all six deployment profiles passed.
- [x] Commit the repaired chart and pass all five new exact-image checks at `f01ef9ff`.
- [x] Run the fresh short ladder and preserve its failed gates.
- [ ] Complete the finalization CPU remediation checks.
- [ ] Pass a fresh generator proof and short 50/100/200/500 RPS ladder.
- [ ] Run all four ten-minute stages with the same image and fixed profile.
- [ ] Save current raw evidence, exact charges, storage state, and final results.

The earlier UI gates remain valid. Migration 143 changes only three local
function settings, and all three upgrade paths passed. The client was
generated again. These regression results are not throughput results. Keep the
plan active until new qualification is complete.

### Native deployment startup repair

The `eed8af71` fresh setup failed before generator proof or gateway load. Helm
kept the legacy worker's four-connection control limit while native projection
declared eight connections. The native pool correctly rejected this mismatch.
Request and API roles then refused admission because projection was not ready.
The failed setup, pod errors, and exact-image logs remain in the evidence folder.
Its disposable cluster is removed. No RPS result was produced by this setup.

For minimal native roles, Helm now derives the database startup limit from
`accounting_hot_path_db_pool_size`. Configuration, environment, capacity report,
and the actual dedicated pool have one allocation. API and legacy worker pools
do not change. No capacity limit, timeout, or qualification gate was increased.
Two new rendered-setting assertions failed before the repair. All 18 native
chart cases and seven real-role PostgreSQL cases then passed. Full confirmation
passed all 265 chart tests and all six profile lint/render checks. Collection
contains 8,654 cases in exactly one lane each. Source lint, changed-file format,
generated capacity reference, and diff checks passed. A new clean image and the
complete RPS series remain open. The earlier full backend, migration, and UI
gates remain valid; no backend runtime, SQL, or UI source changed in this repair.

The migration-131 checkpoint passed all 7,829 tests and all three migration paths.
The first PostgreSQL run failed two compatibility cases. Their unchanged modules
then passed all 15 cases, and a full confirmation passed all 705 PostgreSQL cases.
Keep the original failures: the passing confirmation does not establish their
causes or a production fix. The supervised journal worker is now implemented.
Its first broad database run passed 710 cases and failed one unchanged child-
startup case. The failure occurred after 9.69 seconds of imports, before database
connection completed. The unchanged recovery module then passed all seven cases.
The shared local startup and shutdown checkpoint passed all 7,908 cases across
the five lanes, including all 719 PostgreSQL cases. The final lifecycle guards
passed 114 focused cases and all 5,441 component and Helm cases. Collection now
contains 7,911 cases. Keep the original cold-start failure; confirmation is not
a production fix. Migration 132 now limits inspected settlement keys before
eligibility checks. This checkpoint passed all 7,924 tests and all three migration
paths. Durable backlog health passed 100 focused cases, all 5,489 component and
Helm cases, all 745 PostgreSQL cases, and all three migration paths. Collection
now contains 7,985 cases. Runtime selection, transport integration, current-main
adapters, reporting lanes, and final RPS tests remain pending.

The signed transport, native recovery, fenced projection presence, API-local
owner graph, and minimal native role apps are now implemented. The role apps
have real generation and migration checks. They do not load the inference
bootstrap or provider adapters. The managed launcher and Helm now select them
through the same startup-only role setting. The API owns one dedicated signed
RPC pool and one local issue/return/monitor graph. Native facts and reporting
readers are connected. The complete affected lanes, current-main adapters,
container smoke, and RPS tests remain required.

### Shared batch and Realtime checkpoint

Batch now uses the shared accounting authority for single and grouped chat and
embedding calls. It stores a bounded proof before provider dispatch. A claimed
item cannot repeat a paid provider call after worker loss. The existing completion
outbox owns terminal delivery. Its native transitions require both the live lease
and the attempt fence. Prices and tenant attribution stay fixed during replay.
No new feature pool, worker queue, or ledger was added.

External cancellation tests found an embedding-group cleanup gap. One small
execution owner now closes accounting, heartbeat, and policy resources on every
exit. Native Realtime uses shared accounting health, not the disabled legacy spend
worker. Legacy mode keeps its previous worker and recovery requirements.

Evidence:

- Actual batch provider and cancellation checks: 260 passed.
- Mixed batch, selector, Realtime, HTTP, and cache accounting on PostgreSQL:
  3 passed. All five budget scopes reconciled exact charges, unused reservations,
  and uncertain outcomes. Duplicate completion delivery did not charge twice.
- Migration 142: fresh, last-release, and shared-feature paths passed.
- Final focused bootstrap, checkpoint, and Helm checks: 51 passed.

Keep the earlier failed runs. Four final focused failures asserted that a
disabled worker was unhealthy; disabled is an intentional state. One outbox test
depended on an immediate due-time boundary. The corrected test sets due time with
the database clock. No health rule or production timeout was weakened.

The application lane passed 1,646 tests. The final database lane passed 845 cases
and found two failures. One exposed an admission-monitor race: refreshing a
healthy observation withdrew readiness before its result was known. The monitor
and presence publisher now retain only fresh completed observations during a
refresh. Failure, cancellation, task loss, and expiry still withdraw readiness.
Barrier tests and actual Realtime/report checks passed 69 cases. The second
failure was a stale report-plan assertion. The report now has a bounded selector
primary-key lookup as well as the original scoped page index. Both are checked.
The complete database lane must pass again; keep the failed run.

Final qualification-tool, real-schema, and native chart checks passed 86 cases.
The generator now has rate, duration, sample, task, response-byte, artifact, and
drain limits. Native monitoring now selects both isolated roles and exposes the
worker's real fresh-observation metric to autoscaling. Native production renders
1,524 PostgreSQL connections within the unchanged 2,000 connection limit. Its
45-pod placement calculation includes all peak roles without raising descriptor
ceilings. Native evaluation renders 142/160 connections. These are arithmetic
checks, not a gateway throughput result.

### Remaining completion checklist

- [x] Connect native HTTP/cache, Realtime, batch, selector, and reporting owners.
- [x] Verify all three migration paths through migration 142.
- [x] Verify mixed-feature exact charges, replay, owner loss, and cancellation.
- [x] Fix readiness refresh races and add deterministic expiry/failure checks.
- [x] Render complete native production/evaluation budgets and monitoring targets.
- [x] Implement pinned kind qualification and independent evidence/economic gates.
- [x] Complete final hermetic, application, PostgreSQL, Redis, and Helm lanes.
- [x] Complete UI unit/build/lint comparison, frozen locks, references, collection,
  and style. Full UI lint retains the unchanged 119 baseline findings.
- [x] Commit the tested source; build and smoke-test the exact non-root image.
- [x] Run the direct provider generator proof and short 50/100/200/500 RPS ladder.
- [ ] Restore the missing retained measures and pass a fresh short ladder.
- [ ] Run all four ten-minute stages on that same image and fixed topology.
- [ ] Save final raw evidence, exact reconciliation, storage state, and results.
- [ ] Update the final plan and handoff. Do not push, open a PR, or merge without
  a separate user request.

### Pre-repair regression evidence

- Application: 1,646 passed in 544.10 seconds.
- PostgreSQL confirmation: 848 passed in 748.70 seconds, without required skips.
- Redis: 105 passed in 16.72 seconds, without required skips.
- Hermetic and Helm confirmation after the image-archive fix: 5,967 passed in
  182.71 seconds. Together the five completed lanes contain all 8,566 cases.
- UI unit tests: 274 passed. Node 22.16.0 was used.
- UI build: passed. Initial JavaScript is 378.43 kB gzip, identical to the isolated
  `origin/main` build. The existing large-chunk warning remains.
- UI lint: both touched files have zero findings. Full lint has 116 errors and
  three warnings, identical to `origin/main`; the structured comparison has zero
  new findings. Full lint is not a clean pass.
- Collection: 8,566 cases, each in one lane: 5,708 hermetic, 1,646 application,
  848 PostgreSQL, 105 Redis, and 259 Helm.
- All five base/evaluation/production/native Helm lint and template checks passed.
- Frozen lock, generated settings reference, capacity reference, container contract,
  source lint, touched-file formatting, and whitespace checks passed.
- The 45 new backend modules meet the 500-line and 80-line function limits.

The first database confirmation passed 847 cases and failed the completed selector
case. Its test waited for the first settled grant, not the whole generation. The
selector grant could settle before the answer grant. A bounded event-driven wait
now checks closed grants and completed native reporting. Exact totals and the
original two/three-second deadlines are unchanged. All 13 real mixed-feature
checks passed, followed by the complete 848-case database confirmation. Keep the
failed confirmation in `issue320-final-postgres-confirmation.log`.

Exact-image checks and gateway load evidence remain pending. These regression
results do not establish an RPS result. Final logs are retained in the ignored
`artifacts/qualification/verification-20261006` directory.

### Initial qualification setup check

The source was committed at `fd1709d9`. Its exact non-root image passed startup,
native migration CLI, read-only filesystem, bounded callback, and cancellation
checks. The first kind qualification then stopped before gateway load. The
platform-only metrics-server archive had no repository tag. Containerd imported
a synthetic name that Kubernetes could not resolve. No gateway RPS result was
produced. Keep `artifacts/qualification/native-fd1709d9-20261006` as failed setup
evidence.

The archive now retains its repository tag. The runner checks the platform
manifest digest and uses that immutable image reference on each owned node.
The original registry digest remains recorded. Application code, resource limits,
prices, test durations, and success limits did not change. All 88 focused load
and cluster tests passed. The broader component and chart confirmation passed
all 5,967 cases. A new commit, exact image, and fresh cluster remain required.

### Generated-client startup memory repair

The `5cb093c5` cluster passed resource-metrics setup. All seven application roles
then exceeded their unchanged 1 GiB memory limits during import. No provider
proof or gateway load started. The owned cluster was stopped and removed. Its
pod exit states are retained in `native-5cb093c5-20261006/startup-pods.json`.

The generated database types module contains 887,685 lines. Its uncached import
used 1,505,420 KiB peak RSS. Precompiled code reduced the same import to
723,364 KiB. Those measurements are startup imports, not request memory or RPS.

Repair plan:

- [x] Identify the import memory peak in the exact image. Keep all failed evidence.
- [x] Use Prisma's supported recursive types instead of five levels of repeated
  type expansion. Keep models, database structure, query methods, and money unchanged.
- [x] Compile the generated client during the canonical image build.
- [x] Add a 1 GiB offline image gate for actual API and minimal-role imports.
- [x] Regenerate the client and repeat affected tests and container contracts.
- [x] Build and smoke-test a new exact image. Start fresh kind evidence.

The generator mode is documented in the [Prisma configuration reference](https://prisma-client-py.readthedocs.io/en/stable/reference/config/#recursive-type-depth).
It changes generated Python type definitions, not database DDL. The repository
does not configure Mypy. Recursive types require a compatible static checker if
one is added later. No memory limit or startup timeout is increased.

The smaller client passed 5,969 component/chart cases and 1,646 application cases.
The database repeat passed 846 cases and failed two native Realtime admissions.
Debugging reproduced a timeout during permit funding and recovery. Each database
call reached its unchanged 250 ms limit. Later focused runs passed all four cases
with the original connection settings and about 20 ms funding calls. The cause
of the earlier delay is not established. These confirmations are not a production
fix. Keep the failed run in `issue320-final-recursive-postgres.log`. The complete
database confirmation then passed all 848 cases in 773.26 seconds with the
original connection settings, deadlines, and assertions.

The image identity now has one owner for Python sources, the frozen lock, the
dependency manifest, and the Prisma schema. The image cannot match a checkout
whose generated-client inputs differ. A parity test checks the local and in-image
hash programs.

The development image passed the new 1 GiB import gate. The actual API and native
role modules used 339,668 KiB peak RSS and imported in 5.04 seconds. This is an
import check, not a request benchmark. The generated types module now has
216,511 lines instead of 887,685. Native CLI, non-root, read-only, callback, and
cancellation checks also passed. All 42 focused image and qualification-tool
cases passed. The final clean image and gateway qualification are still required.

Final repair confirmation:

- Application: 1,646 passed in 522.09 seconds.
- PostgreSQL: 848 passed in 773.26 seconds, without required skips.
- Redis: 105 passed in 15.55 seconds, without required skips.
- Hermetic and Helm: 5,974 passed in 106.80 seconds.
- Collection: 8,573 cases, each in one lane: 5,715 hermetic, 1,646 application,
  848 PostgreSQL, 105 Redis, and 259 Helm.
- Source lint, touched-file formatting, canonical container parity, and whitespace
  checks passed.

The UI files, migration SQL, chart values, and runtime settings did not change in
this repair. Their earlier complete gates remain applicable. Keep the failed
database repeat and its funding diagnostics with the passing confirmation in
`artifacts/qualification/verification-20261006`.

### Clean short-ladder failure and retained-measure audit

Commit `22f62c8c` passed all five exact-image checks. All seven application roles
became ready within their original 1 GiB limits. The generator proof completed
all 10,000 requests at 1,000 RPS with no drops. The fresh short ladder produced:

| Target | Successful / scheduled | p95 | p99 | Result |
| --- | --- | --- | --- | --- |
| 50 RPS | 1,500 / 1,500 | 48.68 ms | 238.87 ms | Client trend check failed |
| 100 RPS | 3,000 / 3,000 | 113.14 ms | 288.62 ms | Passed |
| 200 RPS | 5,928 / 6,000 | 951.08 ms | 1,462.85 ms | 64 HTTP 503 and 8 HTTP 429 |
| 500 RPS | 1,749 / 15,000 | 10,001.27 ms | 10,002.51 ms | Overload; generator drops; drain failed |

These are 30-second diagnostics, not ten-minute qualification results. At 50 RPS,
one brief late client-in-flight spike failed the least-squares trend check. The
requests completed and exact charges drained. At 200 RPS, organization preflight
limits and router Redis allocation exhaustion caused rejection. Redis used only
3.45 MiB of its 128 MiB limit, with no server error replies or evictions. The
application allocation, not Redis server memory, was full.

The 500 RPS stage started 12,135 requests and dropped 2,865 scheduled arrivals.
It returned 9,543 HTTP 503, 42 HTTP 429, 38 HTTP 500, and 763 client errors.
One operation remained provisional after the unchanged 180-second drain limit.
No budget window exceeded its limit. The runner stopped and removed its owned
cluster. It did not start the ten-minute series. Keep all evidence in
`artifacts/qualification/native-22f62c8c-20261006`.

Comparison with the accepted source found omitted measures from the original
integration scope. The clean replay still uses the old serialized Redis
connection acquisition, separate routing completion calls, and synchronous
failure telemetry. Its fixture also retains the old 100/50 preflight and
100-slot ingress limits. Thus the accounting implementation alone does not
reproduce the accepted candidate's request path.

Completion plan:

- [x] Preserve the failed image, complete short-stage evidence, and failure details.
- [x] Audit every retained measure against the extraction record and current main.
- [x] Restore combined routing prerequisites and fenced success acknowledgement.
  Preserve current-main failover, batch, streaming, and Realtime behavior.
- [x] Restore bounded Redis waiters and concurrent socket readiness outside the
  driver bookkeeping lock. Keep pool ceilings and one acquisition deadline.
- [x] Restore bounded request logging and optional failure-telemetry shedding.
  Required economic and audit persistence must remain fail closed.
- [x] Restore the measured qualification profile, negative prompt caching, and
  volatile Redis fixture. Record any profile change; do not relabel earlier runs.
- [ ] Add regression checks for all retained measures and complete affected gates.
- [x] Restore bounded per-process metrics snapshots, GC observation, startup
  heap freeze, and bounded off-process harness parsing/export.
- [x] Restore two native terminal lanes and four native reporting lanes. Keep
  one progress observer and two reserved connections in the eight-connection pool.
  Native production now uses 1,548 of 2,000 declared PostgreSQL connections.
  Native evaluation uses 160 of 160, including its reserved capacity.
  Redis connection limits and pod resource limits do not change.
- [x] Record the complete measure map and focused verification in
  [the retained-measure audit](issue-320-retained-measure-audit.md).
- [x] Confirm the full application lane after the combined simulation snapshot
  and bounded readiness inventory changes: 1,649 passed. Keep the stale-read-count
  failure and its 24-case focused confirmation with the final gate evidence.
- [ ] Freeze a new clean image, run the generator proof and short ladder, then
  run all four ten-minute stages only after the short ladder passes.
- [ ] Save final results and update the handoff. Do not claim 500 RPS from the
  experimental branch or this failed diagnostic.

## Integration rules

- PostgreSQL remains the durable economic source of truth.
- Redis and process memory remain bounded, disposable coordination layers.
- Budget and accounting failures stay fail closed before provider dispatch.
- Every replay keeps stable operation, event, owner, fence, and request identities.
- No slice adds an unmeasured SQL or Redis request-path round trip.
- Migrations remain append-only. Each database slice must pass fresh, last-release,
  and shared-feature upgrade checks.
- A slice is complete only after focused tests, required real-dependency tests, Ruff,
  formatting, and `git diff --check` pass.
- Load qualification starts only after all code slices are integrated on one clean
  image.

## Progress

The implementation boxes below describe the current source. Release checks are
separate in the remaining completion checklist above. Later checkpoint sections
retain the state at their recorded revision; their old pending notes do not
override this current checklist.

- [x] Create a clean managed worktree from the latest remote `main`.
- [x] Create branch `codex/issue-320-main-integration`.
- [x] Slice 1: port concurrency observability, durable telemetry acceptance, and the
  reproducible load harness.
- [x] Preserve current `main` realtime startup while adding the runtime metrics owner.
- [x] Preserve the reorganized documentation navigation and register the concurrency
  measurement guide in it.
- [x] Retain the historical baseline samples because a regression recomputes their
  summary and validates their field allowlist.
- [x] Verify slice 1 with focused hermetic, startup-lifecycle, and real-PostgreSQL
  tests.
- [x] Commit slice 1 as one reviewable integration change.
- [x] Slice 2: port ingress isolation and bounded authentication fallback.
- [x] Slice 3: port dependency capacity ownership and startup arithmetic.
- [x] Slice 4: port capacity schema and durable admission foundations.
- [x] Slice 5: port budget and prompt hot-path reductions.
- [x] Slice 6: apply the spend-recovery schema, fixtures, and implementation.
- [x] Slice 6: preserve realtime recovery and the shared billing transaction owner.
- [x] Slice 6: apply request deadlines and bounded callback and guardrail work.
- [x] Slice 6: verify fresh, last-release, and shared-feature migrations.
- [x] Slice 6: port spend recovery, deadlines, and bounded work.
- [x] Slice 7: apply the managed lifecycle and deployment capacity implementation.
- [x] Slice 7: retain current main's realtime, asset-link, and authorization checks.
- [x] Slice 7: begin realtime cleanup before generic request cancellation.
- [x] Slice 7: verify the shared lifecycle, deployment, and real-dependency gates.
- [x] Slice 7: port readiness, drain, and Kubernetes capacity contracts.
- [x] Slice 8: port accounting protocol v2 and atomic grant admission.
- [x] Slice 8: check current main's realtime accounting against the new authority.
- [x] Slice 8: use the validated, transformed request for cost bounds, including
  multiple outputs. Do not parse the original request body again.
- [x] Slice 8: keep one accounting bootstrap owner and remove new policy from
  large composition modules.
- [x] Slice 8: put charged cache hits through the same admission and terminal owner.
- [x] Slice 8: probe the actual accounting pool and active generation for readiness.
- [x] Slice 8: reject preparation and activation while legacy billing work is pending.
- [x] Slice 8: reject unmigrated realtime, batch, and selector writers when v2 is on.
- [x] Before merge: add typed realtime, batch, and selector adapters to the shared
  v2 budget and recovery authority. Prove mixed-feature recovery before removing
  the temporary checks. Legacy mode must retain every current main feature.
- [x] Slice 9: port pre-issued permits and local lease dispatch.
- [x] Slice 9: add the inactive fenced-permit schema and prove current grant parity.
- [x] Slice 9: batch refill and claim work across subjects with a fixed database-call
  bound. Do not copy the source branch's sequential subject loop.
- [x] Slice 9: put permit persistence in a small typed repository owner.
- [x] Slice 9: add the bounded permit bank and test partial grants, cancellation,
  expiry, shutdown, and the warm-path call bound before bootstrap can select it.
- [x] Slice 9: profile all SQL inside the grant allocator before permit activation.
- [x] Slice 9: verify the retained cursor byte budget with the required gates.
- [x] Slice 9: add inactive immutable issued-receipt storage with both entry and
  byte limits. The local runtime must use this owner before activation.
- [x] Slice 9: prove local-lease funding, unused-suffix return, expiry, and conservative
  owner-loss recovery before local dispatch can run.
- [x] Slice 9: separate the local dispatch deadline from the terminal recovery
  deadline in the inactive schema and issue owner. Keep dispatch within the funded
  budget period and refill TTL. Runtime and transport selection are still pending.
- [x] Slice 9: retain typed cost bounds, the shared cache admission owner, and
  missing-owner rejection when adding local or remote accounting clients.
- [x] Slice 9: retain the legacy reporting default while accounting v2 is disabled.
- [x] Slice 9: keep protocol construction in the small accounting bootstrap owner.
- [x] Slice 9: verify immutable financial queue payloads, byte-bounded collection,
  and retained-byte limits across the required gates.
- [x] Slice 10: port the terminal journal and compact acknowledgement path.
- [x] Slice 11: split accounting transport and projection roles.
- [x] Slice 12: port bounded worker runtimes, economic settlement, and recovery limits.
- [x] Slice 12: complete bounded recovery verification and runtime integration.
  Migration 126 already limits active-to-draining expiry transitions. Migration
  132 adds expiry and drain work indexes and limits inspected keys before close
  eligibility checks. Prove scan limits, retained-history plans, cursor loss,
  concurrent foreground admission, and upgrades. Do not edit an applied migration.
- [x] Slice 13: port terminal/read-model lanes and rollup sharding.
- [x] Slice 14: port settled-receipt and narrow streamed projection fast paths.
- [x] Run fresh and upgrade migration verification through migration 142.
- [x] Port the reproducible kind harness and bounded 500 RPS gateway runner.
  The direct-provider generator proof is capped at 1,000 RPS. Generator counts,
  output limits, mock controls, role metrics, CPU counters, and separate throughput,
  economic correctness, and latency gates have focused tests. Actual qualification
  remains pending.
- [ ] Run the 50, 100, 200, and short 500 RPS ladder on one clean kind image.
- [ ] Run the ten-minute 500 RPS qualification only after the short ladder passes.
- [ ] Complete the requested final 50, 100, 200, and 500 RPS tests on reproducible
  kind. Record each result separately. Do not treat native SQL probes as gateway
  qualification.

## Final qualification request

The user requested the complete 50, 100, 200, and 500 RPS series after integration.
Use one clean commit and image, the pinned kind tools, and the fixed local provider
mock. Do not use Rancher. Keep replicas, resources, pool limits, workload, and
pass/fail limits the same across rates. First run the short ladder to detect unsafe
capacity or accounting failures. Then run the ten-minute measurement at each rate.
Any pause between rates must be recorded and must wait for accounting to drain.
There is no cooling pause inside a measurement window.

Keep raw allowed samples and a source/image/configuration manifest in a new
evidence directory. Report offered and received throughput, successes, status and
error counts, p50/p95/p99, gateway and provider time, queue and in-flight trends,
dependency calls, and accounting reconciliation. A successful HTTP response alone
does not prove a correct charge. Record a failure as a failure. A short run does
not replace the ten-minute qualification. If a run reveals unsafe economic state,
stop the remaining load and report the reason; do not change code within a series.

## Slice 9e: retained cursor byte budget

Implementation and required lane verification are complete.
The inactive bank now limits both subjects and retained cursor bytes. Its default
budget is 8 MiB per lane. Cold work that exceeds the budget is rejected before a
database refill. Rejection keeps live grants, and expiry or retirement removes
the byte charge once. The batch call bound remains unchanged. Audit and pricing
bodies are not retained by a cursor. The budget includes Unicode character space
and fixed bounded object and grant metadata space; it is not an RSS measurement.

Focused checks: 66 passed. Native bank and byte-limit checks: 7 passed. The first
new native test incorrectly expected close to settle an unexpired grant. The
corrected test proves that close keeps the escrow, then expiry permits exact
settlement and unused-capacity release. No production recovery rule changed.
Object-graph tests then found that the first fixed object charge was too small for
the largest Unicode identifiers. The fixed cursor and window charges were
increased, without increasing the 8 MiB bank budget. All six object-graph cases
now pass. The 1,000-subject pruning fixture explicitly uses 16 MiB so that its
unchanged 256-scan assertion tests pruning, not byte rejection. Earlier full
runs were interrupted and are not passing gates. Final logs use the
`issue320-slice9e-*-complete.log` names.

Final verification:

- Focused memory, bank, profile, and small-owner checks: 66 passed.
- Native memory and bank checks: 7 passed.
- Full component and Helm lanes: 4,901 passed.
- Full application lane: 1,645 passed.
- Full PostgreSQL lane: 554 passed, with no required-service skips.
- Full Redis lane: 105 passed, with no required-service skips. One original
  server-clock expiry case failed in the first full run. It passed unchanged when
  isolated and in the second full lane. A read-only clock probe measured a
  107.5 ms host/server progression gap; the original test had a 10 ms host-wait
  margin. This is consistent with that timing failure. Keep the first log, and
  make the test observe the Redis expiry deadline in a separate test-only change.
- Collection: all 7,205 tests belong to one lane each: 4,664 hermetic, 1,645 app,
  554 PostgreSQL, 105 Redis, and 237 Helm.
- Ruff, format checks, and `git diff --check`: passed.

The final Redis result is in
`/private/tmp/issue320-slice9e-redis-confirmed.log`. The clock observation is in
`/private/tmp/issue320-slice9e-redis-clock-probe.log`. No migration, runtime
selection, pool, queue, fallback, or production deadline changes in this step.
The applied 115-migration chain is unchanged. Local dispatch, the remaining
integration slices, and the requested final gateway RPS tests are unfinished.

The next local-dispatch step must not copy a source lifetime error. In
`0ac46791`, `deltallm_accounting_allocate_local_permit_grant` extends the grant's
`expires_at` to the reservation lifetime. `_cursor_is_usable` then uses that same
field to allow new dispatch. The original allocator had capped it at the earlier
budget-window end or refill TTL. Recovery must retain funded money for late
receipts, but that must not extend the period for new provider work. Add separate
deadlines, a terminal-lifetime check before local issue, and native period-boundary
tests before activation. The clean runtime still uses assigned admission and
does not contain this source local-dispatch behavior.

### Redis server-clock test correction

The lease-expiry test now waits until Redis reports its declared deadline, within
a fixed two-second host deadline. Each observation waits at most 100 ms before
checking again. The one-second lease, reconnect, new-owner, and old-owner release
assertions are unchanged. No production script, timeout, or capacity changed.

Focused Redis checks: 5 passed. Full Redis lane: 105 passed, without required
skips. Ruff, formatting, and `git diff --check` passed. Logs are in
`/private/tmp/issue320-redis-server-clock-focused.log` and
`/private/tmp/issue320-redis-server-clock-full.log`. The original failure and
clock measurements remain available. The wait uses the declared server TTL;
it does not retry a failed acquisition or increase the lease.

## Slice 9f: local lease integration order

The inactive foundation is implemented in two new migrations. The first keeps
the short dispatch deadline separate from recovery. It uses the existing bulk
claim owner instead of the source's per-grant terminal loop, and prevents claims
from using a returned suffix. The second gives later issues recovery headroom
without extending stored deadlines on replay. The already-applied first migration
was not edited. All 17 new native cases pass, including assigned parity, period
boundaries, wrong identity, zero-cost capacity, duplicate settlement, and owner
loss. Bootstrap selection and the Python local lease lifecycle remain unfinished.

- [x] Complete full database and migration verification for the inactive lease
  foundation. Focused checks passed: 47 native and 35 verifier/lane cases.
- [x] Add inactive local-lease fields and constraints in an append-only migration.
  Keep assigned and durable-claim behavior unchanged by default.
- [x] Retain the allocator's short dispatch deadline. Store a separate bounded
  receipt-recovery deadline. Reject a new local issue if its terminal lifetime
  does not fit the funded lease.
- [x] Add typed bulk refill, unused-suffix return, and terminal persistence owners.
  Keep a fixed call bound across subjects. Never await one call per subject.
- [x] Bound issued receipts and retiring cursors by both entries and bytes.
  Preserve exact operation, request, owner, generation, grant, and ordinal identity
  after an uncertain transport acknowledgement. The inactive owners are verified;
  runtime selection remains incomplete.
- [ ] Supervise one bounded return worker through the existing lifecycle. Close
  must stop new issues, drain terminal work, and return only the proven unused
  suffix. Process loss must keep uncertain money as provisional, not release it.
- [ ] Prove short TTL and budget-period boundaries, partial funding, duplicate
  receipt replay, wrong owner/fence rejection, return races, owner loss, and mixed
  assigned/local settlement against PostgreSQL. Then add runtime selection through
  the small bootstrap owner and every governed configuration surface.

The terminal journal follows this foundation. Its accepted payload must remain
durable and immutable. The materializer must retain funding while accepted work
is pending, and preserve exactly-once settlement after worker loss. The source
append and materializer SQL must retain the clean branch's bounded key probes;
copying an old JSON join must not restore retained-history scans.

The local runtime must carry a complete typed financial issue proof through the
request handle and terminal owner. A terminal retry after receipt-store removal
must replay durable accepted facts, not create another admission or depend on a
mutable process cache. Remote workers must derive their own clock anchors; they
must not use an API process's monotonic value to permit new dispatch.

Plan cold funding before committing a local issue batch. Keep new, never-issued
funding in bounded staging that shares the cursor budget. Complete local issue
without an intervening await after funding checks pass. A later funding failure
must not turn an undelivered warm receipt into unknown provider work. The same
admission owner must protect staging from the return worker until commit or abort.
Do not await a gate release or another cleanup step after the local issue commit.
The result and every proof must be ready before that commit starts.

Before activation, collection must also respect serialized batch bytes. A valid
large terminal payload can exceed the 1 MiB batch limit when joined with other
valid entries. Split collection at the byte limit, not in a per-subject awaited
loop. Bound retained queue bytes and keep accepted facts immutable. Do not reduce
the request payload contract or let a provider success lose its terminal record.

### Slice 9f verification

All 7,223 collected tests passed. Counts are 4,665 hermetic, 237 Helm,
1,645 application, 571 PostgreSQL, and 105 Redis. No required-service test was
skipped. The full suites ran one at a time. All 117 migrations passed fresh install,
upgrade from `v0.1.42`, and shared-feature upgrade. The verifier removed its
disposable databases. Prisma generation, changed-file Ruff checks, format checks,
and `git diff --check` passed.

Logs are `/private/tmp/issue320-slice9f-postgres-full.log`,
`/private/tmp/issue320-slice9f-components-full.log`,
`/private/tmp/issue320-slice9f-app-full.log`,
`/private/tmp/issue320-slice9f-redis-full.log`,
`/private/tmp/issue320-slice9f-collection.log`, and
`/private/tmp/issue320-slice9f-migrations.log`.
These are foundation checks, not gateway qualification results. The local issuer,
bulk return lifecycle, runtime selection, terminal journal, and later plan slices
remain unfinished. Do not activate local dispatch or mark slice 9 complete.

## Slice 9g: inactive bulk local persistence

The typed bulk repository is implemented but remains inactive. Funding, suffix
return, and terminal persistence each use one database call for up to 256 items.
Client payloads are limited to 1 MiB; SQL accepts at most 2 MiB. All phases share
the existing statement, cancellation, result-set, and ambiguity-recovery owner.
Each phase has at most three attempts and one recovery query per attempt within
the caller deadline. There is no extra retry layer or per-subject awaited loop.

Funding returns the database observation time. The caller anchors the dispatch
and recovery horizons to its monotonic clock before each database call. This
subtracts call latency and avoids extending dispatch through host/database clock
differences. The local anchor is process-owned, not a value to trust from a remote
transport. A future remote adapter must derive its own anchor before its request.

The first append-only migration adds two bulk functions. It also checks funded terminal
lifetimes and immutable terminal timestamps after the claim owner holds operation
locks. Exact suffix returns can replay after grant closure. A bad entry rolls back
the whole bulk effect. No setting, default, bootstrap selection, or pool changed.

Final review found SQL null comparisons in return identity checks. Three native
cases confirmed that a missing fence or generation could mark capacity unused.
A second append-only migration now uses null-safe comparisons and explicit
generation validation. It also rejects a missing suffix ordinal. All four
rejection cases preserve active grant metadata and escrow. The earlier applied
migrations remain unchanged. This path is not selected by the gateway.

- [x] Complete the full affected application and PostgreSQL checks and record the
  final results before marking the bulk persistence step complete.
- [x] Prove native partial funding across two replicas, lost acknowledgements in
  all three phases, exact closed replay, wrong return identity, and timestamp races.
- [x] Verify all 119 migrations on fresh, released-version, and shared-feature paths.
- [x] Complete representative nested-plan checks for windows, grants, operations,
  events, reservations, and grant-window records, including prepared warm calls.

The local issuer, entry and byte limits for issued state, supervised return worker,
runtime selection, terminal journal, and later slices are still required. This step
does not remove request-path claims until that runtime work is complete. The final
50/100/200/500 RPS kind qualification remains unchecked.

### Slice 9g verification

The expanded focused check passed 100 cases. Six native plan cases seed 50,000
retained budget windows and 10,000 each of grants, operations, events,
reservations, and grant-window records. They exercise six prepared calls in each
phase, with explicit and implicit window selection. The checks require indexed
financial-history access with bounded rows. Final redacted plans are in
`/private/tmp/issue320-slice9g-plans-final.log`. Migration checks passed all 118
migrations in `/private/tmp/issue320-slice9g-migrations.log`.

The first full PostgreSQL run passed 585 cases and failed the unchanged realtime
test `test_older_accepted_timestamps_are_not_rebuilt`: its immediate worker claim
returned no record. The same test then passed alone, all 19 cases in its family
passed, and 20 further unchanged repetitions passed with an aggregate-only,
read-only empty-claim probe. The cause was not established. No production code,
assertion, deadline, or retry policy was changed to pass this test.

The second, uninstrumented full PostgreSQL run passed all 586 cases. The full
component and Helm run passed all 4,969 cases. All 1,645 application and 105 Redis
cases passed. Those gates covered the typed bulk owner and migration 118. After
the null-identity guard, all 36 local native and nested-plan cases passed. Migration
119 passed all three upgrade paths in
`/private/tmp/issue320-slice9g-null-guard-migrations.log`. The guard changes only
invalid direct SQL inputs; no Python production code or valid caller changed.
The next full PostgreSQL run passed 589 cases and failed
`test_real_native_statement_deadline_and_connection_recovery`. The caller deadline
fired before the native statement-timeout response arrived. The request still
failed closed, but the test required a native error cause. This test and its owner
were unchanged by this slice. All nine cases in its family then passed alone.
The final full lane passed all 590 cases without instrumentation, changed limits,
or changed assertions. Keep the failure in
`/private/tmp/issue320-slice9g-postgres-null-guard-final.log` and the isolated result
in `/private/tmp/issue320-slice9g-allocation-isolated.log`.
Keep the original failure in
`/private/tmp/issue320-slice9g-postgres-full.log`; isolated and diagnostic evidence
is in `/private/tmp/issue320-slice9g-realtime-isolated.log`,
`/private/tmp/issue320-slice9g-realtime-family.log`, and
`/private/tmp/issue320-slice9g-realtime-diagnostic.log`.

Final collection covers 7,309 tests: 4,732 hermetic, 237 Helm, 1,645 application,
590 PostgreSQL, and 105 Redis. Each test belongs to exactly one lane. The full
component, application, and Redis gates passed before migration 119; the final
PostgreSQL gate checks its invalid-input guards. No required service was skipped.
All 12 changed Python files passed Ruff and format checks. `git diff --check`
passed. Final native and collection logs are
`/private/tmp/issue320-slice9g-postgres-null-guard-confirmed.log` and
`/private/tmp/issue320-slice9g-collection-null-guard-final.log`. The other final
gates are in `/private/tmp/issue320-slice9g-components-final.log`,
`/private/tmp/issue320-slice9g-app-final.log`, and
`/private/tmp/issue320-slice9g-redis-final.log`.

This inactive persistence step is complete. Slice 9 as a whole is not complete,
and these tests are not gateway RPS qualification results.

### Next: local issue and return lifecycle

The local issuer must retain an immutable copy of each reservation. Frozen model
fields do not freeze nested pricing and audit dictionaries. Use serialized bytes
for retained request facts and account for both those bytes and scalar proof
overhead. Prove that later dictionary changes cannot alter an issued receipt.
Do not evict an unacknowledged receipt to admit new work.

An expired short dispatch horizon does not prove that its funded escrow is gone.
Recovery must distinguish a live dispatch proof from a return-only funding proof.
Neither a recovered old horizon nor a remote process's monotonic anchor can
authorize new provider work. Prove this boundary before runtime selection.
The return worker must supervise bounded scans and bulk calls, stop issue before
drain, and retain uncertainty until the database terminal owner confirms it.

## Slice 9h: immutable local receipt retention

The inactive receipt store now keeps canonical reservation bytes and a copied,
scalar funding proof. New request work must fit both its entry limit and its byte
limit. The store has no eviction, expiry deletion, database client, task, or pool.
Only an exact operation and generation terminal acknowledgement can remove a
matching issued proof. Duplicate removal has no second effect. Recovery visits
at most 256 entries and rotates that slice without removing its proofs.

The retained-byte charge includes the serialized request plus a conservative
fixed allowance for the grant, wrapper, key, and map entry. Tests compare the
charge with the complete typed object graph for small, large, nested, and maximum
Unicode cases. A 300-entry check verifies the aggregate charge and exact removal.
This charge is a retained-state limit, not a process RSS limit.

Frozen model fields do not freeze nested pricing or audit dictionaries. The store
validates those dictionaries before serialization. Its first draft check found
that a serializer could turn NaN into null before validation. The final store
rejects NaN and oversized changes before it retains a proof. Caller mutations and
changes to a restored copy cannot change the retained request facts.

Funding recovery can now return a proof after its short dispatch horizon expires,
while its recovery horizon is still live. That proof is for return, not dispatch.
The original dispatch time remains unchanged and its monotonic deadline is in
the past. One native lost-response test waits on the database clock for this
boundary, recovers the proof, returns all four never-issued ordinals, and checks
zero economic drift. No production deadline or allocator SQL changed.

- [x] Complete focused, full application, full native PostgreSQL, component,
  Redis, lane, and style checks before committing this inactive step.
- [x] Record immutable-receipt and return-only recovery test results.
- [x] Add the inactive local issuer in slice 9l.
- [x] Complete entry/byte-bounded retiring cursor verification in slice 9i.
- [x] Add the supervised bulk return foundation in slice 9l.
- [ ] Add the shared terminal path and runtime selection after that.

The main gateway still selects assigned admission. Slice 9 and the final kind
50/100/200/500 RPS qualification remain incomplete. The applied 119-migration
chain is unchanged.

### Slice 9h verification

Focused checks passed all 135 cases. Full lanes passed all 7,346 collected cases:
4,767 hermetic, 237 Helm, 1,645 application, 592 PostgreSQL, and 105 Redis. Each
test belongs to one lane. The full suites ran serially and had no required-service
skips or failures. Ruff, format checks, the small typed-owner ratchet, and
`git diff --check` passed. No schema, migration, default, or runtime selection changed.

The first focused repository run misplaced an existing no-operation-insert
assertion in the new successful-settlement case. The assertion was restored to
its original rejection case without a change. The final focused run and all full
lanes passed. The earlier failure remains in
`/private/tmp/issue320-slice9h-focused-expanded.log`.

Final logs are `/private/tmp/issue320-slice9h-focused-final.log`,
`/private/tmp/issue320-slice9h-components-full.log`,
`/private/tmp/issue320-slice9h-app-full.log`,
`/private/tmp/issue320-slice9h-postgres-full.log`,
`/private/tmp/issue320-slice9h-redis-full.log`, and
`/private/tmp/issue320-slice9h-lanes-final.log`.

This inactive retention step is complete. The local issuer, retiring-cursor owner,
supervised returns, runtime selection, terminal journal, later integration slices,
and final gateway RPS qualification remain required.

## Slice 9i: bounded return-only cursors

The inactive cursor owner now shares one entry and byte limit across active
grants and grants waiting for return. Expiry moves a cursor to return-only state.
It does not reduce the byte charge or claim that money was refunded. A matching
suffix acknowledgement removes the charge exactly once. Wrong fences, ordinals,
counts, and generations cannot overwrite or remove retained capacity.

Cursor ordinals are immutable. A borrowed old cursor cannot change the suffix
after retirement. Fully issued grants need no unused-suffix return; their issued
receipts remain with the separate receipt owner. Expiry, close selection, and
return selection each inspect at most 256 entries. One scan never walks the whole
configured bank. Memory tests cover zero, one, and five windows and maximum
Unicode identifiers. No client, worker, task, pool, or runtime default was added.

Review found a second exact-money boundary. Return recovery used the ambient
decimal precision for allowance multiplied by unused count. Four regressions
failed before correction: exact amounts were rejected and rounded amounts were
accepted. The comparison now uses a fixed 80-digit context. The failure remains
in `/private/tmp/issue320-slice9i-return-money-before.log`.

A full-precision native case then found that the generic query client could
convert PostgreSQL NUMERIC into a float. Funding and recovery now project monetary
scalars as text before transport. The same projection serves local and durable
pre-issued funding. No extra call or SQL-history scan was added. The native
failure remains in `/private/tmp/issue320-slice9i-focused-final.log`. The six
retained-history plan cases pass after the projection change.

Focused lease, cursor, receipt, permit, and ratchet checks passed 226 cases before
the last pre-issued full-precision case was added. All 32 final native money,
plan, and focused ratchet cases passed. Full verification passed all 7,381 tests:
4,798 hermetic, 237 Helm, 1,645 application, 596 PostgreSQL, and 105 Redis.
No required-service test was skipped. All nine changed Python files passed Ruff
and format checks. Collection assigned every test to one lane. `git diff --check`
passed.

The first full PostgreSQL run passed 595 cases and failed one unchanged realtime
cleanup case: `test_transient_release_failure_closes_socket_and_retries_cleanup[True]`.
The socket closed and routing release ran twice, but the runtime stayed unready.
The log shows cleanup cancellation, a transaction rollback failure, and a billing
recovery warning. It does not establish why cleanup exceeded its allowance.
The failure remains in `/private/tmp/issue320-slice9i-postgres-full.log`.
All 12 unchanged tests in the realtime failure family then passed in
`/private/tmp/issue320-slice9i-realtime-failures-diagnostic.log`. No assertion,
deadline, or production realtime rule was changed. The complete unchanged
PostgreSQL lane then passed in `/private/tmp/issue320-slice9i-postgres-confirmed.log`.
The earlier failure remains an intermittent signal, not a proven product fix.
The other full logs are `/private/tmp/issue320-slice9i-components-full.log`,
`/private/tmp/issue320-slice9i-app-full.log`,
`/private/tmp/issue320-slice9i-redis-full.log`, and
`/private/tmp/issue320-slice9i-lanes-final.log`.

- [x] Complete all five full lanes, lane collection, Ruff, formatting, and
  `git diff --check` before committing this inactive step.
- [x] Record exact native unused-suffix return, lost-ACK recovery, and full-money
  results with the complete lane counts.
- [ ] Use both bounded state owners in the local issuer. Keep warm issue at zero
  SQL calls and cold issue at no more than two bulk funding rounds.
- [ ] Add the supervised return worker and the shared typed terminal proof path.

The applied 119-migration chain is unchanged. The gateway still uses assigned
admission. Slice 9, later slices, current-main feature adapters, and all four final
kind RPS qualifications remain incomplete.

## Slice 9j: immutable byte-bounded financial queues

The assigned accounting runtime now freezes each validated financial record as
canonical JSON bytes before enqueue. One snapshot owner also serves local receipt
retention. It validates the raw nested graph before JSON conversion, so invalid
non-finite values cannot become JSON null. A caller cannot change queued pricing,
audit, or spend facts through a nested dictionary after submission.

The shared microbatch owner now collects at both an item limit and a 1 MiB JSON
list limit. It includes list delimiters and commas. It leaves the next item in
the queue when a batch is full. It does not split in an awaited subject loop.
Each accounting queue has a separate 8 MiB retained-byte budget by default,
including selected payloads and a fixed metadata charge. The startup settings,
environment example, YAML example, Helm values and schema, and generated 402-field
reference are synchronized. Accounting remains disabled by default. Existing
batch sizes, acknowledgement budgets, pools, and per-record size limits are unchanged.

Cancellation before collection removes one queued entry and its byte charge in
constant time. Cancellation after selection does not remove the charge or cancel
the persistence owner. Owner cancellation, observer failure, a wrong result count,
and shutdown fail affected waiters and release each charge once. A cancelled
handler now stops its worker instead of being swallowed by the per-batch exception
handler. Worker health keeps the failed task visible.

Before implementation, all 17 byte-collection regressions failed because the
queue had no byte owner. After queue support was added, all 10 financial snapshot
regressions still failed: large batches exceeded the transport limit and mutable
or invalid nested records could enter the queue. The original failures remain in
`/private/tmp/issue320-slice9j-byte-queue-before.log` and
`/private/tmp/issue320-slice9j-immutable-queue-before.log`.

Focused financial, cache, bootstrap, settings, and Helm checks passed 177 cases.
All 50 final byte, observer, snapshot, and size-ratchet checks passed. All four
native large-payload cases passed: direct and assigned admission, with and without
a lost terminal acknowledgement. Eight 264 KiB-class terminal records use three
bounded terminal calls. Lost acknowledgement recovery adds one call, not a second
charge. Each case keeps eight reserved and eight finalized events, exact spend
and audit payloads, and zero reserved/provisional balance after settlement.
The default assigned grant retains its 32 units before expiry; the test advances
only its private native expiry to prove exact release. No runtime TTL changed.

The first new native fixtures read audit from the wrong column, counted reserved
events as terminal events, and expected a default partially unused grant to close
before expiry. Those fixture errors were corrected against the schema. The first
observer test edit also misplaced a fake repository method; it was restored.
No existing assertion or deadline was weakened. The failed logs remain available.
The final focused logs are `/private/tmp/issue320-slice9j-settings-queue-focused.log`,
`/private/tmp/issue320-slice9j-observer-byte-confirmed.log`, and
`/private/tmp/issue320-slice9j-native-queue-final.log`.

- [x] Run all five full lanes, complete collection, changed-file Ruff, formatting,
  generated-reference verification, and `git diff --check` before commit.
- [ ] Extend serialized-byte measurement to the complete local financial proof
  envelope when the local terminal path is integrated. Do not measure only its
  finalization body.
- [ ] Complete bounded cold-funding staging, atomic local issue, supervised
  return, and typed proof/replay integration before local runtime selection.

No migration was added or changed. Slice 9 and the final kind qualification remain
incomplete. These native cases are correctness checks, not gateway RPS results.

The complete suite passed 7,429 tests with no service skips: 4,836 hermetic,
1,645 application, 600 PostgreSQL, 105 Redis, and 243 Helm tests. Every collected
test belongs to one lane. Changed-file Ruff, formatting, generated-reference
verification, and `git diff --check` passed. The full logs are
`/private/tmp/issue320-slice9j-components-full.log`,
`/private/tmp/issue320-slice9j-app-full.log`,
`/private/tmp/issue320-slice9j-postgres-full.log`,
`/private/tmp/issue320-slice9j-redis-full.log`, and
`/private/tmp/issue320-slice9j-lanes-final.log`.

## Slice 9k: bounded staging and atomic local issue

The cursor owner now retains active, staged, and return-only proofs under one
entry and byte budget. Staging is not dispatchable or visible to the return
worker. Abort moves a bounded slice to return-only state at ordinal zero, without
changing warm cursors or claiming a refund. Raw grant validation rejects invalid
clocks before JSON conversion.

One small local issue owner freezes all reservation facts, builds dispatch
results, and validates both stores before mutation. Its batch is bounded at 256
receipts and 1 MiB of complete serialized issue proofs. It rejects wrong fences,
subjects, ordinal gaps, duplicate operations, and skipped warm suffixes. It checks
receipt capacity and repeats deadline checks after preparation. The cursor and
receipt commits contain no await. Each complete issued proof remains retained
until an exact terminal acknowledgement.

The 15 staging regressions failed before staging existed. The initial atomic
owner check failed at collection because that owner did not exist. Logs are
`/private/tmp/issue320-slice9k-staging-before.log` and
`/private/tmp/issue320-slice9k-issue-before.log`. Initial focused checks passed
106 cases. All eight native issue and cursor cases passed in
`/private/tmp/issue320-slice9k-native-partial-grants.log`.

The new native fixture initially assumed three four-unit grants under a ten-unit
budget. It now explicitly proves grants of four, four, and two, with all ten
units reserved. The two cold grants follow ordered fences, not caller order.
The fixture selects the larger grant first to exercise a full prefix and a partial
tail. The original failure logs remain in
`/private/tmp/issue320-slice9k-native-issue.log` and
`/private/tmp/issue320-slice9k-native-issue-confirmed.log`. The new negative-clock
fixture also constructed a receipt before shifting its operation expiry; it was
corrected to shift the complete proof before validation. No existing assertion,
database budget, deadline, migration, or runtime default was changed.

- [x] Share entry and byte limits across active, staged, and return-only funding.
- [x] Keep staged funding hidden from dispatch and return scans until commit or abort.
- [x] Prepare immutable proofs and results, then commit both stores without an await.
- [x] Prove native abort, partial funding, exact settlement, and lost acknowledgement.
- [x] Reproduce retained-history scans in the complete allocator and window locks.
- [x] Add guarded, append-only primary-key lookup corrections. Preserve all
  economic calculations, replay decisions, and global lock order.
- [x] Verify all 123 migrations on fresh, released-version, and shared-feature paths.
- [x] Complete final clock, graph, full-lane, collection, style, and diff checks.
- [ ] Integrate bounded funding coordination with a synchronous gate release.
- [ ] Complete terminal proof transport, replay, and supervised return lifecycle.

Bootstrap does not select these owners. Slice 9 and final kind qualification
remain incomplete. These checks are not HTTP RPS results.

### Slice 9k allocator failure and correction

The first full component, application, and Redis gates passed 5,119, 1,645, and
105 cases. The strict PostgreSQL gate passed 603 cases and failed one unchanged
retained-history plan case. One node read 50,002 window rows instead of at most
45. The failure is retained in
`/private/tmp/issue320-slice9k-postgres-sdk-full.log`. The earlier PostgreSQL run
had four SDK skips and is not a complete gate.

The new probe puts the requested window after the seeded history. It covers
automatic, generic, custom, and alternate join plans. The complete allocator
still has the original bounds for windows, grants, and operations. Separate
checks use each funding owner's actual ordered window-lock SQL. No production
planner setting or test row bound was relaxed.

The probes exposed four flattenable lookup shapes: window membership, explicit
window references, missing-operation anti-joins, and funded-grant promotion.
New migrations 120 through 123 replace only those exact fragments. They reject an
unexpected function body before replacement. Money calculations, validation,
counters, and lock order do not change. Applied migrations 116 through 119
remain unchanged.

The original strengthened whole-function failure is retained in
`/private/tmp/issue320-slice9k-keyset-before.log`. All six window-entrypoint cases
failed before the reference correction in
`/private/tmp/issue320-slice9k-all-window-entrypoints-before.log`. The complete
alternate-plan cases then exposed the operation scan in
`/private/tmp/issue320-slice9k-operation-probe-before.log` and grant promotion in
`/private/tmp/issue320-slice9k-all-probes-native-final.log`. These are diagnostic
tests, not gateway load results. Final verification below includes these cases.

The standalone lock probe first reused a no-bitmap assertion from the ordered
overlap helper. Six cases then failed on bounded indexed bitmap probes, not
history scans. Their indexed row counts were at most one, with at most five scope
loops. The lock probe now shares the complete allocator's original no-sequential-
scan and 45-row bounds. It also bounds bitmap index rows so a large bitmap cannot
hide behind a small heap result. The original overlap helper's no-bitmap assertion
is unchanged. The diagnostic failures remain in
`/private/tmp/issue320-slice9k-retained-plans-stdout.log`. The failed tee-capture
attempt in `/private/tmp/issue320-slice9k-retained-plans-final.log` could not start
the Prisma subprocess. Final plan evidence uses separate output streams.

Final focused verification passed 67 funding, plan, and lease cases in
`/private/tmp/issue320-slice9k-retained-plans-verified.log`. It records all 24
complete allocator cases: three owners, two window modes, and four planner modes.
Each executed window, grant, and operation node read at most one row, against
50,000 retained windows and 10,000 closed grants and operations. All six actual
window-lock queries also pass. Five component cases prove that sequential scans,
large results, filtered history, and large bitmap indexes still fail the bounds.
The final focused component checks passed 93 cases in
`/private/tmp/issue320-slice9k-final-focused-hermetic.log`.

All 123 migrations passed fresh install, upgrade from `v0.1.42`, and shared-feature
upgrade in `/private/tmp/issue320-slice9k-migrations-final.log`.
The complete suite passed all 7,509 tests with no service skips: 4,886 hermetic,
1,645 application, 630 PostgreSQL, 105 Redis, and 243 Helm tests. Every collected
test belongs to one lane. All 12 changed Python files passed Ruff and formatting.
The 402-field generated settings reference and `git diff --check` passed.
Full logs are `/private/tmp/issue320-slice9k-verified-components.log`,
`/private/tmp/issue320-slice9k-verified-app.log`,
`/private/tmp/issue320-slice9k-verified-postgres.log`,
`/private/tmp/issue320-slice9k-verified-redis.log`, and
`/private/tmp/issue320-slice9k-verified-lanes.log`.

This inactive staging and issue step is complete. Funding coordination, terminal
proof transport, replay, supervised returns, later integration slices, and the
final 50/100/200/500 RPS kind qualification remain required.

## Slice 9l: bounded admission, complete replies, and suffix returns

The inactive admission owner has one active caller and at most one waiter. Its
release is synchronous, so cancellation cannot enter between local issue and
release. Tests cover queue overflow, timeout, cancellation before and after wakeup,
waiter order, and release without ownership.

The atomic issue owner now prepares dispatch, replay, and denial results together
in the caller's original order. Only dispatch results retain financial proofs.
The 256-result bound covers both issued and denied entries. The 1 MiB bound covers
the complete reply, including scalar permits and full proofs. Result validation,
serialization, and allocation finish before either state owner is changed.

- [x] Add the bounded admission owner with synchronous release.
- [x] Prepare complete mixed results before atomic issue. Reject duplicate IDs,
  stale generations, invalid decisions, incorrect order, and oversized replies.
- [x] Coordinate cold funding in no more than two bulk rounds. Keep warm issue
  at zero SQL calls, and leave warm prefixes unchanged on a cold-funding failure.
- [x] Preflight retained proof and cursor capacity before funding. Retain known
  staged grants for exact return after failure; never invent a refund after an
  unknown acknowledgement.
- [x] Add one supervised return owner with bounded scans and bulk writes. Share
  the admission owner, validate the whole reply before removal, stop admission
  before drain, and keep proof charges on timeout or invalid acknowledgement.
- [ ] Complete the shared terminal proof/replay path before runtime selection.
- [x] Complete full required gates before this next integration step is committed.

Initial mixed-result checks failed ten cases in
`/private/tmp/issue320-slice9l-mixed-results-before.log`. The new size fixture first
used a model-copy method on a dataclass. It now uses the dataclass replacement
method. No existing assertion or monetary contract was changed.
Final focused admission, result, staging, issue, and ratchet checks passed 83 cases
in `/private/tmp/issue320-slice9l-complete-replies-focused.log`. All 19 native lease
and issue checks passed in `/private/tmp/issue320-slice9l-complete-replies-native.log`.
The new funding owner passed 106 focused checks. Three native cases prove zero
SQL calls for warm issue, two bulk calls for partial cold issue, exact balances,
lost funding or terminal acknowledgement recovery, and terminal replay after
grant closure. The log is `/private/tmp/issue320-slice9l-issuer-native.log`.

The return worker shares the issuer's cursor store and admission owner. It skips
a busy owner instead of adding another waiter. Each scan and bulk return covers
at most 256 grants. It validates all counts and proofs before the first removal.
Known unused suffixes keep their entry and byte charge until exact acknowledgement.
Issued proofs keep their separate terminal owner after return-worker close.
Dependency failure makes this worker unready. Failed startup stops admission and
cancels its task. Close uses the caller's deadline, and a failed drain keeps proofs.

Initial return checks had two startup fixture failures. The worker correctly
rejected initial dependency failure; the new fixtures now inject it after a ready
startup. Startup cleanup was added without changing that readiness rule. The log
is `/private/tmp/issue320-slice9l-returns-focused.log`. All 154 focused local-owner
checks passed in `/private/tmp/issue320-slice9l-all-focused.log`.

Two initial native return checks expected partition capacity to drop on suffix
return. The applied return function records unused ordinals but leaves partition
capacity charged until whole-grant settlement. The new tests now check the exact
three-ordinal return and the retained four-slot charge, then require zero charge
and exact cost after settlement. No applied migration or existing test was changed.
The initial log is `/private/tmp/issue320-slice9l-returns-native.log`.

All 24 native local-owner cases then passed in
`/private/tmp/issue320-slice9l-returns-native-fixed.log`. Those cases include lost
return acknowledgement, exact unused-ordinal facts, retained issued-proof charges,
terminal acceptance after return-worker close, and replay after grant settlement.

The first full gates passed 5,198 component and Helm cases and all 1,645 application
cases. PostgreSQL had 634 passes and one overlap-plan assertion failure. The full
failure log is `/private/tmp/issue320-slice9l-full-postgres.log`. Its assertion text
did not retain the complete plan. Do not infer the exact failing branch from that
truncated text. A single isolated check, four planner profiles, and seven planner
cost profiles then passed with the original assertions and unchanged production
settings. Their complete safe plans show a zero-work explicit-reference branch
and an indexed nine-window overlap result.

The window-plan guard inspected unused branches as if they had run. It now requires
zero rows, filtering, and buffer work for each unused window branch. Executed
branches keep the same row and filter bounds and the ban on sequential and bitmap
heap scans. They must also have no sort. All 26 focused plan-guard checks passed,
including tests that reject hidden work and 50,000-row scans. The log is
`/private/tmp/issue320-slice9l-actual-plan-guard-focused.log`. No SQL, deadline,
capacity, or executed-row limit changed. Final full gates must still pass with
the complete overlap plan retained.

The next full PostgreSQL run passed all plan checks but timed out waiting for a
process-death fixture's child commit marker. Its failure log is
`/private/tmp/issue320-slice9l-final-postgres.log`. All seven isolated spend recovery
cases passed with the same ten-second startup and 250 ms statement deadlines.
The diagnostic log is `/private/tmp/issue320-slice9l-spend-child-diagnostic.log`.
No runtime, test deadline, or process-death assertion was changed.

The final full PostgreSQL run passed all 635 cases without skips. It retained
complete safe overlap plans and bounded child diagnostics in
`/private/tmp/issue320-slice9l-verified-postgres.log`. One child spent 8.235 seconds
in imports, 0.349 seconds connecting, and 0.023 seconds committing its intent.
The host had about 17 GB of swap in use. This is evidence of slow test startup,
not proof of the earlier timeout's exact cause. No unrelated host process was
stopped. No Prisma engine remained after the isolated checks.

Final component and Helm checks passed all 5,209 cases in
`/private/tmp/issue320-slice9l-final-components.log`. The unchanged application
code passed all 1,645 cases in `/private/tmp/issue320-slice9l-full-app.log`.
Final collection covers 7,594 cases, each in one lane, in
`/private/tmp/issue320-slice9l-final-lanes.log`. Redis passed all 105 cases without
skips in `/private/tmp/issue320-slice9l-verified-redis.log`. All 123 unchanged
migrations passed fresh install, upgrade from `v0.1.42`, and shared-feature upgrade
in `/private/tmp/issue320-slice9l-verified-migrations.log`. The verifier removed
only its disposable databases. All 15 changed Python files passed Ruff and format
checks. The generated reference remains current at 402 fields. `git diff --check`
passed. No schema, configuration default, or runtime selection changed in 9l.

Runtime selection, the rest of slice 9, later integration slices, and the four final
kind RPS runs remain incomplete.

## Slice 9m: shared local proof and terminal owner

- [x] Add typed local permit and request-handle contracts. Keep the existing
  assigned contracts unchanged. Check the complete reservation, owner, generation,
  partition, grant fence, and ordinal before provider work.
- [x] Freeze local terminal input before its first await. Reject duplicate
  operations, stale generations, NaN, and entry or byte excess before persistence.
- [x] Add a typed bulk terminal owner with the caller's single deadline. Check
  every acknowledgement before removing any issued proof or retained-byte charge.
- [x] Prove cancellation, blocked transport, wrong acknowledgement, and replay
  after local receipt removal. Do not create another admission on terminal retry.
- [x] Add the shared service path and preserve local proofs through provider retry
  and charged cache admission. Missing proof must fail closed, not use assigned
  or legacy billing.
- [x] Add compact authenticated transport with process-local clock anchors. Keep
  financial proof identity separate from a process's monotonic clock value.
- [x] Prove the shared path against PostgreSQL, then run all required gates before
  runtime selection. Keep later slices and the final four-rate kind series pending.

The repository now contains the typed local handles, bulk terminal owner, shared
byte-queue service, compact wire contracts, and bounded signed HTTP transport.
Full proof checks also reject invalid scalar model copies. Each constructed handle
gets its own request dictionaries. Terminal acknowledgement prepares every removal,
then checks the caller's deadline before it changes retained entries or bytes.

The shared service cannot accept an assigned handle. The assigned service cannot
accept a local handle. Provider retries keep one proof. Charged cache requests use
that same terminal path. One HTTP regression passed the provider response followed
by a charged cache response with no legacy write. Compact replies omit the repeated
reservation and process-local monotonic clock. They retain only the remaining warm
dispatch lifetime. Terminal proof comparison excludes only clock observation fields,
not a durable grant field, ordinal, or reservation fact.

Focused local proof checks passed 132 cases in
`/private/tmp/issue320-slice9m-final-focused.log`. The native proof, return, issue,
and lease group passed 27 cases in
`/private/tmp/issue320-slice9m-native-proof-focused.log`. Six of those checks cover
the shared terminal owner and queued service with normal or lost funding and
terminal acknowledgements. They use different process clock anchors and replay
after local proof removal and grant closure. The focused HTTP provider/cache group
passed five cases in `/private/tmp/issue320-slice9m-cache-app-focused.log`.

Initial new fixtures had three errors: a call to a missing test method, a direct
repository's 2-second statement budget inside the service's unchanged 1-second
acknowledgement deadline, and a shifted grant expiry without its reservation expiry.
Two transport checks also used the wrong fixed error label. Correcting the fixtures
and using the existing error enum fixed these checks. No production deadline,
capacity limit, or SQL assertion changed. The first native failure remains in
`/private/tmp/issue320-slice9m-native-shared.log`; the first compact transport failure
remains in `/private/tmp/issue320-slice9m-transport-focused.log`.

Full gates passed all 7,701 tests: 5,069 hermetic, 243 Helm, 1,646 application,
638 PostgreSQL, and 105 Redis cases. No required-service case was skipped. Each
test belongs to one dependency lane. Logs are
`/private/tmp/issue320-slice9m-full-components.log`,
`/private/tmp/issue320-slice9m-full-app.log`,
`/private/tmp/issue320-slice9m-full-postgres.log`,
`/private/tmp/issue320-slice9m-full-redis.log`, and
`/private/tmp/issue320-slice9m-final-lanes.log`.

All 123 unchanged migrations passed fresh install, upgrade from `v0.1.42`, and
shared-feature upgrade in `/private/tmp/issue320-slice9m-full-migrations.log`.
The verifier removed its disposable databases. All 20 changed Python files passed
Ruff and format checks. The generated configuration reference is current at
402 fields, and `git diff --check` passed. No schema or default changed in 9m.

This is an inactive integration checkpoint, not gateway RPS qualification. Worker
endpoint construction, remote runtime selection, the terminal journal, remaining
integration slices, and all four final kind rates remain unfinished.

### Slice 9m transport destination follow-up

Review after the checkpoint found a missing transport safeguard. The inactive
signed HTTP primitive did not yet use the shared outbound destination policy.
It now checks scheme, port, DNS results, and private-network permission before
connecting. It pins the checked address and retains the original Host and TLS
server name. Metadata targets remain blocked. DNS and body read share the caller's
deadline. No new URL policy or retry owner is added.

Focused accounting, shared webhook destination, and small-owner checks passed
67 cases in `/private/tmp/issue320-slice9m-egress-focused.log` before adding the
explicit plain-HTTP rejection case. Full component and Helm verification then
passed all 5,316 cases in
`/private/tmp/issue320-slice9m-egress-components.log`. This follow-up changes only
the unselected transport; the application, database,
schema, settings, and reporting paths from the checkpoint remain unchanged.

## Next slice 10: inactive durable terminal journal

- [x] Add the journal and pending-payload tables in a new migration. Add matching
  Prisma models. Keep compact accepted identities separate from large documents.
- [x] Use a typed journal acknowledgement. Do not present its journal sequence as
  a canonical accounting event that later processing has not yet created.
- [x] Append one bounded terminal batch with complete immutable payload hashes,
  exact money, generation, owner, grant, fence, and ordinal checks. Use indexed key
  probes. Reject missing fields and conflicting operations or ordinals atomically.
- [x] Keep accepted journal entries charged until canonical processing succeeds.
  Prevent an unused-suffix return or grant close from releasing accepted work.
  Preserve exact retry after grant closure and local proof removal.
- [x] Add bounded, fenced claim and canonical-processing calls. Retain one durable
  outcome through lease loss and retry. A failed record must remain visible and
  charged; it cannot be removed to make a queue look empty.
- [x] Prove normal and lost acknowledgements, changed-payload rejection, duplicate
  concurrent workers, return races, expiry, cancellation, and exact reconciliation
  with PostgreSQL. Check actual nested plans with substantial retained history.
- [ ] Run the required test and migration gates before selecting the journal.
  Do not copy the source's full-history expiry update. Its bounded replacement,
  worker roles, reporting lanes, current-main adapters, and final kind series
  remain tracked in the later integration slices above.

Source review covers the original journal migration and its payload-isolation
update. Their large repository and bootstrap methods, absent Prisma journal
models, nullable-field checks, and history-wide joins must not enter the clean
replay without the typed, bounded boundaries stated here. Slice 10a adds the
inactive acceptance foundation below. The worker and runtime selection remain
unfinished.

### Slice 10a: bounded journal acceptance

- [x] Add immutable compact identities, full document hashes, and a distinct
  journal receipt. Bound the complete encoded batch, not just its metadata.
- [x] Add primary-database bulk acceptance and exact lost-acknowledgement recovery.
  Update each partition counter once per batch and insert documents in bulk.
- [x] Add durable entry and payload-byte limits. Queue-full rejects new work and
  keeps prior documents. Replay can succeed while capacity is full.
- [x] Keep pending work funded through expiry. Block return of an accepted ordinal
  and grant closure with unresolved journal work. Bound the expiry transition.
- [x] Check native acceptance, lost replies, changed facts, concurrent append,
  missing fields, and eight retained-history plans. Worker reconciliation remains
  a later gate; these checks do not prove canonical processing.
- [x] Complete overload, return-race, expiry-limit, full lane, and migration gates.
- [x] Add fenced worker claims, canonical processing, and complete replay after
  closure. Then connect journal acknowledgements to the local terminal owner.

The initial schema check found PostgreSQL's shortened automatic constraint name.
The Prisma map now matches the actual name. One new snapshot test used a sync
fixture that needs an event loop, then assumed an allowance of 1 instead of the
fixture's 1.25. Both fixtures are corrected without changing production limits.
The initial native acceptance and plan runs passed 16 and eight cases.

Bulk commit review then found an ambiguous payload result column. A direct owned
database probe confirmed the SQL error. A new migration qualifies that result;
applied migrations stay unchanged. All 55 focused journal and small-owner checks
passed in `/private/tmp/issue320-slice10a-result-focused.log`. The failed bulk run
remains in `/private/tmp/issue320-slice10a-bulk-focused.log`. Extra overload checks
passed 27 cases before the final expiry-limit test was added.

This is inactive foundation work. Slice 10, worker roles, economic settlement,
reporting lanes, current-main adapters, and the four final kind rates are not
complete. Do not treat these SQL checks as RPS qualification.

Full 10a verification passed all 7,746 tests: 5,086 hermetic, 243 Helm,
1,646 application, 666 PostgreSQL, and 105 Redis cases. No required-service case
was skipped. Collection confirms one lane per test. All 126 migrations passed
fresh install, upgrade from `v0.1.42`, and shared-feature upgrade. Prisma generation,
changed-file Ruff and format checks, and `git diff --check` passed. The generated
configuration reference stays current at 402 fields. The verifier removed only
its owned disposable databases.

Logs are `/private/tmp/issue320-slice10a-full-components.log`,
`/private/tmp/issue320-slice10a-full-app.log`,
`/private/tmp/issue320-slice10a-full-postgres.log`,
`/private/tmp/issue320-slice10a-full-redis.log`,
`/private/tmp/issue320-slice10a-full-migrations.log`, and
`/private/tmp/issue320-slice10a-final-lanes.log`.
The expiry-limit case proves that each limit-one pass changes only one of three
expired grants. Entry and byte overload preserve accepted documents and counters.
The return race has one winner and cannot release an accepted ordinal.

### Slice 10b: fenced canonical journal processing

- [x] Add typed claim handles with generation, worker, lease nonce, and bounded
  positive sequence keys. Revalidate raw fields so copied booleans cannot become
  integer keys through serialization.
- [x] Claim at most 256 entries and 1 MiB of documents. Each pending and expired
  index branch inspects at most the requested entry limit.
- [x] Commit canonical operations, exact reservations, events, grant usage,
  capacity release, and document removal in one transaction. Bulk writes update
  each grant and partition once, not once per subject.
- [x] Recover a lost claim reply by its original nonce. Recover a lost commit
  reply by its original keys. Do not issue another claim or financial write.
- [x] Prove stale-worker rejection, concurrent disjoint claims, expiry, exact
  closed replay, payload corruption rollback, cancellation after commit, payload
  limits, zero and fractional money, and worker crash exhaustion with PostgreSQL.
- [x] Complete actual nested query-plan checks with retained journal, payload,
  grant, window-reference, reservation, event, and operation history.
- [x] Complete full required lanes, migration paths, collection, and style gates.
- [x] Connect the distinct journal receipt to the shared local terminal owner.
  Runtime selection remains off until this path and worker lifecycle are complete.

The first focused run found a PostgreSQL restriction on row-locking queries
inside a set operation. Migration 128 moves the two separately bounded claims
into materialized query blocks. Applied migration 127 remains unchanged.
The corrected focused run passed 45 cases. Expanded financial cases pass,
including capacity retained after five worker crashes and exact provisional
money for zero or fractional charges.

The first plan fixture did not retain enough payload or grant-window rows to
measure those lookups. The expanded fixture retains 10,000 journal, grant,
operation, event, reservation, and window-reference rows. The final fixture also
retains 10,000 charged dead-letter documents. Its owned diagnostic capacity is
20,000 entries; production defaults are unchanged. The alternate-join planner exposed a real grant
update scan of 10,001 rows for one result. Migration 129 restricts that update
and payload deletion to the current claimed keys. Migration 130 adds bounded
key-dependent document probes and transaction-local row-location deletion. It
also rejects duplicate, null, or nonpositive failure keys before any write.
The 500-document fixture exposed a small-table scan: PostgreSQL estimated ten
row locations and chose the four-page table scan. The final 10,000-document
fixture provides substantial payload history without changing planner settings.
All four planner modes, with normal and lost replies, pass the unchanged
bounded-row assertions. Keep the failed logs as diagnostic history.

The latest focused logs are `/private/tmp/issue320-slice10b-focused128.log`,
`/private/tmp/issue320-slice10b-complete-focused.log`, and
`/private/tmp/issue320-slice10b-key-focused.log`.
Final focused verification passed 71 cases in
`/private/tmp/issue320-slice10b-final-focused.log`. Raw SQL rejects malformed
materialization and failure keys. An observed grant-lock wait lets a claim lease
expire during processing; all financial effects roll back and capacity stays
charged. The required lanes and the 130-migration chain passed final verification.
The component and Helm lanes passed 5,347 cases. The first full application lane
passed 1,645 and failed one selected `/v1/messages` stream-close case: it observed
only the answer call instead of the expected classifier and answer calls. Its
stream charge and terminal-delivery assertions passed before that call-count
assertion. The complete stream-accounting module passed all 28 cases unchanged.
Keep `/private/tmp/issue320-slice10b-full-app.log` and
`/private/tmp/issue320-slice10b-stream-confirmation.log`. A full application retry
will record bounded selector cause and latency diagnostics without changing
production execution, deadlines, or assertions. The cause of the first mismatch
is not yet confirmed.
The first full PostgreSQL lane passed 700 cases and failed one Realtime receipt
recovery case: its first claim returned no rows. The unchanged Realtime module
then passed all 19 cases, and a complete PostgreSQL confirmation passed all 701.
The cause of that first mismatch remains unconfirmed. Preserve both runs and the
bounded read-only claim diagnostic; do not treat a passing retry as a cause fix.
The real-Redis lane passed all 105 cases. Fresh install, `v0.1.42`, and shared-feature
upgrade checks passed with all 130 migrations. Collection assigns all 7,799 tests
to exactly one dependency lane, and the 402-field configuration reference is current.

The first selector diagnostic used a fixture from an early pytest setup hook. That
temporary diagnostic caused a setup error and left later test patches active.
The contaminated application retry was interrupted and is invalid evidence. The
diagnostic now uses a normal autouse fixture, with pytest-owned cleanup. Neither
production code nor assertions or deadlines changed. Its focused check and a clean
full application retry must pass before the full-lane checkbox can be completed.
Keep `/private/tmp/issue320-slice10b-full-app-confirmed.log` as the invalid run,
and `/private/tmp/issue320-slice10b-full-app-valid.log` as the new confirmation.
The corrected diagnostic passed all 28 stream-accounting cases. The clean full
application confirmation passed all 1,646 cases. Final required counts are 5,104
hermetic, 243 Helm, 1,646 application, 701 PostgreSQL, and 105 Redis: 7,799 tests,
with no required skips. Changed Python files passed Ruff check and format, and
the diff check passed. The original stream and Realtime mismatch causes remain
unconfirmed; their passing confirmations do not establish a production fix.
No gateway RPS test has run on this clean replay yet. The four requested kind
rates remain pending after integration.

### Slice 10c: shared journal acceptance receipts

- [x] Keep journal acceptance and canonical event receipts as distinct typed
  contracts. Neither receipt can stand in for the other owner's result.
- [x] Use the existing local terminal owner and complete batch validation for
  journal acceptance. Keep every local proof until all acknowledgements are valid.
- [x] Use the same shared request queues for provider calls and paid cache hits.
  No legacy spend writer or second financial authority handles either path.
- [x] Prove lost acceptance replies, lost canonical replies, cancellation after
  acceptance, exact replay after grant closure, and exact balances with PostgreSQL.
- [x] Verify malformed or mismatched receipt kind, identity, outcome, sequence,
  generation, replay flag, and batch length cannot release a local proof.
- [x] Complete required full lanes, collection, migration checks, and style gates.
- [ ] Select this runtime only after supervised processing, unused-grant returns,
  recovery, and transport ownership are complete in the following slices.

The receipt contracts now live in a small leaf module. This keeps the journal and
shared terminal owner free of a circular import. The journal persistence adapter
delegates one bounded append call; it adds no database call, fallback, or writer.
Canonical finalization remains the default receipt contract. A journal owner must
select the journal contract explicitly, and rejects a canonical receipt.

Focused verification passed all 191 cases in
`/private/tmp/issue320-slice10c-focused.log`. Native shared-queue tests prove that
four accepted operations release local queue and proof bytes but retain all four
durable documents and their reserved money. Canonical processing then commits
four outcomes once, releases pending capacity, and permits exact grant closure.
Closed replay returns the original journal keys after large documents are removed.
Cancellation after a committed append retains all local proofs until exact retry.
The provider and paid-cache checks prove both paths share this acceptance owner.
The full component and Helm checks passed 5,365 cases, and the full application
lane passed 1,646. The first PostgreSQL lane passed 704 cases but failed one
Realtime transient-release case, with a teardown error. Its initial WebSocket
admission returned HTTP 503 after the local Prisma query-engine connection was
lost; later cleanup could not reconnect. The journal runtime was not selected.
The unchanged Realtime failure module passed all 12 cases with bounded engine
process diagnostics. This does not establish why the first connection was lost.
Preserve `/private/tmp/issue320-slice10c-full-postgres.log` and
`/private/tmp/issue320-slice10c-realtime-engine-focused.log`. The next full PostgreSQL
run passed all Realtime cases but found four admission planner failures. A focused
repeat confirmed that the grant-counter update could scan 10,000 retained grants.
The reservation-reference join could also lose its dependent key lookup. These
are real history-dependent work defects, not reasons to relax the row limits.

Append-only migration 131 restricts the counter update to the current grant keys
and makes each reservation-reference lookup depend on its inserted operation.
It changes no amount, ownership check, capacity limit, lock order, or driver-call
count. The stronger regression retains 10,000 grant-window references as well as
the existing grant, window, and operation history. All four planner modes keep
the original row limits. The allocator, journal, and shared-terminal focused run
passed all 108 cases in `/private/tmp/issue320-slice10c-key-focused.log`.

The pre-correction real-Redis lane passed all 105 cases, and all three migration
paths passed through migration 130. The migration-131 full run passed 5,373
component and Helm cases and all 1,646 application cases. PostgreSQL passed 703
and failed two: the official-SDK two-turn transcription case received a terminal
event that did not match its assertion, and the intent-phase process-death test
timed out waiting for its child's committed signal. The child diagnostic recorded
9.61 seconds in imports before connection began; the underlying reason for the
slow startup is not established. The SDK terminal type was not recorded, so its
cause is also not established. Neither failure is a passing gate or a fixed issue.

Preserve `/private/tmp/issue320-slice10c-final-components.log`,
`/private/tmp/issue320-slice10c-final-app.log`, and
`/private/tmp/issue320-slice10c-final-postgres.log`. The serial chain stopped at
PostgreSQL. The current-source Redis, migration, collection, and configuration
checks did not run in that first chain. Ruff check and format passed for all 12
changed Python files, and `git diff --check` passed.

Both unchanged compatibility modules passed all 15 cases with bounded SDK and
child-startup diagnostics. The full PostgreSQL confirmation passed all 705 cases
in `/private/tmp/issue320-slice10c-postgres-diagnostic-confirmation.log`. No assertion,
production budget, deadline, or financial policy changed to obtain that result.
The original failure causes remain unconfirmed. The final real-Redis lane passed
105 cases. Fresh install, `v0.1.42`, and shared-feature upgrades passed all 131
migrations. Collection assigns 7,829 tests to exactly one lane: 5,130 hermetic,
243 Helm, 1,646 application, 705 PostgreSQL, and 105 Redis. The configuration
reference remains current at 402 fields. Final logs use the same
`issue320-slice10c-final-` prefix for Redis, migrations, lanes, and configuration.
Runtime selection remains off. The final four-rate kind series remains pending.

### Slice 10d: supervised journal processing

- [x] Add one owned worker task with startup, liveness, bounded backoff, and
  cancellation-safe shutdown through the existing lifecycle helpers.
- [x] Retain at most one immutable claim with 256 keys and a fixed byte charge.
  Reject another tick without a waiter or a second claim.
- [x] Retry the same claim after an uncertain materialization or failure reply.
  Never release durable documents, funding, or capacity on process cancellation.
- [x] Add fixed action/outcome metrics and safe failure health details. Task
  shutdown must not claim that the durable accounting backlog has drained.
- [x] Prove lost claim and commit replies, cancellation after commit, owner loss,
  lease recovery, exact money, and complete document removal with PostgreSQL.
- [x] Complete focused checks, the affected real-dependency gates, collection,
  style checks, and the small typed-owner regression before closing this step.
- [ ] Connect processing, terminal drain, unused-suffix return, and transport
  ownership through the accounting bootstrap and deployment roles in later steps.

The first focused run passed all 112 cases in
`/private/tmp/issue320-slice10d-focused.log`. This includes six new native worker
cases. An empty tick makes no materialization call. A normal tick uses one claim
and one bulk commit. Cancellation keeps the original handle for exact retry;
lost claim ownership keeps all documents and money until lease recovery. The
largest Unicode and integer-key object graph fits the fixed 16 KiB charge.
Configuration and claim copies now use raw-field revalidation without a serializer.
The final source passed all 5,416 component and Helm cases, including the 43 new
component cases. The full PostgreSQL run passed 710 of 711 cases, including all
six new worker cases. One existing process-death case failed while starting its
child. The timed child imported modules for 9.69 seconds before connection began;
the parent then reached its unchanged ten-second wait limit. No accounting SQL
had started. This identifies the failed stage, not the cause of the import delay.
The unchanged recovery module then passed all seven cases, with imports of
4.44 and 4.40 seconds. Do not treat the confirmation as a production fix.

Redis passed all 105 cases. Collection assigned all 7,878 cases to exactly one
lane: 5,173 hermetic, 243 Helm, 1,646 application, 711 PostgreSQL, and 105 Redis.
The 402-field configuration reference, Ruff, formatting, and whitespace checks
passed. The unchanged application graph passed its 1,646 cases in slice 10c;
this step did not rerun that lane. Preserve the `issue320-slice10d-full-` logs,
`issue320-slice10d-spend-confirmation.log`, and `issue320-slice10d-final-` artifacts
in `/private/tmp`. The full slice-10e run later passed all 719 PostgreSQL cases
without changing this worker or the failed compatibility case. No application route,
startup selector, configuration field, schema, or database function changes here.

### Slice 10e: shared local startup and shutdown

- [x] Add one typed runtime owner for the existing local service and return worker.
  Reject mismatched owners and adoption of tasks that are already running.
- [x] Start the return task before admission queues. Require one actual active-
  generation probe before service selection. Bound concurrent readiness probes.
- [x] Stop issue before the first shutdown await. Share one caller and process
  deadline across terminal drain, unused-suffix returns, and task cleanup.
- [x] Keep unfinished tasks visible through the existing shutdown owner. Retain
  issued proof charges and report an incomplete drain after uncertain replies.
- [x] Prove two-replica hard budgets, lost funding, acceptance, return and commit
  replies, cancellation, and the separation of local drain from durable settlement.
- [x] Complete the full affected lanes and style checks on the final source.
- [ ] Connect the owner to bootstrap only after transport, bounded recovery,
  durable health, and deployment capacity checks are complete.

The final focused run passed all 111 cases in
`/private/tmp/issue320-slice10e-focused-final.log`. It includes 22 new component
cases and eight new PostgreSQL cases. The initial component check used a scheduler
yield as a test-order assumption. An explicit queue-close event replaced that
assumption; no production deadline or assertion was relaxed. The first native
check expected a zero capacity sum when no terminal-capacity row existed. The
new case now checks the actual NULL result, not an invented zero. All existing
financial assertions remain unchanged.

Successful local drain leaves accepted documents and reserved money charged until
canonical processing and grant settlement complete. Unreported issued operations
keep their proof and byte charge. Cancelled funding replies keep database funding
reserved even when no cursor received the reply. Normal startup adds one generation
probe. The existing warm admission, terminal batching, and recovery call bounds are
unchanged. No configuration, schema, database function, application route, or
runtime selector changes here.

The full checkpoint passed 7,908 cases: 5,195 hermetic, 243 Helm, 1,646 application,
719 PostgreSQL, and 105 Redis. Final review added three lifecycle cases. Expired
startup now makes no task or database call; a return task that stops during the
generation probe cannot expose a service. Interrupted close is cached so repeated
close calls cannot add callbacks to a resistant task. The final source passed all
114 focused cases, including all eight new native runtime cases, and all 5,441
component and Helm cases. Application, Redis, and database functions did not
change after their full runs. The other PostgreSQL cases do not use this new
runtime. Collection now covers 7,911 cases in one lane each: 5,198 hermetic,
243 Helm, 1,646 application, 719 PostgreSQL, and 105 Redis. Ruff, formatting,
whitespace, and the 402-field configuration reference passed. Logs use
`/private/tmp/issue320-slice10e-full-`, `issue320-slice10e-guards-`, and
`issue320-slice10e-startup-guards-final.log`.

### Slice 12a: bounded settlement scans

The existing journal migration already caps active-to-draining expiry transitions.
The remaining close selection applies eligibility before its result limit. A new
isolated probe with 10,000 blocked grants and a close limit of one settled exactly
one eligible grant, but its actual plan used a sequential grant scan, removed
10,000 rows by filter, and recorded 30,746 shared-buffer hits. Financial effects
remained exact. This is a measured recovery-scan gap, not evidence from a new
gateway RPS run. Preserve `/private/tmp/issue320-slice10e-settlement-scan-probe.log`.

- [x] Measure the actual nested settlement plan with retained blocked history.
- [x] Add an append-only migration with indexed expiry and drain work ranges.
- [x] Apply the inspected-key limit before eligibility checks. Rotate a disposable
  cursor so a blocked prefix cannot hide eligible work behind it.
- [x] Retain complete grant, budget-window, partition, and pending-journal guards.
- [x] Prove scan limits, fair progress, cursor loss, concurrent recovery, foreground
  admission, exact provisional owner-loss balances, and migration paths.

Migration 132 has two separate work limits per call. At most `p_limit` expired
active grants move to draining state, and at most `p_limit` draining keys are
inspected for closure. The second limit applies before pending-journal and billing
eligibility checks. A cursor stores only scan position. It advances past blocked
keys and wraps at the end of the range. Cursor loss restarts inspection; it cannot
release money, remove financial work, or create capacity. A locked cursor makes
another recovery owner skip closure. Grant, window, and partition locks and all
exact-money effects retain their previous owner.

The focused financial run passed 60 cases in
`/private/tmp/issue320-slice12a-focused-finance.log`. Eight new cases cover limits
of 1, 4, and 256, blocked-prefix progress, cursor loss, concurrent recovery and
funding, locked-cursor recovery, and unknown-generation parity. Four actual-plan
cases passed in `/private/tmp/issue320-slice12a-focused-plans.log`. Each retains
10,000 blocked grants and runs six calls with a four-key limit in automatic,
generic, custom, and alternate-join planner modes. Executed grant scans use an
index and stay within the unchanged row and filter limits. These are native
database checks, not gateway RPS evidence. No runtime selector changes in this
slice.

The final source passed all 7,924 cases: 5,199 hermetic, 243 Helm, 1,646
application, 731 PostgreSQL, and 105 Redis. The PostgreSQL run includes the twelve
new functional and actual-plan cases. Fresh install, last-release (`v0.1.42`), and
shared-feature upgrades passed through migration 132. Their verification checks
the cursor primary key and cascading protocol relation, both valid partial work
indexes, bounded selection order, and retained financial guards. Full collection
is exhaustive and non-overlapping. Ruff, formatting, whitespace, generated Prisma,
and the unchanged 402-field configuration reference passed. Logs use
`/private/tmp/issue320-slice12a-full-`, `issue320-slice12a-final-`, and
`issue320-slice12a-migration-paths.log`.

### Slice 12b: bounded durable backlog health

- [x] Add one typed read of maintained partition and terminal-capacity counters.
  Check all 64 possible partition keys. Missing, extra, and misnumbered partitions
  must remain unavailable, not a false zero backlog.
- [x] Add an append-only partial index for the oldest unsettled terminal record.
  Include pending, processing, and failed records; exclude completed history.
- [x] Keep one immutable observation and reject concurrent refresh without a task,
  waiter, extra pool, or financial payload. Preserve one caller deadline.
- [x] Report unknown, stale, failed, aged, capacity-full, and inactive-generation
  states with fixed safe codes. Cancelled or failed reads cannot reset counters.
- [x] Keep task execution, healthy backlog, and sampled durable drain separate.
  A completed journal can leave grant funding and capacity outstanding.
- [x] Complete the affected lanes, collection, style, and all migration paths.
- [ ] Connect this required check to the accounting roles and readiness owner
  during runtime integration. Do not select local or journal service without it.

The final focused run passed 100 cases in
`/private/tmp/issue320-slice12b-focused-final.log`. Ten new native cases
cover empty state without capacity rows, exact acceptance-to-settlement states,
charged dead letters, the oldest record in each unsettled state, a full partition,
and malformed partition coverage. Four actual-plan cases retain 10,000 completed
receipts and 10,000 other generations with partition and capacity rows. Six calls
per planner mode use only indexed keys, at most 64 partition lookups per relation,
and one oldest-work key. No retained history is counted or filtered.

The probe owns one fixed-shape scalar observation with a 4 KiB retained-byte
charge. Four maximum-scalar cases check the full retained object graph. It never
owns balances, issue proofs, or pending documents. It uses no database write.
A refresh uses one
deadline-bounded native read and no recovery query. Query time counts toward
observation age; a slow reply cannot reset freshness. A failed or cancelled refresh
keeps the previous observation but makes health unavailable. Unexpected faults
remain visible to the caller. A sampled empty state is not a cluster cutover
proof: callers must first stop and fence new admission across all replicas.

Migration 133 adds one partial oldest-work index. A new journal receipt now writes
six indexes instead of five. Completed receipts do not enter this new work index.
No configuration field, request-path call, runtime selector, or application route
changes here.

All three migration paths passed through migration 133. The final source passed
5,246 hermetic, 243 Helm, and 745 PostgreSQL cases. The earlier slice-12a run
passed all 1,646 application and 105 Redis cases; those runtime graphs and their
configuration did not change in this inactive health slice. Current collection
is exhaustive and non-overlapping at 7,985 cases. Ruff, formatting, whitespace,
small typed-owner checks, and the unchanged 402-field configuration reference
passed. Logs use `/private/tmp/issue320-slice12b-full-`,
`issue320-slice12b-final-`, and `issue320-slice12b-migration-paths.log`.

The full component run took 556.91 seconds. One unchanged selector structure
check took 197.11 seconds, compared with 4.51 seconds in the prior checkpoint.
A read-only host snapshot reported high load averages. Preserve these timings;
they are not gateway load results or proof of a code bottleneck. The full
PostgreSQL run passed in 681.18 seconds. No assertion or deadline was relaxed.

### Next: runtime and transport integration

The request role will fund local grants, return unused suffixes, and accept
terminal journals. The API replica keeps the existing local issue, cursor, and
receipt owners. Do not move these owners into a load-balanced request worker:
a terminal can reach another worker and leave the first worker's proof retained.
Each signed call includes the complete generation and financial proof. Funding
includes the API owner. PostgreSQL checks its fence and stable identities. A
warm API reservation has zero SQL and zero transport calls. Cold funding and
suffix returns each use one bounded batch. Terminal acceptance uses one bounded
batch and the same journal replay authority. Projection and recovery have a
separate pool. This replaces the source branch's central local-issue transport
arrangement, not its economic protocol.

Rollback keeps the assigned-grant path and disables local dispatch. Stop local
admission first. Drain accepted terminals and return unused suffixes before role
shutdown. Lost owners keep conservative funded charges in PostgreSQL. Never
evict an unsettled proof or treat cached emptiness as a cluster cutover proof.

- [x] Add the native recovery worker and strict three-action repository.
- [x] Verify fixed calls, cancellation, overlap rejection, real journal settlement,
  owner-loss charges, and missing-generation failure: 42 focused tests passed.
  The first new expiry fixture violated dispatch/recovery deadline order; the
  database rejected it. The corrected fixture keeps the constraint unchanged.
- [ ] Connect recovery to the projection role and run the complete affected gates.
- [x] Implement signed, byte-bounded funding, suffix-return, and journal routes.
- [x] Keep the API local issue and receipt owners with a narrow generation probe.
- [x] Verify transport and shared-service checks: 102 focused tests passed.
  The first run found lifecycle test spies on the old private repository field.
  The spies now use the generation-probe field. All assertions and deadlines stay
  unchanged. One new fixture required an asynchronous test context.
- [x] Verify cross-worker funding, terminal replay, exact charges, and local drain
  with real PostgreSQL: 15 affected tests passed.
- [x] Add durable, bounded projection-role presence. A cached empty backlog does
  not prove that the projection tasks are alive in a different process.
  Migration 134 adds 64 fixed health slots per generation and one primary index.
  Each projection owner has a UUID fence and a ten-second lease. Its existing
  recovery task renews the lease only after actual recovery, backlog, and journal
  worker checks. Disabled workers cannot publish ready. No money or event history
  is changed by presence. Four actual planner modes with 10,000 other generations
  use at most 64 primary-key reads. The 14 affected native tests passed.
- [x] Verify migration 134 on fresh, last-release, and shared-feature paths.
  All three paths passed with 134 migrations. The verifier removed its owned
  disposable databases. The migration is now immutable.
- [ ] Connect one bounded admission monitor to the request and API lifecycles.
  The API keeps one cached signed health observation; each request role samples
  the backlog and fixed presence slots. Failed or stale observations block even
  warm local issue before cursor commit, without per-request SQL or HTTP.
  Terminal acceptance and fenced suffix return remain separate from admission.
  The focused monitor run found a fixture that expected an exception from a
  malformed dependency reply; the monitor correctly returns false and degrades.
  Keep that fail-closed assertion. The corrected affected suite passed 247 tests.
- [x] Build typed API-local, request, and projection runtime graphs. Real role
  tests verify zero-call warm issue, exact journal settlement, loss of projection
  presence, terminal acceptance during degraded admission, suffix returns, and
  synchronous process withdrawal.
- [x] Add minimal native role apps with fixed ingress and reserved health slots.
  Each app uses one native pool and the existing shutdown owner. Workers stop
  before their pool closes. Import-isolation checks found eager package exports
  that loaded the full inference stack. Package owners are now explicit; the
  API imports its existing services directly. Inference behavior is unchanged.
  The affected app/lifecycle suite passed 113 tests. Four real-database role and
  app tests passed. The initial new native fixtures used the wrong handle field
  and settlement query name; only those fixtures changed, with unchanged limits.
- [ ] Run all five lanes against the unchanged role-app source checkpoint.
  The first run passed 5,616 component and Helm cases. The application lane
  passed 1,645 cases and failed one malformed MCP request with HTTP 503.
  All 16 rate-limit cases then passed unchanged in isolation. The assertion now
  includes the safe response body for diagnosis. No timeout or assertion was
  relaxed. The final safeguard suite passed 94 cases. A failed monitor remains
  failed after close; an active observation prevents a false drain result.
  The fixed snapshot is now at
  `/Users/mehditantaoui/Documents/Challenges/deltallm/.worktrees/issue320-role-validation-v0aLpy`.
  The completed run passed 5,619 component/Helm, 1,645 application,
  760 PostgreSQL, and 105 Redis cases. It failed one application case and three
  PostgreSQL cases. Two health-plan modes selected a scan of 10,000 completed
  receipts; the partial queue index had many empty pages after prior tests.
  Applied migration 137 keeps this lookup on the required ordered index inside
  one database function. It does not change pool or database planner defaults.
  All four actual nested plan modes now pass on the bloated index.
  The other PostgreSQL failure is a realtime cleanup timeout. Its 12 isolated
  cases pass, but the complete-lane cause is not yet proved.
  The MCP failure occurs before the first repository call. The authentication
  deadline expires before the first lookup check. The first probe reported no
  garbage collection, but that probe replaced the exported callback
  list instead of registering with the collector. Its GC result is not valid.
  A second run with synchronous step timers passed all 1,646 application cases
  in 537.85 seconds. This does not prove a fix. A new full-lane run registers
  with the actual collector. No deadline, retry limit, or success assertion changed.
- [ ] Add minimal role startup, deployment selection, and complete required gates.

- [ ] Add typed, minimal accounting request and projection roles. Keep their
  pools, queues, ingress, and worker allocations separate from inference.
- [ ] Connect signed transport to the shared local issue and terminal owners.
  Keep complete generation, owner, fence, ordinal, and deadline checks.
- [ ] Require actual generation, local-runtime, journal-task, and durable backlog
  readiness before service selection. Keep one lifecycle owner and drain deadline.
- [ ] Add bounded native maintenance before selecting the complete journal path.
  Stop admission before proving durable settlement; do not use sampled emptiness
  as a cluster drain or cutover proof.
- [ ] Complete deployment arithmetic, configuration surfaces, role smoke tests,
  current-main adapters, and reporting work before final kind qualification.

### Native spend and audit projection

The immutable accounting event is the source of truth. The projection role writes
usage facts, day/month rollups, canonical audit, and its checkpoint in one database
transaction. It does not send events through the legacy spend or audit outboxes.
The expanded reporting view is now selected after tenant, owner, exact-cost,
retention, and writer-rollback checks pass. The view retains legacy history and removes duplicate
event IDs. A reporting failure does not repeat a provider call or release a debit.

The worker owns one task and one frozen handle of at most 256 event keys. Each
page has a one-MiB source-byte limit. Payloads stay in PostgreSQL; Python does not
retain a second copy of each wide event. Claims inspect at most 64 checkpoint
slots. Event reads use a partial generation/partition/sequence index and a fixed
page limit. Each commit checks the generation, worker, UUID lease, partition,
prior sequence, exact page keys, and live lease. A stale worker cannot advance
the checkpoint. Replays update rollups only for facts first inserted by the
transaction. Malformed or missing facts make the worker unready, not empty.

Rollup rows include the accounting partition as a shard. Readers sum the shards.
This prevents independent workers from sharing one hot organization rollup row.
The presence lease must check journal processing and read-model processing before
it can publish ready. All tasks stop before their shared projection pool closes.

The migration is expand-only. New fact indexes retain the existing reporting
filters: tenant, account owner, team, key, user, model, provider, time, and tags.
The fact insert has eleven indexes, including its primary and sequence keys.
Each rollup has one primary and three reporting indexes. A finalized source event
adds one partial projection index; reserved events do not enter it. No automatic
deletion is added. Retain source events and recovery proof until all required
checkpoints and financial effects are complete. Retain usage and audit under the
existing tenant retention and export contract. Before a production rollout, run
ANALYZE on the new tables and inspect vacuum, index growth, and projection age.
Archival and purge remain explicit bounded maintenance, not inference work.

- [ ] Add the append-only native fact/view/sharded-rollup schema.
- [ ] Add bounded native page claims and one atomic fenced sink/checkpoint commit.
- [ ] Add a supervised read-model worker and require it for projection presence.
- [ ] Verify replay, stale lease, malformed data, byte caps, exact charges, audit,
  sharded rollups, tenant/owner reporting, and rollback with real PostgreSQL.
- [ ] Verify all migration paths and the complete affected lanes.

Native reporting verification in progress:

- [x] Add strict key-page claims, exact native facts, sharded rollups, canonical
  redacted audit, and an atomic full-fence checkpoint transaction as drafts.
- [x] Connect the read-model worker to projection task and presence ownership.
- [x] Pass 176 focused worker, progress, recovery, presence, role, and structure
  cases. Missing cells and stale observations stay unready.
- [x] Run the draft SQL on real PostgreSQL inside a transaction that rolls back
  all DDL and data. Twenty serial semantic cases pass, including replay, stale
  fences, malformed input, source-byte caps, and prefix checks. This is a
  diagnostic check, not the migrated PostgreSQL lane or qualification evidence.
- [x] Apply and verify migrations 135–137 on all supported migration paths.
  Fresh, last-release (`v0.1.42`), and shared-feature verification all pass.
- [x] Prove two-worker behavior, bounded physical plans, reporting parity,
  tenant/owner isolation, deletion, and rollback before selecting the new view.
  The migrated database run passed 36 cases. Five health-plan/index-loss cases
  also passed. Initial parity fixtures passed UUIDs without text conversion and
  read exact amounts through a float JSON decoder; these fixture boundaries
  now retain typed text. The larger checkpoint fixture exposed a real empty-
  partition lookup problem: the global sequence index filtered 7,503 old rows.
  The lookup now uses the complete indexed partition/sequence key. All four
  actual plan modes pass with 10,000 retained events, facts, and checkpoints.
- [ ] Verify the expanded readers and deletion inventory in the affected suites.

### Native launcher and deployment integration

Ownership and failure policy:

- API and batch own their existing application graph plus one bounded accounting
  HTTP pool. They do not open an unused direct accounting SQL pool. Warm local
  issue retains its zero-database-call contract. Cold funding, terminal acceptance,
  and unused returns use the same signed, deadline-bound transport.
- `accountingRequest` owns a native SQL pool, terminal microbatcher, and bounded
  dependency monitor. Its signed service is internal-only. It has no provider,
  Redis, control-plane Prisma, or gateway credentials. Fixed replicas, rolling
  surge, and retiring generations all count in deployment capacity.
- `accountingWorker` in native mode owns one native SQL pool, journal processor,
  native fact/audit projector, recovery worker, and fenced presence. It does not
  run old spend/audit projection queues or import the inference runtime.
- The full API still owns unrelated legacy control-plane audit/spend workers if
  their outboxes are configured. Those pools are counted. The minimal native
  projector is not their owner. Keep this compatibility responsibility explicit
  until the other feature adapters are migrated.
- Missing, stale, or failed observations close admission. Presence also requires
  native read-model processing to be ready. A reporting failure does not repeat
  provider work or release a debit.
- API shutdown stops local issue, drains terminals, returns unused suffixes,
  stops its monitor, then closes its transport. The parent owns the whole graph
  before startup awaits, including failed-start cleanup. It never closes only
  the exposed service and leaves the return worker running.

Configuration and rollout:

- `deployment_capacity_role` is the single startup-only selector, rendered even
  without extended capacity. The native execution mode also requires a restart.
- RPC origins are credential-free and port-allowlisted. They reuse the existing
  bounded DNS/rebinding policy. Private CIDRs and local unencrypted HTTP require
  explicit permission. Signing keys come from a pre-existing Kubernetes Secret,
  not a plaintext ConfigMap or default key.
- The native overlay uses kind's service CIDR as an example. Supply your actual
  CIDR and PostgreSQL/monitoring network rules. Production native roles require
  network protection. The bare overlay grants no general outbound access.
- The request role has fixed replicas, fully counted. API saturation autoscaling
  remains unchanged. Both accounting roles reuse one deployment template, image
  command, probes, non-root security policy, resources, and drain contract.
- Migrations 135–137 are applied and immutable. Fresh, last-release (`v0.1.42`),
  and shared-feature upgrades pass. Writer rollback retains the combined reporting
  view. An old image that cannot read native facts needs the documented backfill
  and coordinator procedure; it is not an immediate safe image rollback.

Checks and remaining work:

- [x] Select minimal native roles before importing the full application.
- [x] Remove the eager dynamic configuration import from the startup package.
  The actual launcher graph imports no inference bootstrap or provider module.
- [x] Own API local processing and its RPC pool through one managed closer.
- [x] Match SQL allocation and readiness to the pools actually opened.
- [x] Add request Service, role config, Secret reference, network protection,
  disruption budgets, and exact four-role connection/descriptor arithmetic.
- [x] Pass 87 focused launcher, lifecycle, readiness, and structure cases.
- [x] Pass 79 existing Helm cases and 12 new native deployment cases.
- [x] Pass 18 real-PostgreSQL launcher/reporting/retention/physical-plan cases.
  The first launcher test shared RPC shutdown context with its API. It now binds
  separate process owners. No production deadline or success assertion changed.
- [ ] Finish complete application, PostgreSQL, Redis, Helm, and configuration gates.
- [ ] Finish shared Realtime, batch, and selector billing/recovery adapters.
- [ ] Finish narrow settled-receipt paths, image smoke, and kind qualification.

The initial native production fixture declared 1,524 SQL connections against a
1,000 limit: default API HPA maxima still count API-owned compatibility workers.
Capacity validation correctly rejected it. The test pins one API replica to
inspect the native contract. Production maxima and safety checks were not relaxed.

### Slice 14: exact compact receipt settlement

The experimental fast path skipped per-window reservation writes. That cannot
be replayed unchanged: this branch's grant closer still reads those reservations
and would lose charges. Change the writer and closer together, in append-only
migration 138.

Use the existing terminal journal as the durable compact receipt owner, not a
new accounting ledger. A fully completed receipt with no unresolved attempts
records its exact committed/provisional/released split and a checked
`receipt_only` marker in the same transaction as its operation, event, grant
counter, and payload deletion. Only these receipts skip duplicate per-window
reservations. Uncertain and retry-ambiguous outcomes keep reservation-backed
settlement and operator recovery.

Grant closing adds compact receipt totals once per grant to the retained
reservation totals. The existing grant/ordinal unique key bounds a grant to
1,024 receipts; sorted grant/window/partition locks, limited candidate scans,
and conservative unknown-owner debit remain unchanged. Reject a missing or
inconsistent economic basis instead of closing a grant with a zero charge.
Keep only narrow scalar proof metadata in the materializer's temporary array;
stream the validated stored documents into their operation/event destinations.
No new request-path round trip, table, queue, or index is introduced.

The four added journal columns add one bounded row update, not four indexes.
Existing journal retention must keep these receipts until grant settlement and
archival verification are complete. Keep the existing vacuum/analyze schedule
for the journal and payload tables. The metadata-only schema expansion leaves
old rows on reservation-backed settlement. Rolling old Python readers continue
to call the same database function signature. Do not roll back the SQL closer
after receipt-only rows exist; disable new admission, finish settlement, and
retain the expanded functions on writer rollback.

- [x] Add compact receipt constraints and exact closer parity.
- [x] Stream wide payloads without retaining duplicate JSON documents.
- [ ] Prove mixed outcomes, every scope, lost replies, replay, concurrent workers,
  owner loss, corrupted basis, and retained-history plans in real PostgreSQL.
- [ ] Verify fresh, last-release, and shared-feature migrations and generated client.
- [ ] Complete all regression lanes and the fixed-image kind qualifications.

Migrations 138 and 139 are applied and immutable. The first physical-plan test
found a real failure: an alternate planner read 10,004 payload rows. Migration
139 uses a parameterized, indexed document lookup for each claimed receipt.
All 53 focused PostgreSQL tests then passed. They include mixed completed and
uncertain outcomes, lost replies, four planner settings, corrupt settlement
bases, and native routing-cost reports. Keep the initial failure log.

The full application test found eight old reporting fixture assertions. The
fixtures now use the combined read view. Negative budget checks still reject
reads from both old history and the combined view. The full component test
found one old assertion that expected inline health SQL. The assertion now
checks the bounded SQL function and its limits. The focused reporting, worker,
Helm, and billing check passed all 161 tests. Complete lane confirmation remains
required. The native worker saturation signal now includes terminal work and
reporting work. An unavailable observation retains its last known value and
timestamp. It does not publish zero.

## Shared feature adapter sequence

Keep PostgreSQL as the money authority. Reuse the existing local issue proofs,
reservation queue, terminal queue, and grant recovery. Do not add a Realtime or
batch accounting pool. Do not use HTTP request objects in these domain owners.

- [x] Move the existing dispatch decision and frozen price fields to typed billing
  modules. HTTP and cache keep explicit import facades until their callers move.
- [x] Extract exact charge and uncertain-result preparation. Feature owners must
  freeze terminal bytes and timestamps before the first persistence attempt.
- [x] Add tests for shared policy parity and immutable replay inputs.
- [x] Add the Realtime domain adapter, bounded turn proofs, exact replay, and
  conservative disconnect recovery. Qualify all cost ceilings before dispatch.
- [x] Connect batch claims, grouped provider work, and completion recovery to the
  same native accounting service. Preserve item leases and owner epochs.
- [x] Connect selector costs to that service with stable component identities.
- [x] Prove mixed HTTP, cache, Realtime, batch, and selector recovery before
  removing temporary startup checks.

The initial shared Realtime check passed 122 tests after a test fixture correction.
The local fake returned completed acknowledgements for uncertain receipts. The
fixture now returns the actual submitted outcome. The production reply check
was not changed. Four new PostgreSQL cases passed through the signed transport
and native role graph. They cover response tokens, transcription tokens,
transcription duration, every budget scope, duplicate receipts, disconnect
debits, native facts, and owner denial. The Realtime startup check now selects
the shared adapter. Complete WebSocket/SDK and regression verification remains
required. The complete focused Realtime check then passed 328 tests. It includes
late usage, duplicate close, lost replies, and turn lifetime limits. The batch
startup check remains until its shared adapter passes recovery tests.

The selector focused check passed 427 tests. Nine PostgreSQL tests passed for
selector, routing costs, and Realtime. The selector cases prove exact charges
across all five budget scopes, stable component identities, uncertain and unsent
outcomes, and rejection of a foreign parent link. Native reports count the
selector as a component, not as a second answer. No charge is copied into the
old spend table. The first selector run passed 394 tests and found a reporting
function above the size limit. The SQL now has one small query owner. The size
limit was not changed.

The full migration-139 PostgreSQL lane passed 806 tests and failed four native
reporting plan tests. Retained operation history changed the planner's cost
estimate. Migration 140 adds projection-local index settings and a required
operation-key guard. All 34 focused database tests then passed, including all
four planner settings, index loss, compact/direct receipt mixtures, and every
budget scope. No applied migration or query-plan limit was changed. Complete
lane confirmation remains required. Fresh install, last-release upgrade, and
shared-feature upgrade all passed with migration 140.

### Batch adapter design and checks

Use the existing batch item and completion outbox as execution recovery owners.
Use the shared accounting service as the only money owner. An item checkpoint
is a provider replay fence. It is not another money ledger or worker queue.

Before provider dispatch, freeze customer and provider prices and reserve a
conservative allowance. Save the bounded proof under the live item claim and
claim epoch. Do not hold a SQL transaction during a provider call. A new claim
must not repeat a provider call after it finds a dispatch proof from an old
claim. It must recover the stored result or keep an uncertain debit.

Save completed output and immutable terminal bytes in the existing completion
transaction. The outbox worker submits those bytes through the shared terminal
queue. It then marks delivery complete under the live outbox lease and attempt
count. A lost reply can repeat delivery, but cannot repeat a charge. Never fall
back to old spend writes for a stored native completion.

Keep proofs at or below 24 KiB and checkpoints at or below 64 KiB. Keep the
existing group size, worker concurrency, retries, and lease limits. Group work
must fund every item before dispatch. Checkpoint writes use one bounded SQL
batch. Add no accounting pool, feature task, grant bank, or append-table index.
Use a nullable item field in the next append-only migration. Old rows retain
their old execution path. Drain native proofs before disabling native billing.

- [x] Extract one exact token quote policy. Keep the old float result only at
  the legacy display boundary. Prove price selection and metadata parity.
- [x] Add the typed durable checkpoint and fenced repository. Prove stale
  claim denial, tenant scope, payload bounds, fresh install, and upgrades.
- [x] Connect single and grouped chat and embedding calls. Freeze prices before
  dispatch and preserve the existing provider retry boundary.
- [x] Connect completion delivery to shared accounting. Prove lost replies,
  owner restart, duplicate delivery, and stale outbox attempt denial.
- [x] Verify the supported managed-internal batch mode, cancellation, and mixed
  features. Main does not implement a provider-managed executor. Do not add a
  second executor to satisfy an obsolete source-branch checklist item.
- [x] Remove the batch startup check only after these proofs pass.

Evidence logs:

- `/private/tmp/issue320-native-realtime-complete.log`: 328 passed.
- `/private/tmp/issue320-native-selector-focused.log`: 394 passed, one failed.
- `/private/tmp/issue320-native-selector-focused-2.log`: 427 passed.
- `/private/tmp/issue320-native-selector-postgres.log`: nine passed.
- `/private/tmp/issue320-migration140-paths.log`: all three paths passed.
- `/private/tmp/issue320-native-features-app.log`: 1,646 application tests passed.
- `/private/tmp/issue320-exact-token-policy-focused-2.log`: 139 passed. The shared
  policy keeps decimal rates and the 18-place rounding rule. The old quote API
  delegates to that policy. Invalid configured prices are unpriced, not free.

## Slice 9a: inactive permit foundation

This step copies the fenced-permit migration from `0ac46791` without changes. It
adds the matching Prisma fields and migration checks. The runtime does not select
permit allocation yet. Assigned grants, HTTP and cache admission, terminal writes,
reporting, and current main's legacy features keep their existing owners.

Seven new real-PostgreSQL cases prove assigned-grant parity, unique fenced claims,
duplicate settlement, owner and ordinal rejection, two-replica hard-budget limits,
and release of unused grants. The source branch's sequential database call per
subject is not copied. The next step must give refill and claim batches a fixed
database-call bound and put permit persistence in a small typed repository.

Verification:

- Focused permit and native accounting checks: 37 passed.
- Full real-PostgreSQL lane: 522 passed, with no required-service skips.
- Fresh install, upgrade from `v0.1.42`, and shared-feature migration checks: all
  passed with 109 migrations. The verifier removed its disposable databases.
- Migration-verifier and dependency-lane regressions: 34 passed.
- Full collection: 7,076 tests in one lane each: 4,567 hermetic, 1,645 app, 522
  PostgreSQL, 105 Redis, and 237 Helm.
- Prisma client generation, Ruff, format checks, and `git diff --check`: passed.

Logs are in `/private/tmp/issue320-slice9a-postgres.log`,
`/private/tmp/issue320-slice9a-postgres-full.log`,
`/private/tmp/issue320-slice9a-migrations.log`, and
`/private/tmp/issue320-slice9a-collection.log`.

Slice 9 is not complete. This result is not gateway load qualification.

## Slice 9b: permit batch persistence

The clean replay adds a small typed permit repository. The assigned and permit
repositories use one deadline, metric, error, and cancellation owner. The new
repository is not selected by bootstrap yet. No pool, queue, setting, admission
fallback, or reporting default changes.

One refill call covers up to 256 subjects. One claim call covers up to 256
operations across grants. The tests verify this bound for batches of 1, 8, 32,
and 256 items. Stable window and grant lock order protects concurrent batches.
Recovery verifies the complete request snapshot and every fence and owner field.
Exact exhausted or closed claims replay without a provider dispatch token.

The new window-lock plan check uses 25,000 expired same-subject windows and
25,000 live other-subject windows. It uses the scope/time index and returns one
window. This check does not prove the plans for all SQL inside the allocator.

An extra regression found that the copied source function marked a zero-cost
grant as draining after its first ordinal. The test failed before the fix. A new
append-only migration keeps it active until its operation limit is reached. The
applied source migration remains unchanged.

Verification:

- Final focused permit and accounting checks: 123 passed.
- Full component and Helm lanes: 4,848 passed.
- Full application lane: 1,645 passed.
- Full real-PostgreSQL lane: 535 passed, with no required-service skips.
- Full real-Redis lane: 105 passed, with no required-service skips. The first run
  omitted the dedicated memory-service variables and skipped one case; the final
  configured run has no skips.
- All five lanes cover 7,133 tests: 4,611 hermetic, 1,645 app, 535 PostgreSQL,
  105 Redis, and 237 Helm. Collection is exhaustive and does not overlap.
- Fresh install, upgrade from `v0.1.42`, and shared-feature checks: passed with
  111 migrations. The verifier removed its disposable databases.
- Prisma generation, Ruff, formatting, the small-owner ratchet, and
  `git diff --check`: passed.

Final logs are in `/private/tmp/issue320-slice9b-focused-final.log`,
`/private/tmp/issue320-slice9b-components-final.log`,
`/private/tmp/issue320-slice9b-app-full.log`,
`/private/tmp/issue320-slice9b-postgres-full.log`,
`/private/tmp/issue320-slice9b-redis-final.log`,
`/private/tmp/issue320-slice9b-migrations-final.log`, and
`/private/tmp/issue320-slice9b-collection-final.log`.

The permit bank, local dispatch, and remaining integration slices are unfinished.
This verification is not a new 50/100/200/500 RPS gateway result.

## Slice 9c: inactive bounded permit bank

The bank batches subjects and grants through the typed permit repository. A warm
batch uses one claim call. A cold batch uses at most two refill rounds and one
claim call, including partial grants. The call bound does not grow with subject
count. Remaining unfunded work receives a capacity rejection, not another
admission path. The existing per-call recovery limits still apply.

Subject capacity is fixed. Expiry cleanup checks at most 256 subjects, not the
whole bank. An uncertain claim retires its touched cursors; it cannot reuse an
ordinal. Close rejects new work and leaves durable claims for recovery. The tests
cover shared hard budgets, partial grants, expiry, cancellation, and crash recovery.
Subject identity also retains the allowance text required by the database hash.
Metrics use fixed labels, and representations hide grantees that contain fences.

The native comparison harness now supports direct, assigned, and pre-issued
admission through the same production pool and terminal owner. It records exact
database calls and source hashes. The initial 50 RPS, ten-second development probe
completed 500 operations in each mode with no drops or economic drift. It is a
dirty-tree development result, not a gateway qualification or evidence of 500 RPS.
Fewer refills did not remove each request's claim and terminal acknowledgement.

Verification:

- Final bank, privacy, profile, small-owner, and lane checks: 61 passed.
- Focused native bank and accounting checks: 134 passed.
- Full component and Helm lanes, run without overlapping lanes: 4,874 passed.
- Full application lane: 1,645 passed.
- Full real-PostgreSQL lane: 540 passed, with no required-service skips.
- Full real-Redis lane: 105 passed, with no required-service skips.
- Collection assigns all 7,164 tests to one lane each: 4,637 hermetic, 1,645 app,
  540 PostgreSQL, 105 Redis, and 237 Helm.
- Ruff, formatting, the typed-owner ratchet, and `git diff --check`: passed.
- No migration or Prisma schema changed in this step. The 111-migration chain
  retains slice 9b's verified fresh, last-release, and shared-feature paths.

Two overlapping component runs each failed one existing lifecycle timing case:
a cold child-process shutdown and an HTTP rejection during withdrawal. Both passed
separately, and the full serial lane passed with their original deadlines and
unchanged source. No test was removed, skipped, retried inside its assertion, or
given a larger deadline. Keep these failures as a test-isolation signal.

Final logs are in `/private/tmp/issue320-slice9c-bank-final.log`,
`/private/tmp/issue320-slice9c-focused-rechecked.log`,
`/private/tmp/issue320-slice9c-components-serial.log`,
`/private/tmp/issue320-slice9c-app-full.log`,
`/private/tmp/issue320-slice9c-postgres-full.log`,
`/private/tmp/issue320-slice9c-redis-final.log`, and
`/private/tmp/issue320-slice9c-lanes-final.log`.

Bootstrap still cannot select the bank. Full allocator plan checks, retained-state
byte limits, local dispatch, transport, journal, reporting, and current-main feature
adapters remain unfinished. Slice 9 and final load qualification remain unchecked.

### Clean slice 9c database comparison

The repeat used clean commit `76bcb8aa`, Python 3.11, PostgreSQL 16, two API-like
processes, the same two-connection accounting pool per process, an 8-item batch,
2 ms dwell, 32-operation grants, and 50 offered RPS for ten seconds. Each mode
completed 500 of 500 operations with no generator drops or errors. Each ended with
300 exact units committed, zero reserved, and zero provisional.

| Mode | Request-path database calls | Caller p95 | Caller p99 |
| --- | ---: | ---: | ---: |
| Direct | 998 | 27.86 ms | 33.43 ms |
| Assigned grants | 999 | 27.42 ms | 46.07 ms |
| Pre-issued permits | 1,013 | 28.52 ms | 42.22 ms |

The permit mode used 16 refills, 499 claim calls, and 498 terminal calls. This
confirms refill amortization, not a throughput improvement. The probe excludes
HTTP, Redis, provider latency, and projection. It is not a 500 RPS result.

Raw samples, summaries, source hashes, and the clean-worktree marker are in
`/private/tmp/issue320-slice9c-native-clean-76bcb8aa/`. The initial dirty-tree probe
remains separate. Neither output directory was overwritten.

## Slice 9d: bounded allocator SQL plans

The first failing regression showed that a nine-row window result still read
25,000 rows to sort tied window times. A new migration removes the unused ID tie
sort and replaces broad generation scans at every grant window stage with indexed
scope lookups. A partial renewal index and scalar active-window probes keep
expired policy checks independent of retained history.

Actual nested plans then found full retained-operation and grant scans in JSON
batch joins. Three further append-only migrations use bounded key arrays for
ordered locks and primary-key probes for identity, new-work, and replay checks.
They preserve atomic settlement, immutable identities, stable lock order, zero-cost
grants, and the existing request deadline. No pool, call, fallback, or runtime flag
is added. Already applied migrations remain unchanged.

The native plan recorder loads `auto_explain` on one owned diagnostic connection.
It changes no server setting or production pool. Its reports omit SQL text,
conditions, outputs, and parameter values. Count and byte limits bound its memory;
setup failure and cancellation close the connection.

Four cases each seed 50,000 windows, 10,000 closed grants, and 10,000 closed
operations. They capture every nested statement for six cold/warm calls, including
the prepared-statement threshold. Assigned and permit modes both pass with explicit
and implicit windows. The four reports contain 234, 240, 236, and 242 statement
plans. No retained-history scan runs; each observed history-table scan returns at
most one row in these normal-admission cases. The invalid-overlap lookup is bounded
at nine rows. This is plan evidence, not a new gateway load result.

Verification:

- Native bounds and nested-plan regressions: 12 passed. The original window-sort
  and retained-key scan regressions failed before their corrections.
- Final recorder, profile, ownership, and lane checks: 42 passed.
- Migration-verifier and lane regressions: 34 passed.
- Full component and Helm lanes: 4,880 passed.
- Full application lane: 1,645 passed.
- Full real-PostgreSQL lane: 552 passed, with no required-service skips.
- Full real-Redis lane: 105 passed, with no required-service skips.
- Fresh install, upgrade from `v0.1.42`, and shared-feature checks: passed with
  115 migrations. The verifier removed its disposable databases.
- Collection assigns all 7,182 tests to one lane each: 4,643 hermetic, 1,645 app,
  552 PostgreSQL, 105 Redis, and 237 Helm.
- Prisma generation, Ruff, formatting, and `git diff --check`: passed.

Logs are in `/private/tmp/issue320-slice9d-before.log`,
`/private/tmp/issue320-slice9d-nested-failure.log`,
`/private/tmp/issue320-slice9d-nested-rechecked.log`,
`/private/tmp/issue320-slice9d-plans-final.log`,
`/private/tmp/issue320-slice9d-component-final.log`,
`/private/tmp/issue320-slice9d-components-full.log`,
`/private/tmp/issue320-slice9d-app-full.log`,
`/private/tmp/issue320-slice9d-postgres-full.log`,
`/private/tmp/issue320-slice9d-redis-final.log`,
`/private/tmp/issue320-slice9d-migrations.log`, and
`/private/tmp/issue320-slice9d-lanes-final.log`.

The permit bank is still inactive. Next: byte limits, local-lease schema and recovery,
then the supervised local-dispatch owner. Keep the remaining slices and final kind
qualification unchecked until their own gates pass.

### Clean slice 9d database comparison

The repeat used clean commit `9d65f8af` with all 115 migrations. It kept the same
50 RPS, ten-second, two-process settings as the slice 9c comparison. All three
modes completed 500 of 500 operations with no errors or generator drops. Each
again ended with 300 exact units committed and no reserved or provisional balance.

| Mode | Request-path database calls | Caller p95 | Caller p99 |
| --- | ---: | ---: | ---: |
| Direct | 1,000 | 26.83 ms | 39.99 ms |
| Assigned grants | 998 | 28.34 ms | 39.08 ms |
| Pre-issued permits | 1,014 | 31.26 ms | 50.12 ms |

The permit mode still used 16 refills, 500 claim calls, and 498 terminal calls.
The query corrections add no request-path database call. This small, empty-history
probe does not establish a latency gain. The native seeded-plan cases establish
the removal of retained-history scans. Local dispatch and the terminal journal
remain necessary to remove the per-request claim and compact terminal work.

Raw samples, summaries, source hashes, and the clean-worktree marker are in
`/private/tmp/issue320-slice9d-native-clean-9d65f8af/`. The log is
`/private/tmp/issue320-slice9d-native-clean-9d65f8af.log`. This is an isolated
accounting probe, not 50/100/200/500 RPS kind gateway qualification.

## Slice 1 source decisions

The slice replays the behavior from `30f4b1e7`, `2efcf685`, and `e82f24c5`. It does
not replay `1527785d`, because that commit only added CI triggers for the old feature
branch names. Current main's normal CI gates will cover the integration branch.

The original conflict in `src/main.py` was resolved by starting both current main's
realtime runtime and the issue 320 bounded metrics runtime under the same ordered
lifecycle. The documentation conflict was resolved by retaining current main's new
navigation and adding the concurrency guide to its operations reference.

## Slice 1 verification

- `ruff check`: 23 touched Python files passed.
- `ruff format --check`: 23 touched Python files passed.
- Focused observability and harness suite: 71 tests passed; one evidence test initially
  failed because its raw historical samples had been omitted.
- Restored evidence regression: 1 test passed.
- Current-main startup lifecycle: 2 tests passed.
- Real PostgreSQL telemetry acceptance and spend recovery: 4 tests passed against a
  fresh PostgreSQL 16 database with all 99 main migrations.

The only reported warnings are the existing Prisma/Pydantic Python 3.14 compatibility
warning and pytest-asyncio deprecation warnings.

## Slice 4 source decisions

The durable admission behavior comes from `e39512db`, `4ca6abdd`, `04282772`, and
`154aeb81`, with the batch race/replay regressions from `073e2d3f`, `f976bcb4`, and
`bd291a51`. The inactive capacity-partition schema comes from `bbc2c597`.

The design contracts from `6c532178` were retained and registered in current main's
documentation structure. The large historical PR 4 raw benchmark archive from
`61a4081c` was not copied because it is neither a release qualification result nor a
regression input. The design records explicitly document that decision. The migration
remains inactive: it prepares bounded partition tables but does not enable a second
admission authority.

## Slice 4 verification

- `ruff check` and `ruff format --check`: all 13 touched Python files passed.
- Hermetic admission benchmark and batch selector regressions: 59 passed.
- Native PostgreSQL admission and schema tests: all 66 unique tests passed. One combined
  host-pressure run produced an allocation-full result before SQL in one strict race;
  the exact case and then the full 20-test durable-admission file passed unchanged.
- Prisma client generation: passed.
- Repository migration-path verifier: fresh install, upgrade from `v0.1.42`, and the
  shared route-policy feature path all passed with seeded compatibility records. The
  verifier removed all of its disposable databases.
- `git diff --check`: passed.

The only reported warnings are the existing Prisma/Pydantic Python 3.14 compatibility
warning and pytest-asyncio deprecation warnings.

## Slice 5 source decisions

The budget and prompt changes come from `024f2e71` and `44b70fda`. The integration
keeps current main's realtime settings, managed-asset relations, migration checks,
and documentation navigation.

Normal combined budget checks use one SQL call. Missing or invalid budget counters
cause an unavailable response. Operators must repair counters outside inference.
Optional budget alerts use durable, deduplicated intents and a bounded worker.
Cold prompt bindings use one Redis read, one bounded SQL lookup, and one cache-write
pipeline. PostgreSQL remains the source of truth.

The large historical PR 5 sample directory was not copied. It is not an input to a
regression test or a release qualification gate. The design and deployment pages
record this choice. The final integrated image still requires new load evidence.

## Slice 5 verification

- Focused budget, prompt, bootstrap, and configuration checks: 151 passed.
- Real Redis prompt-fill check: 1 passed.
- Real PostgreSQL budget concurrency checks: 15 passed.
- Resume checks for budget, alerts, configuration, and Helm: 79 passed.
- Prisma client generation: passed.
- Fresh install, upgrade from `v0.1.42`, and shared-feature migration verification:
  passed with all 103 migrations and the budget and realtime fixtures.
- Helm lint and template: base, evaluation, and production profiles passed.
- Ruff check and format: all 30 changed Python files passed.
- `git diff --check`: passed.

The interrupted verifier did not produce a final result. The complete rerun used
the installed Prisma 5.17.0 CLI directly through the verifier's `--prisma` option.
This uses the same pinned engine and avoids repeated Python-wrapper startup.
Its output is in `/private/tmp/issue320-slice5-native-migration-verification.log`.
Five disposable databases left by the interrupted and superseded checks were
removed after the complete rerun passed.

## Slice 6 source decisions

The spend schema comes from `6708948d`. Spend recovery comes from merge
`4d75b560`. Request deadlines and bounded work come from merge `4d856670`.
The replay uses the first parent of each merge.

The integration keeps current main's realtime recovery, settings, managed assets,
and documentation structure. Admission and settlement use main's shared billing
transaction helper. The production transaction budget remains 250 ms.

Python 3.14 exposed a cancelled-thread waiter that retained a payload through an
error log. The executor now waits for its owned future through `asyncio.wait`.
It reads the result only after completion. A cancelled caller does not cancel
the owned thread. The existing ownership and payload-release tests cover this
change. The SQL race fixture now patches the budget in its actual owner, the
shared transaction helper. Its two-second functional-test budget is unchanged.

The two large historical sample directories were not copied. They are not
regression inputs. The design and deployment pages record this decision.
New load evidence is still required for the complete integration.

## Slice 6 verification

- Python 3.11 matches CI and the production image.
- Focused application, component, configuration, and Helm checks: 1,417 passed.
- Real PostgreSQL spend and realtime checks: 78 passed, with no skips. This run
  includes the separate environment for the pinned official realtime SDK.
- Full Redis lane: 102 passed. The memory-isolation check first skipped because
  its service variables were absent. The configured check then passed.
- Python 3.14 bounded-work and transaction regressions: 19 passed after the fix.
- Prisma client generation: passed.
- Fresh install, upgrade from `v0.1.42`, and shared-feature migration checks:
  passed with all 104 migrations and the realtime compatibility fixtures.
- Full test collection: 6,714 tests in exactly one lane each. Counts are 4,348
  hermetic, 1,637 app, 475 PostgreSQL, 103 Redis, and 151 Helm.
- Settings reference: current, with all 368 fields.
- Helm lint and template: base, evaluation, and production profiles passed with
  an existing test secret. Rendering without a required secret failed as designed.
- Ruff check and format: all 91 changed Python files passed.
- `git diff HEAD --check`: passed.
- Full application lane: all 1,637 tests passed.
- SQL probe: passed with 100,000 history rows, 10,000 retained outbox rows,
  and 1,000 expired operations. Admission used one transaction and three SQL
  statements. Receipt acceptance used one transaction and two statements.
  Recovery used the expiry index and a row-bound update.
- Bounded callback and blocking-work probe: passed. Its raw result is in
  `/private/tmp/issue320-slice6-request-work.json`.

Logs are in `/private/tmp/issue320-slice6-python311-focused-tests.log`,
`/private/tmp/issue320-slice6-python311-postgres-tests.log`,
`/private/tmp/issue320-slice6-full-app-tests.log`,
`/private/tmp/issue320-slice6-full-redis-tests.log`, and
`/private/tmp/issue320-slice6-migration-verification.log`.

## Slice 7 source decisions

This slice replays `9a3f2cfc` and `5be17a63`. It retains current main's realtime
runtime, asset-link reconciliation, and four creator authorization owners.
These checks now use the shared readiness inventory. Fixed diagnostics retain
asset counts and timestamps without exposing raw exception messages.

Realtime cleanup starts at the first process drain signal. Bootstrap and Helm
require its cleanup and write budget to fit before generic response cancellation.
Deployment capacity includes realtime upstream and downstream sockets and provider
connection limits. No extra connection pool or inference query is added.

The old policy-listener tests lacked current main's creator-model owner. The
fixtures now provide that owner. Two regressions also prove that missing or failed
creator policy refresh prevents readiness. The production check remains closed.

The historical lifecycle sample archive was not copied. The source plans retain
their history but now point to this plan for clean-replay progress.

The first kind run found a migration Job memory failure. The Python Prisma CLI
imports the full generated client and exits with code 137 at 1 GiB, even for its
version command. The bundled native Prisma 5.17.0 CLI succeeds at the same limit.
The canonical image now selects that native CLI at runtime. Build-time client
generation still uses the Python CLI. The Railway image is generated from the
canonical image. No memory, timeout, migration, or admission limit was increased.
The image check now verifies CLI selection and execution at 1 GiB.

## Slice 7 verification

- Focused current-main readiness, realtime drain, and capacity checks: 62 passed.
- Full component and Helm suite: 4,682 passed. The first run found four fixture
  failures; the final run has none.
- Full application lane: 1,641 passed.
- Full real-Redis lane: 105 passed, with no skips. The first run found the same
  missing creator-model fixture; the final run has no failures.
- Real PostgreSQL migration, allocation, spend, recovery, and realtime checks:
  63 passed, with no skips. This includes the pinned official realtime SDK.
- Full real-PostgreSQL lane: all 485 passed, with no skips.
- Fresh-install, `v0.1.42` upgrade, and shared-feature upgrade checks: passed
  with all 104 migrations. Image history and realtime fixtures also passed.
- Native CLI, container contract, migration, and managed-server regressions:
  16 passed.
- Base, evaluation, and production Helm lint and template: passed.
- Effective capacity-profile documentation check: passed.
- Frozen dependency install and lock check: passed.
- Container and generated-settings checks: passed after the native CLI fix.
- Image build, offline non-root startup, and blocked shutdown: passed before and
  after the native CLI fix. The new 1 GiB native CLI image check passed.
- Optional Presidio image variant: build and offline checks passed. The analyzer
  made no external network request. Migration and blocked shutdown checks passed.
- Ruff check and format: all 134 changed or new Python files passed.
- Full collection: 6,915 tests in exactly one lane each. Counts are 4,464
  hermetic, 1,641 app, 485 PostgreSQL, 105 Redis, and 220 Helm.
- Settings reference: current, with 380 fields.
- Representative SQL probe: passed with 100,000 ledger rows, 10,000 retained
  outbox rows, and 1,000 expired operations. Receipt acceptance now uses one
  fenced statement and one implicit transaction. It used two statements in the
  previous slice. Admission and indexed recovery retain their row bounds.
- `git diff HEAD --check`: passed.
- Fresh disposable kind lifecycle run after the native CLI fix: passed. Checks
  cover fresh and concurrent migrations, Redis readiness recovery, both
  failed-migration rollout gates, stream drain, repeated signals, and shared
  backlog recovery. All 20 batch items completed after rollout, with one ledger
  entry each. Forced pod loss preserved one spend charge and the required audit.
  All four interrupted streams closed upstream without a success marker.
- The harness removed its own cluster after completion. It did not change the
  user's Kubernetes context or stop unrelated containers.

The before- and after-rollout samples each completed 100 requests at 10 RPS.
They prove lifecycle behavior only. Fixed-replica and autoscaling comparison
remains in the capacity CI job and is not claimed as a local result here. The
final integration still requires the full qualification ladder.

Application logs are in `/private/tmp/issue320-slice7-full-app-tests.log`.
Component and Helm logs are in
`/private/tmp/issue320-slice7-hermetic-helm-tests-final.log`.
Redis logs are in `/private/tmp/issue320-slice7-redis-tests-final.log`.
PostgreSQL logs are in `/private/tmp/issue320-slice7-postgres-tests.log`.
Image results are in `/private/tmp/issue320-slice7-native-image-smoke`.
Kind results are in `/private/tmp/issue320-slice7-kind-native-lifecycle`.
These are slice checks, not the final 50/100/200/500 RPS certificate.

## Slice 8 integration checks

The source protocol predates current main's realtime journal and selector billing
owner. Those paths still write through the legacy spend owner. The v2 profile can
disable that worker and can use a separate budget-window authority. Before v2
activation, prove that each enabled writer shares the same authority and recovery
owner. Do not restore a legacy worker merely to make startup pass. That would not
prove shared hard-budget safety. Migration checks must include unresolved realtime
and selector work, not only legacy spending holds.

The source request path also reads the original JSON body to calculate cost bounds.
Current main can change the validated payload before provider dispatch. The clean
replay must calculate bounds from that final payload and cover multiple outputs.
This change must add no SQL, Redis, or network call.

Accounting construction must move to a small bootstrap owner. The existing spend
module is already above the size guard. New accounting audit and cost-bound policy
must use separate typed modules, not new concerns in that large file.

## Slice 8 source decisions

This slice replays `ab4837e9`, `718fac95`, `062ab0c4`, `7986e1c7`, and `4b204a37`.
Current main's realtime schema, provider library versions, capacity checks, and
documentation structure remain in place. The historical accounting plan was not
copied. This file owns clean-replay progress.

Accounting construction now has one small bootstrap owner. Cost-bound and terminal
audit policy also have separate typed owners. Cost bounds use the final validated
payload. They cover multiple chat outputs, embedding inputs, images, speech
characters, and rerank documents. Unbounded audio pricing fails closed in v2.
These calculations add no database, Redis, or network call.

The replay found a paid-cache bypass: a cache hit had no provider dispatch and thus
no v2 reservation. Charged cache hits now reserve their known charge and use the
existing terminal owner. Success requires both acknowledgements. Budget rejection
stays HTTP 429; local capacity rejection stays HTTP 503. Cache fees and provider
cost metadata keep their existing contracts.

Readiness now checks the accounting pool and active generation, not only Prisma.
The direct pool remains within the declared telemetry allocation. The isolated
accounting probe now uses the same direct pool with two connections per process.
It no longer measures the superseded Prisma request transport.

The additive cutover migration has one database-owned check for pending realtime,
spend, selector, and batch work. Preparation uses the same check and activation
owner. Operators must stop legacy writers first. This is not an online cutover
fence. A first attempt used an obsolete main batch status; the new, unshared
migration rolled back fully. The failed marker was cleared on the private test
database, and the corrected migration then passed all upgrade paths. No shared
or historical migration was changed.

At this earlier checkpoint, Realtime, batch, and selector billing did not share
v2 budget authority. Realtime and selector adapters are now connected, as stated
in the shared feature sequence above. Batch remains guarded. These checks do not
remove features from legacy mode. Complete shared adapter verification remains
required before merge.
Capacity rendering now checks the accounting-worker role only when it is enabled.
The regression tests retain main's batch storage and realtime capacity assertions.

The final review found another fallback gap. A configured v2 service that is absent
or invalid must not select legacy provider execution or cache charging. One HTTP-edge
resolver now rejects both paths. One admission result mapper serves provider calls
and cache hits. Neither change adds a dependency call. The new accounting modules
use bounded typed contracts without `Any`. A regression also limits new functions
and modules to the repository's size targets.

## Slice 9 integration checks

The source change `0ac46791` includes permits, local dispatch, terminal journal,
HTTP accounting transport, read models, metrics snapshots, and routing reductions.
It must not overwrite the clean replay's accounting bootstrap, final cost bounds,
paid-cache admission, or missing-owner checks. These owners remain shared.

The source also selects the v2 reporting view by default. That view combines legacy
records with v2 facts and removes duplicate event IDs. Thus the switch alone does
not hide legacy charges. The clean replay must prove parity for main's tenant,
owner, cost, and deletion contracts. It must also retain all v2 history after a
rollback. A reporting default must not change until those checks pass.

## Slice 8 verification

- Full hermetic and Helm gates: 4,804 passed. Counts are 4,567 hermetic and 237 Helm.
- Full application gate: 1,645 passed.
- Full PostgreSQL gate: 515 passed, with no skips. Redis and the pinned official
  realtime SDK were present for the current-main compatibility tests.
- Full real-Redis gate: 105 passed, with no skips. Three separate memory domains
  were present for the cache-eviction test.
- Full collection: 7,069 tests, each in exactly one dependency lane.
- Native accounting tests: 30 passed. They cover grant concurrency, idempotency,
  uncertainty, native deadlines, readiness, and all pending legacy billing lanes.
- Fresh install, `v0.1.42` upgrade, and shared-feature upgrade: passed with 108
  migrations. The verifier removed its disposable databases.
- Paid-cache and missing-owner regressions: passed. Rejection occurs before a
  cached success or provider execution, with the existing error contract.
- All 77 changed Python files: Ruff check and format passed.
- Frozen lock, generated dependency export, 400-field settings reference, and
  effective capacity documentation: passed.
- Base, evaluation, production, and accounting evaluation Helm lint and template:
  passed. No secret was put in a rendered artifact.
- Base and optional Presidio images: build and offline, non-root, read-only smoke
  checks passed. The 1 GiB migration CLI check and blocked-shutdown checks passed.
- `git diff HEAD --check`: passed.

The final image IDs are
`sha256:46486b7e532e1d9ff5d5e08fa1b30602150426533f407a5b645ed14c3f18e284`
and `sha256:226cd6d75a24691a3a1e813d1883a0cedee8a6c7df7659450d289a57822681bd`.

The isolated native accounting probe ran each mode at 50 RPS for ten seconds with
two processes. Direct-window and grant modes each completed all 500 operations.
Each operation used one admission call and one terminal call. Both modes left exact
committed balances and zero reserved or provisional balance. Direct p95/p99 were
26.35/56.84 ms; grant p95/p99 were 28.78/44.00 ms. This is an accounting probe, not
a 500 RPS run or a gateway qualification. It excludes HTTP, Redis, providers, and
projection. Raw samples are in `/private/tmp/issue320-slice8-accounting-probe-final`.

Final logs are `/private/tmp/issue320-slice8-final-components.log`,
`/private/tmp/issue320-slice8-final-app.log`,
`/private/tmp/issue320-slice8-final-postgres.log`, and
`/private/tmp/issue320-slice8-final-redis.log`. Image smoke outputs are in
`/private/tmp/issue320-slice8-final-image-smoke` and
`/private/tmp/issue320-slice8-final-presidio-smoke`.

The first broader run found validation of a disabled accounting role and one
shutdown fixture timeout under concurrent build pressure. The final full run kept
the original assertions and timeouts and passed. All temporary v2 compatibility
checks remain until the shared adapters pass their own recovery tests.

## Slices 2 and 3 source decisions

These slices were integrated and verified together because the ingress/authentication
qualification harness imports the database allocation layer. The historical PR numbers
suggested the opposite order, but the source code dependency is unambiguous.

The ingress and authentication behavior comes from `034be4dc`, `5c3d5361`,
`df67f7a8`, and `42216598`. The dependency ownership behavior comes from `891a4a17`,
`7aa53ed2`, `261bfc89`, `b3c1257c`, `301ed1eb`, `8c13b3f9`, `9ca8c2e8`, and
`ea77c231`. The allocated telemetry failure classification from `c4ebe3ee` was applied
after its database allocation dependency existed. Old feature-branch CI trigger changes
were not replayed; the Redis memory-isolation services required by the current test lane
were retained.

Current main's realtime runtime, managed-asset authorization, reconciliation service,
and documentation structure were preserved. All startup resources now share one bounded
cleanup owner, including the newer reconciliation service. The legacy dynamic-config
regression was adapted to use the production partial-update contract so full default
serialization cannot accidentally pin environment-owned pool limits for a later restart.

## Slices 2 and 3 verification

- `ruff check`: all 70 touched Python files passed.
- `ruff format --check`: all touched Python files passed after formatting four replayed
  files to current main's canonical style.
- Focused hermetic suite: 436 passed and 1 Redis memory test skipped until dedicated
  servers were supplied.
- Real PostgreSQL and Redis suite: 19 passed against isolated PostgreSQL 16 and Redis
  7.2 containers.
- Dedicated Redis no-eviction/eviction isolation: 1 passed against two physically
  separate Redis 7.2 containers.
- `git diff --check`: passed.

The only reported warnings are the existing Prisma/Pydantic Python 3.14 compatibility
warning and pytest-asyncio deprecation warnings.
