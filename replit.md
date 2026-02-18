# DeltaLLM - LLM Proxy Gateway

## Overview
DeltaLLM is an open-source LLM gateway/proxy (similar to LiteLLM) that provides a unified API for multiple LLM providers with enterprise features like RBAC, budget enforcement, guardrails, and monitoring.

## Project Architecture

### Backend (Python/FastAPI)
- **Location**: `src/`
- **Framework**: FastAPI with Uvicorn
- **Database**: PostgreSQL via Prisma ORM (`prisma/schema.prisma`)
- **Config**: YAML-based (`config.yaml`, see `config.example.yaml`)
- **Port**: 8000 (backend API in dev), 5000 (in production)
- **Static serving**: In production, backend serves the built frontend from `ui/dist/`

### Frontend (React/TypeScript)
- **Location**: `ui/`
- **Framework**: React + Vite + TypeScript + Tailwind CSS
- **Port**: 5000 (dev server, proxies API calls to backend on 8000)
- **Build output**: `ui/dist/`

### Key Backend Modules
- `src/routers/` - API route handlers (chat, embeddings, health, models, spend, metrics)
- `src/ui/routes.py` - Admin UI API endpoints (models, keys, teams, users, spend, guardrails, settings)
- `src/providers/` - LLM provider adapters (OpenAI, Anthropic, Azure)
- `src/router/` - Request routing with strategies (round-robin, latency-based, cost-based)
- `src/billing/` - Spend tracking, budget enforcement, alerts
- `src/guardrails/` - Content safety (Presidio PII, Lakera prompt injection)
- `src/auth/` - JWT, SSO, RBAC, custom auth
- `src/cache/` - Response caching (memory, Redis, S3)
- `src/middleware/` - Auth, rate limiting, error handling

### Admin UI Pages
- **Dashboard**: Overview stats, daily spend chart, model usage pie chart
- **Models**: CRUD for model deployments/providers
- **API Keys**: Create, edit, revoke, regenerate keys with budget controls
- **Teams**: Team management with member management
- **Users**: User management with block/unblock
- **Usage**: Spend analytics with daily trends, per-model/key/team breakdowns, request logs
- **Guardrails**: Configure content safety policies
- **Settings**: Routing strategy, caching, health checks

### Running Locally
1. Redis must be running: `redis-server --daemonize yes`
2. Database: PostgreSQL via Replit's built-in database (DATABASE_URL env var)
3. Prisma: `prisma generate && prisma db push`
4. Backend: `python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload`
5. Frontend: `cd ui && npm run dev`
6. Master key for dev: `sk-deltallm-dev-master-key` (set in config.yaml)

### Environment Variables
- `DATABASE_URL` - PostgreSQL connection string (auto-set by Replit)
- `DELTALLM_SALT_KEY` - Salt for hashing API keys
- `DELTALLM_CONFIG_PATH` - Path to config.yaml

### Deployment
- Build: `cd ui && npm run build`
- Run: Backend serves both API and built frontend on port 5000
- Type: VM deployment (needs Redis running alongside)

## Recent Changes
- Built complete admin dashboard UI from scratch for v2-revamp branch
- Set up backend with PostgreSQL database, Redis, and Prisma ORM
- Fixed API key creation bug (missing UUID generation in raw SQL insert)
- Added static file serving to backend for production deployment

## User Preferences
- None recorded yet
