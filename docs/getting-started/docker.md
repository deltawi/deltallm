# Docker Deployment

Run DeltaLLM with Docker for a quick, reproducible setup.

## Prerequisites

- Docker and Docker Compose v2+
- A `config.yaml` file (copy from `config.example.yaml` and edit)

## Using Docker Compose (Recommended)

The project includes a `docker-compose.yaml` with two deployment profiles: **single** (one instance) and **ha** (high availability with load balancing).

## Before You Start

Copy the example config:

```bash
cp config.example.yaml config.yaml
```

For the quickest first successful request, set this in `config.yaml` before starting:

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

docker compose --profile single up -d
```

This starts:
- DeltaLLM on port **4000**
- PostgreSQL 15 database
- Redis 7 cache

DeltaLLM is available at `http://localhost:4000`.

Once a model is available, see [Quick Start](quickstart.md) for `curl`, Python, and JavaScript usage examples.

### High availability (multi-instance)

Run two DeltaLLM instances behind an Nginx load balancer:

```bash
docker compose --profile ha up -d
```

This starts:
- 2 DeltaLLM instances (load balanced)
- Nginx reverse proxy on port 80
- PostgreSQL database
- Redis cache

DeltaLLM is available at `http://localhost`.

Once a model is available, see [Quick Start](quickstart.md) for `curl`, Python, and JavaScript usage examples.

## Environment Variables

Create a `.env` file in the project root.

Required for the sample `config.yaml`:

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
```

The `docker-compose.yaml` automatically sets these for the bundled services:

- `DATABASE_URL`
- `REDIS_URL`

You do not need to configure them manually for the default Compose setup.

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

## Health Check

Verify the container is healthy:

```bash
curl http://localhost:4000/health/liveliness
```
