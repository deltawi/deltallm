# Issue 320: concrete concurrency measures

This is an implementation index, not a release certificate. Use the
[RPS report](issue-320-rps-report.md) for measured results and the
[production guide](../deployment/production-rps-requirements.md) for deployment
requirements. Current main still needs integration and verification.

The original long record and all historical results remain recoverable with the
[archive procedure](issue-320-rps-reproduction.md#restore-historical-records).
The earlier experimental branch and the clean replay used different resources,
limits and images. Do not combine their passing stages into one qualification.

## Test validity

1. Use a disposable kind cluster, a fresh database and one immutable source-matched
   image. Record settings, topology, dependency versions and image identity.
2. Use direct Kubernetes Services and synchronized constant-arrival generator
   shards. Keep the total offered rate fixed and measure generator drops.
3. Generate bounded Prometheus snapshots outside the API event loop.
4. Classify local capacity, queue, pool, lock, timeout and persistence failures
   without exposing request data or marking providers unhealthy.

## Request and accounting work

5. Combine routing reads and successful completion writes in bounded Redis
   operations. Bound negative prompt caching and measure dependency call counts.
6. Separate optional failure diagnostics from required financial and audit
   acceptance. Keep required accounting fail closed.
7. Make immutable accounting events and PostgreSQL the financial authority.
   Retain stable operation IDs, exact money and generation fences.
8. Combine grant assurance and admission in one bounded database operation.
   Reserve database capacity for required acknowledgements.
9. Escrow bounded budget amounts in short-lived grants instead of locking every
   budget window for every ordinary request.
10. Pre-issue one-use permits through one bounded refill owner per subject.
    Never return an ordinal that might have been used.
11. Use the local grant fence and ordinal as dispatch proof. Keep possibly used
    capacity conservative after owner loss; recovery never resends provider work.
12. Keep internal permit responses compact and validate them against the
    caller's frozen reservation within the response byte limit.
13. Accept small, durable terminal journal records on the response path.
    Materialize the canonical result in a bounded worker.
14. Separate the compact validation envelope from bounded terminal documents.
    Keep owner, generation, grant and replay checks on acceptance.
15. Separate accounting request transport from processing, reporting and recovery
    ownership. Scaling transport must not multiply maintenance loops.
16. Give each role finite queues, byte limits, executors and database allocations.
    Count them across maximum replicas and rollout overlap.
17. Bound backlog and health queries independently of retained history.
18. Reconcile unused permit suffixes across request owners with stable identity,
    guarded ownership and conservative lost-acknowledgement handling.
19. Settle economic counters before heavier reporting work. Native processing and
    reporting retain separate fenced ownership.
20. Bound Redis recovery and release bursts so cleanup cannot exhaust critical
    coordination capacity.
21. Run bounded terminal-processing lanes without duplicating worker lifecycles.
22. Size native reporting lanes against terminal throughput and reserved database
    capacity; do not scale them without a deployment-wide budget.
23. Shard reporting rollups to avoid rewriting one hot aggregate for every event.
24. Keep admission guards and deadlines explicit. Resource tuning is a measured
    profile choice, not permission to relax acceptance limits.
25. Skip redundant reconstruction and writes for already settled receipts.
26. Project compact receipts through bounded stages rather than repeatedly
    decoding large documents or scanning completed history.

## Clean replay and later fixes

27. Read the current master-key field without copying all configuration per request.
28. Avoid repeated process-wide logging-cache resets on ordinary requests.
29. Package the async detector required by the HTTP stack in the frozen lock.
30. Keep terminal aggregation stages consistent so accepted results remain visible
    through processing and reporting.
31. Keep short accounting SQL functions out of unnecessary JIT compilation while
    retaining their declared execution policy.
32. Reuse immutable terminal proofs inside a process without changing the wire
    contract, independent ownership or retained byte limits.
33. Compare immutable local financial handles without duplicate JSON encoding.
34. Reduce repeated empty legacy outbox claims while preserving wake-up, recovery,
    readiness and shutdown behavior.
35. Back off empty native processing and reporting claims within a finite discovery
    interval; reset the wait after work or a wake-up.
36. Recheck reporting work after the checkpoint lock. Bound candidate discovery and
    cold claims with indexed key ranges.
37. Protect budget edits and resets with durable policy fences. Keep current-period
    charges and holds; a removed cap cannot be restored by a racing reset.
38. Order terminal event publication and parent locks so reporting cannot skip an
    earlier uncommitted result. Preserve replay and deduplication.
39. Replace repeated upstream connection-pool work with the shared bounded
    transport adapter. Preserve TLS, proxy, cancellation and timeout contracts.

## What is not part of the runtime upgrade

The billing and database folder reorganizations are separate review work.
Historical benchmark archives, old implementation checklists and diagnostic-only
adapters are not production dependencies. Financial repair commands, migrations,
failure tests and production deployment checks remain required.

Rejected probes remain in the archive. No failed gate was changed or removed.
No new RPS qualification is implied by this cleanup.

## Merge and release order

1. Integrate current main without removing its output-token, authentication or
   migration-recovery features.
2. Pass regression, real-dependency, migration, container and Helm checks.
3. Build one unchanged image and run the normal 50/100/200/500 RPS schedule.
4. Use the accounting activation, drain and rollback procedure before production.

See the [runtime decision](../design/concurrency-runtime.md) and
[accounting runbook](../deployment/accounting-v2.md).
