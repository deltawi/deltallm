# Database pooler (PgBouncer)

By default, each gateway process opens its own PostgreSQL connections for each of its
database pools. Every pod pool counts against PostgreSQL `max_connections`, multiplied
by the peak number of processes, including surge and retiring pods. Most of these
connections are idle most of the time, but the [connection budget](dependency-capacity.md)
must still reserve them. This limits how large you can make the pod pools.

A transaction pooler such as [PgBouncer](https://www.pgbouncer.org/) removes this limit.
Pods connect to the pooler, and the pooler gives each transaction one of a small number
of PostgreSQL connections. With a pooler, PostgreSQL counts only the pooler's server
connections, and you can make the pod pools larger.

The pooler is optional. Without `DELTALLM_DATABASE_POOLER_URL`, nothing changes.

## How the gateway uses the pooler

Each database pool has its own server deadlines (`statement_timeout`, `lock_timeout`
and `idle_in_transaction_session_timeout`). Without a pooler, the gateway sends them as
startup options. A transaction pooler does not send startup options to PostgreSQL, so:

- Each pool connects to its own pooler database (an alias): `control`, `foreground`,
  `telemetry`, `telemetry_worker`, `telemetry_settlement` and `accounting`.
- Each alias sets the deadlines of its pool with `connect_query` when PgBouncer opens a
  server connection.
- At startup, each pool reads the deadlines from the server. If they are not correct,
  the process does not start. A pooler with a stale configuration cannot make the
  gateway run without deadlines.
- Prisma pools add `pgbouncer=true`, and the accounting pool turns off its prepared
  statement cache. Neither keeps named prepared statements between transactions.
- The accounting pool does not send `RESET ALL` when it returns a connection. In
  transaction pooling, that would clear the alias settings for the next client.

These connections do not use the pooler:

- Migrations (`python -m src.prisma_bootstrap` and the Helm migration Job). They keep
  `DATABASE_URL`.
- The external-auth pool (5 connections per API process). It keeps `DATABASE_URL`.

The gateway does not use session-level advisory locks, `LISTEN`/`NOTIFY` or session
`SET` commands, so transaction pooling is safe for all pools.

## Set up PgBouncer

1. Run PgBouncer in `pool_mode = transaction` (CI tests PgBouncer 1.22). Do not set
   `server_reset_query_always = 1`: `DISCARD ALL` clears the alias settings.
2. Generate the aliases from the gateway's own configuration. Run this where the
   gateway's configuration and environment are available, for example in an API pod:

   ```bash
   python -m src.db.runtime.pooler_config --host postgres.internal --port 5432 --dbname deltallm
   ```

   The output is the `[databases]` section, for example:

   ```ini
   [databases]
   deltallm_control = host=postgres.internal port=5432 dbname=deltallm connect_query='SET statement_timeout = 5000; SET lock_timeout = 1000; SET idle_in_transaction_session_timeout = 10000'
   deltallm_foreground = host=postgres.internal port=5432 dbname=deltallm connect_query='SET statement_timeout = 1000; SET lock_timeout = 200; SET idle_in_transaction_session_timeout = 2000'
   ...
   deltallm_accounting = host=postgres.internal port=5432 dbname=deltallm connect_query='SET search_path = "public"; SET statement_timeout = 250; SET lock_timeout = 200; SET idle_in_transaction_session_timeout = 250; SET jit = off'
   ```

   The alias names come from `DELTALLM_DATABASE_POOLER_URL` when it is set. Use
   `--alias 'name_{allocation}'` to choose others.
3. Set the server pool size of each alias (`default_pool_size`, or `pool_size` on each
   line) and `max_db_connections`. The sum over all aliases and all PgBouncer replicas
   is the number of PostgreSQL connections that the gateway can use.
4. Generate the aliases again after you change a database deadline setting
   (`db_*_timeout_seconds` or `accounting_statement_timeout_ms`). All API, batch and
   accounting pods must use the same deadline settings, because they share the
   aliases.

## Configure the gateway

Set `DELTALLM_DATABASE_POOLER_URL` on API, batch-worker and accounting pods. It is a URL
template that contains `{allocation}`:

```bash
DELTALLM_DATABASE_POOLER_URL='postgresql://deltallm:<password>@pgbouncer:6432/deltallm_{allocation}?sslmode=require'
```

- The gateway replaces `{allocation}` with the pool name.
- Do not put `options` in this URL. The gateway refuses it at startup, because
  PgBouncer does not pass it to PostgreSQL.
- Keep `DATABASE_URL` set to the direct PostgreSQL URL for migrations.
- The setting is read only at startup. Restart the pods after you change it.

## Helm

The chart connects to an existing PgBouncer. It does not deploy one.

```yaml
runtime:
  databasePooler:
    enabled: true
    existingSecret:
      name: deltallm-database-pooler
      urlKey: DELTALLM_DATABASE_POOLER_URL
    # PostgreSQL connections that PgBouncer opens in total (all aliases, all replicas).
    serverConnections: 120
    # Client connections that PgBouncer accepts in total (max_client_conn, all replicas).
    maxClientConnections: 4000
```

With the pooler enabled, the chart changes the PostgreSQL budget:

- The pod pools count against `maxClientConnections`, not against PostgreSQL. The
  `postgresql-pooler-client-connections` value in the dependency-capacity ConfigMap
  shows the peak.
- PostgreSQL counts `serverConnections` once, plus the direct pools (migrations, the
  external-auth pool, reserved and other connections).
- The render fails if the peak pod pools are more than `maxClientConnections`, or if
  roles have different database deadline settings.

The migration Job keeps `runtime.database`.

## Sizing

- Pod pools: the in-process pool gate still limits each process. With a pooler, you can
  make `db_pool_size` and the other pools larger, because an idle client connection
  costs PgBouncer very little.
- Server connections: start with the peak number of transactions that run at the same
  time on PostgreSQL. A transaction holds one server connection from `BEGIN` to
  `COMMIT`. The `deltallm_database_caller_hold_seconds` metrics show how long each kind
  of work holds a slot.
- A query that waits in PgBouncer for a free server connection still uses its
  acquisition and statement deadlines. Watch `cl_waiting` and `maxwait` in
  `SHOW POOLS` on PgBouncer, and `deltallm_database_allocation_events_total` on the pods.

## Rollback

Unset `DELTALLM_DATABASE_POOLER_URL` (or `runtime.databasePooler.enabled`) and restart
the pods. They connect directly to PostgreSQL again. Before that, make sure the direct
connection budget fits the pod pools.
