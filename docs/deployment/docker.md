# Docker and Compose Boundaries

The repository's Docker Compose profiles are for local development, evaluation, demos, and
single-host testing. Start with [Getting Started: Docker](../getting-started/docker.md) for the
working commands.

## What the profiles provide

| Profile | Purpose | Production status |
| --- | --- | --- |
| `single` | One API container with bundled PostgreSQL and Redis | Evaluation only |
| `ha` | Two API containers behind Nginx on one host | Multi-instance behavior test; **not** high availability |

The `ha` profile demonstrates load balancing and shared state, but Nginx, PostgreSQL, Redis,
storage, and both application containers still share one Docker host and failure domain. It
uses plain HTTP and development-oriented service exposure. Each application container also
runs the image's migration bootstrap before starting.

## Why it is not a production reference

The checked-in Compose stack does not establish:

- independent host or zone failure domains;
- externally managed, authenticated, TLS-protected PostgreSQL and Redis;
- one coordinated migration workflow before application rollout;
- immutable signed image digests;
- TLS and private operational endpoints;
- non-root/read-only container hardening;
- automated backup/restore verification;
- controlled rolling deployment and rollback; or
- deployment-wide capacity and alert ownership.

Do not copy the Compose file, add `restart: always`, and label the result production-ready.

## Safe evaluation setup

Generate unique secrets and keep them in an uncommitted `.env` file:

```bash
python3 -c 'import secrets; print("DELTALLM_MASTER_KEY=sk-" + secrets.token_hex(20) + "A1")'
python3 -c 'import secrets; print("DELTALLM_SALT_KEY=" + secrets.token_hex(32))'
docker compose --profile single up -d --build
```

Verify only the process probe from the host:

```bash
curl http://localhost:4002/health/liveliness
```

Detailed health and `/metrics` are unauthenticated in the application. Keep the evaluation
stack on a trusted machine and do not bind it directly to an untrusted network.

## Using the image outside Compose

The current image command runs `src.prisma_bootstrap` and then starts Uvicorn. That default is
convenient for one-container evaluation. It is not the production multi-replica migration
contract.

For a production orchestrator:

1. Pin the image by immutable version or digest.
2. Run the release's migration command once in a dedicated job.
3. Wait for migration verification to succeed.
4. Start API and worker replicas with an explicit command that launches the application without
   the image's per-container bootstrap wrapper.

See [Database migrations](database-migrations.md), the
[production checklist](production-checklist.md), and [Kubernetes](kubernetes.md) for the
supported operational shape.

## Single-host acceptance

If you intentionally operate DeltaLLM on one host, document that availability is limited to
that host and independently provide:

- a TLS reverse proxy;
- firewall rules that expose only intended application paths;
- remote encrypted PostgreSQL and object-storage backups;
- Redis authentication and network restriction;
- process supervision and disk monitoring; and
- an exercised restore and upgrade procedure.

That can be an acceptable small installation, but it remains a single failure domain rather
than an HA deployment.
