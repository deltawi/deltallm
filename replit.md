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

### Authentication & RBAC
- **Auth modes**: Session-based (cookie) login with email/password AND master key fallback
- **Session cookie**: `deltallm_session` (HttpOnly, set by backend)
- **Auth endpoints**: `/auth/internal/login`, `/auth/internal/logout`, `/auth/me`, `/auth/internal/change-password`, `/auth/mfa/enroll/start`, `/auth/mfa/enroll/confirm`
- **SSO flow**: `/auth/login` + `/auth/callback`
- **Platform roles**: `platform_admin`, `platform_co_admin`, `org_user`
- **Org roles**: `org_member`, `org_owner`, `org_admin`, `org_billing`, `org_auditor`
- **Team roles**: `team_admin`, `team_developer`, `team_viewer`
- **RBAC APIs**: `/ui/api/rbac/accounts`, `/ui/api/rbac/organization-memberships`, `/ui/api/rbac/team-memberships`
- **Auth flow**: Login -> Force password change (if required) -> MFA enrollment prompt (optional) -> Dashboard

### Scoped Access Control
- **AuthScope**: Helper in `common.py` determines if user is platform_admin or org-scoped via RBAC memberships
- **Platform admins**: See all resources across the platform
- **Org users**: See only organizations, teams, keys, and users within their assigned orgs
- **Models/spend/settings**: Readable by any authenticated user (models are global config, spend is aggregate)
- **Settings security**: Master key is redacted from settings response for non-admin users
- **Cross-team protection**: Users cannot query users/keys for teams outside their org scope (403)
- **Frontend guards**: Guardrails, Settings, Access Control pages and nav items hidden for non-platform-admins
- **Create buttons**: Organization create button hidden for non-platform-admins

### Admin UI Pages
- **Dashboard**: Overview stats, daily spend chart, model usage pie chart
- **Models**: CRUD for model deployments/providers
- **API Keys**: Create, edit, revoke, regenerate keys with budget controls
- **Organizations**: Organization management with RPM/TPM rate limits (scoped for org users)
- **Teams**: Team management with member management and rate limits (scoped for org users)
- **Users**: User management with block/unblock and rate limits (scoped for org users)
- **Usage**: Spend analytics with daily trends, per-model/key/team breakdowns, request logs
- **Guardrails**: Configure content safety policies (platform admin only)
- **Access Control**: Platform account management, org/team membership assignment with roles (platform admin only)
- **Settings**: Routing strategy, caching, health checks (platform admin only for writes)

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
- Added session-based auth (email/password login) alongside existing master key auth
- Implemented force password change flow and optional MFA enrollment (TOTP)
- Created Access Control page for RBAC: platform accounts, org memberships, team memberships
- Updated Login page with tabbed Email Login / Master Key interface
- Updated AuthProvider to support dual-mode auth (session cookie + master key)
- Added API layer for auth endpoints and RBAC management endpoints
- Updated Layout sidebar with user info display and Access Control nav item
- Built complete admin dashboard UI from scratch for v2-revamp branch
- Set up backend with PostgreSQL database, Redis, and Prisma ORM
- Fixed API key creation bug (missing UUID generation in raw SQL insert)
- Added static file serving to backend for production deployment
- Fixed UI model CRUD routes to use ModelHotReloadManager for database persistence (models survive restarts)
- Fixed spend logging: added UUID id column, timestamp casting, JSONB serialization, removed FK constraint
- Added master key auth fallback to proxy endpoints (chat, audio, etc.) via `_is_master_key()` in auth middleware
- Chat cost calculation now uses deployment-level pricing from model_info instead of only static cost map
- Prisma schema updated to remove litellm_spendlogs FK to litellm_verificationtoken

## User Preferences
- None recorded yet
