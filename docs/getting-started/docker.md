# Docker Deployment

Run DeltaLLM with Docker for a quick, reproducible setup.

## Prerequisites

- Docker and Docker Compose v2+
- A `config.yaml` file copied from `config.example.yaml`

## Using Docker Compose (Recommended)

The project includes a `docker-compose.yaml` with two deployment profiles: **single** (one instance) and **ha** (high availability with load balancing).

## Before You Start

Copy the starter config:

```bash
cp config.example.yaml config.yaml
```

`config.example.yaml` is the curated starter config used by the docs:

- the active settings are the minimum recommended local/dev setup
- secrets come from environment variables
- advanced features such as email, SSO, and governance notifications stay commented until you need them

For the quickest first successful request, enable one-time model bootstrap before starting:

```yaml
general_settings:
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: true
```

That seeds the sample `model_list` into the database on first startup. After the first successful boot, you can set `model_deployment_bootstrap_from_config` back to `false`.

### Single instance

The fastest way to get everything running:

```bash
# Edit config.yaml with your API keys and settings

docker compose --profile single up -d --build
```

Run the command from the repository root so Compose can read the project `.env` file automatically.

On startup, the DeltaLLM container applies the Prisma schema with:

```bash
prisma db push --schema=./prisma/schema.prisma --accept-data-loss
```

Then it starts the API server.

This starts:
- DeltaLLM on port **4000**
- PostgreSQL 15 database
- Redis 7 cache

DeltaLLM is available at `http://localhost:4000`.

Once a model is available, see [Quick Start](quickstart.md) for `curl`, Python, and JavaScript usage examples.

### High availability (multi-instance)

Run two DeltaLLM instances behind an Nginx load balancer:

```bash
docker compose --profile ha up -d --build
```

Each DeltaLLM container applies the Prisma schema with `prisma db push --schema=./prisma/schema.prisma --accept-data-loss` before starting the API.

This starts:
- 2 DeltaLLM instances (load balanced)
- Nginx reverse proxy on port 80
- PostgreSQL database
- Redis cache

DeltaLLM is available at `http://localhost`.

Once a model is available, see [Quick Start](quickstart.md) for `curl`, Python, and JavaScript usage examples.

## Environment Variables

Create a `.env` file in the project root.

!!! warning "Generate the master key and salt key before you start"
    DeltaLLM will not start with placeholder values such as `change-me`.
    You must generate both a unique `DELTALLM_MASTER_KEY` and a unique `DELTALLM_SALT_KEY`.

    Copy and run:

    ```bash
    python3 -c 'import secrets; print("DELTALLM_MASTER_KEY=sk-" + secrets.token_hex(20) + "A1")'
    python3 -c 'import secrets; print("DELTALLM_SALT_KEY=" + secrets.token_hex(32))'
    ```

    Then paste the generated values into your `.env` file.
    `DELTALLM_MASTER_KEY` must be at least 32 characters long and include both letters and numbers.
    `DELTALLM_SALT_KEY` must be a real secret value and must not be `change-me`.

Required for the starter `config.yaml`:

```env
DELTALLM_MASTER_KEY=sk-your-master-key
DELTALLM_SALT_KEY=your-random-salt-key
OPENAI_API_KEY=sk-your-openai-key
```

Recommended if you want to log into the Admin UI with an initial platform admin account:

```env
PLATFORM_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
PLATFORM_BOOTSTRAP_ADMIN_PASSWORD=ChangeMe123!
```

Optional, only if you enable the related features in `config.yaml`:

```env
LAKERA_API_KEY=
DELTALLM_S3_BUCKET=
REDIS_PASSWORD=
RESEND_API_KEY=
SENDGRID_API_KEY=
SSO_CLIENT_ID=
SSO_CLIENT_SECRET=
```

The `docker-compose.yaml` automatically sets these for the bundled services:

- `DATABASE_URL`
- `REDIS_URL`

You do not need to configure them manually for the default Compose setup.

### Optional feature blocks in `config.yaml`

The starter config keeps the common optional features commented out with guidance inline.

- Email delivery: enable when you want invitation emails, password reset, admin test email, or governance notifications
- Governance notifications: opt-in only, and intended to be enabled after email delivery is configured
- SSO: requires Redis and identity-provider credentials
- JWT auth: optional bearer-token validation for proxy traffic
- Guardrails and S3 callbacks: enable only when the related provider credentials are configured

The container applies the Prisma schema automatically on boot, so you do not need a separate schema initialization step for the default Compose setup.

## Custom Config

By default, Compose mounts `./config.yaml` into the container:

```yaml
volumes:
  - ./config.yaml:/app/config/config.yaml:ro
```

## Using the Dockerfile Directly

```bash
docker build -t deltallm .
docker run -p 4000:4000 \
  -e DATABASE_URL="postgresql://..." \
  -e DELTALLM_MASTER_KEY="sk-your-key" \
  -e DELTALLM_SALT_KEY="your-salt-key" \
  -v ./config.yaml:/app/config/config.yaml:ro \
  deltallm
```

The image runs `prisma db push --schema=./prisma/schema.prisma --accept-data-loss` before starting `uvicorn`, so the target database must be reachable when the container starts.

## Health Check

Verify the container is healthy:

```bash
curl http://localhost:4000/health/liveliness
```

List the available models:

```bash
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

If this list is empty, enable one-time bootstrap in `config.yaml` and restart once, or create a deployment in the Admin UI before sending requests.
