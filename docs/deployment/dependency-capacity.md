---
title: Dependency capacity budgets
description: Reserve Redis connections for coordination and size process and pod ceilings.
status: experimental
audience: operators
---

# Dependency capacity budgets

Use explicit connection allocations to stop response-cache traffic from exhausting
Redis connections needed by authentication, routing, rate limits, and coordination.
These settings are process ceilings, not a measured throughput rating. PostgreSQL
foreground/background isolation and native query deadlines require separate sizing
and validation; Redis connection isolation does not supply them.

## Configure Redis allocations

Set startup values in `general_settings` (`config.general_settings` in Helm):

```yaml
general_settings:
  redis_critical_max_connections: 64
  redis_bulk_max_connections: 16
  redis_acquisition_timeout_seconds: 0.2
  redis_socket_timeout_seconds: 1.0
  redis_connect_timeout_seconds: 1.0
  redis_bulk_url: null
```

Explicit YAML/durable settings override `DELTALLM_REDIS_*` environment values for
these fields. Restart processes after changes; the dynamic configuration API rejects
changes, including adding an explicit default that would override the environment.
The existing critical endpoint still comes from startup file/environment settings;
its selection is preserved when durable configuration contains older endpoint values. Capacity, socket deadlines,
decoding, and zero-retry settings override conflicting Redis URL query parameters.

The critical allocation serves existing Redis consumers except the response cache.
Response-cache reads, writes, and administration use the bulk allocation, including
after dynamic model/configuration reloads. Other optional/control Redis consumers
still share the critical allocation and require further allocation work. Long-lived
pub/sub connections count against critical capacity. No capacity waiters are allowed:
excess work fails immediately. An admitted acquisition has a finite deadline that
includes the driver lock, connection setup, and validation. Native connect/socket
timeouts also apply, and this client layer adds no automatic retries.

The bound precedes the driver's connection lock: otherwise slow connection setup
could retain arbitrarily many waiting callers even with a finite connection count.
Successful checkouts hold their allocation until release. Cancellation and failed
setup return the permit; startup failures and shutdown close owned clients after
the configuration listener stops. The clients are built centrally after durable
startup configuration has loaded. These changes add no Redis commands or SQL calls
per request. See the [redis-py connection implementation](https://redis.readthedocs.io/en/v5.2.1/_modules/redis/asyncio/connection.html)
for driver acquisition and URL behavior; tests use the repository's locked version.

A full bulk pool makes response-cache reads miss and writes skip through the existing
cache failure handling. Critical consumers retain their own declared dependency
failure policies. Do not interpret a connection allocation as permission to bypass
authorization, quota, or durable accounting when a dependency fails.

## Isolate server memory as well as connections

`redis_bulk_url: null` uses the existing Redis endpoint with a separate client pool.
For production memory isolation, point `redis_bulk_url` to a separate response-cache
Redis deployment. Supply credentials using the existing secret-reference mechanism,
for example `redis_bulk_url: os.environ/DELTALLM_REDIS_BULK_URL` with a Secret-backed
environment variable.

Separate client pools or Redis database numbers do not isolate server eviction.
Critical coordination keys must not be evicted by response-cache traffic. Provision
the critical server with a finite `maxmemory`, `noeviction`, measured client/server
buffer headroom, and alerts before memory exhaustion. `noeviction` rejects writes
when full; validate the critical consumers' configured fail mode under that condition.
A separate bulk server can use a bounded cache eviction policy such as `allkeys-lru`.
Redis documents these [memory and eviction semantics](https://redis.io/docs/latest/develop/reference/eviction/).

The gateway does not modify server memory settings. A shared-server deployment still
needs a tested memory budget and cannot claim isolation from bulk memory pressure.
Qualify with separate test instances: fill the bulk cache to eviction, verify critical
lease/counter TTLs and presence, then exhaust critical memory and verify rejection
without a policy bypass. Server CPU and network capacity also remain shared unless
the deployments are separated.

Switching the bulk endpoint starts with a cold response cache. Existing coordination
keys and auth invalidation channels stay on the critical endpoint. Roll back by
restoring the previous bulk endpoint and restarting; old response-cache entries
expire under their existing TTL. No durable data migration or cache flush is needed.

## Calculate peak deployment connections

For each role, count all simultaneously live pods, including rolling-update surge
and retiring pods whose clients are still open, then multiply by processes per pod.
Sum across API pods, batch workers, and any other process using infrastructure
bootstrap. Background tasks in a process share that process's pools; they are not
additional independent pools.

With the example defaults, each process can open 64 critical and 16 bulk connections.
Three API pods plus one surge pod, each running one process, therefore permit up to
256 critical and 64 bulk connections. If one additional retiring pod remains live,
the ceiling becomes 320 critical and 80 bulk connections. Two workers per pod double
those values. A shared Redis endpoint sees the sum of both allocations. Add worker
roles, operator clients, and monitoring before choosing Redis `maxclients` and
application replica limits. Idle pools create connections lazily.

This slice does not add PostgreSQL pools. Existing configured ceilings remain
`db_pool_size` plus `telemetry_db_pool_size` when durable telemetry is enabled,
multiplied by the same peak process count. Required enqueue and telemetry consumers
still share the telemetry pool until foreground/background allocation is configured.

Monitor `deltallm_redis_allocation_occupied`,
`deltallm_redis_allocation_events_total`, and
`deltallm_redis_allocation_acquisition_seconds` by fixed `allocation` labels
(`critical`, `bulk`). Correlate saturation/deadlines with client latency and Redis
connections, command latency, memory, evictions, and errors. Run real Redis outage,
reconnect, cancellation, and mixed cache/coordination load tests under the intended
pod resources before increasing the limits or asserting supported concurrency.
