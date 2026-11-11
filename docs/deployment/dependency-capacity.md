---
title: Dependency capacity budgets
description: Reserve PostgreSQL and Redis capacity and check peak deployment connection ceilings.
status: experimental
audience: operators
---

# Dependency capacity budgets

Use separate allocations for gateway authentication, admission and required durable
acceptance so reporting, consumers and optional caches cannot exhaust their connection
slots. The connection ceilings below are allocation budgets, not measured throughput
or a supported concurrency rating. Qualify the complete deployment with real services
and representative traffic before increasing admission or replica limits.

## PostgreSQL ownership and configuration

Infrastructure bootstrap owns four named allocations in an API process with durable
telemetry enabled. The split batch-worker Deployment runs the same infrastructure
bootstrap and opens the same enabled allocations; its background tasks are not extra
pools. Disabling durable ingestion removes both telemetry allocations.

Separate native clients reserve actual connections. A semaphore or priority queue
around one shared native pool cannot reclaim a connection already occupied by a
report or consumer. Each allocation therefore has its own client and a bounded
admission owner. This adds fixed client/engine overhead, which must fit the pod's
memory and the deployment connection budget; it adds no request SQL round trips.

| Allocation | Consumers | Default connections |
| --- | --- | ---: |
| `control` | Configuration, admin/reporting, ordinary repositories, key-invalidation discovery and background control work | `db_pool_size`: 20 |
| `foreground` | Gateway API-key lookup, organization lifecycle authorization/freshness, budget admission and required legacy audit/spend | `db_foreground_pool_size`: 8 |
| `telemetry` | Required audit/prompt-render acceptance, spend admission, bounded synchronous fallback and selector operation acceptance | `telemetry_db_pool_size` minus the enabled settlement share |
| `telemetry_settlement` | Owner-fenced receipts for already-admitted ordinary operations | `spend_settlement_db_pool_size`: 1, included in the telemetry total of 5 |
| `telemetry_worker` | Audit/spend consumers, lease refresh, reconciliation, cleanup, optional audit enqueue, audit retention and selector recovery | `telemetry_worker_db_pool_size`: 5 |

Audit administration reads use the control allocation. Outbox replay administration
uses the worker allocation, including its atomic operator audit. Legacy best-effort
audit persistence and retention use control capacity. Gateway API-key authentication
is distinct from control-plane login, account administration and password reset.
Shared database rows and advisory locks remain shared correctness boundaries:
connection reservation cannot make a foreground transaction pass a lock held by
another transaction. The lock deadline bounds that failure.

Configure startup values in `general_settings` (`config.general_settings` in Helm):

```yaml
general_settings:
  db_pool_size: 20
  db_foreground_pool_size: 8
  telemetry_db_pool_size: 5
  telemetry_worker_db_pool_size: 5
  db_acquisition_timeout_seconds: 0.2
  db_capacity_wait_seconds: 0  # batch worker pods: 2 (Helm default)
  db_lock_timeout_seconds: 0.2
  db_statement_timeout_seconds: 1.0
  db_transaction_timeout_seconds: 2.0
  db_background_lock_timeout_seconds: 1.0
  db_background_statement_timeout_seconds: 5.0
  db_background_transaction_timeout_seconds: 10.0
```

Lock deadlines must be no greater than statement deadlines, which must be no greater
than transaction deadlines. The shorter budgets apply to foreground and telemetry
acceptance; the background budgets apply to control and telemetry workers. Repository
transaction options can shorten the configured transaction/acquisition ceilings.

Capacity admission happens before Prisma: each pool permits at most its configured
number of operations or transactions, plus a bounded number of waiting callers (see
below). Excess work returns a controlled HTTP 503 with code `database_unavailable`. Native connection,
lock and statement timeouts use the same response; record/constraint errors retain
their repository semantics. Local dependency failures never mark a provider unhealthy
or trigger provider retries. A transaction owns one slot through
commit or rollback, and permits only one outstanding native query on that transaction.
Model queries, raw SQL and generated batch execution share this owner. There are no
new per-request SQL calls or client retries.

The control, foreground and telemetry pools each have one waiting position per
connection. When all connections are busy, a call waits for a free one inside its
acquisition deadline (`db_acquisition_timeout_seconds`, default 0.2 seconds). It does
not fail at once. Most queries take a few milliseconds, so a short burst that is
larger than the pool waits a few milliseconds instead of returning HTTP 503. The
deadline does not change: a call that gets no slot in that time still fails with
`database_unavailable`, and a call that finds every waiting position taken fails at
once. Only the background telemetry worker pool fails at once when it is busy.

