# ProxyLLM Architecture

This document describes the architecture and design decisions of ProxyLLM.

## Overview

ProxyLLM is built as a modular, enterprise-grade LLM gateway with the following architecture:

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Applications                       │
└────────────────────┬────────────────────────────────────────────┘
                     │ HTTPS / HTTP2
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Load Balancer                            │
│                     (nginx / cloud LB)                          │
└────────────────────┬────────────────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ProxyLLM Server                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   FastAPI    │  │   RBAC       │  │   Rate Limiting      │  │
│  │   Router     │  │   Manager    │  │   & Quotas           │  │
│  └──────┬───────┘  └──────────────┘  └──────────────────────┘  │
│         │                                                       │
│  ┌──────▼───────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   LLM        │  │   Budget     │  │   Guardrails         │  │
│  │   Router     │  │   Enforcer   │  │   (PII/Toxicity)     │  │
│  └──────┬───────┘  └──────────────┘  └──────────────────────┘  │
│         │                                                       │
│  ┌──────▼───────────────────────────────────────────────────┐  │
│  │                    Provider Adapters                       │  │
│  │  (OpenAI, Anthropic, Azure, Google, Groq, Mistral...)     │  │
│  └────────────────────┬──────────────────────────────────────┘  │
└───────────────────────┼─────────────────────────────────────────┘
                        │ HTTPS
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   OpenAI     │ │  Anthropic   │ │    Azure     │
└──────────────┘ └──────────────┘ └──────────────┘
```

## Core Components

### 1. FastAPI Router

The entry point for all requests. Handles:
- Request validation
- Authentication
- Routing to appropriate handlers
- Response formatting

**File**: `deltallm/proxy/app.py`

### 2. LLM Router

Intelligent routing layer that:
- Selects the best provider based on strategy
- Implements fallback mechanisms
- Manages provider cooldowns
- Tracks health of each provider

**File**: `deltallm/router.py`

### 3. Provider Adapters

Unified interface for multiple LLM providers:
- OpenAI (GPT-4, GPT-3.5)
- Anthropic (Claude 3, Claude 2)
- Azure OpenAI
- Google (Gemini)
- Groq
- Mistral
- AWS Bedrock
- Cohere

Each adapter implements the `BaseProvider` interface:

```python
class BaseProvider(ABC):
    async def completion(self, ...) -> CompletionResponse: ...
    async def streaming_completion(self, ...) -> AsyncIterator[StreamingChunk]: ...
    async def embedding(self, ...) -> EmbeddingResponse: ...
