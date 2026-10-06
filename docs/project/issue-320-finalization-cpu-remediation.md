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
- [ ] Prove the detector exists in the exact non-root image.
- [x] Match native API and request aggregation within the existing batch bounds.
  Check effective settings and preserve all financial mutation-isolation tests.
- [ ] Run focused tests, full affected lanes, collection, lint, chart profiles,
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
- [ ] Pass the exact-image checks and fixed-image load gates.

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
