# Installation

## Prerequisites

- Python 3.11 or later
- Node.js 20 or later
- PostgreSQL database
- Redis server (optional — enables distributed caching and rate limiting)

## Clone the Repository

```bash
git clone https://github.com/your-org/deltallm.git
cd deltallm
git checkout v2-revamp
```

## Backend Setup

### 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up the Database

Set the `DATABASE_URL` environment variable to your PostgreSQL connection string:

```bash
export DATABASE_URL="postgresql://user:password@localhost:5432/deltallm"
```

Fetch the Prisma engine binaries and push the schema to the database:

```bash
python -m prisma py fetch
python -m prisma db push --schema=prisma/schema.prisma
```

### 3. Configure DeltaLLM

Copy the example configuration:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your LLM provider credentials. At minimum, set:

- `general_settings.master_key` — required for API authentication
- `general_settings.salt_key` — required for API key hashing
- At least one deployment source:
  - Recommended: `general_settings.model_deployment_source: db_only` and add models via Admin UI/API after startup
  - Optional one-time seed: set `model_deployment_bootstrap_from_config: true` with `model_list`, start once, then switch it back to `false`

See the [Configuration Reference](../configuration/index.md) for full details.

### 4. Start Redis (optional)

If you have Redis installed, start it for distributed caching and rate limiting:

```bash
redis-server --daemonize yes
```

The app works without Redis — it falls back to in-memory caching and rate limiting.

### 5. Start the Backend

```bash
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

## Frontend Setup

### 1. Install Dependencies

```bash
cd ui
npm install
```

### 2. Start the Dev Server

```bash
npm run dev
```

The admin UI is available at `http://localhost:5000`. It proxies API requests to the backend on port 8000.

## Production Build

For production, build the frontend and let the backend serve it:

```bash
cd ui && npm run build && cd ..
python -m uvicorn src.main:app --host 0.0.0.0 --port 5000
```

The backend serves the built frontend from `ui/dist/` alongside the API.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `DELTALLM_MASTER_KEY` | Yes (or set in config.yaml) | Master API key for admin access |
| `DELTALLM_SALT_KEY` | Yes (or set in config.yaml) | Salt for hashing API keys |
| `DELTALLM_CONFIG_PATH` | No | Path to config.yaml (default: `./config.yaml`) |
| `REDIS_URL` | No | Redis connection string (enables distributed caching and rate limiting) |
