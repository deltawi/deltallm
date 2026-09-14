# PR 4: durable admission contention

Tracking: [issue #320](https://github.com/deltawi/deltallm/issues/320).

## Contract

PostgreSQL remains the sole durable acceptance authority. An accepted audit or spend event is acknowledged only after its owning transaction commits. Bundles remain scoped to one organization; admission uses a fresh statement snapshot after queue and content-policy locks. Required reserves, stable event IDs, redaction before persistence, fenced completion, blocked retention, and replay identity remain mandatory. The existing transaction owner supplies finite acquisition, lock, statement, and transaction deadlines. Caller cancellation or a lost commit acknowledgement never permits an automatic provider retry.

## Implementation and evidence plan

1. Reproduce the existing admission SQL against isolated, migrated PostgreSQL 15 using PR 3 allocated clients. Record offered/completed rates, per-call samples, SQL counts, lock occupancy, transaction latency, realized events per commit, and queue growth for mixed and hot organizations. Compare claim polling and full/duplicate admission before and after the change.
2. Remove measured unnecessary capacity-row writes without changing lock order, schema, or the durability protocol. Add native failure and concurrency coverage for the whole admission contract.
3. Evaluate the proposed 32-event/2-ms same-organization coalescing window against observed arrivals. Implement it only if measured benefit justifies its extra latency, memory ownership, and shutdown complexity. Decide explicitly whether the singleton requires a separately versioned partition/credit protocol.
4. Run the affected native and application gates, review and fix findings, publish the evidence and PR, and link the acceptance results in the tracking issue.

The repository benchmark measures the durable admission boundary; it must not be presented as a production gateway RPS or Kubernetes pod rating. Final end-to-end capacity certification remains PR 10.

## Decisions and delivered changes

The [reproducible measurements and raw samples](../benchmarks/pr4-admission/README.md) support these decisions:

- **Remove zero-delta writes.** The audit claim capacity update runs only when exhausted best-effort records release slots. Audit enqueue increments capacity only when it inserts an event. Duplicate and full submissions preserve the existing admission result without rewriting or waiting for the capacity row. Spend already used a conditional increment and serves as the unchanged comparison.
- **Keep current bundles; do not add a timed coalescer.** For the recorded after profiles, grouping actual same-organization arrivals over 2 ms would still yield one event per commit. There is no demonstrated saving to offset a collection delay. Existing prompt/audit bundles continue to share one same-organization decision. The proposed memory buffer and its configuration/lifecycle are unnecessary for these measured profiles.
- **Design partitions; retain the current writer until cutover is qualified.** The 200-RPS audit profiles show contention and allocation rejection. The [partition protocol](pr4-capacity-partitions.md) specifies durable quotas, privacy locking, fencing, migration and rollback. A separate child migration prepares inactive, constrained quota tables. It does not switch live writers or claim improved throughput. The runtime cutover and its production qualification remain explicit prerequisites to enabling partitions.
- **Keep privacy lock semantics and ordering.** Queue locks still precede exclusive organization policy locks, and dependent reads remain in a subsequent SQL statement. Policy waits can still hold up other organizations; native tests verify bounded timeout, rollback and recovery. Changing shared/exclusive policy semantics requires an inventory of every privacy writer and a separately reviewed rollout.

The source change adds no awaited call, request-path task, retry, connection pool, index, setting or migration. Required admission still uses two SQL RPCs plus transaction start/commit through PR 3's bounded owner. Successful callers observe committed state; cancellation or unavailable acknowledgement is not success. Retrying the same stable event identity has one durable effect.

## Native acceptance coverage

`tests/test_audit_claim_capacity_postgres.py` exercises both plain Prisma and the production allocated client: empty polls, ordinary claims, required-only exhaustion, reclaims, mixed exhaustion, exactly-once capacity release and rollback under a held capacity-row lock. Fixtures explicitly put work in the past instead of depending on sub-millisecond timestamp rounding.

`tests/test_durable_admission_postgres.py` exercises the production allocated clients with separate concurrent writers: full/near-full bounds, mixed/hot organizations, required reserves, duplicate retries, cancellation while waiting for a lock, lost commit acknowledgements, fresh post-lock policy/redaction, stale-worker fencing, blocked retention, replay identity and policy-timeout recovery. The measurement probe has its own native check that it retains the policy lock and durable-acceptance boundary. Existing telemetry integration coverage also verifies prompt-render bundles and the transaction that atomically replays a record with its operator audit.

## Rollout, rollback and remaining series work

There is no mixed-writer schema transition. Old and new writers retain the same advisory keys, transaction ordering, capacity counter and event identities; a rolling application update is compatible. Rolling back restores unnecessary row writes but preserves all accepted work. Keep the [telemetry rollout contract](../deployment/telemetry-ingestion-rollout.md) and PR 3 allocation settings.

Reopen coalescing only with actual same-organization arrival traces that demonstrate useful batching after accounting for collection delay, bounded bytes/callers/tenants, cancellation and shutdown. Partition activation must follow the linked protocol's delivery gates, including reproduction on a declared deployment profile. The inactive migration is deliberately separate from the live-writer cutover.

PR 4 supplies the contention fixes, measurements, acceptance tests and conditional protocol design; its child PR supplies the additive migration. Neither enables the partition runtime, closes PR 9's Kubernetes deployment documentation, nor closes PR 10's sustained end-to-end, failure, stream and N−1 capacity qualification. The local admission probe excludes auth, Redis, HTTP, providers and consumer throughput; it cannot assign a supported concurrency number to a pod.

## Local validation

Validation used the frozen environment and generated Prisma client. PostgreSQL 15.19 applied all 90 migrations to a fresh isolated database. The full native lane passed after setting that database to UTC, matching CI; its first run exposed seven existing batch scheduling failures with the server in Asia/Riyadh, all of which passed after this environment correction. No batch scheduling source was changed.

```text
uv run --frozen --no-sync pytest -q -m postgres --tb=short --show-capture=no --durations=10
363 passed, 5363 deselected

uv run --frozen --no-sync pytest -q -m hermetic --tb=short --show-capture=no
3680 passed, 3 sandbox socket skips, 2043 deselected

uv run --frozen --no-sync pytest -q tests/test_batch_webhook_delivery.py tests/providers/test_control_transport.py -rs --tb=short --show-capture=no
14 passed with local socket permission; this includes all three previously skipped cases

uv run --frozen --no-sync python scripts/docs/report_health.py --check
92 public pages, no missing headings/images or unnavigated pages

uv run --frozen --no-sync mkdocs build --strict --site-dir /private/tmp/deltallm-pr4-docs-site
Build passed

uv run --frozen --no-sync python scripts/docs/verify_public_site.py /private/tmp/deltallm-pr4-docs-site
Public artifact containment passed
```

Touched Python Ruff checks and formatting checks passed. The PR's CI supplies the full application, Redis, Helm, migration-path, UI and documentation gates on the combined integration-branch diff. These gates do not replace PR 10's deployment load qualification.
