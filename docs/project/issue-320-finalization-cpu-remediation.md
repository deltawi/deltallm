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
- [ ] Commit clean source and pass all five checks on its exact image.
- [ ] Repeat the short 50/100/200/500 RPS ladder with Rancher stopped.
- [ ] Complete all four ten-minute stages on that same image and cluster.
- [ ] Restore Rancher and the user's contexts, then record final evidence.

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
