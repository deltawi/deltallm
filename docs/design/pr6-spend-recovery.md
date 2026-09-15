# PR 6: durable spend intent and settlement recovery

Status: implemented and reviewed. Current CI is tracked in [PR 329](https://github.com/deltawi/deltallm/pull/329). Base: `b2dd49d3` on `feature/issue-320-concurrency`.
Tracking: [issue 320](https://github.com/deltawi/deltallm/issues/320).

## Problem and ownership

Ordinary inference first persists its accounting after provider execution. A process
loss before that write leaves no durable evidence; a full queue can reject a charge
for work already admitted. Required failure reporting can also throw while handling
an earlier error, escaping cache middleware as a transport failure.

The spend ingestion service remains the sole acceptance/finalization owner, and its
existing fenced worker remains the sole ledger writer. PostgreSQL is authoritative.
The existing selector billing-operation journal retains its specialized reservation
and selector semantics. Ordinary inference reuses its intent/unknown/receipt protocol
and the canonical spend outbox, without inventing hard monetary reservations or a
second ledger writer. Batch retains its existing completion-outbox owner.

## Implementation checklist

- [x] Add an inactive, separately reviewable schema child: bounded operation intent,
  owner fence, expiry and reconciliation state on the existing spend outbox.
- [x] Reserve one durable outbox slot before ordinary external inference. Record
  verified attribution and frozen deployment/tier pricing for each bounded attempt;
  preserve the existing server-owned event identity across finalization and replay.
- [x] Atomically replace the intent with its frozen receipt in the reserved slot.
  Duplicate acceptance compares identity and payload. No provider retry repairs
  accounting. Cache hits retain their existing single durable acceptance.
- [x] Carve a settlement allocation out of the existing telemetry connection budget;
  admission/reporting/consumers cannot use its slots. Reject new work before external
  execution when durable capacity or admission persistence is unavailable.
- [x] Recover expired unacknowledged intents as explicit unknown outcomes; never
  infer zero cost or repeat external execution. Preserve late valid receipts and
  provide an investigated, idempotent operator reconciliation procedure.
- [x] Keep selector worker/readiness requirements and one owned spend worker; no
  assumption that split batch workers drain API telemetry.
- [x] Convert secondary required-persistence failures to safe local `503` responses,
  preserve cancellation, and avoid provider health/cooldown consequences.
- [x] Test real PostgreSQL admission races, duplicate/ambiguous commits, process loss,
  worker fencing, cancellation/disconnect, terminal stream markers, outages and
  reconciliation. Run all affected application and dependency lanes and migration
  paths and review/fix until no actionable findings. Final CI is tracked in the PR.
- [x] Publish before/after dependency counts, representative plans and controlled
  constant-arrival samples; synchronize settings, Helm, rollout and rollback docs.

## Durable protocol

An intent uses the existing outbox event primary key and occupies one capacity slot.
Its row is blocked from ordinary consumption until it has a receipt. Additional
nullable columns distinguish live intent, unknown outcome and accepted receipt.
Existing binaries already count and retain blocked records. Database constraints
prevent replaying an unresolved intent as an ordinary spend event. New writers are
opt-in until schema deployment and binary rollout are complete.

The request creates a server-owned owner token. Each actual provider attempt records
its immutable, bounded, redacted attribution/pricing snapshot before external I/O.
The request owns the bounded attempt history; it never stores prompts, bodies,
credentials or provider payloads in the intent. Failed or interrupted attempts remain
explicitly ambiguous unless authoritative usage resolves them. A successful aggregate
receipt does not prove that an earlier failed provider attempt was free.

Receipt acceptance validates the owner and attribution, persists frozen usage,
pricing, currency and exact cost, and makes the reserved row claimable in one short
transaction. It does not acquire additional queue capacity or run the ordinary full
queue fallback. Ledger changes and acknowledgement retain the existing transaction
and claim fence. Recovery never calls a provider. Unknown records are retained and
capacity-accounted until investigated; a timeout is not proof of non-execution.

## Budgets and failure policy

The operation feature is startup-only and requires spend outbox plus its worker.
Settlement takes a configured share of `telemetry_db_pool_size`; the remaining share
accepts new work and required audit. Thus the sum of configured PostgreSQL connections
per process and maximum-replica rollout arithmetic does not increase. Every allocation
keeps bounded acquisition and native statement/lock/transaction deadlines.

The hot-path cost is one additional short transaction per provider attempt before I/O.
Receipt acceptance replaces ordinary post-call outbox admission and its global lock.
No database connection or transaction spans provider I/O. The implementation records
exact calls and measured p50/p95/p99 before making throughput claims. Unknown outcomes
and configured queue exhaustion shed new work; they never become implicit success.
Ordinary budget checks remain soft preflight controls.

## Migration, operation and rollback

Use append-only coordinated DDL, fresh/release/shared upgrade fixtures, explicit lock
and statement limits, and no API-startup DDL. The child schema is inactive with old
binaries. Enable only after all replicas support the protocol and the settlement
allocation. Roll back producers with the feature disabled while leaving the new
worker running until known receipts drain. Unknown operations require reconciliation;
do not downgrade the final worker or delete their rows to recover space.

The outbox retains its existing write/index and vacuum/analyze policy. Add only a
partial recovery index bounded to unresolved operations. Completed known receipts use
existing cleanup; unknown work is excluded. Monitor age, reserved slots, unknown and
blocked records, allocation rejection and recovery failures with bounded labels.
Operator reconciliation uses reviewed provider evidence and stable identities, with
no automatic retry of external work and no silent edits to accepted receipts.

### DDL operating budget

The expansion adds nullable columns without a backfill. Its one partial index scans
existing outbox rows, so pause telemetry producers and workers during the coordinated
migration window. Locks wait at most two seconds; any statement exceeding 30 seconds
rolls back the complete transaction. Measure index build duration on a restored copy
at deployment cardinality before release. If it cannot fit that window, do not relax
the timeout: ship a separate concurrently built-index migration with its own invalid
index recovery procedure. Keep existing accepted/blocked records; never truncate to
make the migration fit. The feature stays disabled until this migration succeeds.

## Review and remediation log

First implementation review found that selector receipt writes should also use the
reserved settlement allocation, and that ordinary dispatch must consult the durable
spend-event identity after outbox retention. The application slice routes selector
receipt acceptance to settlement and rejects redispatch of a settled event using one
indexed post-lock lookup. It also adds explicit unknown-transition metrics and
strengthens terminal-frame/disconnect coverage before qualification.

A second review measured retained-history plans and caught a full target-table scan
in bounded expiry recovery. Recovery now locks at most 100 candidates through the
partial expiry index and updates their transaction-local tuple locations directly.
A populated PostgreSQL regression requires that plan and enforces admission/receipt
SQL counts. Admission batches its guarded mutations after a separate lock-only
statement, preserving fresh snapshots while removing unnecessary round trips.
Catalog fallback pricing is now frozen with deployment/tier inputs before dispatch;
HTTP tests mutate both sources during provider I/O and verify the original charge.

Final configuration review moved cross-field cutover validation to resolved startup
settings, preserving file/environment precedence. Worker changes now require restart,
and adding an explicit default to any bound spend setting is rejected before dynamic
config persistence. This avoids committing a config that the live worker cannot apply.

The final review found no remaining actionable items after these fixes. The
[checked-in measurements](../project/benchmarks/spend-recovery-2026-09-15/README.md)
record the additional dependency cost, all HTTP responses, bounded query plans and
complete post-shutdown drain. The one-active-slot profile sheds more requests at
25 offered RPS; no production throughput improvement or capacity certificate is claimed.
