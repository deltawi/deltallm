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
| `telemetry` | Required audit/prompt-render acceptance, spend acceptance, bounded synchronous spend fallback and selector operation acceptance | `telemetry_db_pool_size`: 5 |
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
number of operations or transactions and has zero capacity waiters. Excess work
returns a controlled HTTP 503 with code `database_unavailable`. Native connection,
lock and statement timeouts use the same response; record/constraint errors retain
their repository semantics. Local dependency failures never mark a provider unhealthy
or trigger provider retries. A transaction owns one slot through
commit or rollback, and permits only one outstanding native query on that transaction.
Model queries, raw SQL and generated batch execution share this owner. There are no
new per-request SQL calls or client retries.

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

These fields use explicit effective YAML/durable values before environment defaults
and reject live changes. The existing critical endpoint still comes from the startup
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

The production example has 12 maximum API pods, one surge pod, one retiring generation
and one process per pod: 25 peak processes. With both outboxes enabled, this is
`25 × (20 + 8 + 5 + 5) = 950` PostgreSQL connections and
`25 × (64 + 16 + 16) = 2400` Redis connections before reserves. Enabling two fixed
batch-worker pods adds five peak processes: 190 PostgreSQL and 480 Redis connections.
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
critical/cache connection totals. File descriptors, provider quotas/transports,
autoscaling signals and measured N−1 traffic capacity need their own qualification.

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

Required validation includes real PostgreSQL reporting/consumer saturation, lock and
statement deadlines, transaction expiry, cancellation/recovery, and real Redis cache
floods, idle subscriptions, socket failures and memory isolation. Test definitions and
rendered arithmetic are not a concurrency certificate. Publish supported traffic
only with matching workload, commit/image, resources, error rates and latency evidence.
