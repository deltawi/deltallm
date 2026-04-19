# Docker Deployment

See the [Getting Started: Docker](../getting-started/docker.md) guide for the basic local flow.

## Repository Compose File

The checked-in [docker-compose.yaml](../../docker-compose.yaml) is the supported local, evaluation, and demo Compose path. It runs:

- a one-shot `migrate` service that applies pending Prisma migrations with `prisma migrate deploy`
- the default `deltallm` service in Prisma verification mode
- bundled PostgreSQL and Redis services with local-development defaults
- optional HA services behind Nginx through the `ha` profile

It is not a production deployment template: it hardcodes bundled infrastructure and local credentials for convenience.

## Environment File

Create a `.env` file in the repository root and set at least:

```bash
DELTALLM_MASTER_KEY=replace-me
DELTALLM_SALT_KEY=replace-me
OPENAI_API_KEY=sk-...
PLATFORM_BOOTSTRAP_ADMIN_EMAIL=admin@company.com
PLATFORM_BOOTSTRAP_ADMIN_PASSWORD=initial-admin-password
```

Generate working values for the master key and salt key with:

```bash
python3 -c 'import secrets; print("DELTALLM_MASTER_KEY=sk-" + secrets.token_hex(20) + "A1")'
python3 -c 'import secrets; print("DELTALLM_SALT_KEY=" + secrets.token_hex(32))'
```

The generated master key always satisfies DeltaLLM's validator: it is longer than 32 characters and includes both letters and numbers.

## Starting

```bash
docker compose up -d --build
```

With the default host mapping, DeltaLLM is reachable at `http://localhost:4002`.

## Upgrading

```bash
docker compose pull
docker compose up -d --build
```

The `migrate` service runs before the API container starts, and the API container then verifies the migration state before serving traffic.

If you are upgrading a legacy database that was originally initialized with `prisma db push`, use the one-time [Prisma migration runbook](prisma-migration-runbook.md) before relying on the normal deploy path.

## Production Docker Pattern

For production Docker deployments, use the image as a building block rather than the checked-in Compose file:

- run a one-shot migration container with `python -m src.prisma_bootstrap --mode deploy`
- run the long-lived API containers with `DELTALLM_PRISMA_STARTUP_MODE=verify`
- point both at external PostgreSQL and Redis instances managed outside the container stack
- supply real secrets through your orchestrator or secret manager instead of the repo `.env` defaults

For legacy `db push` databases, run the one-time [Prisma migration runbook](prisma-migration-runbook.md) before switching to this contract.

## High Availability

```bash
docker compose --profile ha up -d --build nginx
```

Targeting `nginx` starts the HA stack and its dependencies without also launching the default single-instance `deltallm` service.
