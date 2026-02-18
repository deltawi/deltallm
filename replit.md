# DeltaLLM - LLM Proxy Gateway

## Overview
DeltaLLM is an open-source LLM gateway/proxy (similar to LiteLLM) that provides a unified API for multiple LLM providers with enterprise features like RBAC, budget enforcement, guardrails, and monitoring.

## Project Architecture

### Backend (Python/FastAPI)
- **Location**: `src/`
- **Framework**: FastAPI with Uvicorn
- **Database**: PostgreSQL via Prisma ORM (`prisma/schema.prisma`)
- **Config**: YAML-based (`config.example.yaml`)
- **Port**: 8000 (backend API)

### Frontend (React/TypeScript)
- **Location**: `ui/`
- **Framework**: React + Vite + TypeScript + Tailwind CSS
- **Port**: 5000 (dev server)
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
- **API Keys**: Create, revoke, regenerate keys with budget controls
- **Teams**: Team management with member management
- **Users**: User management with block/unblock
- **Usage**: Spend analytics with daily trends, per-model/key/team breakdowns, request logs
- **Guardrails**: Configure content safety policies
- **Settings**: Routing strategy, caching, health checks

## Recent Changes
- Built complete admin dashboard UI from scratch for v2-revamp branch

## User Preferences
- None recorded yet
