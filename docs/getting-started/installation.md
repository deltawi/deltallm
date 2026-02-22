# Installation

## Prerequisites

- Python 3.11 or later
- Node.js 20 or later
- PostgreSQL database
- Redis server

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

Generate the Prisma client and push the schema:

```bash
prisma generate
prisma db push
```

### 3. Configure DeltaLLM

Copy the example configuration:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your LLM provider credentials. At minimum, set:

- Your master key in `general_settings.master_key`
- At least one model in `model_list`

See the [Configuration Reference](../configuration/index.md) for full details.

### 4. Start Redis

```bash
redis-server --daemonize yes
```

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
| `DELTALLM_MASTER_KEY` | Recommended | Master API key (can also be set in config.yaml) |
| `DELTALLM_SALT_KEY` | Recommended | Salt for hashing API keys |
| `DELTALLM_CONFIG_PATH` | No | Path to config.yaml (default: `./config.yaml`) |
