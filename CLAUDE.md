# ProxyLLM - Claude Code Context

## Project Overview
- Unified LLM gateway replicating LiteLLM functionality
- Single API interface to 100+ LLM providers
- Enterprise features: RBAC, budgets, organizations, teams

## Current Status (~98% Complete)

### Completed (All Core Features)
- [x] 8 providers (OpenAI, Anthropic, Azure, Bedrock, Gemini, Cohere, Mistral, Groq)
- [x] Router with 5 load balancing strategies
- [x] Caching (in-memory + Redis)
- [x] RBAC with Organizations/Teams
- [x] 48 API endpoints across all features
- [x] 356+ tests across 24 test files
- [x] Vision/multimodal support
- [x] Function calling across providers
- [x] Budget enforcement (hierarchy: org > team > key)
- [x] Guardrails & content filtering (PII, toxic content, injection detection)
- [x] Admin dashboard (React/TypeScript, 8 pages, 22 components)

### Pending (Milestones 5-6)
- [ ] Additional endpoints (audio, images, batch) - optional
- [ ] Documentation site
- [ ] v1.0 release

## Key Files & Directories

### Core SDK
- `proxyllm/main.py` - completion/embedding functions
- `proxyllm/router.py` - load balancing router
- `proxyllm/providers/` - 8 provider adapters (10 files)

### Enterprise Features
- `proxyllm/db/models.py` - 12 SQLAlchemy tables
- `proxyllm/rbac/` - RBACManager, PermissionChecker, AuditLogger
- `proxyllm/proxy/routes/` - 11 route files (48 endpoints)
- `proxyllm/guardrails/` - ContentFilter, PIIFilter, GuardrailManager
- `proxyllm/budget/` - BudgetEnforcer, BudgetTracker

### Admin Dashboard
- `proxyllm/admin-dashboard/` - React/TypeScript frontend (22 files)

### Tests
- `tests/unit/` - 17 unit test files
- `tests/integration/` - integration tests
- `tests/guardrails/` - 3 guardrail test files
- `tests/budget/` - 2 budget test files

## Common Commands

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=proxyllm --cov-report=html

# Start server
proxyllm server --port 8000

# Docker
docker-compose up -d
```

## Database Schema (12 Tables)
organizations, teams, users, org_members, team_members,
roles, permissions, role_permissions, api_keys, api_key_permissions, spend_logs, audit_logs

## RBAC Roles
superuser, org_owner, org_admin, org_member, org_viewer, team_admin, team_member

## API Endpoints (48 total)
- Core: /v1/chat/completions, /v1/embeddings, /v1/models (5)
- Org: 10 endpoints (CRUD + member management)
- Team: 9 endpoints (CRUD + member management)
- Budget: 7 endpoints (tracking, enforcement)
- Keys: 5 endpoints (CRUD)
- Guardrails: 5 endpoints (policy management)
- Health: 4 endpoints
- Audit: 3 endpoints

## Priority Focus (v1.0 Release)
1. Documentation site (MkDocs)
2. Test coverage to 80%+
3. PyPI package publication
4. Docker Hub image
