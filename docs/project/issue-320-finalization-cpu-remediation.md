# Issue 320: finalization CPU remediation

Status: active

## Evidence

Clean code `f01ef9ff` passed all five image checks and the 1,000 RPS generator
proof. Its fixed-image short ladder used four API processes, two request
processes, one projection process, and the same six-CPU kind VM.

| Rate | Successful/offered | p95 | p99 | Result |
| --- | ---: | ---: | ---: | --- |
| 50 RPS | 1,500/1,500 | 31.53 ms | 64.59 ms | Pass |
| 100 RPS | 3,000/3,000 | 101.27 ms | 333.02 ms | Fail: p99 |
| 200 RPS | 6,000/6,000 | 441.25 ms | 703.39 ms | Fail: latency |
| 500 RPS | 8,588/15,000 | 4,015.14 ms | 4,723.45 ms | Fail: rate, queue, latency |

The 500 RPS generator started 8,622 requests, dropped 6,378 arrivals, and received
34 HTTP 503 responses. All stages kept safe budget state and drained in 10.1 to
12.4 seconds. The first three stages proved exact successful charges. The last
stage matched all durable scope counters but did not prove charge identity for
every failed request. It is not an economic qualification pass. No ten-minute
stage started. All raw evidence remains under
`artifacts/qualification/native-f01ef9ff-20261006`.

Redis used approximately six core calls per request and passed its call budget.
At 500 RPS, API finalization queue wait averaged approximately three seconds per
request. Selected batches contained approximately eight entries and took about
120 ms. Reservation queue wait was much smaller. Recorded application CPU use
approached the six-CPU VM limit. API garbage collection also had long pauses.
These observations do not establish the cost of every HTTP 503 response.

A separate 200 RPS diagnostic profiled one API process with standard-library
`cProfile`. Its results are not release evidence. The first diagnostic helper
failed before load because a spawned report worker repeated its entry point.
The repaired helper collected the profile and removed its disposable cluster.
The profile and helper remain under
`artifacts/qualification/cpu-diagnostic-f01ef9ff-20261006-2`.

The profile records these avoidable costs:

- Authentication copied the complete application configuration once per request
  to read its master key. The copy cost 1.73 seconds across 1,501 requests in the
  instrumented process. This is profile time, not normal request latency.
- The outbound privacy guard called `Logger.setLevel` 11,268 times. Global log
  cache clearing cost 0.82 seconds in that profile.
- HTTPcore tried to import its absent optional async detector repeatedly.
- Financial proofs were frozen and serialized repeatedly. Their validation and
  mutation isolation must remain. Do not remove a financial check for speed.

## Invariants and ownership

The dynamic configuration manager remains the master-key authority. A narrow
scalar read must use its current committed configuration and must not return a
mutable configuration reference. Constant-time comparison and deny behavior
remain unchanged. No SQL or Redis call is added.

The existing runtime logging owner sets dependency levels at startup. The
reference-counted outbound privacy guard still suppresses sensitive DEBUG
traces during concurrent operations and cancellation. It must not reset an
unchanged logger level on the request path.

The frozen dependency lock and canonical image own the async detector package.
The dependency adds no client, pool, task, service, or configuration path.

The existing accounting microbatch setting owns aggregation. Any new native
profile allocation must stay within current entry, byte, queue, and deadline
bounds. Do not increase pool, replica, resource, or SLO limits. A response still
requires its durable terminal acknowledgement. PostgreSQL remains the money
authority, and every process retains its existing lifecycle owner.

## Work checks

- [x] Preserve the fixed-image ladder and distinguish failed gates from passes.
- [x] Locate the long finalization queue wait in per-process evidence.
- [x] Capture a separate CPU profile and identify its direct callers.
- [x] Replace full-config copies in master-key checks with a typed scalar read.
  Test current-generation changes, caller-copy isolation, and deny behavior.
- [x] Set safe dependency logging levels through the existing startup owner.
  Skip unchanged level writes. Test normal operation, DEBUG privacy, concurrent
  operations, and cancellation.
- [x] Add the missing async detector to the frozen runtime dependencies.
  Regenerate exports without upgrading the other dependencies.
- [x] Prove the detector exists in the exact non-root image.
- [x] Match native API and request aggregation within the existing batch bounds.
  Check effective settings and preserve all financial mutation-isolation tests.
- [x] Run focused tests, full affected lanes, collection, lint, chart profiles,
  lock/export checks, migration paths, and exact-image checks. No UI change is planned.
- [ ] Compare finalization batch time, queue wait, CPU, and charges with the
  saved baseline. If proof conversion still limits capacity, redesign that
  internal boundary with immutable bytes and its own failure/isolation tests.
- [ ] Pass a fresh fixed-image short 50/100/200/500 RPS ladder.
- [ ] Complete all four ten-minute runs without an arrival pause.
- [ ] Save raw samples, exact charge evidence, storage state, and final results.

Keep this plan active until the final qualification is complete. A passing unit
test or a diagnostic profile is not a passing RPS certificate.

## Implementation checkpoint

Authentication now reads one immutable string from the current committed
configuration. The control-plane copy API stays unchanged. Failed reloads keep
the old key and generation. The typed startup fallback is used only when no
dynamic manager exists. The shared application test settings now declare the
same optional master-key field as the actual settings contract.

The runtime logging owner sets all six HTTPcore trace levels. DEBUG traces stay
disabled. The existing guard retains its lock and concurrent-operation count,
but does not write an unchanged level on entry or exit. Its normal-operation
regression observes zero level writes. Privacy, concurrent delivery, and
cancellation checks remain.

The runtime lock adds only `sniffio==1.3.1`. The generated requirements and
image check use this same pin. No other package version changed.

The next qualification profile uses a maximum of 32 entries in both the API and
request role, rather than API 8 and request 32. This is an explicit profile
change, not a result from the earlier sealed image. The production default stays
8. Entry, retained-byte, batch-byte, queue, dwell, and deadline limits are not
increased. Resources, role counts, pools, and latency limits stay unchanged.

The first focused run passed 171 cases and failed six route cases whose test
settings omitted the real optional key field. After that fixture correction,
all 177 focused cases passed. Source lint, changed-file format, frozen lock, and
generated container parity passed. Collection assigns all 8,665 tests to exactly
one dependency lane.

The first full component/chart run passed 6,053 cases and failed one unchanged
source-identity subprocess timeout at its five-second limit. Keep that log. Its
deadline and equality assertion are unchanged. Full confirmations remain open.

The unchanged source-identity module then passed all five cases in 0.14 seconds.
The full component/chart confirmation passed all 6,054 cases in 93.91 seconds.
The full application lane passed all 1,649 cases in 533.77 seconds. Real
PostgreSQL, Redis, and exact-image checks remain open.

The first full PostgreSQL run passed 828 cases and failed 22. Twenty failures
came from the Realtime fixture, which replaced the shared test settings without
the optional master-key field. It now declares that field as `None`, like the
actual settings contract. The other failures were the unchanged journal lease-
expiry wait and native selector projection wait. Their deadlines and assertions
stay unchanged. All 50 affected database cases then passed in 45.59 seconds.

Host inspection found two six-CPU, 12 GiB test VMs and substantial memory
compression. The owned qualification VM had no containers. It was stopped with
its disk and images preserved. Reported free-memory capacity rose from 36% to
59%. This observation does not prove the cause of either timeout. The full
database confirmation runs with that idle VM stopped. The qualification VM will
restart with its exact settings and without changing the active user context.

## Bounded SQL execution policy

The next database confirmation passed 479 cases, then failed the cross-worker
funding test. Both RPC cases also failed alone at their unchanged one-second
deadline. Database observation found the funding statement active for about
0.8 seconds, without a lock wait or a blocking session. No observed garbage
collection pause explained that time.

A controlled test disabled PostgreSQL JIT compilation in the isolated database.
Both cases passed. Restoring the original database setting made both cases fail
again. A second controlled test changed only the bulk local funding function.
Both cases passed, with test calls of 0.11 and 0.10 seconds. The function setting
was restored after that test. A direct asyncpg plan probe did not reproduce the
same large compilation cost; do not report it as a measurement of that cost.

