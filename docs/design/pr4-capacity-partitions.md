# Durable telemetry capacity partitions

Status: protocol proposal with a separate **inactive schema preparation** PR under [issue #320](https://github.com/deltawi/deltallm/issues/320). Live admission continues using the singleton. The [measurements](../benchmarks/pr4-admission/README.md) show audit lock pressure at 200 RPS; they justify preparing a design, not a production throughput claim.

## Ownership and durable invariant

The telemetry repositories remain the only owners of enqueue, capacity debit, fenced completion and replay. PostgreSQL owns quotas and event identity. Redis and process memory never grant capacity. Bootstrap retains PR 3's bounded foreground/worker allocations; this design adds no pool or background lifecycle.

Use a fixed layout per queue, with capacity `M`, required reserve `R`, epoch `E` and `N` partitions (`1 <= N <= 64`, `N <= M`). Spend has `R = 0`. For partition `i`, use:

```text
quota(i)       = M // N + (1 if i < M % N else 0)
best_effort(i) = (M - R) // N + (1 if i < (M - R) % N else 0)
0 <= pending(i) <= quota(i)
```

Unique partition IDs, their range check, immutable layout values enforced by a composite foreign key, and row checks enforcing these formulas prove `sum(quota) <= M` and `sum(best_effort) <= M-R`, even while only some partitions exist. No cross-row `CHECK` or request-time history scan is needed. Missing partitions fail closed. An audit best-effort insertion requires **total** partition occupancy after insertion at or below `best_effort(i)`, preserving the existing conservative reserve rule; required events may use all of `quota(i)`. Quotas count queued, retrying, processing and blocked required work. A blocked record does not return its credit.

The preparation migration creates one layout row at most per queue and up to 64 partitions. It permits only `prepared` state and zero pending counts, inserts no rows, and changes no existing outbox or counter. It cannot be used as a second live capacity authority. A later activation migration must replace those guards together with the complete writer/fencing implementation and its tests.

## Proposed admission and completion

The activation implementation must add nullable epoch/partition attribution to both existing outboxes, preserving their global event-ID primary keys. Attribution is immutable after insertion. Select a primary partition using a specified, versioned hash of the stable server-owned operation identity. Every event in an existing same-organization bundle uses that operation's partition. Persist the chosen partition; retries first resolve existing event IDs and never double-debit a different partition. A mixed duplicate/new bundle may debit only newly inserted rows, atomically.

Use two ordered SQL RPCs within the existing allocated transaction, followed by commit:

1. Acquire a shared queue protocol barrier, organization policy locks in sorted order, then the candidate partition locks in numeric order. Read no dependent policy or quota state in this statement.
2. Read the authoritative epoch, policy and partition counter from a fresh snapshot, verify the supported writer protocol, redact the envelope, atomically insert new event IDs and increment only their partition's counter within its ceiling. A protocol mismatch is unavailable, never a fallback to singleton admission.

Keep one deterministic secondary partition as an optional bounded fallback. Acquire both candidate locks in sorted order and select at most one partition for the whole bundle. Do not scan all partitions or retry other partitions after an ambiguous commit. Return the existing queue-full result when neither fits. This can reject while another partition has free space; expose a bounded `partition_full` reason and document the reduced usable capacity for skewed workloads. A bundle larger than any quota must fail before SQL; activation must validate that the configured layout can fit the maximum supported bundle.

Completion releases credits in the same transaction as the existing owner/token-fenced terminal transition. A retried completion releases nothing. Exhausted best-effort work releases its original credit; required exhaustion and replay retain it. Retention must never delete an occupied record. Reconciliation runs off the request path, locks the affected partition, counts its bounded-by-capacity active population using an activation-time index/plan check, and fails closed on mismatches. It never repairs capacity during enqueue.

The normal path removes the global exclusive admission lock and global counter update. It does not add awaited RPCs. Its expected benefit is concurrent commits across partitions, subject to privacy locking and the existing connection budget. This is a design expectation to measure, not a throughput result. Record actual SQL/commit counts, WAL/write amplification, lock waits and accepted/rejected latency before enabling it.

## Privacy and deterministic ordering

All active v2 paths must follow `shared protocol barrier -> sorted policy keys -> sorted partition IDs -> event rows`. Epoch changes take the protocol barrier exclusively and cannot run from a request. Quota completion that does not inspect content needs no policy lock but must never acquire one after holding a partition lock. Consumer batches must establish the policy/partition order before mutating outbox rows; do not reuse an event-first completion path without changing its lock order.

The current privacy inventory is:

| Owner | Protected behavior | Activation requirement |
| --- | --- | --- |
| `OrganizationAdminRepository.upsert/update` | Policy/version mutation and active-envelope redaction | Retain exclusive policy locks through transaction commit; cover create, enable and disable |
| `AuditIngestionRepository.enqueue_bundle` | Fresh policy read and redacted insertion | Candidate for shared policy lock, with a separate fresh read |
| `AuditService` legacy persistence and outbox sink transaction | Policy read, sink write and claimed-envelope redaction | Inventory both paths when changing locks; retain exclusive mode for mutation paths initially |
| Repository `redact_active_for_current_policy`, `redact_pending_for_organization`, `redact_claimed_records` | Scrub stored envelopes | Exclusive lock or verified caller ownership; never acquire partition locks afterward in reverse order |
| Repository single/multiple `lock_content_policy` helpers | Locks used by services and consumers | Keep deterministic ordering and explicit read/write modes |
| Organization deletion and prompt/audit retention | Removal of organization or retained content | Verify deletion barriers, missing-policy redaction and no content resurrection |

This inventory identifies work for activation; it is not permission to change the current lock modes. Before making enqueue locks shared, test every writer listed above concurrently with enqueue, consumers, deletion, disable/re-enable and replay. A waiting policy change must prevent persistence of content forbidden by its committed version. Shared enqueue locks alone do not require shared locks for redaction writers. Until that review passes, retain exclusive privacy locks and explicitly expect one hot organization to remain serialized even with multiple capacity partitions.

## Rebalancing, rollout and rollback

Initial quotas are fixed within an epoch. There is **no automatic grant transfer or per-pod credit cache**. Two candidate partitions bound request-side balancing. Changing `N`, `M` or `R` requires a coordinated drain and a new epoch; this avoids an unproven concurrent credit-transfer protocol. Operationally, stop new admission, drain or retain already accepted work, lock the protocol barrier exclusively, verify counters and install the new layout in one bounded control transaction. Never reset a counter while its accepted work remains. Blocked required work prevents the empty-queue shortcut and must be replayed to completion, or migrated with its exact identity and debit by an explicitly tested migration.

Delivery gates:

1. **Preparation child PR:** append-only migration and Prisma models for inactive layouts/partitions; native formula, foreign-key, duplicate, concurrency and inactive-state tests; fresh/last-release/shared-feature migration checks. Old writers do not reference the new tables. Rollback is an application rollback leaving the unused tables in place.
2. **Activation child PR, before any v2 traffic:** additive event attribution, full repository/worker/replay/reconciliation integration, database-enforced writer fencing, capability/readiness checks, metrics and configuration/Helm parity. The preparation migration alone must not expose an enable flag. Test both old and new binaries, including old enqueue, completion, claim exhaustion, reconciliation and replay attempts after fencing. A stale writer must be rejected by PostgreSQL, not merely by a cached application epoch.
3. **Coordinated cutover:** deploy a compatibility release everywhere while v1 remains active. Stop/drain old writers and terminate/revoke their database sessions or credentials under the release workflow; a same-role old writer cannot be reliably excluded by a process-local flag. Acquire the exclusive protocol barrier and existing v1 queue locks; reconcile/attribute retained records, verify quota coverage, install the database fence and activate epoch E together. Resume only ready v2 writers. A migration failure rolls back the entire transition with v1 still authoritative.
4. **Qualification:** real PostgreSQL multi-writer full/near-full and mixed/hot tests; duplicate and partial-bundle retry; stale epochs/workers; lost acknowledgements; cancellation/process death; privacy mutations; blocked replay; retention and concurrent reconciliation; zero credit drift. Run constant-arrival comparisons with production resources, cold starts, slow consumers and N−1 capacity. More partitions must improve accepted throughput without merely moving contention to policy locks or increasing rejections.
5. **Rollback after activation:** stop admission and fence v2, drain or transactionally transfer every occupied partition into the legacy counter, verify exact event/counter parity, then restore the legacy writer fence. Never roll back to an old binary against an active v2 layout. Do not delete accepted outboxes or advance an epoch to make pending counts appear empty.

The series remains responsible for the activation child PR before it can claim partitioned throughput. Preparation does not fix the measured 200-RPS limit. The benchmark and this explicit gate list keep that remaining work reviewable rather than treating it as completed runtime behavior.

## Storage and operating cost

The preparation adds at most two layout rows and 128 partition rows, no append-only history, no event-table index and no runtime writes. The layout primary key and one composite unique key support the foreign key; partitions need only their composite primary key. Normal vacuum/analyze is sufficient for these bounded tables. Activation adds counter updates per accepted/released bundle and an event-attribution index only after measuring its write cost and representative query plans. Existing outbox retention/archival remains authoritative. No accepted data may be purged to rebalance capacity.

## Preparation migration and validation

The child migration is `20260914100000_telemetry_capacity_partition_preparation`. It creates only `deltallm_telemetry_capacity_layout` and `deltallm_telemetry_capacity_partition`, with matching Prisma models. It does not seed layouts, change existing outboxes, copy history, grant runtime permissions, expose configuration or enable partition admission. Applying it through the coordinated migration workflow is additive; rolling the application back leaves the unused tables in place. No down migration is required or supplied.

Local PostgreSQL 15.19 validation applied all 91 migrations, generated and validated the Prisma client, and passed 32 native schema cases. Those cases cover exact quota/reserve sums at small and maximum BIGINT capacities, invalid layouts, forged quotas/epochs, concurrent duplicate partition grants, activation rejection, foreign-key protection and transactional rollback. They execute the actual migration in isolated schemas. `scripts/verify_migration_paths.py` passed fresh, locally available release `v0.1.42`, and shared-feature upgrade checks; CI fetches full history and selects its latest stable release. The runtime admission regression suite remains required against the expanded schema.

PostgreSQL's [constraint rules](https://www.postgresql.org/docs/15/ddl-constraints.html) motivate the row-local formula plus foreign key rather than a cross-row check. Its [READ COMMITTED snapshot semantics](https://www.postgresql.org/docs/15/transaction-iso.html) require a separate read after waiting for locks; [explicit lock ordering](https://www.postgresql.org/docs/15/explicit-locking.html) governs the protocol barrier and deadlock analysis.
