# Installation

Use this guide to run DeltaLLM locally for development, evaluation, or contribution. It covers the backend API, PostgreSQL schema, optional Redis, and the admin UI.

## Choose your path

- Use [Docker Compose](docker.md) if you want the fastest working setup with PostgreSQL and Redis included.
- Use this page if you want a full local installation for development, debugging, or contributing.

!!! tip "Most developers should start with Docker Compose"
    The Docker setup is the quickest route to a working DeltaLLM instance. This page is intentionally focused on the manual local install path.

## Requirements

Before you begin, make sure you have:

- Python 3.11 or later
- Node.js 20 or later
- PostgreSQL 15 or later
- Redis 7 or later (optional for local development, recommended for production)
- At least one provider API key such as OpenAI, Anthropic, Azure OpenAI, or Groq

## 1. Clone the repository

```bash
git clone https://github.com/deltawi/deltallm.git
cd deltallm
```

## 2. Install backend dependencies

The project includes a `uv.lock` file, so `uv` is the recommended installer.

=== "uv (recommended)"

    ```bash
    uv sync --dev
    ```

=== "pip"

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

!!! note
    The backend commands below use `uv run ...`. If you installed with `pip`, run the same commands without the `uv run` prefix.

## 3. Configure environment variables

Create an empty PostgreSQL database first, then export the variables DeltaLLM needs to start.

!!! warning "Generate the master key and salt key before you start"
    DeltaLLM will not start with placeholder values such as `change-me`.
    You must generate both a unique `DELTALLM_MASTER_KEY` and a unique `DELTALLM_SALT_KEY`.

    Copy and run:

    ```bash
    export DELTALLM_MASTER_KEY="$(python3 -c 'import secrets; print(\"sk-\" + secrets.token_hex(20) + \"A1\")')"
    export DELTALLM_SALT_KEY="$(openssl rand -hex 32)"
    ```

    `DELTALLM_MASTER_KEY` must be at least 32 characters long and include both letters and numbers.
    `DELTALLM_SALT_KEY` must be a real secret value and must not be `change-me`.

```bash
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/deltallm"
export DELTALLM_MASTER_KEY="sk-local-1234567890abcdefghijklmnop"
export DELTALLM_SALT_KEY="$(openssl rand -hex 32)"
export OPENAI_API_KEY="sk-your-provider-key"
export PLATFORM_BOOTSTRAP_ADMIN_EMAIL="admin@example.com"
export PLATFORM_BOOTSTRAP_ADMIN_PASSWORD="ChangeMe123!"
```

`OPENAI_API_KEY` is used by the sample model in `config.example.yaml`. If you plan to use a different provider, update `config.yaml` in the next step to match that provider's credentials and base URL.

Redis is optional for local development. If Redis is available, also export:

```bash
export REDIS_URL="redis://localhost:6379/0"
```

Without Redis, DeltaLLM can still start locally, but readiness checks may report a degraded status until Redis is configured.

## 4. Create your local config

Copy the example config and point the application at it:

```bash
cp config.example.yaml config.yaml
export DELTALLM_CONFIG_PATH=./config.yaml
```

The example config is ready for a quick local start:

- It defines a sample `gpt-4o-mini` deployment
- It reads secrets from environment variables instead of hardcoding them
- It uses `model_deployment_source: db_only`, which is the recommended steady-state mode

### Choose how to load your first models

For the getting-started flow, model bootstrap is optional:

- Recommended for the quickest first request: set `model_deployment_bootstrap_from_config: true` so DeltaLLM seeds the sample `model_list` into the database on first startup.
- Recommended for steady-state operations: leave it at `false` and create model deployments later from the Admin UI or API.

If you want the quickest path, update `config.yaml` before starting:

```yaml
general_settings:
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: true
```

After your first successful startup, you can set `model_deployment_bootstrap_from_config` back to `false`.

If you want to route to a different model or provider, edit `config.yaml` now. See the [model configuration guide](../configuration/models.md) for the full reference.

## 5. Initialize Prisma and apply the database schema

Generate the Prisma client artifacts, fetch the required binaries, and apply the schema to your local database:

```bash
uv run prisma generate --schema=./prisma/schema.prisma
uv run prisma py fetch
uv run prisma db push --schema=./prisma/schema.prisma
```

## 6. Start the backend API

```bash
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

DeltaLLM is now available at `http://localhost:8000`.

Useful endpoints:

- Liveliness: `http://localhost:8000/health/liveliness`
- Readiness: `http://localhost:8000/health/readiness`
- OpenAI-compatible API: `http://localhost:8000/v1`

## 7. Start the admin UI

In a second terminal:

```bash
cd ui
npm ci
npm run dev
```

The admin UI runs at `http://localhost:5000` and proxies API requests to the backend on port `8000`.

## 8. Verify the installation

Check that the service is live:

```bash
curl http://localhost:8000/health/liveliness
```

You should receive:

```json
{
  "status": "ok"
}
```

List the available models:

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

If this list is empty, you likely skipped model bootstrap. Either enable `model_deployment_bootstrap_from_config: true` and restart once, or create a deployment from the Admin UI before sending chat requests.

For complete usage examples after a model is available, continue to [Quick Start](quickstart.md).

Send a test chat completion:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Hello from DeltaLLM"}
    ]
  }'
```

## Optional: serve the built UI from the backend

For a single-process local preview, build the UI and let the backend serve it:

```bash
cd ui
npm run build
cd ..
uv run uvicorn src.main:app --host 0.0.0.0 --port 5000
```

In this mode, the backend serves both the API and the built frontend on `http://localhost:5000`.

## Next steps

- [Quick Start](quickstart.md)
- [Configure models and providers](../configuration/models.md)
- [General settings reference](../configuration/general.md)
- [Authentication and SSO](../features/authentication.md)