A query has one budget: the wait for its slot, then its statement deadline. A
transaction has two budgets before its body runs. First it waits up to
`db_acquisition_timeout_seconds` (plus `db_capacity_wait_seconds`, if set) for its
slot. Then it has up to `db_acquisition_timeout_seconds` again to start the native
transaction. With the defaults on API pods, a transaction can take up to 0.4 seconds
before its body runs: 0.2 seconds to get a slot and 0.2 seconds to start. Starting
usually takes a few milliseconds, because a slot always has a free connection.

`db_capacity_wait_seconds` adds a longer wait for control and foreground work: a
waiting call may take up to this many seconds plus the acquisition deadline before it
fails. Keep it at `0` on API pods so request latency stays bounded. Batch worker pods
have no caller waiting on each query, and a busy pool there used to fail whole batch
items, so the Helm chart sets `2` for the batch worker unless you set the value
yourself. Waits on these pools show up as
`deltallm_database_allocation_events_total{outcome="queue_timeout"}` when they expire,
and `outcome="full"` when every waiting position is taken.

Size `db_pool_size` on batch worker pods for the batch work one process runs at once:
`embeddings_batch_worker_concurrency` running items, plus the completion outbox (the
same number, at most 8, when `embeddings_batch_completion_outbox_worker_enabled` is on),
plus 2 for claims, the stale lease sweeper and finalization, plus 1 for each extra slice
the worker runs at once with work-slice scheduling
(`embeddings_batch_worker_max_slices_in_flight` minus 1; a slice that has finished its
items saves its leftovers and job progress while the next one runs).
For example, a concurrency of 16 with the default 2 slices needs a `db_pool_size` of at
least 27. A batch worker logs a warning at startup when its pool is smaller. A bigger
pool counts toward the PostgreSQL connection budget below, so to run more items at once
you can also add batch worker pods instead.

The telemetry acceptance allocation reserves one bounded waiting position per
configured connection for required writes. The settlement allocation reserves 64
waiting positions (or one per connection, if that is more): its receipts follow
provider work that already ran, arrive in bursts when one admission lock wait
releases several requests together, and each takes a few milliseconds, so they
queue instead of turning a paid answer into a 503. With ordinary operation intents
enabled, the default telemetry total of five connections splits into four acceptance
connections/waiters and one settlement connection with 64 waiters.
Each wait uses the acquisition deadline; a transaction then gets the same deadline
again to start. Overflow and expired waits return the same explicit unavailable result. No native
slot is freed while a cancelled operation is still running.

A readiness probe borrows one existing slot and temporarily permits one business
waiter, bounded by the same acquisition deadline. Each allocation permits only one
probe; cancellation retains its slot until native work ends. This prevents health
traffic from immediately shedding settlement on a one-connection allocation.
No extra connection is added. With all allocations enabled, the waiting operations per
process are at most `db_pool_size + db_foreground_pool_size` (control and foreground),
plus the telemetry acceptance connections, plus the larger of 64 and the settlement
connections, plus one during a telemetry worker probe. With the defaults (20, 8, four
acceptance connections and one settlement connection) that is 96, or 97 during a probe:
4,268 at most at the illustrative 44-process rollout ceiling below. A waiting operation
is a suspended coroutine, not a connection or a thread. `db_capacity_wait_seconds`
makes control and foreground waits longer, but it does not add waiting positions.
`deltallm_database_allocation_seconds{operation="readiness"}` measures probe time;
`deltallm_database_allocation_events_total{outcome="queue_timeout"}` records
business acquisition deadlines exceeded in these bounded queues.

