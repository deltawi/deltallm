# DeltaLLM

Open-source LLM gateway with an OpenAI-compatible API, multi-provider routing, API key management, budgets, caching, and an admin UI.

**Docs:** https://deltallm.readthedocs.io/en/latest

## Quick Start

Use Docker Compose if you want the fastest working setup.

### 1. Clone the repository

```bash
git clone https://github.com/deltawi/deltallm.git
cd deltallm
```

### 2. Create a local config

```bash
cp config.example.yaml config.yaml
```

For the quickest first successful request, enable one-time model bootstrap in `config.yaml`:

```yaml
general_settings:
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: true
```

This seeds the sample `model_list` into the database on first startup. After the first successful boot, set `model_deployment_bootstrap_from_config` back to `false`.

### 3. Generate required secrets

DeltaLLM will not start with placeholder values such as `change-me`.

```bash
python3 -c 'import secrets; print("DELTALLM_MASTER_KEY=sk-" + secrets.token_hex(20) + "A1")'
python3 -c 'import secrets; print("DELTALLM_SALT_KEY=" + secrets.token_hex(32))'
```

Create a `.env` file in the project root:

```env
DELTALLM_MASTER_KEY=sk-your-generated-master-key
DELTALLM_SALT_KEY=your-generated-salt-key
OPENAI_API_KEY=sk-your-openai-key
PLATFORM_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
PLATFORM_BOOTSTRAP_ADMIN_PASSWORD=ChangeMe123!
```

The sample config uses `OPENAI_API_KEY`. If you want a different provider, edit `config.yaml` before starting.

### 4. Start DeltaLLM

```bash
docker compose --profile single up -d --build
```

This starts:

- DeltaLLM on `http://localhost:4000`
- PostgreSQL
- Redis

### 5. Verify the gateway

Check liveliness:

```bash
curl http://localhost:4000/health/liveliness
```

List available models:

```bash
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

If this list is empty, you did not bootstrap a model and must either:

- set `model_deployment_bootstrap_from_config: true` and restart once, or
- create a model deployment in the Admin UI before sending requests

### 6. Send your first request

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Hello from DeltaLLM"}
    ]
  }'
```

### 7. Open the Admin UI

Open `http://localhost:4000`.

If you set `PLATFORM_BOOTSTRAP_ADMIN_EMAIL` and `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD`, you can log in with that initial admin account. You can also keep using the master key for gateway calls.

## Local Development

Use this path if you want to work on the backend or UI locally instead of running the full Compose stack.

### Requirements

- Python 3.11+
- Node.js 20+
- PostgreSQL 15+
- Redis 7+ optional

### 1. Install dependencies

`uv` is the recommended backend installer because the repo includes `uv.lock`.

```bash
uv sync --dev
```

In another shell for the UI:

```bash
cd ui
npm ci
cd ..
```

### 2. Export environment variables

```bash
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/deltallm"
export DELTALLM_CONFIG_PATH=./config.yaml
export DELTALLM_MASTER_KEY="$(python3 -c 'import secrets; print(\"sk-\" + secrets.token_hex(20) + \"A1\")')"
export DELTALLM_SALT_KEY="$(openssl rand -hex 32)"
export OPENAI_API_KEY="sk-your-openai-key"
export PLATFORM_BOOTSTRAP_ADMIN_EMAIL="admin@example.com"
export PLATFORM_BOOTSTRAP_ADMIN_PASSWORD="ChangeMe123!"
```

If Redis is available:

```bash
export REDIS_URL="redis://localhost:6379/0"
```

### 3. Create config and enable one-time bootstrap if needed

```bash
cp config.example.yaml config.yaml
```

For a fresh database, enable one-time bootstrap in `config.yaml` if you want the sample model available immediately:

```yaml
general_settings:
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: true
```

### 4. Initialize Prisma and the database

```bash
uv run prisma generate --schema=./prisma/schema.prisma
uv run prisma py fetch
uv run prisma db push --schema=./prisma/schema.prisma
```

### 5. Start the backend

```bash
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### 6. Start the UI

```bash
cd ui
npm run dev
```

The local development UI runs at `http://localhost:5000` and proxies API requests to the backend on `http://localhost:8000`.

## Features

- OpenAI-compatible endpoints for chat, embeddings, images, speech, transcription, and rerank
- Multi-provider routing and failover
- Admin UI for models, route groups, API keys, organizations, teams, and usage
- RBAC with platform, organization, and team scopes
- Budgets and spend tracking
- Response caching with memory or Redis
- Guardrails and audit logging
- Prometheus metrics and health endpoints

## Useful Links

- [Docker quick start](docs/getting-started/docker.md)
- [Local installation](docs/getting-started/installation.md)
- [Gateway usage examples](docs/getting-started/quickstart.md)
- [Configuration reference](docs/configuration/index.md)
- [Model configuration](docs/configuration/models.md)
- [Authentication](docs/features/authentication.md)

## Testing

```bash
uv run pytest
```

## License

See [LICENSE](LICENSE).
