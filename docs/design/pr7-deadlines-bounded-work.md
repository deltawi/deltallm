# PR7: request deadlines and bounded optional work

Tracking: [issue #320](https://github.com/deltawi/deltallm/issues/320).
Base: `4d75b560` on `feature/issue-320-concurrency`.

## Plan and acceptance

- [x] Extend the existing monotonic request deadline to inference ingress,
  including queue/body/auth/preflight, cache hits, routing, MCP, retries,
  response delivery and normal finalization. Preserve the existing timeout
  envelope, stream framing and cancellation-safe resource cleanup.
- [x] Bound callback delivery by concurrent work, retained events/bytes and
  per-integration time. Snapshot accepted events, shed optional work on overflow,
  observe failures and bound shutdown. Required audit/accounting/business delivery
  stays on its existing durable path.
- [x] Move Presidio inspection to a centrally owned bounded executor. Charge
  running work until actual completion even if its waiter times out or disconnects.
  Apply the same executor contract to synchronous callback adapters, with separate
  allocations so optional telemetry cannot occupy guardrail capacity.
- [x] Synchronize typed startup settings, reload rejection, environment/examples,
  Helm schema/profiles, metrics and operator documentation.
- [x] Review/fix until no actionable items remain. Run focused cancellation,
  overload, streaming and isolation tests, all affected dependency lanes;
  publish reproducible latency/event-loop/retained-work evidence.

## Ownership and invariants

There is one `RequestDeadline` implementation. The ASGI inference edge establishes
its monotonic origin before admission or dependency work; routing may shorten but
never restart or extend that budget. Existing per-operation, connection, pool,
write, chunk-idle read and attempt limits remain subordinate to the total budget.
No SQL, Redis or network round trip is added by deadline propagation.

Expiry stops new provider work and returns the existing safe timeout envelope if
headers have not been sent. A started stream cannot be replaced or retried. Normal
settlement consumes the request budget; cancellation cleanup uses the existing
short resource-release grace, preserving PR6's durable ambiguous-operation record
when a provider outcome or receipt cannot be confirmed. A deadline is not permission
to repeat an external side effect or discard accepted accounting.

CallbackManager remains the callback owner. Optional events are disposable and
process-local. Admission is nonblocking and rejects oversized/full work before
creating a delivery task. Payload snapshots and exceptions must not retain a
request traceback. Configured callbacks are trusted extensions, but delivery must
still have finite task, byte, execution and shutdown limits. Cancellation-resistant
work continues to occupy its allocation; it must never create replacement capacity.

Bootstrap owns guardrail and callback blocking allocations through one executor
implementation. Threads cannot safely be killed: cancellation stops waiting and
discards late results, while the executor retains capacity until actual completion.
Queued cancellation marks work as abandoned without completing its pool future:
the entry retains its count/byte charge until a worker dequeues and skips it.
Shutdown stops admission, cancels queued work and waits only for its declared grace;
remaining running work stays bounded. Required guardrail overload fails closed with
a local unavailable error and never reaches the provider. Registry reloads reuse
the same execution allocation.

## Rollout and qualification

No schema migration or additional database/Redis/client pool is planned. Each API
process owns its finite callback and guardrail allocations; operator documentation
will include process/replica multiplication. Settings that size long-lived workers
are startup-only. Existing router timeout configuration owns the request budget.
Changes to timeout coverage and optional delivery overflow are documented operator
contracts, with explicit metrics. PR7 supplies per-change measurements; production
throughput, autoscaling and pod-loss certification remain in PR9/PR10.

## Design references

- [Python cancellation and deadlines](https://docs.python.org/3.11/library/asyncio-task.html)
- [Executor cancellation and shutdown](https://docs.python.org/3.11/library/concurrent.futures.html)
- [HTTPX timeout dimensions](https://www.python-httpx.org/advanced/timeouts/)
- [PostgreSQL conflict arbitration](https://www.postgresql.org/docs/15/sql-insert.html#SQL-ON-CONFLICT)

## Review and validation disposition

Review found and fixed request finalization escaping through the legacy background
write helper, request contexts retained by offloaded callbacks, missing SDK
retirement on reload, keyword-only callback compatibility, hidden Pydantic payload
state, obsolete guardrail engines retained by reload, and repeated suffix scans in the fallback email regex. Cancellation and
real-HTTP regressions verify these fixes; subsequent code review found no remaining
actionable implementation items in this slice.

CI then exposed an existing billing reservation race: concurrent identical inserts
could violate the selector-event unique index because only operation-ID conflicts
were handled. Insertion now arbitrates both unique indexes, then retains the
locked operation-ID lookup and frozen ownership/snapshot check. Concurrent replay
and injected selector-event collision tests verify one hold/capacity slot and
rejection of a different operation. The transaction deadline, lock order and SQL
call count remain unchanged; no retries or global locks were added.

A subsequent review found that cancelling a queued pool future released accounting
before Python's thread pool removed its work item and payload. Repeated disconnects
behind a blocked worker could therefore exceed both admission bounds. The executor
now marks abandoned work with a thread-safe flag, skips it on dequeue and records
cancelled completion then. Shutdown still physically drains queued entries after
closing admission. This keeps the existing owner and executor instead of adding a
second queue or relying on private thread-pool internals. The trade-off is explicit
load shedding while blocked workers retain abandoned entries; no dependency calls,
retries, configuration limits or public error contracts change.

Regression tests offer 100 distinct 256-KiB payloads behind one blocked worker.
Independent weak references demonstrate that the old code retains all 100 while
reporting no queued work. The fix retains one and rejects the other 99 for both
count and byte bounds, under both caller cancellation and timeout. Tests verify
that skipped functions never run, metrics complete only on dequeue, capacity
recovers after drain, and shutdown removes queued payloads without waiting for the
running worker.

[Measurements](../project/benchmarks/request-work-2026-09-15/README.md) retain raw
HTTP, callback, CPU, dependency and shutdown evidence. Callback retention is bounded
and CPU loop interference improves in the controlled probe. The ordinary HTTP
sample's p95 increases, so this slice makes no throughput or lower-tail-latency
claim. The production profile remains subject to PR9/PR10 qualification.

The implementation uses pure ASGI ingress ownership so it also covers body/auth/cache
work. Bounded threads preserve the existing in-process Presidio engine and callback
interfaces without serializing NLP engines or request objects into a second process
runtime. They cannot preempt arbitrary GIL-holding extensions; those need process
isolation if a strict event-loop latency contract is required. Queue overflow sheds
optional telemetry and fails required guardrail execution closed. Rollback restores
the previous source/config defaults; no accepted durable work or schema is removed.

CI on the PR is the authoritative final gate, including all five test lanes,
migration paths, UI, lint and documentation. Merge only after those checks pass.