```

**Files**: `deltallm/providers/*.py`

### 4. RBAC System

Role-based access control with:
- Organizations (multi-tenancy)
- Teams (sub-organization groups)
- Users
- Roles and Permissions
- API Keys (scoped to org/team)

**Files**:
- `deltallm/db/models.py` - Data models
- `deltallm/rbac/manager.py` - Permission checking
- `deltallm/rbac/permissions.py` - Permission definitions

### 5. Budget Enforcement

Hierarchical budget tracking:
- Organization budgets
- Team budgets  
- API Key budgets
- Real-time spend tracking

**Files**:
- `deltallm/budget/enforcer.py` - Budget checking
- `deltallm/budget/tracker.py` - Spend tracking

### 6. Guardrails

Content safety system:
- PII detection and redaction
- Toxicity filtering
- Prompt injection detection
- Custom policy rules

**Files**: `deltallm/guardrails/*.py`

### 7. Audit Logging

Compliance and security logging:
- All RBAC changes logged
- Budget changes tracked
- API usage recorded
- Immutable audit trail

**File**: `deltallm/rbac/audit.py`

## Data Flow

### Chat Completion Request

```
1. Client → POST /v1/chat/completions
   ↓
2. Authentication (API Key validation)
   ↓
3. Rate Limiting (check RPM/TPM)
   ↓
4. Budget Enforcement (check limits)
   ↓
5. RBAC Check (verify model:use permission)
   ↓
6. Guardrails (content filtering)
   ↓
7. LLM Router (select provider)
   ↓
8. Provider API Call
   ↓
9. Response Processing
   ↓
10. Budget Tracking (record spend)
   ↓
11. Return to Client
```

### Organization Creation

```
1. Admin → POST /org/create
   ↓
2. Authentication (User token)
   ↓
3. RBAC Check (verify org:create permission)
   ↓
4. Create Organization (DB)
   ↓
5. Create Owner Membership (DB)
   ↓
6. Audit Log Entry
   ↓
7. Return Organization Data
```

## Database Schema

### Core Tables

```sql
-- Organizations
type Organization {
  id: UUID (PK)
  name: String
  slug: String (unique)
  max_budget: Decimal (optional)
  spend: Decimal
  settings: JSONB
  created_at: Timestamp
  updated_at: Timestamp
}

-- Teams
type Team {
  id: UUID (PK)
  name: String
  slug: String
  org_id: UUID (FK)
  max_budget: Decimal (optional)
  spend: Decimal
  created_at: Timestamp
  updated_at: Timestamp
}

-- Users
type User {
  id: UUID (PK)
  email: String (unique)
  password_hash: String
  is_superuser: Boolean
  is_active: Boolean
  created_at: Timestamp
}

-- Organization Members
type OrgMember {
  user_id: UUID (FK)
  org_id: UUID (FK)
  role: Enum (owner, admin, member, viewer)
  joined_at: Timestamp
}

-- API Keys
type APIKey {
  id: UUID (PK)
  key_hash: String (unique)
  user_id: UUID (FK, optional)
  org_id: UUID (FK, optional)
  team_id: UUID (FK, optional)
  max_budget: Decimal (optional)
  spend: Decimal
  permissions: Array[String]
  expires_at: Timestamp (optional)
  created_at: Timestamp
}

-- Spend Logs
type SpendLog {
  id: UUID (PK)
  request_id: String
  api_key_id: UUID (FK)
  org_id: UUID (FK)
  team_id: UUID (FK)
  model: String
  tokens: Integer
  spend: Decimal
  latency_ms: Float
  status: String
  created_at: Timestamp
}

-- Audit Logs
type AuditLog {
  id: UUID (PK)
  org_id: UUID (FK)
  user_id: UUID (FK)
  action: String
  resource_type: String
  resource_id: UUID
  old_values: JSONB
  new_values: JSONB
  created_at: Timestamp
}
```

## Security Considerations

### Authentication

- API Keys use SHA-256 hashing
- JWT tokens for user sessions
- Master key for admin operations
- Rate limiting per key

### Authorization

- RBAC with principle of least privilege
- Resource-level permissions
- Hierarchical permissions (org → team → user)
- Audit logging for all authz decisions

### Data Protection

- PII redaction in logs
- Encryption at rest (database)
- TLS for all communications
- No API keys in logs

## Performance

### Caching

- Redis for response caching
- Cache keys based on model + messages hash
- TTL configurable per model

### Database

- Connection pooling
- Async SQLAlchemy
- Indexed columns for frequent queries
- Read replicas for analytics

### Rate Limiting

- Sliding window algorithm
- In-memory + Redis backing
- Distributed rate limiting support

## Scalability

### Horizontal Scaling

- Stateless server design
- Shared Redis for rate limiting
- Shared PostgreSQL for persistence
- Load balancer health checks

### Vertical Scaling

- Async I/O for concurrency
- Provider connection pooling
- Request timeouts and retries
- Backpressure handling

## Monitoring

### Metrics

- Request latency (p50, p95, p99)
- Error rates by provider
- Token usage per org/team
- Budget utilization

### Health Checks

- `/health` - Basic health
- `/health/live` - Liveness probe
- `/health/ready` - Readiness probe

### Logging

- Structured JSON logs
- Request/response logging (configurable)
- Audit logs for compliance
- Error tracking with context

## Development Guidelines

### Adding a New Provider

1. Create provider class in `deltallm/providers/`
2. Implement `BaseProvider` interface
3. Add provider to registry
4. Add tests

### Adding a New Permission

1. Define in `deltallm/rbac/permissions.py`
2. Update role permissions in seed data
3. Add to relevant API endpoints
4. Document in API reference

### Database Migrations

```bash
# Create migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

## Future Enhancements

- [ ] GraphQL API
- [ ] WebSocket streaming
- [ ] Custom model fine-tuning
- [ ] A/B testing framework
- [ ] Advanced analytics dashboard
- [ ] Multi-region deployment
- [ ] Federated learning support
