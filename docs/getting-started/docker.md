# Docker Deployment

Run DeltaLLM with Docker for a quick, reproducible setup.

## Using Docker Compose (Recommended)

Create a `docker-compose.yml`:

```yaml
version: "3.8"

services:
  deltallm:
    build: .
    ports:
      - "5000:5000"
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/deltallm
      - DELTALLM_MASTER_KEY=sk-your-master-key
      - DELTALLM_SALT_KEY=your-salt-key
    depends_on:
      - db
      - redis
    volumes:
      - ./config.yaml:/app/config.yaml

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: deltallm
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

volumes:
  pgdata:
```

Start the stack:

```bash
docker compose up -d
```

DeltaLLM is available at `http://localhost:5000`.

## Using the Dockerfile Directly

```bash
docker build -t deltallm .
docker run -p 5000:5000 \
  -e DATABASE_URL="postgresql://..." \
  -e DELTALLM_MASTER_KEY="sk-your-key" \
  -v ./config.yaml:/app/config.yaml \
  deltallm
```

## Configuration

Mount your `config.yaml` into the container at `/app/config.yaml`. Environment variables referenced in the config (e.g., `os.environ/OPENAI_API_KEY`) must be passed to the container.

```bash
docker run -p 5000:5000 \
  -e DATABASE_URL="postgresql://..." \
  -e OPENAI_API_KEY="sk-..." \
  -e DELTALLM_MASTER_KEY="sk-your-key" \
  -v ./config.yaml:/app/config.yaml \
  deltallm
```

## Health Check

Verify the container is healthy:

```bash
curl http://localhost:5000/health
```