PostgreSQL explains that JIT can cost more than it saves for short queries.
See [PostgreSQL 15 JIT guidance](https://www.postgresql.org/docs/15/jit-decision.html)
and [function-local settings](https://www.postgresql.org/docs/15/sql-alterfunction.html).

The migration is the only owner of this execution policy. Set `jit=off` on
three short, bounded native accounting entry functions: bulk local funding,
backlog snapshot, and read-model projection. The last two already set local
index-plan controls. A high estimated plan cost must not turn their bounded
key probes into expensive compilation work. Do not change query bodies,
indexes, money constraints, tenant scope, retries, call counts, or deadlines.
The caller keeps its original JIT policy after success or error. Reporting and
other database operations keep their existing policy.

An application pool override is not used: it would create another policy owner
and would miss compatibility callers. A database-wide override is not used:
it could change reporting performance. Increasing a deadline is not a fix.
No connection, queue, memory, or CPU allocation is increased.

Migration 143 follows all shared migrations. It changes no retained data.
Rollback removes only `jit` from these three function settings with
`ALTER FUNCTION ... RESET jit`, in one transaction. Keep their index-plan
settings. Rollback can restore the timeout; it must not claim a capacity pass.
There is no new compatibility path to remove.

- [x] Prove the funding timeout with the original JIT policy.
- [x] Prove that the function-local change passes the unchanged RPC test.
- [x] Add tests for the three exact signatures, nested plans, caller setting
  restoration, error unwind, and unchanged budget effects.
- [x] Add migration 143 and generate the client.
- [x] Verify fresh, last-release, and shared-feature upgrade paths.
- [x] Pass the full database confirmation and dependency test gates.
- [x] Pass all five exact-image checks at `476904b4`.
- [ ] Pass the fixed-image load gates.

The new policy regressions failed four cases before the migration. The migration
then applied to the test database. All 61 focused policy, RPC, capture, health,
funding, and read-model plan cases passed in 88.00 seconds. The nested-plan
probe deliberately enabled caller JIT and proved that the bounded funding
function did not compile its nested statements. Success and error both restored
the caller setting. Exact return and settlement counters remained correct.
The existing three-path migration fixture now checks all three policies.

The final collection contains 8,674 cases in exactly one dependency lane each:
5,794 hermetic, 1,649 application, 854 PostgreSQL, 112 Redis, and 265 Helm.
Source lint, changed-file formatting, the frozen lock, and container export
parity passed. The final component/chart run passed 6,059 cases in 95.29 seconds.
The full PostgreSQL confirmation passed all 854 cases in 701.88 seconds. All
112 Redis cases passed in 16.00 seconds. The earlier 1,649-case application
confirmation remains valid: no application Python source changed after it.
Migration 143 changes SQL execution settings, not the application contract.
Fresh install, upgrade from `v0.1.42`, and the shared-feature upgrade all passed
through migration 143. Their fixture checks the exact three function policies.
The 413-field generated configuration reference and generated capacity profiles
are current. All six declared profiles passed lint and rendering. Failed and
passing test logs, bounded diagnostic helpers, and database observations remain
under `artifacts/qualification/verification-cpu-remediation-20261006`.

## Isolated qualification checkpoint

The exact image from clean commit `476904b4` passed all five image checks.
Its first short run passed all requests at 50 and 100 RPS, but failed the queue
slope gate. At 200 RPS, it returned 215 HTTP 503 responses and left six unsettled
operations after the unchanged 180-second drain limit. Keep this failed run
under `artifacts/qualification/native-476904b4-20261006`.

Host inspection found a separate Rancher VM using approximately 4.4 CPU cores.
The user approved a temporary shutdown and restart. The isolated rerun used the
same clean commit, image, resources, profile, deadlines, and gates. Rancher was
stopped for this run and restarted after it. No data was deleted. The original
Docker and Kubernetes contexts were restored. The disposable cluster was removed.

| Short rate | Successful/offered | p95 | p99 | Result |
| --- | ---: | ---: | ---: | --- |
| 50 RPS | 1,500/1,500 | 28.13 ms | 53.30 ms | Pass |
| 100 RPS | 3,000/3,000 | 26.89 ms | 42.32 ms | Pass |
| 200 RPS | 6,000/6,000 | 47.60 ms | 115.42 ms | Pass |
| 500 RPS | 11,325/15,000 | 3,193.58 ms | 3,706.63 ms | Fail |

Generator proof passed 10,000/10,000 requests at 1,000 RPS. The 500 RPS stage
started 11,329 requests, dropped 3,671 arrivals, and returned four HTTP 503
responses. The measured queue slope was positive, at 6.15 requests per second.
Two metrics scrapes failed. All stages drained in 10.0 to 12.2 seconds and kept
safe budget state. The first three stages proved exact charges. The last stage
matched scope counters but did not prove each failed request's charge identity.
It is not an economic pass. No ten-minute stage started. Raw evidence is under
`artifacts/qualification/native-476904b4-isolated-20261006`.

Stopping Rancher removed the observed 200 RPS failure on the same image. This
supports host contention as a cause of that run. It does not explain or remove
the remaining 500 RPS limit. At 500 RPS, measured finalization batches averaged
28.1 entries and approximately 310 ms. Finalization queue wait averaged about
1.15 seconds per request. Database calls averaged 9.11 ms. The earlier CPU
profile and current call trace show repeated proof freeze, restore, and encoding
through the API, transport, request queue, and journal. Database latency alone
does not explain the finalization batch time.

## Immutable terminal boundary slice

Keep the current local terminal owner, signed RPC transport, journal repository,
and receipt store. PostgreSQL remains the economic authority. Replace repeated
internal graph conversion with one typed, immutable terminal snapshot. Validate
the complete nested graph at mutable caller and received-wire boundaries. The
snapshot retains canonical reservation and finalization bytes, the complete
proof, and bounded scalar reply identity. Reuse those bytes within one process.
Do not expose mutable internal graphs or accept unchecked model copies.

Keep existing caller contracts through the same owner. The non-native database
adapter can restore a private graph at its existing boundary. Native transport
and journal paths must use the snapshot directly. No new policy, lifecycle,
client, pool, setting, migration, or compatibility service is needed.

- [x] Capture a deterministic before/after conversion benchmark.
- [x] Add the typed immutable terminal snapshot and complete graph validation.
- [x] Keep the shared API and RPC queues byte-bounded; reuse accepted snapshots.
- [x] Reuse canonical documents for signed transport, journal hashes, recovery,
  reply checks, and local proof removal.
- [x] Test nested mutation, forged model copies, malformed bytes, duplicate
  identities, stale generations, size bounds, cancellation, and reply mismatch.
- [x] Prove that native handoffs do not repeat full snapshot conversion.
- [x] Run all affected regression gates and preserve their raw logs.
- [x] Commit clean source and pass all five checks on its exact image.
- [x] Repeat the short 50/100/200/500 RPS ladder with Rancher stopped.
- [ ] Complete all four ten-minute stages on that same image and cluster.
- [x] Restore Rancher and the user's contexts, then record this run's evidence.

The snapshot stores immutable bytes and scalar identity. Its retained grant has
no mutable nested graph. Caller-facing restored graphs are private copies.
The request queue accounts for all stored documents plus a fixed object bound.
Its 1 MiB batch and 8 MiB retained limits stay unchanged. Wide-document object
size tests prove that the declared retained charge covers measured object size.

The wire JSON, canonical document hashes, SQL parameters, and receipt types stay
unchanged. No data migration or backfill is needed. The earlier image can read
the same durable documents. An image rollback restores the earlier conversion
cost, not a throughput pass. The existing mutable caller interface is a wrapper
through the same snapshot owner, not a second terminal implementation.

All 220 existing focused cases passed after the handoff change. The first new
test run failed 21 cases because the fixture requires an active event loop.
Those tests now use the fixture's async clock. No assertion or deadline changed.
The confirmation passed all 249 focused cases, including 29 new snapshot cases.
The signed native-cycle regression observes one snapshot per entry at the
mutable caller boundary and one at the received-wire boundary. It observes
none at the intermediate transport or journal handoffs. The module-size,
function-size, and typed-boundary check now includes the snapshot owner.

A deterministic in-process diagnostic used the actual local terminal owner,
signed transport, request service, and journal repository with a fake SQL peer.
It processed 100 batches of 32 entries in each version. The `cProfile` run fell
from 5.20 to 1.84 seconds, and function calls fell from 9.53 million to 3.63
million. These are instrumented diagnostic results, not normal request latency
or load qualification. Full confirmations and the new image remain required.
Collection contains 8,704 cases in exactly one lane: 5,824 hermetic, 1,649
application, 854 PostgreSQL, 112 Redis, and 265 Helm. Source lint, changed-file
formatting, and diff checks passed.

The full snapshot confirmation passed all 8,704 cases: 6,089 component and Helm
cases in 104.18 seconds, 1,649 application cases in 531.10 seconds, 854
PostgreSQL cases in 783.67 seconds, and 112 Redis cases in 25.58 seconds.
No test was skipped or weakened. The frozen lock, generated image exports,
413-field configuration reference, capacity reference, source lint, changed-file
formatting, and diff checks passed again. No SQL or UI source changed in this
slice. The previous migration-path and UI evidence remains applicable.

The complete logs, the initial async-fixture failure and its confirmation, and
both conversion profiles are retained under
`artifacts/qualification/verification-terminal-snapshots-20261006`.
The user approved another temporary Rancher shutdown for the new fixed-image
qualification. Restart Rancher and restore the original contexts after the
tests, including after a failed gate. Do not delete Rancher data.

## Snapshot image qualification checkpoint

Clean code `5a28ca26` passed all five exact-image checks. Its arm64 platform
manifest is `sha256:8f81fa16b99094f8eb40673977fb764fd0e4ad1c76e04865bb924cdf134bc6a1`.
The Docker manifest-list ID is
`sha256:829b42c0c7984a22a664fc608a2b988560544db547e08215781e60d8f7b91dc1`.
Do not treat these two identities as interchangeable. The generator proof
passed 10,000/10,000 requests at 1,000 RPS. Rancher was stopped for this approved
series and restarted afterward. Its node is Ready, both original contexts are
restored, and the disposable cluster is removed. No Rancher data was deleted.

| Short rate | Successful/offered | p95 | p99 | Result |
| --- | ---: | ---: | ---: | --- |
| 50 RPS | 1,500/1,500 | 28.15 ms | 112.25 ms | Pass |
| 100 RPS | 3,000/3,000 | 27.73 ms | 89.94 ms | Pass |
| 200 RPS | 6,000/6,000 | 57.12 ms | 199.84 ms | Pass |
| 500 RPS | 12,048/15,000 | 3,394.09 ms | 4,129.31 ms | Fail |

The 500 RPS generator started 12,874 requests and dropped 2,126 arrivals.
It received 826 HTTP 503 responses: 753 `no_healthy_deployments` and 73
unclassified HTTP errors. Queue slope was positive, at 38.56 requests per second.
Five metrics scrapes failed. Accounting did not drain within 180 seconds: four
operations remained unsettled. Terminal, reporting, spend, and audit queues were
empty, and no grant remained open. The committed scope counters matched durable
facts, but each scope retained 0.098328 provisional capacity. Budget state stayed
safe. This is not an economic pass, and failed-request charge identity is not
proved. No ten-minute stage started.

Raw samples, metrics, resource observations, economics, wrapper, host snapshot,
and run log remain under
`artifacts/qualification/native-5a28ca26-isolated-20261006`. Image checks remain
under `artifacts/qualification/native-image-5a28ca26-20261006`.
The top-level `results.json` contains only the first three stages because the
500 RPS drain raised an error. Its complete result is in
`short-500rps/qualification.json`; do not omit it from the report.

The terminal change reduced measured finalization batch time from approximately
307 ms to 158 ms, and mean finalization queue wait from 1.15 seconds to 252 ms.
The new run used 720 batches for 12,053 terminal entries, versus 403 batches for
11,326 entries in the earlier isolated run. Batch size and workload changed under
saturation, so these observations are not an isolated normal-latency benchmark.
They show that the terminal cost fell but did not remove the load limit.

Captured logs show Redis acquisition deadlines and full allocation on API
processes. Router state then became unavailable, which caused fail-closed route
rejection. The public `no_healthy_deployments` label is not proof that the mock
provider failed. One log also reports `Provider unavailable`; its exact source
still needs tracing. API processes reached approximately one CPU core each.
Request workers, PostgreSQL, and Redis did not show the same CPU limit. The
resource observation includes startup and transfer windows, not only arrivals.

### Remaining measured-cost work

- [x] Profile the latest exact image and locate the remaining API CPU work.
  Keep instrumented results separate from release evidence.
- [ ] Trace router-state rejection, provider-unavailable errors, and the four
  unsettled operations. Do not release uncertain capacity without durable proof.
- [ ] Remove the measured repeated work through its existing typed owner.
  Preserve complete validation, mutation isolation, and deadline behavior.
- [ ] Run focused failure tests and all affected regression gates.
- [ ] Repeat exact-image checks and the fixed-profile qualification series.

The latest separate profile used image `5a28ca26` on disposable kind, with
Rancher running. It is not release evidence. One API process handled 1,501
requests, including warmup. It called `reservation_bytes` 13,509 times.
The handle validator encoded two mutable input documents, then encoded both
validated documents again for comparison. That duplicate comparison is now
removed. The original exact bytes are reused; both full input checks and private
copies remain. All 140 focused cases passed, including three new encoding and
canonical-money cases. The 10,000-handle instrumented diagnostic fell from
2.04 to 1.44 seconds. This is not request latency or an RPS result.

The profile also traced most API database-client work to the existing legacy
spend and audit outbox consumers. They continued to poll empty tables while
native journal accounting handled the test traffic. Keep these consumers: they
still own supported non-native and control-plane work. Change only their idle
wait through one small typed helper, not their persistence or lifecycle.

- [x] Capture the latest profile and its direct conversion/database callers.
- [x] Remove duplicate handle encodes and retain exact canonical comparison.
- [x] Extract one bounded empty-worker wait for the existing spend/audit owners.
  Start at the configured flush interval, then double after empty claims up to
  one second. Preserve an existing interval greater than one second.
- [x] Clear the local wake signal before the claim, not after an empty result.
  A local enqueue during a claim must interrupt the idle wait. Reset on work,
  wakeup, startup, and reconfiguration. A different process's work is found by
  the bounded poll. Durable acceptance, leases, retries, and shutdown remain.
- [x] Prove empty-poll bounds, prompt local wakeup, cross-process poll bounds,
  restart/reload reset, cancellation, and busy-worker behavior.
- [ ] Confirm all affected gates and run the next exact-image series.

This changes only idle background processing delay. With the default 100 ms
flush interval, a quiet consumer eventually polls once per second, rather than
ten times per second. New local durable work wakes it at once. Durable writes
and request acknowledgement do not wait for this delay. No client response,
financial release policy, pool, resource, or qualification limit changes.

All 113 focused worker, handle, and structure cases passed. The 20 idle-wait
cases cover the existing spend and audit owners, including startup reset,
reconfiguration, busy processing, wakeup during a claim, cancellation, and
shutdown. The deterministic default-interval check requires at most 64 claims
in a virtual idle minute, rather than the previous 600. This is a call bound,
not a load result. Source lint, formatting, and diff checks passed. Full
confirmation used all 8,728 collected cases in five exclusive dependency
lanes. Its failures and focused confirmations are recorded below. A new exact
image and load series remain required.

The first full database run passed 852 cases and failed two. The batch lease
case found no immediately due row before it reached any lease assertion.
The table stores due times at millisecond precision. A read-only check proved
that rounding can put a newly stored due time after the next instant. The test
now makes its row explicitly due, as the other lease-transition cases do.
No production scheduling or lease assertion changes. The minimal-role case
reported unready admission dependencies during startup. Both unchanged cases
passed together in 1.44 seconds. This does not prove the startup cause or a
product fix. Preserve the failed full run and repeat the full database gate.

### Targeted confirmation and next load run

The second full PostgreSQL run passed 852 cases and failed two different cases.
The earlier two failures did not repeat. The new admission failure occurred
while opening a transaction during the eight-connection warmup, before the
admission race. The 200 ms acquisition deadline expired. The log does not prove
why it expired. Keep the simultaneous cold-pool check and its original bounds.
The proposed phased warmup was rejected by the safety check and was not applied.

The new Realtime conflict failure claimed no immediately due outbox record.
Recovery creates that record during the claim. Its due time has millisecond
precision; the read-only precision check confirms that rounding can leave a new
row in the future. The conflict case now runs recovery and sets only its own row
explicitly due before claiming it. Receipt rejection and exact cost assertions
remain. This does not change production scheduling or release money.

Both unchanged failed cases passed together in 1.45 seconds. All affected
Realtime and admission cases passed in the targeted 80-case check. That command
passed 79 cases and failed one existing native statement-timeout case: the
unavailable error had no native server error as its cause. The unchanged case
passed alone in 1.59 seconds. This does not prove the cause or a product
fix. Source lint and formatting passed all nine touched Python paths.

Component/chart passed 6,113 cases, application passed 1,649, and Redis passed
112. The batch and minimal-role confirmation passed all 26 cases. Preserve
both failed full database runs and both focused confirmations. Do not report
the full PostgreSQL gate as passed.

The user requested faster iteration. Do not add optional runtime or evidence
features before this load run, and do not repeat every full lane to seek a
passing result. Seal the present candidate and run its exact image checks,
then the fixed 50/100/200/500 RPS ladder. The known regression limitations remain
part of the report. Keep the same profile, economic checks, and stop conditions.
Only an all-pass short ladder starts the four ten-minute stages.

- [x] Run the full affected lanes and preserve each failed result.
- [x] Check the failed cases and affected modules without changing production bounds.
- [x] Confirm source lint, format, and diff checks.
- [x] Seal the source and pass all five exact-image checks at `861947c8`.
- [x] Run the fresh fixed-profile short ladder and retain every result.
- [ ] Run all four ten-minute stages if the short ladder passes.

### Follow-up failure evidence

The two sealed `861947c8` runs triggered this follow-up. The first passed short
50 and 100 RPS, failed short 200 RPS, then stopped before 500 RPS because an
offline Kubernetes read timed out. The second passed short 50 and 200 RPS.
Its short 100 RPS failed only the queue slope gate (+0.0526/s). Short 500 RPS
completed 2,075 of 15,000 offered requests. It dropped 710 arrivals and returned
12,215 failed responses. The drain deadline left 69 unsettled operations and
three open grants; terminal and reporting queues were empty. Budget state was
safe. Neither run started a ten-minute stage. Both failed runs remain in
`artifacts/qualification/native-861947c8-20261006` and its `-2` sibling.

The second run logged Redis acquisition timeouts and router-state failures.
The post-provider completion handler already preserves a successful provider
response when cleanup fails. Do not treat that warning as a lost provider
response or release uncertain money. Capture the operation state before the
next diagnosis.

- [x] Save each stage in the aggregate result before a failed drain stops the run.
  Keep the same stop conditions and all pass limits.
- [x] After a failed drain, capture at most 64 unsettled operations from the
  fixture database. Record scalar state, exact held amount, journal outcome,
  and a fixed uncertainty class. Do not copy tenant, prompt, credential, or
  full financial documents. Bound the query and mark unavailable data as unknown.
- [x] Prove the capture is read-only against PostgreSQL. Test result preservation,
  truncation, failed capture, and cancellation before using the new capture.

These are offline evidence changes. They add no request or arrival-window work.
They do not release uncertain money or turn a failed stage into a pass.

The capture uses a read-only transaction, a two-second statement limit, a
250 ms lock limit, and a five-second caller limit. It queries at most 65 rows
and exports at most 64, with an explicit truncation flag. It copies no full
payload. Missing data remains unknown, and cancellation propagates. All 47
focused tool and PostgreSQL cases passed. PostgreSQL confirmed the read-only
setting, unchanged exact money, fixed reason classes, and real truncation.
Lint, format, and diff checks passed. No runtime source or qualification gate
changed in this evidence slice.

- [ ] Seal the evidence slice and pass its exact-image checks.
- [ ] Use the captured failure state to identify and test the next runtime fix.
- [ ] Pass the unchanged short ladder, then all four ten-minute stages.

### Selected upper-tier iteration

The user requested that the next iterations skip 50 and 100 RPS. The runner
now accepts `--diagnostic-rates 200 500` or `--diagnostic-rates 500`. It uses
the same image, topology, resource limits, arrival schedule, and pass checks.
It runs only the selected short stages. A passing selected series is never
release-eligible and cannot replace the complete four-tier qualification.
Run the full series once after the upper tiers pass.

The sealed `d3ec62bc` run passed all five image checks and the 1,000 RPS
generator proof. It preserved all four failed short stages in `results.json`:

| Rate | Successful/offered | p95 | p99 | Result |
| --- | ---: | ---: | ---: | --- |
| 50 RPS | 1,479/1,500 | 64.16 ms | 553.73 ms | Fail: availability, p99, economics |
| 100 RPS | 3,000/3,000 | 37.00 ms | 82.50 ms | Fail: queue slope +0.0148/s |
| 200 RPS | 6,000/6,000 | 214.24 ms | 319.11 ms | Fail: latency and queue |
| 500 RPS | 5,163/15,000 | 6,888.77 ms | 10,002.75 ms | Fail: rate, latency, diagnostics, drain |

At 500 RPS, 4,439 arrivals were dropped. Failed requests included 499 client
connection timeouts, 26 client connection errors, 1,715 accounting-availability
responses, six authentication-availability responses, and 3,152 other HTTP
errors. After 181.66 seconds, all recorded operations had settled. Seven grants
remained open; all processing queues were empty. Budget state was safe. The new
operation capture was available and correctly empty. No ten-minute stage ran.
Keep `artifacts/qualification/native-d3ec62bc-20261006` as a failed run.

Post-arrival Kubernetes evidence records API and native-role readiness failures
during the 500 RPS stage. This supports a readiness/connection diagnosis, but
does not identify each failure's cause. The host also had unrelated active
tests and substantial swap usage. Do not call this an isolated host result.

The remote funding exchange has no response-loss recovery, whereas its database
adapter has fenced recovery. A lost funding response is a possible source of
an open grant without a recorded operation. It is not yet the proven cause of
the seven retained grants. The next failed-drain capture therefore also reads
at most 64 open local grants: exact scalar balances, used/returned counts,
dispatch expiry, and remaining recovery time. It copies no owner, subject,
fence secret, or full document, and preserves the exact opaque grant ID.

- [x] Add selected short-tier runs without changing full qualification or pass limits.
- [x] Prove that even an all-pass selected series is not release-eligible.
- [x] Add bounded read-only open-grant evidence for failed drains.
- [x] Pass 61 focused tool/PostgreSQL cases; preserve the two initial assertion failures.
- [ ] Identify the upper-tier cause with selected diagnostics and controlled fault injection.
- [ ] Implement and verify the confirmed runtime fix.
- [ ] Pass selected 200 and 500 RPS, then the complete fixed-image qualification.

### Native empty-claim polling

The first selected 500 RPS diagnostic used sealed image `cd172bc7`. All five
image checks and the 1,000 RPS generator proof passed. The gateway precheck then
returned HTTP 503 (`spend_persistence_unavailable`). No 500 RPS arrival stage
started. Keep this result as a failed precheck, not a throughput result.

The bounded diagnostic recorded accounting health failures. Several API database
probes timed out, and two organization-generation refreshers became stale. The
projection role also recorded unobserved asyncpg connection-release timeouts.
These observations show that required services lost health before the arrival
stage. They do not prove why all earlier 500 RPS stages failed. The host retained
unrelated CPU load and large swap use.

The native profile has six processing lanes and a 20 ms poll interval. Empty
lanes therefore schedule about 300 claim calls per second, before progress,
presence, recovery, or connection-reset work. This repeats the empty-poll cost
already removed from the spend/audit consumers. Reuse that bounded wait owner;
do not add another task, queue, notification system, or database authority.

PostgreSQL keeps every claim and financial fence. Empty successful claims now
start at the configured interval and double to `max(interval, 1 second)`.
Completed work resets the wait and starts another claim immediately. A wake
received during a claim stays visible. Errors retain the existing health state,
failure backoff, and caller deadline. Cross-process discovery can add up to the
idle wait plus bounded database time. The normal one-second cap stays below the
unchanged five-second reporting-health freshness bound. No pool, resource,
money-release rule, or qualification limit changes.

- [x] Preserve the failed selected precheck and bounded diagnostic data.
- [x] Use the existing idle wait for journal and reporting lanes.
- [x] Add deterministic checks for idle call reduction, active reset, wake
  preservation, and unchanged failure backoff in both owners.
- [x] Pass 127 focused worker and contract checks in 0.74 seconds.
- [x] Pass 264 affected component checks in 4.22 seconds and 56 real PostgreSQL
  role, replay, recovery, reporting, and shutdown checks in 33.61 seconds.
- [x] Seal `c1b3ee9d` and run selected upper tiers without lower-tier reruns.
- [ ] Resolve remaining release-timeout or funding-response failures if they recur.
- [ ] Pass upper-tier diagnostics, then complete final qualification.

### Reporting checkpoint claim race

The `c1b3ee9d` selected run completed all 6,000 requests at 200 RPS, with p95
50.81 ms and p99 104.33 ms. Its queue slope was +0.22435, above the unchanged
+0.01 limit. It is not a pass. The 500 RPS stage completed 4,725/15,000
requests, dropped 260 arrivals, and returned 10,015 HTTP 503 responses. One
provisional operation remained after the 181.25-second failed drain. Keep
`artifacts/qualification/native-c1b3ee9d-upper-20261006` as failed evidence.

Three bounded 500 RPS diagnostics added cause checks, not release results:

- `native-c1b3ee9d-500-diagnostic-20261006`: 6,730 successes, 278 dropped
  arrivals, and seven provisional operations after the failed drain. Provider
  failures entered cooldown, but the first diagnostic did not capture their
  original cause.
- `native-c1b3ee9d-500-provider-cause-20261006`: 13,851 successes and 1,149
  billing HTTP 503 responses. No arrivals dropped. No provider error or
  cooldown occurred. All work drained in 10.15 seconds. p95 was 441.26 ms
  and p99 was 575.98 ms, so this is still a failed short diagnostic.
- `native-c1b3ee9d-500-native-cause-20261006-2`: 13,812 successes and 1,188
  billing HTTP 503 responses. No arrivals dropped. Reporting workers became
  unavailable, then admission rejected funding. All work drained in 12.12
  seconds. p95 was 323.01 ms and p99 was 433.16 ms. The first helper attempt,
  without the `-2` suffix, stopped before any 500 RPS arrivals because it tried
  to signal a completed setup pod. Preserve it as an interrupted helper run.

The last trace recorded no database-call error. A successful query with an
invalid result was therefore a specific hypothesis. The controlled database
test confirmed it: worker B selects unfinished work, worker A commits all of
that work and clears its lease, then B locks the advanced checkpoint. The old
query claims the row but returns no event keys. Strict result validation then
marks the worker unavailable. The committed empty lease remains live for 30
seconds. Reporting health also blocks new billing requests.

The repair locks the current checkpoint, then checks its current frontier with
an indexed, single-row event lookup before it claims the row. Completed work
returns a normal empty result with no new lease. Partially completed work
returns only its remaining events. No health, result-validation, money, lease,
pool, resource, deadline, or qualification bound is relaxed.

- [x] Reproduce the invalid empty claim against unchanged runtime SQL.
- [x] Repair the post-lock work check and prove both full and partial advancement.
- [x] Confirm strict decoding, exact reporting effects, and all retained-history plans.
- [x] Seal `5b271273` and run 500 RPS without repeating lower tiers.
- [ ] Pass selected 200/500, then the complete fixed-image qualification once.

Keep the initial query-plan failure: an outer existence check allowed a history
scan under the alternate join profile. The bounded lookup replaces that shape.
The host still has unrelated load. Do not call these isolated host results.

Focused confirmation passed 109 component cases in 0.73 seconds and 37 real
PostgreSQL cases in 27.37 seconds. All four planner profiles passed. The first
bounded-query confirmation passed 41 cases but failed one existing native-role
startup/cleanup case. That unchanged case passed alone in 1.64 seconds.
Preserve all initial race, test-barrier, query-plan, and startup failures with
their confirmations in `artifacts/qualification/verification-reporting-race-20261006`.
This targeted result does not close the earlier full PostgreSQL limitation.

### Reporting-race repair: upper-tier result

The `5b271273` image passed all five smoke checks. Fresh generator proof also
passed. The selected short 500 RPS stage completed 9,092/15,000 requests and
dropped 1,235 arrivals. It returned 4,432 billing HTTP 503 responses, 82 other
HTTP 503 responses, and 159 client connection errors. p95 was 3,158.82 ms and
p99 was 10,000.96 ms. The 181.18-second drain failed with one open grant and
no unsettled operation. All terminal and reporting queues were empty. All
budget windows remained safe. No lower tier or ten-minute stage ran. Preserve
`artifacts/qualification/native-5b271273-500-20261006` as a failed result.

The open grant had 32 allocated permits, no consumed or returned permit, and
0.786624 exact reserved capacity. Its dispatch deadline had passed, but its
safe recovery deadline was still about 655 seconds away. This is consistent
with an unreceived funding acknowledgement; it is not proof of that cause.
Do not release unknown issued capacity early to satisfy the drain limit.

Host snapshots recorded 148,963 swap-in pages and 161,504 swap-out pages across
setup, load, and post-arrival capture. With 16 KiB pages, that is about 2.44 GB
read from disk and 2.65 GB written. The host still had another active Colima VM
and unrelated workloads. These facts make an isolated comparison necessary;
they do not prove that host pressure caused every failed request.

A new bounded 500 RPS diagnostic captured failures after database calls as
well as inside them. It completed 4,997/15,000 requests and dropped 478
arrivals. Database statements and pool acquisition failed across funding,
terminal acceptance, journal claims, and reporting commits. No invalid-result
claim failure was recorded. Two provider `ReadError` failures became service-
unavailable errors and triggered cooldown on the single fixture deployment.
The provider did not restart. This identifies the cooldown trigger, but not
the cause of those connection failures. The failed 180.87-second drain retained
two safe provisional operations and one open unused grant. Preserve
`artifacts/qualification/native-5b271273-500-native-cause-20261006`; its
instrumentation means it is not release qualification.

- [x] Preserve both failed upper-tier results and bounded cause evidence.
- [x] Distinguish the repaired claim race from the new deadline and connection failures.
- [x] Use the user's approval to stop the other Colima VM for a fixed-image
  500 RPS check, then restore it and its original workloads.
- [ ] Keep the qualification VM exclusive for the complete test window.
- [ ] If failures remain, verify the funding-acknowledgement and connection
  failure paths with controlled tests before another runtime change.
- [ ] Pass selected 200 and 500 RPS without lower-tier iteration reruns.
- [ ] Run the complete fixed-image qualification once after upper tiers pass.

### Approved VM shutdown: fixed-image comparison

The user approved the temporary shutdown of `issue320-kind`. It was stopped
before setup and restored after the failed stage. All eight containers that
were running before shutdown were restored. The original Docker context and
Kubernetes context were also restored. No data or unrelated container was
deleted. Rancher remained off, as it was before this check.

The unchanged `5b271273` image ran a fresh selected 30-second 500 RPS stage.
The generator proof passed all 10,000 requests at 1,000 RPS. The gateway
started all 15,000 arrivals and dropped none. It completed 11,462 requests
successfully, for 76.41 percent success. It returned 3,508 `no_healthy_deployments`
responses and 30 other HTTP 503 responses. p95 was 441.00 ms, p99 was
1,040.59 ms, and the queue slope was +3.369 requests per second. These gates
failed. No selected 200 RPS stage, lower tier, or ten-minute stage ran.

Accounting drain failed at 181.08 seconds. All grants and processing queues
were closed or empty. One provider operation remained provisional with a
completed `uncertain` journal result and `service_unavailable` classification.
Each applicable budget scope retained 0.024582 exact provisional capacity.
All windows were safe. Recorded successful charges, including one warmup,
totaled 0.080241 exactly in the facts and all four budget scopes. Do not release
the uncertain operation merely to pass the drain gate.

Memory snapshots across setup, load, and drain recorded 59,967 swap-in pages
and no swap-out pages. At 16 KiB per page, that is about 0.98 GB read from
disk. Native database calls averaged 13.85 ms; their p95 histogram bound was
50 ms and their p99 bound was 100 ms. The request and latency failures remain
despite less paging. This does not establish the cause of the provider failure.

The qualification VM was empty before setup. Another task created
`deltallm-output-tpm-ci-fix-redis` in that VM at 14:39:54 UTC. Gateway arrivals
started at 14:40:30 UTC. The container has no Docker CPU or memory limit;
its Redis server has a 128 MB data limit. Preserve this as a failed comparison,
not a fully isolated result. Do not assume that this container caused the
failure. Do not stop another task's service without authority.

Evidence is in
`artifacts/qualification/native-5b271273-500-isolated-20261006`. The folder
contains raw arrivals, metrics, accounting results, the failed runner log,
host snapshots, VM shutdown and restart logs, and the original workload list.
The later attempt to collect pod logs ran after cluster cleanup and failed;
it supplies no provider-cause evidence. No application code, resource limit,
deadline, safety check, or qualification limit changed for this comparison.

Next, reserve the qualification VM from concurrent test tasks. Capture the
first provider failure with bounded diagnostics on the same image. Verify its
connection and uncertainty paths with controlled tests before a runtime fix.
Do not repeat lower tiers during this work.

### Coordinated exclusive diagnostic

The user approved coordination with the output-TPM and issue-344 chats. Both
chats cleared their services. The next `5b271273` diagnostic recorded only its
two owned kind nodes before arrivals and after drain. The generator proof
passed all 10,000 requests. No other test container overlapped this run.

The 500 RPS stage failed: 2,118/15,000 successful requests, 7,038 dropped
arrivals, 1,118 billing HTTP 503 responses, 870 auth-fallback rejections,
2,884 other HTTP errors, and 972 client connection failures. p95 was
10,001.35 ms and p99 was 10,002.47 ms. The failed 180.56-second drain retained
30 safe provisional operations and four open grants. All terminal and
reporting queues were empty. No provider transport error or cooldown was
captured. Bounded traces instead recorded statement deadlines and pool waits
across terminal and reporting work, plus Redis and accounting readiness
failures. Startup restarts ended before arrivals; no application or provider
restart occurred during load. These observations do not prove the earlier
provider read-error cause.

Host snapshots across setup, load, and drain recorded 168,177 swap-in pages
and 112,196 swap-out pages: about 2.76 GB read and 1.84 GB written at 16 KiB
per page. Peak sampled pod memory was 3,082,850,304 bytes. Peak sampled pod
CPU was about 2.25 cores; samples can miss short CPU peaks. Database samples
showed no lock or I/O waiter, but they can miss short waits. Preserve
`artifacts/qualification/native-5b271273-500-exclusive-cause-20261006` as a
failed, instrumented result, not release qualification. Both VM settings,
original workloads, and original contexts were restored.

- [x] Obtain coordination approval and clear concurrent task services.
- [x] Record exclusive environment checks and the bounded 500 RPS trace.
- [x] Preserve the failed result and restore the other VM and its workloads.
- [x] Test a temporary 6-CPU, 8-GiB VM to leave more host memory headroom.
  Keep the image, pod limits, workload, safety, and latency gates unchanged.
  Record this distinct environment and restore its original 12-GiB setting.
- [ ] If host pressure falls, use that comparison to select the next controlled
  software test. Do not infer a software repair from a noisy failed run.

The first two 8-GiB attempts stopped during dependency setup. No gateway
arrivals ran, so neither attempt is an RPS result. Docker could not resolve
the pinned PostgreSQL registry through the VM's DNS relay. A direct public
DNS probe answered successfully, but temporary Colima DNS flags did not
change the resolver used by Docker. Keep both interrupted folders and their
restoration logs: `native-5b271273-500-8g-exclusive-20261006` and its `-2`
folder under `artifacts/qualification`.

The canonical test loader now checks a cached image by its exact pinned
registry reference and verifies its repository digest before reuse. It never
accepts a tag as proof. A missing image is pulled by digest, then verified.
Wrong, missing, malformed, or oversized proof fails closed. All 70 focused
tool checks passed. A real Docker check verified all three cached fixture
digests without registry access. The gateway image and runtime source remain
unchanged at `5b271273`; this is a host-side test-loader change only.

- [x] Preserve both setup failures without calling them failed RPS stages.
- [x] Verify immutable cached fixtures and test hit, miss, and invalid-proof paths.
- [x] Resume the fresh 8-GiB comparison with the verified fixture cache.

### Completed 8-GiB comparison

The third attempt verified all three cached fixture digests and completed a
fresh uninstrumented 30-second 500 RPS stage on image `5b271273`. It started
all 15,000 requests, completed all successfully, and dropped none. Throughput,
dependency diagnostics, and exact accounting passed. Charges totaled
0.105007, including one warmup, in the facts and all four budget scopes.
The 12.09-second accounting drain closed every grant and provisional operation
and emptied all terminal and reporting queues. Core Redis work was
6.000067 round trips per request. No application CPU throttle was recorded.

This is still a failed qualification stage. p95 was 187.79 ms, above the
150 ms limit. p99 was 299.23 ms, below its 300 ms limit. The middle-window
in-flight slope was +0.9213 requests per second, above its +0.01 limit.
The driver stopped; no 200 RPS, lower tier, or ten-minute stage ran.

Host paging across setup, arrivals, and drain fell to 12,570 swap-in pages
and no swap-out pages: about 0.206 GB read at 16 KiB per page. Peak sampled
pod memory was 3,045,490,688 bytes and CPU was 4.75 cores. These are sampled
values, not proof that no short resource stall occurred. Per five-second
windows, p95 rose from 130.16 ms initially to 197.94 ms at the end, with a
313.91 ms spike in the 10-to-15-second window. Existing phase metrics do not
separately cover the full accounting RPC path. Do not infer a runtime fix
from this environment comparison alone.

Evidence is in `artifacts/qualification/native-5b271273-500-8g-exclusive-20261006-3`.
The original failed and interrupted folders remain unchanged. Both VMs were
restored to their original settings, all eight original running workloads
were restarted, and both original contexts were restored.

- [x] Confirm 500 RPS once on the same image and 8-GiB environment. Preserve
  both results; do not repeat lower tiers or relax any gate.
- [ ] If 500 RPS passes, run selected 200 RPS, then one full fixed-image series.
- [x] If latency still fails, measure the unreported request-path delay before
  changing accounting, pooling, or safety behavior.

### Unchanged 8-GiB confirmation

The fresh `native-5b271273-500-8g-exclusive-20261006-4` confirmation failed.
It completed 14,866 of 15,000 offered requests successfully, dropped 72
arrivals, and returned 62 HTTP 503 responses. p95 was 1,187.54 ms, p99 was
2,251.47 ms, and the in-flight slope was +14.0848 requests per second.
The 12.12-second accounting drain passed with no open grant, provisional
operation, pending terminal, pending reporting partition, or unsafe window.
Facts and all four scopes matched 0.104069 exactly, including one warmup.
Partial-run success identity remains unknown, so the complete economic gate
correctly failed. Do not replace unknown identity with a success assertion.

Host paging across setup, load, and drain was only 2,369 swap-in pages
(about 0.039 GB) and no swap-out pages. This failure cannot be explained by
high host paging alone. Existing metrics recorded 86 Redis acquisition
deadlines and 109 cancellations. Database calls averaged 7.79 ms, while
API finalization queue wait averaged 54.24 ms and its batches averaged
33.89 ms. Per-operation queue time and per-batch service time are different
measurements; do not add them as a proven per-request critical path.

Both results remain unchanged. All original VM settings, workloads, and
contexts were restored after confirmation. Do not rerun 50 or 100 RPS.
Next, keep the 8-GiB environment and capture bounded timing buckets around
local issue, terminal ownership, signed RPC, DNS, and native journal append.
Include fixed-label native RPC queue metrics and guest resource-pressure
totals. Keep all return values, deadlines, safety proofs, and pod limits.
This instrumented cause check is not release qualification.

### Bounded RPC timing result

The exclusive 8-GiB timing diagnostic completed all 15,000 requests without
errors or drops. Exact accounting passed and drained in 12.14 seconds.
It still failed p95 (231.47 ms), p99 (353.21 ms), and queue slope (+1.5822).
Evidence is in `native-5b271273-500-8g-timing-20261006`. Its hooks and all
raw logs are retained with the original unchanged image identity. This is
instrumented evidence, not release qualification.

The complete per-request local finalization path averaged 62.94 ms, versus
114.35 ms total client latency. Across 3,373 API finalization batches,
terminal ownership averaged 31.93 ms, signed RPC 30.43 ms, and native RPC
service time 20.90 ms. The 2,381 native journal batches averaged 11.72 ms
at the database. DNS averaged 3.43 ms over 4,134 accounting calls. These
measures overlap and have different batch counts. They locate a material
finalization delay; do not add them or call DNS the main cause. No provider
transport error, cooldown, or native database deadline was captured.

The guest CPU pressure total increased by 17.97 seconds across the whole
stage, including setup, transfer, and drain. Its CPU pressure avg60 reached
14.56 percent. Guest memory and I/O pressure stayed low. Host paging read
about 0.658 GB and wrote 0.476 GB across setup, load, and drain. Resource
contention remains a contributing possibility. Both original VMs and all
workloads and contexts were restored.

- [x] Capture bounded timing across local finalization, RPC, DNS, and native SQL.
- [x] Verify exclusive ownership and capture guest pressure before and after.
- [x] Check a private two-batch pipeline for shared retained bytes, unchanged
  selected-entry capacity, cancellation, result mapping, and owned shutdown.
  Its first fixtures omitted the existing 4-KiB entry charge and assumed a
  selected-cancellation error type; corrected fixtures use the real charge
  and compare shutdown directly with the original batcher. All five
  self-check groups passed. No production implementation has changed.
- [x] Probe two concurrent API terminal batches in this same environment.
  Share the original queue and byte owner, and split the original selected
  entry budget across both lanes. Keep durable ACKs, deadlines, and all
  monetary proofs. Treat the probe as diagnostic, not qualification.
- [ ] Implement a runtime change only if the controlled probe supports it,
  then test its failure paths and qualify a fresh immutable image.

The private two-batch probe failed and is not a runtime fix. All 15,000
requests succeeded, exact accounting passed, and drain took 12.17 seconds.
However, p95 rose to 419.39 ms, p99 to 528.82 ms, and queue slope to +3.9713.
Local finalization averaged 100.20 ms, signed RPC 55.06 ms, and native
journal SQL 20.98 ms. Compared with the preceding one-batch diagnostic,
these delays worsened. Host paging was low: about 0.041 GB read and no
writes. All original resources were restored. Preserve
`native-5b271273-500-8g-pipeline-probe-20261006` and reject this experiment;
do not add its scheduling change to production.

Fixed cgroup counters showed about 98.74 CPU-seconds for the four API
processes, versus 34.10 for PostgreSQL and 13.21 for both native roles
together. This window includes warmup and transfer, not only arrivals.
Next, capture a three-second CPU profile in one API process under 500 RPS
to identify costly request work. Keep original single-batch scheduling and
all existing limits. The profiler changes timing and cannot qualify release.

- [x] Reject the failed private pipeline; keep runtime source unchanged.
- [x] Identify API CPU hotspots with one bounded profile before another change.

### API CPU profile and immutable queue probe

The three-second profile ran in one API process, with original single-batch
scheduling. It recorded 4,397,647 function calls, 18,457 JSON encodes, 7,132
Python model validations including nested calls, and 8,302 model serializer
calls. JSON encoding was its largest individual internal-time entry. The
profiled process used 2.89 CPU-seconds. These measurements include profiler
overhead; do not treat them as uninstrumented per-request costs.

All 15,000 stage requests succeeded and exact accounting passed, but the
profiled stage failed latency (p95 1,456.36 ms; p99 1,847.82 ms). Its queue
slope was negative, and accounting drained in 12.10 seconds. Preserve
`native-5b271273-500-8g-cpu-profile-20261006` as diagnostic evidence. Both
VM settings, original workloads, and contexts were restored.

Source inspection found that `LocalAccountingService.finalize_operation`
builds a validated `FrozenLocalTerminal`, extracts its document, and drops
the cached fields. `LocalTerminalOwner.finalize_documents` then rebuilds the
same snapshot when the queue drains. The remote and native wire boundaries
still need their separate validation; this API queue reconstruction does not.

The next private probe retains that immutable object through the original
single queue, preserving durable acknowledgements and monetary proofs.
It charges the complete snapshot conservatively inside the original byte
budget. Five existing retry, paid-cache, ownership, and signed-wire checks
passed. A direct snapshot-identity check passed with the repository bootstrap:
one snapshot reaches persistence unchanged and all retained state clears
after acknowledgement. The first standalone private test lacked that
bootstrap and failed collection; its log remains preserved.

This prototype uses the conservative retained charge for collection too.
It does not establish full-width payload compatibility and must not be
shipped as-is. If it helps the benchmark, separate wire-size and retained-
memory accounting and test exact-width and oversized payloads before release.

- [x] Verify the redundant queue-boundary reconstruction in source.
- [x] Verify existing financial paths and one retained snapshot through ACK.
- [x] Run the bounded retained-snapshot 500 RPS probe on the same image.
- [ ] Implement only a supported change, preserve full-width contracts, and
  run source checks and immutable-image qualification before completion.

### Retained-snapshot result and header-only probe

The private retained-snapshot probe failed. It completed 5,199 of 15,000
requests successfully, dropped 1,795 arrivals, and returned 8,006 HTTP 503
responses. p95 was 3,415.45 ms and p99 was 4,116.57 ms. Native database
deadlines came first, followed by accounting readiness failures and then a
provider read error and cooldown. This does not establish that snapshot
reuse fixes the latency problem. Do not ship the private prototype.

Accounting drain missed its 180-second limit with one safely held uncertain
provider operation. All grants and terminal and reporting queues were empty;
no unsafe budget window was recorded. Charges matched all four scopes, but
partial-run success identity remained unknown. Do not release that operation
early or turn an unknown identity into success to pass the test.

Keep `native-5b271273-500-8g-frozen-probe-20261006`, its private proof tests,
and raw logs. Both VMs, original running workloads, and contexts were restored.
No runtime source has changed.

The API profile also recorded task-wrapper overhead in Starlette's base
middleware. The next small private check bypasses that wrapper only for
rate-limit response headers. It forwards the original receive stream, adds
the same headers at response start, and does not buffer response bodies or
create a child task. Admission, billing, deadlines, and accounting stay
unchanged. Profiled cumulative time is a lead, not proof of an unprofiled
percentage saving. Treat this probe as diagnostic, not release qualification.

- [x] Reject the unsupported retained-snapshot prototype and preserve evidence.
- [x] Verify header parity, streaming, cancellation, disconnect, and non-HTTP paths.
  All 29 existing and private checks passed; both injected hooks compiled.
- [x] Run the same bounded 500 RPS header-only diagnostic.
- [ ] Ship only a supported change, then qualify a fresh immutable image.

The header-only diagnostic completed all 15,000 requests with no drops or
errors. Exact accounting passed and drained in 12.11 seconds. It still
failed p95 (173.11 ms) and queue slope (+0.9548); p99 passed at 240.86 ms.
Keep `native-5b271273-500-8g-pure-header-probe-20261006`. This result does
not qualify release or prove that this wrapper is the remaining fix.
All original resources were restored. Runtime source remains unchanged.

### Separate eight-CPU capacity check

Repeated six-CPU checks have shown guest CPU pressure even with low host
paging. The declared pod CPU ceilings also share those six VM CPUs with
the generator and Kubernetes. Next, test the original unchanged image
without private hooks on eight VM CPUs and eight GiB. Keep the exact pod
limits, topology, deadlines, money proofs, and strict pass gates.

This is a new environment comparison, not a repair or a passing result for
the six-CPU profile. Record its actual VM CPU count in the normal manifest
and preserve the six-CPU failures. Stop the other approved VM only during
the run and restore its original running workloads and both contexts.
Only if selected 500 and 200 RPS pass in this environment, run one full
50/100/200/500 fixed-image series there. A complete passing result would
establish capacity on eight CPUs, not on six.

- [x] Run the unchanged-image eight-CPU selected 500 RPS stage.
- [ ] If it passes, run selected 200 RPS without repeating lower tiers.
- [ ] If both pass, run one full fixed-image series and state its resource needs.

The eight-CPU 30-second stage completed 15,000/15,000 requests, with no
drops or errors. p95 was 109.00 ms and p99 was 141.39 ms: both passed.
Exact accounting passed and drained in 12.12 seconds. The stage still
failed because the in-flight slope was +1.1104. In-flight count was mostly
18–26 during the first fifteen seconds, then 23–58 later. Preserve
`native-5b271273-500-8cpu-8g-exclusive-20261006`. This comparison supports
CPU contention as a latency contributor, not a complete qualification pass.
Both VMs and all original running workloads and contexts were restored.

Next, use the driver's existing supported 60-second short duration on the
same image and eight-CPU environment. This doubles offered work and keeps
the exact same latency, slope, diagnostics, drain, and money gates. It
checks whether the observed growth continues beyond thirty seconds;
do not discard failed samples or add an arrival pause. No software code
change or pass limit change is involved. Only proceed to selected 200,
then one full series with this declared 60-second short duration, if each
preceding stage passes.

- [x] Check selected 500 RPS for 60 seconds with the original strict gates.

The 60-second confirmation completed 30,000/30,000 requests, with exact
accounting and a 12.15-second safe drain. p95 passed at 129.86 ms; p99
failed at 533.29 ms, and queue slope failed at +0.09954. In-flight count
mostly stayed near 20–30, with short spikes to 291 and 150. Thus longer
measurement did not pass and cannot replace the failed 30-second result.
Keep `native-5b271273-500-8cpu-8g-60s-exclusive-20261006`. All original
resources were restored; neither selected 200 nor full qualification ran.

No long API or native event-loop pause was recorded. API loop-lag sums
were 0.021–0.047 seconds per process across the whole captured window;
API GC sums were 1.95–2.00 seconds across many short collections. Host
paging across setup, arrivals, and drain read only 118 pages (about
1.93 MB), with no swap writes. These measures do not support host paging
as the explanation for this run's tail spikes. Server response time
tracks client wait; provider and database call p99 upper bounds were
100 ms and 25 ms. They do not separately cover the full terminal RPC wait.

Next, apply the existing bounded wait-path timing diagnostic to this
eight-CPU 60-second environment without the rejected private prototypes.
Use it to locate the long wait before another runtime edit. Keep all
financial checks and strict limits. This trace is not qualification.

- [x] Locate the tail wait with bounded timing on the unchanged image.

The eight-CPU 60-second timing diagnostic failed: 29,937/30,000 successes,
63 HTTP 503 responses, no drops, p95 940.71 ms, p99 1,453.29 ms, and slope
+4.7524. Accounting drained safely in 12.16 seconds. Preserve
`native-5b271273-500-8cpu-8g-60s-wait-timing-20261006`; it is not qualification.
All original resources were restored. Bounded snapshots cover only their
armed interval, not all sixty arrival seconds. Do not report them as complete
stage totals. Local finalization reached 889.93 ms; native materialization
reached 510.56 ms. The trace also captured a journal-append lock timeout.
DNS mean was 2.43 ms and maximum 279.46 ms. It is not a DNS-only failure.

### Narrow terminal-claim capacity locks

Source shows that an ordinary journal claim locks the same partition capacity
row as foreground journal append, even though it changes only journal leases.
It needs that lock only when marking exhausted entries failed and increasing
`failed_entries`. The controlled PostgreSQL 15 check reproduced this coupling:
an ordinary claim timed out behind a held capacity lock. Two exhausted-claim
checks passed before the fix, proving that their counter lock remains needed.

The append-only `20261006120000_accounting_terminal_claim_capacity_locks`
migration restricts the lock's bounded candidate keys to entries with five
attempts. It guards the exact installed function shape and fails closed on
an unexpected definition. It does not add a query, remove a lease/fence or
byte/entry limit, release capacity early, or change monetary calculations.
All 30 full worker checks passed after the migration, including concurrent
claims, retry, replay, expiry, cancellation, corrupt payloads, and exact money.

This removes a demonstrated unnecessary contention point. It is not yet proof
that 500 RPS qualifies. Verify retained-history plans, current/upgrade migration
paths, and affected source gates before sealing a new immutable image.

Rollout uses the existing coordinated Prisma migration job before application
deployment. There is no schema or generated-client change. The explicit
rollback script is `scripts/migration_fixtures/accounting_terminal_claim_lock_rollback.sql`.
It restores the prior, more restrictive lock without deleting or changing
records. Validate rollback and forward reapplication on the owned fixture.
Do not edit applied migration files or manually rewrite migration history.

- [x] Reproduce ordinary-claim contention before editing the function.
- [x] Keep exhausted claims fenced and atomic; pass all 30 worker checks.
- [x] Verify four retained-history planner modes for both claim branches.
  All 54 existing affected PostgreSQL checks and four new exhausted-key
  plan checks passed. The three focused lock checks passed again.
- [x] Verify fresh/upgrade migrations and rollback/forward reapplication.
  Fresh, v0.1.42, and shared-feature upgrade paths passed. Rollback reproduced
  the original contention, and forward reapplication restored all three checks.
- [x] Complete affected source gates and seal a new image.
- [ ] Pass selected upper tiers, then the full strict qualification series.

Source verification completed: 30 worker checks, 54 affected PostgreSQL
checks, four additional plan checks, 93 component checks, and all 1,649
application-route checks passed. The generated client, touched-file lint,
format, and diff checks passed. The application lane reported 147 warnings
from existing test/dependency paths; no runtime Python code was changed.
Keep all before, after, rollback, and confirmation logs in
`artifacts/qualification/verification-terminal-claim-lock-20261006`.
Next, seal the changed migration in a fresh canonical image, verify it,
and run selected 500 then 200 in the declared eight-CPU environment.
Use the same supported 60-second short duration and strict pass gates.
Only start the full series after both upper tiers pass.

The canonical `deltallm-native:issue320-main-7b24cc12` image is sealed from
runtime commit `7b24cc12e116dc048b60f9ad9b16ddeb04b157e6`. Its immutable
index digest is `sha256:fea18e870466af92ffb16f686c5ac119f677f2fa022e6f65101c9db63035e6e2`;
the arm64 platform manifest is
`sha256:3fef4513ef2f5255fbe8f20ce20033c81a139c76b8b3286ea411b943b5f35261`.
All five bounded offline non-root image checks passed. Independently of the
Python source fingerprint, the packaged migration SHA-256 matches the source:
`bfc908fcfeae5261b081e5c8aad38eceba77ce8570133270f680f5e58ec448fd`.

Two builds failed because the VM could not resolve the npm registry. The same
unchanged Dockerfile then built through a temporary loopback-only TLS relay,
restricted to the package and engine endpoints, with normal TLS verification.
No proxy environment is present in the image; the relay was stopped before
qualification. Keep both failures and the successful build in
`artifacts/qualification/native-image-7b24cc12-20261006`.

The first uninstrumented new-image 500 RPS check completed all 30,000
requests without errors or drops. p95 was 86.86 ms and p99 170.41 ms;
both passed. Exact accounting and the 10.16-second safe drain passed,
with no unsettled operations, open grants, pending work, or unsafe windows.
Redis core calls passed at 6.00003/request; diagnostic failures were empty.
The stage still failed only the unchanged middle-window slope gate:
`+0.15214` versus `+0.01` allowed. Keep
`native-7b24cc12-500-8cpu-8g-60s-exclusive-20261006`; do not call it qualified.

Average request latency increased from about 40 ms to 58 ms across the
minute. Existing API terminal-batch means also increased from about 13 ms
to 21 ms; these nested measurements are not pure database execution time.
No sampled lock wait, new checkpoint, or Redis client failure explains it.
Make one unchanged 60-second confirmation before another runtime edit.
Keep the first failure. Advance to selected 200 and the full strict series
only if preceding stages pass. Both original VM settings, eight running
workloads, and global contexts were restored after the first run.

### Avoid indexed checkpoint lease churn

The unchanged confirmation also completed 30,000/30,000 requests, with no
errors/drops, p95 81.52 ms, p99 162.40 ms, exact accounting, and a 10.12-second
safe drain. It failed only strict queue slope (+0.32403). Keep
`native-7b24cc12-500-8cpu-8g-60s-exclusive-20261006-2`; do not repeat lower
tiers or describe either run as qualified. All original resources were restored.

Existing metrics show reporting-claim means increasing from about 1.5 ms
to 5.1 ms during the first run. The lease-expiry index changes on every claim
and completion, although readers scope checkpoint keys by projection and
generation. A controlled owned PostgreSQL fixture compared 8,000 updates:
the index prevented all heap-only updates, created 8,000 dead tuple versions,
and grew the heap from 8 KiB to 224 KiB. Without it, all 8,000 updates were
heap-only, only 48 dead versions remained, and heap size did not increase.
The diagnostic restored the original index. Keep its actual plan/stat logs in
`artifacts/qualification/verification-checkpoint-churn-20261006`.

This proves avoidable write churn, not yet the full cause of queue growth.
Add an append-only guarded migration removing only that secondary index,
retain the composite primary key, and provide an explicit recreation rollback.
No query, lease, deadline, financial calculation, or runtime Python changes.
Use the existing coordinated migration job. Confirm legacy readers and native
readers retain bounded key plans, fence/replay correctness, and exact money.

- [x] Compare real checkpoint churn and restore the original fixture index.
- [x] Preserve a failing retained-history heap-reuse regression before migration.
- [x] Apply the guarded index-only migration and verify native/legacy behavior.
- [x] Verify fresh/upgrade paths and rollback/forward reapplication.
- [x] Seal the new image and verify all five offline checks and migration hashes.
- [ ] Pass selected 500 and 200 with the original strict gates.
- [ ] Pass the full four-tier short and 600-second qualification series.

The original regression failed with 0/2,048 heap-only updates. All nine reporting
plan checks then passed with the guarded index-only migration, including all
four planner modes for retained-history checkpoint churn. All 90 affected
PostgreSQL reporting, race, parity, protocol compatibility, and worker tests
passed. Fresh, v0.1.42, and shared-feature upgrade paths passed. Rollback
recreated the original index and reproduced the expected zero-reuse failure;
forward reapplication restored all four regressions. Touched-test lint, format,
and diff checks passed. The schema/client and all runtime Python are unchanged.
Previous application-route verification still covers the unchanged Python;
do not present this index-only check as a new full application-lane rerun.
Rollback is `scripts/migration_fixtures/accounting_checkpoint_hot_update_rollback.sql`.
Next, seal `20261006130000_accounting_checkpoint_hot_updates` in a fresh image.

The canonical `deltallm-native:issue320-main-1aaf2bda` image is now sealed from
commit `1aaf2bdae8165dc9083d217790280857ee028861`, with index digest
`sha256:c927258c258d910ffa9f79189c84323bb7180a8224568634af15728fefb296bd`
and arm64 platform digest
`sha256:c2a4f56b3fecab039342b03194d204e05251fad6abe03e69dd699ff14bb8c9b1`.
All five offline non-root checks passed. Both migration hashes match source;
the new checkpoint migration SHA-256 is
`b66ea9551d69a0b2aa319e7bad7c1c609f582205450e829f0396d32a7490d1cb`.
The temporary allowlisted build relay is stopped, and no proxy environment
entered the image. The owned PostgreSQL fixture is stopped with data retained.
Run selected uninstrumented 500 for 60 seconds first in the declared eight-CPU
environment, then selected 200 and the full series only on preceding passes.

The uninstrumented `1aaf2bda` cold 500 check completed 30,000 requests with
zero errors/drops, p95 78.41 ms, p99 131.83 ms, exact accounting, and a
12.15-second safe drain. Only strict slope failed (+0.12560). Keep
`native-1aaf2bda-500-8cpu-8g-60s-exclusive-20261006` and all earlier failures.
Both original VMs, exact running workloads, and contexts were restored.

Next use the existing supported selected-tier schedule `200,500` on one
disposable instance. This matches the increasing-load pattern of the canonical
series and tests retained-state behavior without repeating 50 or 100. Both
stages remain 60 seconds, count every request, and use the unchanged financial,
latency, throughput, drain, and slope gates. No added warmup, excluded requests,
or extra cooling delay. The cold failure remains a failure. Advance to one
canonical full series only if both selected stages pass; do not claim the
selected ladder itself is release qualification.

The supported upper ladder passed 200 RPS completely: 12,000 successes,
p95 24.93 ms, p99 37.35 ms, slope 0, and exact accounting/drain. Its 500
stage completed 30,000/30,000 with p95 90.83 ms, p99 137.71 ms, diagnostics,
and exact accounting/drain passing, but slope still failed at +0.05254.
Thus cold start alone does not explain the remaining drift. Preserve
`native-1aaf2bda-upper-8cpu-8g-60s-exclusive-20261006`; full qualification did
not start. All original resources and contexts were restored.

API work remains the largest measured CPU consumer. Next test the previously
checked pure ASGI header forwarding alone on the current image, without any
profiling or timing wrappers. Only the private API header method is overridden;
the accounting image, queues, concurrency, money, resources, and gates remain
unchanged. This comparison is explicitly not release eligible. It must not
erase the old failed header prototype or any cold/warm load failure. Adopt a
runtime change only after the focused behavior checks and measured evidence.

- [x] Verify the header-only comparison and decide whether to adopt it.

The header-only comparison passed 29 behavior checks but worsened 500 RPS:
30,000 successes, p95 148.42 ms, p99 208.03 ms, and slope +0.72378 (failed).
Exact accounting and safe drain passed. Reject this prototype; no header
runtime change is adopted. Preserve its evidence in
`native-1aaf2bda-500-8cpu-8g-60s-header-only-20261006`. All resources restored.

### Remove duplicated reporting discovery

Each of the four existing reporting lanes currently checks every one of the
64 partition heads for every claim. Compare disjoint discovery: lane 0 checks
0,4,...; lane 1 checks 1,5,..., and so on. This keeps all partitions covered,
the same four lanes and pool, bounded key pages, current leases/fences, one
global progress observer, and all original limits and gates. No new queue,
coordinator, worker, or financial calculation. First use a clearly labeled
private SQL-only comparison on the existing sealed image, without timing hooks.
Native adoption requires explicit validated lane scope (not worker-ID parsing).

- [x] Verify full partition coverage and actual four-mode scoped claim plans.
- [x] Compare selected 500 RPS without rerunning lower tiers.
- [x] Decide whether to adopt; reject the private comparison below.
- [ ] Seal any adopted runtime change and pass the strict upper tiers.
- [ ] Run one full four-tier short and 600-second qualification after upper passes.

The private discovery comparison passed 35 focused PostgreSQL checks, including
64-cell discovery under all four planners. Initial private harness failures
(using a closed capture connection and a stale imported query constant) remain
saved alongside the corrected run. At 500 RPS all 30,000 requests succeeded;
p95 99.62 ms, p99 140.43 ms, exact money and a 12.18-second drain passed.
Slope +0.24235 failed; do not adopt the scope override. Preserve
`native-1aaf2bda-500-8cpu-8g-60s-report-discovery-20261006` and the private
checks in `/private/tmp/issue320-report-scope.Aj1C5S`. All resources restored.

### Separate software work from test-machine capacity

A clean isolated terminal experiment compared reconstructing the validated
snapshot with retaining it across the existing queue: 5,000 operations cost
0.76/0.80 seconds versus 0.41/0.40 seconds, with identical wire documents.
This is not gateway throughput proof. The private queue implementation passed
89 checks but failed two existing batching/byte-budget checks, so it is not
adopted. Keep `/private/tmp/issue320-frozen-only.Q5UhBZ`, including harness
errors and behavioral failures. No further load run on this incomplete prototype.

Next use the unchanged sealed `1aaf2bda` image with ten virtual CPUs and 8 GiB
for the whole disposable cluster, up from the previous eight virtual CPUs.
Keep every pod count/limit, queue/deadline, financial proof, and strict gate
unchanged. This is an explicitly different capacity envelope, not an eight-CPU
pass or a software fix. The Mac has fourteen CPU cores. Leave native apps alone
and stop only the already approved other VM. Record the allocation and restore
the original six-CPU/12-GiB VM settings and exact workloads on exit. Run selected
500 first, then selected 200, then the canonical full series only on passes.

- [x] Complete the unchanged-image ten-CPU selected 500 comparison.
- [ ] If it passes, verify selected 200 in that same declared capacity envelope.
- [ ] If upper tiers pass, run all four short and 600-second qualification stages.
- [ ] Record honest resource-specific results and verify full environment restoration.

The ten-CPU unchanged-image comparison completed 30,000/30,000 requests,
zero errors/drops, p95 69.02 ms, p99 109.13 ms, exact accounting, and a
10.14-second safe drain. Only slope +0.14259 failed. Do not claim a ten-CPU
qualification pass, and do not increase CPU again based on this result alone.
Keep `native-1aaf2bda-500-10cpu-8g-60s-exclusive-20261006`. All resources restored.

Next make an unchanged-image 500 RPS diagnostic in the same ten-CPU envelope,
extending only the existing host-side database-counter query. Keep nine fixed
SQL operation groups and seven fixed table groups: 78 additional numeric fields
every five seconds, under the existing snapshot and export limits. No raw SQL
text, request values, application timing wrappers, server settings, or workload
changes. Collect statement execution/call/block counters and estimated table
update/dead-row statistics to distinguish physical churn from shared scheduling.
This is not release qualification; preserve both earlier cold and warm failures.

- [x] Verify the added read-only numeric query against the owned fixture schema.
- [x] Capture the selected 500 SQL-cost diagnostic with the sealed image.
- [x] Identify the growing cost before another runtime change.

The numeric query prepared all 78 fields; the lightweight fixture does not
preload statement statistics, while kind does. The diagnostic executed without
snapshot errors. All 30,000 requests succeeded; latency, exact money, and drain
passed, but slope +0.23904 failed. Keep
`native-1aaf2bda-500-10cpu-8g-60s-sql-costs-20261006`. Reporting claims grew
from about 1 to 16 ms; terminal claims from under 1 to 26 ms. After automatic
cleanup, both dropped sharply. Checkpoint updates were already over 99% heap-only;
the index-removal fix is working. All original resources restored.

### Keep candidate discovery correlated to each frontier

A controlled exact claim with 64 cells and 40,000 finalized source events found
the remaining history join: the first EXISTS is flattened, reading 625 old
events per partition (all 40,000), even when only sequence 40,000 is new.
Generic/custom plans cost 23.20/17.75 ms. Adding OFFSET 0 inside that EXISTS
keeps its LIMIT 1 frontier seek correlated: 3.70/2.90 ms and at most one event
per seek, with unchanged leases and key page. This is query-shape evidence,
not yet an RPS pass. Replanning alone does not fix the history join; do not
change pool/global planner settings. Previous history fixtures mostly supplied
nonterminal events and refreshed statistics, missing this case.

- [x] Preserve a failing actual-repository regression on 40,000 finalized events,
  cold cached plans, no statistics refresh, and all four planner modes.
- [x] Add the single correlated-seek boundary to candidate discovery.
- [x] Pass cold and existing retained-history/race/parity/fence checks.
- [ ] Seal the query fix in a normal image and verify exact-image checks.
- [ ] Recheck upper tiers, then run the full four-tier qualification on passes.

All four original regression cases failed with 625 historical event rows per
partition seek. With OFFSET 0 inside the first EXISTS, all 103 PostgreSQL cold,
retained, race, reporting, protocol, and journal-worker checks passed; all 120
affected component checks passed. Touched-source/test lint, formatting, and diff
checks passed. Preserve all logs in
`artifacts/qualification/verification-correlated-reporting-claim-20261006`.
This is a runtime query-only fix: no migration, client generation, caller planner
settings, deadlines, queues, or financial calculation changed. Seal a fresh
normal image; first compare selected 500 in the existing eight-CPU/8-GiB envelope,
then selected 200 and the canonical full series only on preceding passes.
