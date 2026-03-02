# Docker Deployment

Run DeltaLLM with Docker for a quick, reproducible setup.

## Prerequisites

- Docker and Docker Compose v2+
- A `config.yaml` file (copy from `config.example.yaml` and edit)

## Using Docker Compose (Recommended)

The project includes a `docker-compose.yaml` with two deployment profiles: **single** (one instance) and **ha** (high availability with load balancing).

### Single instance

The fastest way to get everything running:

```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your API keys and settings

docker compose --profile single up -d
```

This starts:
- DeltaLLM on port **4000**
- PostgreSQL 15 database
- Redis 7 cache

DeltaLLM is available at `http://localhost:4000`.

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

## Environment Variables

Create a `.env` file in the project root. These are passed into the container and referenced by `config.yaml`:

```env
DELTALLM_MASTER_KEY=sk-your-master-key
DELTALLM_SALT_KEY=your-random-salt-key
DELTALLM_OPENAI_API_KEY=sk-your-openai-key
```

The `docker-compose.yaml` automatically sets `DATABASE_URL` and `DELTALLM_REDIS_URL` to point at the companion PostgreSQL and Redis containers. You do not need to configure those.

## Custom Config

By default, `config.example.yaml` is mounted as the config file inside the container. To use your own config, update the volume mount in `docker-compose.yaml`:

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
