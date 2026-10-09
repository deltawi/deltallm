# Concurrency runtime design

This decision replaces the separate PR4, PR5, PR6, PR7 and PR9 design notes.
It records the retained contracts, not a production capacity claim.
The upgrade is a draft until current main is integrated and qualified.

## Ownership and alternatives

PostgreSQL is the durable authority for policy, budgets, charges, required audit
and accepted background work. Redis coordinates bounded shared state. Process
memory holds bounded, disposable snapshots and request-local work.

Bootstrap owns clients, pools, executors and supervised workers. Repositories
own SQL and transactions. Helm owns peak deployment capacity arithmetic; startup
checks its versioned report. Neither Redis nor a capacity report grants money.

Keep one owner for each side effect. Do not add a second ledger, an application
autoscaling controller, per-request clients or a separate policy implementation.
The native accounting decision is in
[Accounting protocol v2](accounting-protocol-v2.md).

## Admission and durable acceptance

The dependency-free ingress gate runs before authentication and expensive work.
Authentication fallback and authenticated preflight have separate finite limits.
Final model authorization and budget admission run after request mutation.
Overload returns a local rejection; it does not mark a provider unhealthy.

Required spend and audit acceptance is durable only after transaction commit.
Stable event IDs, fresh reads after locks, redaction before storage and fenced
completion protect replay and tenant policy. Queue locks precede organization
policy locks. Native database tests cover cancellation, lost acknowledgements,
policy changes, full queues and stale workers.

Do not rewrite a capacity row when no credit changes. Do not add a timed
same-organization coalescer without arrival traces that show a benefit after
including its delay, retained bytes and cancellation cost.

The telemetry partition preparation migration remains inactive. It is not the
native accounting partition protocol and does not enable partitioned outbox
writers. Its row constraints preserve exact quota and reserve sums. Activation
would need a separately reviewed database writer fence, stable event attribution,
ordered locks, privacy tests, exact credit reconciliation and a drained cutover.
Never reset an occupied counter or treat this schema as a second live authority.

See [Ingress admission](../deployment/ingress-admission.md) and
[Telemetry ingestion rollout](../deployment/telemetry-ingestion-rollout.md).

## Budgets, prompts and notifications

Budget and policy reads use the authoritative primary and a bounded foreground
allocation. A missing or corrupt budgeted counter is unavailable, not zero.
No admission path scans retained event history or repairs a rollup.
Legacy budget preflight remains soft; hard reservations belong to accounting v2.

The ordinary combined budget snapshot uses one SQL call. Legacy due resets use
a guarded compare-and-swap and reread on a lost race, with at most four scopes
and nine calls under one deadline. First-denial order remains unchanged.
Native recurring resets use the accounting recovery owner instead.

Team/model counter repair is explicit maintenance. It uses complete reviewed
totals, including archived spend, while writers are paused and drained.

Prompt cache fills use server-derived tenant scopes, bounded caches and the
existing cache pool. A cold fill uses one MGET, one SQL query and one pipeline.
Indexed top-one SQL bounds canonical and alias candidates. Epoch invalidation
prevents reuse of a result under a newer local namespace. Redis failure is an
observable cache miss; it is not permission to skip authorization.

Budget notification delivery runs outside admission. Durable intents retain
deduplication, finite capacity, token-fenced leases and delivery deadlines.
Unknown external delivery is not automatically replayed. Accepted work keeps
its compatible delivery owner until it drains. Retention and configuration are
defined in [Budget and prompt performance](../deployment/budget-prompt-performance.md).

## Spend recovery and native accounting

Legacy spend intents reserve a durable outbox slot before provider execution.
They freeze verified attribution, pricing and each bounded provider attempt.
Receipt acceptance replaces that intent in the same slot. Unknown outcomes
retain their credit until reviewed evidence resolves them.

No database connection spans provider I/O. No accounting retry sends paid work
again. Duplicate receipts preserve identity, payload and blocked-worker evidence.
Settlement has reserved database capacity, separate from new acceptance.

Native mode uses budget grants, one-use local permits, durable terminal
acceptance, canonical processing and native reporting. HTTP, charged cache hits,
batch, Realtime and selector calls share that authority. Legacy and native money
owners must not overlap for one generation.

See [Spend recovery](../deployment/spend-recovery.md) and
[Accounting activation and rollback](../deployment/accounting-v2.md).
Keep the activation and reconciliation commands and their real-database tests.

## Deadlines, optional work and lifecycle

One monotonic deadline starts at ingress and covers body, auth, queues, routing,
provider attempts, response delivery and normal finalization. Subsystems may
shorten it, but must not restart it. After response bytes are sent, a stream
cannot be replaced or retried.

Optional callbacks have finite task, byte, execution and shutdown limits.
Required accounting and audit do not use disposable callback delivery.

The central bounded executor owns blocking guardrail and callback work, with
separate allocations. A cancelled waiter does not release capacity still used
by its thread. Queued work keeps its charge until dequeue; late completion is
observed. Threads cannot preempt arbitrary extensions that hold the GIL.

Readiness reflects required dependencies and workers. Drain stops new work,
retains accepted work and finishes or cancels within the declared shutdown bound.
Kubernetes termination grace must cover that bound.
See [Request deadlines](../deployment/request-deadlines.md) and
[Process lifecycle](../deployment/process-lifecycle.md).

## Production capacity and rollout

Count each pool and process across maximum replicas, surge and retiring pods.
Include API, accounting request, projection, batch, migrations, auxiliary HTTP
clients, provider quotas and file descriptors. An allocation does not provision
physical resources or reserve provider RPM, TPM or concurrency.

Production keeps explicit admission, durable acceptance and fail-closed critical
controls. Native accounting has protected request and projection roles.
Autoscaling uses saturation as well as CPU. Missing or stale metrics are
unavailable, not zero. The monitoring release owns the cluster metrics adapter.

Apply coordinated, append-only migrations before compatible application rollout.
Preserve accepted work and its owner during rollback. Inactive additive tables
may remain. Never downgrade a financial owner while accepted work needs it.

Use [Dependency capacity](../deployment/dependency-capacity.md),
[Saturation autoscaling](../deployment/saturation-autoscaling.md) and
[Production RPS requirements](../deployment/production-rps-requirements.md).
Run fresh and supported-upgrade migration checks, all affected dependency lanes,
container and Helm checks, then the unchanged-image load qualification.
Historical records are recoverable through the
[evidence restore procedure](../project/issue-320-rps-reproduction.md#restore-historical-records).
