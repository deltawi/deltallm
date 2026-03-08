# DeltaLLM

An open-source LLM gateway and proxy that provides a unified OpenAI-compatible API for multiple LLM providers. Route requests across OpenAI, Anthropic, Azure OpenAI, and more through a single endpoint with enterprise features built in.

**[Read the full documentation](https://deltallm.readthedocs.io/en/latest)**

## Features

- **Unified API** — OpenAI-compatible endpoints for chat completions, embeddings, image generation, text-to-speech, speech-to-text, and reranking
- **Multi-Provider Routing** — Route requests to OpenAI, Anthropic, Azure OpenAI with strategies like round-robin, latency-based, and cost-based
- **Intelligent Failover** — Automatic retries with exponential backoff, context-window and content-policy aware fallback chains
- **Admin Dashboard** — Full-featured React UI for managing models, API keys, organizations, teams, users, usage analytics, and settings
- **RBAC** — Platform, organization, and team-level role-based access control
- **Authentication** — Session-based login with email/password, optional MFA (TOTP), SSO (Microsoft Entra, Google, Okta, Generic OIDC), and master key fallback
- **Budget Enforcement** — Per-key, per-team, and per-organization spend limits with alerts
- **Guardrails** — Content safety with PII detection (Presidio) and prompt injection detection (Lakera), scoped at global, org, team, or key level
- **Caching** — Response caching with memory, Redis, or S3 backends
- **Observability** — Prometheus metrics, spend tracking, request logging

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL 15+
- Redis 7+ (optional — enables distributed caching and rate limiting)

### 1. Clone the repository

```bash
git clone https://github.com/deltawi/deltallm.git
cd deltallm
```

### 2. Set up the backend

```bash
pip install -r requirements.txt
```

### 3. Set up the database

Make sure PostgreSQL is running, then set the `DATABASE_URL` environment variable:

```bash
export DATABASE_URL="postgresql://user:password@localhost:5432/deltallm"
```

Fetch the Prisma engine binaries and push the schema to the database:

```bash
python -m prisma py fetch
python -m prisma db push --schema=prisma/schema.prisma
```

### 4. Configure DeltaLLM

Copy the example config and edit it with your provider API keys:

```bash
cp config.example.yaml config.yaml
```

Set required environment variables:

```bash
export DELTALLM_CONFIG_PATH=./config.yaml
```

Generate a valid master key and salt key before you start. These commands always produce values that satisfy DeltaLLM's startup requirements:

```bash
export DELTALLM_MASTER_KEY="$(python3 -c 'import secrets; print(\"sk-\" + secrets.token_hex(20) + \"A1\")')"
export DELTALLM_SALT_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

Edit `config.yaml` and set the values under `general_settings`. You can either hardcode them or reference environment variables with the `os.environ/VAR_NAME` syntax:

```yaml
general_settings:
  master_key: "sk-your-master-key"               # or os.environ/DELTALLM_MASTER_KEY
  salt_key: "your-random-salt-key"                # or os.environ/DELTALLM_SALT_KEY
  database_url: "postgresql://user:pass@localhost:5432/deltallm"  # or os.environ/DATABASE_URL
  redis_host: localhost
  redis_port: 6379
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: false
  platform_bootstrap_admin_email: "admin@example.com"
  platform_bootstrap_admin_password: "your-secure-password"
```

For one-time migration from file models, you can temporarily set `model_deployment_bootstrap_from_config: true`, start once, then set it back to `false`.
After startup in `db_only`, manage models through the dashboard/API (`/ui/api/models`).
For Redis, you can provide either `redis_url` (recommended) or `redis_host` + `redis_port`.

If using environment variable references, export them before starting:

```bash
export DATABASE_URL="postgresql://user:pass@localhost:5432/deltallm"
```

### 5. Start Redis (optional)

If you have Redis installed, start it for distributed caching and rate limiting:

```bash
redis-server --daemonize yes
```

The app works without Redis — it falls back to in-memory caching and rate limiting.

### 6. Start the backend

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

The API is now available at `http://localhost:8000`. Check health at `http://localhost:8000/health/liveliness`.

### 7. Start the frontend (development)

```bash
cd ui
npm install
npm run dev
```

The admin dashboard is available at `http://localhost:5000`.

### 8. Build for production

```bash
cd ui
npm run build
```

In production, the backend serves the built frontend from `ui/dist/` on a single port:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 5000
```

## Running with Docker

### Single instance

The fastest way to get everything running:

```bash
# Create a config file
cp config.example.yaml config.yaml
# Edit config.yaml with your API keys and settings

# Start all services (DeltaLLM + PostgreSQL + Redis)
docker compose --profile single up -d
```

DeltaLLM will be available at `http://localhost:4000`.

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

DeltaLLM will be available at `http://localhost`.

### Environment variables for Docker

Create a `.env` file in the project root. These are passed into the container and referenced by `config.yaml`:

```env
OPENAI_API_KEY=sk-your-openai-key
DELTALLM_MASTER_KEY=replace-me
DELTALLM_SALT_KEY=replace-me
```

Generate working values for the master key and salt key with:

```bash
python3 -c 'import secrets; print("DELTALLM_MASTER_KEY=sk-" + secrets.token_hex(20) + "A1")'
python3 -c 'import secrets; print("DELTALLM_SALT_KEY=" + secrets.token_hex(32))'
```

The generated master key always exceeds 32 characters and always contains both letters and numbers.

The `docker-compose.yaml` automatically sets `DATABASE_URL` and `REDIS_URL` to point at the companion PostgreSQL and Redis containers. You do not need to configure those.

### Custom config with Docker

Mount your own config file:

```bash
docker compose --profile single up -d
```

By default, `config.yaml` is mounted as the config:

```yaml
volumes:
  - ./config.yaml:/app/config/config.yaml:ro
```

## API Usage

### Chat Completions

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-your-master-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Embeddings

```bash
curl http://localhost:8000/v1/embeddings \
  -H "Authorization: Bearer sk-your-master-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "text-embedding-3-small",
    "input": "Hello world"
  }'
```

### List Models

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-your-master-key"
```

## Project Structure

```
deltallm/
├── src/
│   ├── main.py                 # FastAPI application entry point
│   ├── config.py               # YAML config loader
│   ├── routers/                # API route handlers
│   │   ├── chat.py             # POST /v1/chat/completions
│   │   ├── embeddings.py       # POST /v1/embeddings
│   │   ├── images.py           # POST /v1/images/generations
│   │   ├── audio_speech.py     # POST /v1/audio/speech
│   │   ├── audio_transcription.py  # POST /v1/audio/transcriptions
│   │   ├── rerank.py           # POST /v1/rerank
│   │   ├── models.py           # GET /v1/models
│   │   ├── health.py           # Health check endpoints
│   │   ├── spend.py            # Spend tracking endpoints
│   │   └── metrics.py          # Prometheus metrics
│   ├── providers/              # LLM provider adapters
│   │   ├── openai.py
│   │   ├── anthropic.py
│   │   └── azure.py
│   ├── router/                 # Request routing strategies
│   ├── billing/                # Spend tracking & budget enforcement
│   ├── guardrails/             # Content safety (Presidio, Lakera)
│   ├── auth/                   # JWT, SSO, RBAC
│   ├── cache/                  # Response caching
│   ├── middleware/             # Auth, rate limiting, error handling
│   ├── db/                     # Database repositories
│   └── ui/                     # Admin UI API endpoints
├── ui/                         # React frontend (see ui/README.md)
├── prisma/
│   └── schema.prisma           # Database schema
├── config.example.yaml         # Example configuration
├── Dockerfile
├── docker-compose.yaml         # Single + HA deployment profiles
├── nginx.conf                  # Nginx config for HA mode
└── requirements.txt            # Python dependencies
```

## Configuration

DeltaLLM is configured via a YAML file. See [`config.example.yaml`](config.example.yaml) for all available options, or visit the [Configuration Reference](https://deltallm.readthedocs.io/en/latest/configuration/) for detailed documentation.

### Key sections

| Section | Description |
|---------|-------------|
| `model_list` | Bootstrap/file deployments (not the primary runtime write path in `db_only`) |
| `router_settings` | Routing strategy, retries, timeouts, cooldown |
| `deltallm_settings` | Callbacks, guardrails, message logging |
| `general_settings` | Master key, database, Redis, cache, SSO, auth session settings, deployment source mode |

### Environment variable references

Config values can reference environment variables:

```yaml
general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
```

## Testing

```bash
pytest tests/
```

## License

Open source. See [LICENSE](LICENSE) for details.