PostgreSQL receives native `statement_timeout`, `lock_timeout` and
`idle_in_transaction_session_timeout` options on every connection. Startup verifies
that the server applied them. Prisma also receives finite connect, socket, pool and
interactive-transaction deadlines. Its URL connect/pool timeouts are whole seconds,
so a 0.2-second allocation setting becomes a one-second native connect/pool ceiling;
interactive transaction acquisition supports the finer deadline. The application
also bounds ordinary query callers by acquisition plus statement time. See the
[Prisma connection options](https://docs.prisma.io/docs/orm/v6/overview/databases/postgresql)
and [transaction options](https://prisma-client-py.readthedocs.io/en/stable/reference/transactions/).

Cancellation stops the caller without freeing a slot still occupied by native work.
The pool owner observes and drains the bounded remaining operation. A bounded
expiry task also reclaims an abandoned manually opened transaction and disables its
old client; it is cancelled when normal commit/rollback releases the slot. Abandoned
transaction starts roll back; ambiguous start/commit acknowledgements retain their
slot through the conservative native expiry bound. No failed or uncertain commit is
retried by this client layer. Existing event identities, atomic outbox acceptance,
privacy locks, claim fencing and replay remain the durability boundaries. Required
persistence never falls back to a memory-only acceptance queue.

Set database allocation budgets in the startup file or `DELTALLM_DB_*` /
`DELTALLM_TELEMETRY_WORKER_DB_POOL_SIZE` environment settings. Explicit file values win
for the new allocation fields. Durable configuration must agree with these startup
values; startup fails on a mismatch, and live API changes require a restart, including
adding an explicit default that would override the environment. Existing database URL
selection is preserved. The older `db_pool_timeout` and
`telemetry_db_pool_timeout_seconds` fields remain for configuration compatibility;
allocated clients override those URL waits using `db_acquisition_timeout_seconds`.
TLS, credentials, schema and unrelated URL options are preserved. A proxy must pass
through the required startup options; a proxy that strips them fails validation.
The legacy timeout fields can be removed in a breaking configuration release after
deployment profiles migrate to the allocation deadlines. Direct audit/spend service
constructors retain an optional worker-client argument for compatibility; production
bootstrap always supplies the isolated worker client. Remove that constructor fallback
only after its external callers migrate.

### Find which work holds the database slots

The error "Database capacity is temporarily unavailable" means one of these:

- The pod's own pool had no free slot and no free waiting position. The event
  outcome is `full`.
- The caller waited for a slot for the full queue time. The outcome is
  `queue_timeout`.
- The caller had a slot, but the query did not finish in time. The outcome is
  `caller_deadline`.
- PostgreSQL refused the connection or cancelled the statement.

The first two come from the pod, not from PostgreSQL. To find which work fills the
pool, use these metrics. Each one has the `allocation` label (`control`,
`foreground`, `telemetry`, `telemetry_worker` or `telemetry_settlement`):

| Metric | What it shows |
|---|---|
| `deltallm_database_caller_slots{caller}` | Slots that each kind of work holds now |
| `deltallm_database_caller_hold_seconds{caller}` | How long each kind of work holds a slot. A transaction holds one slot from `BEGIN` to `COMMIT`. |
| `deltallm_database_caller_wait_seconds{caller}` | How long each kind of work waited for a slot |
| `deltallm_database_caller_events_total{caller,outcome}` | `acquired`, `completed`, `rolled_back`, `error`, `full`, `queue_timeout`, `caller_deadline` and `caller_cancelled`, by kind of work. A transaction records one result: `completed` (committed), `rolled_back` (no statement failed) or `error`. |
| `deltallm_database_allocation_waiters` | Callers that wait for a slot now |
| `deltallm_database_allocation_abandoned` | Slots that are still held for a query or transaction whose caller stopped waiting, also during `COMMIT`. The slot stays held until PostgreSQL finishes the work or its timeout expires. |

The `caller` label has a fixed set of values:

| Caller | Work |
|---|---|
| `batch_claim` | Batch worker loop: claims, scheduler reads and other work outside a slice |
| `batch_item` | Item work in a claimed slice that has no more specific class |
| `batch_checkpoint` | Billing checkpoint writes before a provider call |
| `batch_result_save` | Result saves with their completion outbox rows |
| `batch_requeue` | Items given back for a retry or released after a claim |
| `batch_lease` | Item and job lease renewals |
| `batch_progress` | Job progress refreshes |
| `batch_finalize` | Job finalization |
| `batch_outbox` | The completion outbox worker |
| `batch_webhook` | Batch webhook delivery and its observability scan |
| `batch_maintenance` | Stale lease sweeper, retention cleanup, create-session cleanup and scheduler backfill |
| `auth` | API key lookups after a key cache miss |
| `key_last_used` | Key last-used writes |
| `spend` | Spend writes and the spend outbox worker |
| `audit` | Audit writes and the audit outbox worker |
| `cache_invalidation` | The cache invalidation worker |
| `organization_deletion` | The organization deletion worker |
| `readiness` | Readiness probes |
| `other` | Work that has no class yet |

Example queries:

```promql
# Which work holds the batch pool, per pod
max by (pod, caller) (deltallm_database_caller_slots{allocation="control"})

# Which work is refused a slot
sum by (caller, outcome) (rate(deltallm_database_caller_events_total{outcome=~"full|queue_timeout|caller_deadline"}[1m]))

# p95 slot hold time by work
histogram_quantile(0.95, sum by (le, caller) (rate(deltallm_database_caller_hold_seconds_bucket{allocation="control"}[5m])))
```

To make sure that PostgreSQL itself is not the limit, compare these metrics with the
server during the same minutes. Look at CPU, active connections in `pg_stat_activity`
by `application_name` (`deltallm_control`, `deltallm_foreground` and the others), and
statement times in `pg_stat_statements`. Many `full` events while PostgreSQL has free
CPU and connections mean that the pod pool is too small for the work that it runs.
More `caller_deadline` events and longer statement times mean that PostgreSQL is
slow.

## Redis ownership and configuration

```yaml
general_settings:
  redis_critical_max_connections: 64
  redis_cache_max_connections: 16
  redis_bulk_max_connections: 16
  redis_acquisition_timeout_seconds: 0.2
  redis_socket_timeout_seconds: 1.0
  redis_connect_timeout_seconds: 1.0
  redis_bulk_url: null
```

| Allocation | Consumers |
| --- | --- |
| `critical` | Authentication/session state, admission/limits, routing coordination and configuration/governance/audit invalidation |
| `cache` | Prompt, MCP registry, route snapshot and guardrail caches; optional notification deduplication |
| `bulk` | Response-cache reads, writes and administration, including dynamic cache reloads and MCP result-cache use |

Long-lived subscriptions count against critical capacity. No capacity waiters are
allowed. Admission precedes the driver's connection lock and has a finite deadline
covering setup and validation. Native connect/socket deadlines apply; automatic
client retries are disabled. Idle subscriptions use bounded polling without adding
Redis commands. Borrowed clients return their lease without closing the owner's pool.
Pool saturation preserves each consumer's existing failure policy: optional caches
miss/skip, and required controls must not authorize or accept durable work by failure.

These fields use explicit YAML values before environment defaults and reject live
changes. The effective durable limits must match that startup allocation snapshot;
startup fails before opening additional clients when they differ. The check also
covers PostgreSQL pool counts and whether either outbox enables the telemetry pools.
This prevents persisted settings from exceeding the connection counts validated by
Helm. Before rollout, align persisted allocation values and ingestion modes with the
rendered startup configuration. The existing critical endpoint still comes from the startup
file/environment so an older durable endpoint value cannot redirect coordination.
Capacity/deadline/retry options override conflicting URL query parameters.

## Protect critical Redis memory

Both optional allocations use `redis_bulk_url` when provided. With `null`, they use
the existing endpoint through separate client pools. Separate pools or Redis database
numbers do not isolate server eviction. For production isolation, use a separate
cache Redis deployment and provision finite memory plus buffer/client headroom on
both servers. The critical server must use `noeviction`; the cache server may use
`allkeys-lru`. Redis documents the [memory and eviction behavior](https://redis.io/docs/latest/develop/reference/eviction/).

Supply the cache endpoint through the existing secret resolver, for example
`redis_bulk_url: os.environ/DELTALLM_REDIS_BULK_URL` with a Secret-backed environment
variable. The field is secret-typed and the settings API returns `**********`.
Missing, empty or redacted update values preserve it; actual live endpoint writes
return `restart_required`. Set or remove it in startup configuration and restart.

The gateway never changes server memory policy. The eviction regression test requires
two dedicated empty test instances, verifies different server identities, fills the
cache to eviction, verifies critical lease value/TTL and zero critical evictions, then
forces critical `noeviction` pressure and verifies fail-closed rate admission. It
restores server settings and deletes only its own keys. A shared-server deployment
must separately qualify memory and CPU/network contention; client separation alone
is not evidence of isolation.

## Check peak deployment connections

The chart renders a `*-dependency-capacity` ConfigMap and rejects a peak that exceeds
the declared `dependencyCapacity` PostgreSQL/Redis budgets. It calculates each enabled
role from its HPA maximum or fixed replicas, adds surge (rounding percentages up) and
a full retiring-generation allowance, multiplies by processes, then sums its enabled
pools. It includes reserved connections/clients for migrations, operators, other
applications and operating headroom.

```text
peak role processes = (maximum replicas + surge + maximum replicas × retiring generations)
                      × processes per pod
PostgreSQL peak = Σ(peak role processes × enabled database pool sizes) + reserved connections
Redis peak = Σ(peak role processes × all three Redis pool sizes) + reserved clients
```

The production example has 12 maximum API pods, one surge pod, two retiring
generations and one process per pod: 37 peak processes. With both outboxes enabled,
this is `37 × (20 + 8 + 5 + 5) = 1406` PostgreSQL connections and
`37 × (64 + 16 + 16) = 3552` Redis connections before reserves. Enabling two fixed
batch-worker pods adds seven peak processes: 266 PostgreSQL and 672 Redis connections.
Including the production example's 100 operating and two migration connections,
the API-only totals are 1508 PostgreSQL and 3680 Redis; the split-role totals are
1774 PostgreSQL and 4352 Redis. Additional clients must be declared separately.
Both worker and API telemetry jobs share their process allocations. A batch-worker
Deployment is not a distinct telemetry-only process role.

The evaluation overlay pins its bundled PostgreSQL server to 100 connections. One
steady, one surge and one retiring process use `3 × (20 + 8) = 84` connections,
leaving 16 reserved. Enabling outboxes, batch workers or more replicas in that profile
requires resizing the actual database and its declared budget; Helm rejects an
unchanged 100-connection ceiling. Evaluation settings are not a production profile.

`dependencyCapacity.apiProcessesPerPod` and `batchWorkerProcessesPerPod` set
`WEB_CONCURRENCY` and `UVICORN_WORKERS`; rendered pool-size environment values prevent `envFrom` from
silently raising the two legacy pool counts. Duplicate overrides through explicit
`env` and explicit `--workers`/`-w` arguments are rejected. Custom launchers must honor the declared process counts; do not
use a separate `--workers` value or start additional unsized worker processes. The
retiring-generation setting assumes rollouts do not overlap beyond its declared
allowance; increase it if the deployment process permits more overlap.

The supplied maximum-service budgets are illustrative declarations, not changes to
PostgreSQL `max_connections` or Redis `maxclients`, and not proof those connections
are available. Replace them with the actual allocation supplied by the service owner,
including operating headroom, before deployment. The Redis ceiling conservatively
sums all allocations even with separate servers; also check each server's own
critical/cache connection totals. The extended report also checks declared FD,
proxy transport and provider envelopes. Follow the
[saturation autoscaling preflight](saturation-autoscaling.md) to compare those
declarations with physical limits and the custom metrics API. Measured N−1 traffic
capacity remains a separate qualification.

## Rollout, rollback and evidence

Provision the calculated downstream headroom first, then roll the binary/configuration
together. Old replicas still have shared pools, so the isolation guarantee starts only
after every API and worker process is updated. No database schema migration or durable
record rewrite is introduced. Switching the optional Redis endpoint produces cold
caches; critical keys and invalidation channels remain on the original server.

Rollback by restoring the previous binary/configuration and restarting after the
existing durable-telemetry drain procedure. Keep the old and new process pools inside
the retiring budget. Restoring a shared Redis endpoint also restores shared memory
pressure; it does not preserve the isolated-server guarantee.

Observe `deltallm_database_allocation_occupied`,
`deltallm_database_allocation_events_total`, `deltallm_database_allocation_seconds`,
`deltallm_redis_allocation_occupied`, `deltallm_redis_allocation_events_total` and
`deltallm_redis_allocation_acquisition_seconds`. Labels contain fixed allocation and
operation/outcome classes. Correlate them with server connection/lock/eviction metrics,
required acceptance failures, consumer backlog age and request latency.

Readiness probes the critical Redis client and each enabled PostgreSQL allocation,
including foreground and telemetry-worker clients. Missing clients, saturation,
failures and probe timeouts make readiness fail. These checks run concurrently with
one second per dependency: one Redis `PING` and two SQL `SELECT 1` calls in legacy
mode, or four SQL calls with an outbox enabled, per probe. They use the existing
bounded allocations and add no inference-path calls. Optional cache clients do not
gate readiness. Liveness remains independent of all dependencies.

Required validation includes real PostgreSQL reporting/consumer saturation, lock and
statement deadlines, transaction expiry, cancellation/recovery, and real Redis cache
floods, idle subscriptions, socket failures and memory isolation. Test definitions and
rendered arithmetic are not a concurrency certificate. Publish supported traffic
only with matching workload, commit/image, resources, error rates and latency evidence.

The [managed lifecycle](process-lifecycle.md) uses a 90-second pod grace. Serialize
release and scale operations within the declared terminating-pod allowance; live Helm
upgrades reject existing terminating pods, while offline rendering cannot inspect them.
