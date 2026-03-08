# Docker Deployment

See the [Getting Started: Docker](../getting-started/docker.md) guide for basic Docker setup.

## Production Docker Compose

A production-ready `docker-compose.yml`:

```yaml
version: "3.8"

services:
  deltallm:
    build: .
    ports:
      - "4000:4000"
    environment:
      - DATABASE_URL=postgresql://postgres:${POSTGRES_PASSWORD}@db:5432/deltallm
      - REDIS_URL=redis://redis:6379/0
      - DELTALLM_CONFIG_PATH=/app/config/config.yaml
      - DELTALLM_MASTER_KEY=${DELTALLM_MASTER_KEY}
      - DELTALLM_SALT_KEY=${DELTALLM_SALT_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - PLATFORM_BOOTSTRAP_ADMIN_EMAIL=${ADMIN_EMAIL}
      - PLATFORM_BOOTSTRAP_ADMIN_PASSWORD=${ADMIN_PASSWORD}
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./config.yaml:/app/config/config.yaml:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:4000/health/liveliness"]
      interval: 30s
      timeout: 10s
      retries: 3

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: deltallm
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
  redisdata:
```

## Environment File

Create a `.env` file alongside `docker-compose.yml`:

```bash
POSTGRES_PASSWORD=strong-random-password
DELTALLM_MASTER_KEY=replace-me
DELTALLM_SALT_KEY=replace-me
OPENAI_API_KEY=sk-...
ADMIN_EMAIL=admin@company.com
ADMIN_PASSWORD=initial-admin-password
```

Generate working values for the master key and salt key with:

```bash
python3 -c 'import secrets; print("DELTALLM_MASTER_KEY=sk-" + secrets.token_hex(20) + "A1")'
python3 -c 'import secrets; print("DELTALLM_SALT_KEY=" + secrets.token_hex(32))'
```

The generated master key always satisfies DeltaLLM's validator: it is longer than 32 characters and includes both letters and numbers.

## Starting

```bash
docker compose up -d
```

## Upgrading

```bash
docker compose pull
docker compose up -d --build
```

The application container runs the shared database bootstrap script on startup before launching the API. It prefers `prisma migrate deploy` and falls back to `prisma db push` for legacy or unbaselined databases.
