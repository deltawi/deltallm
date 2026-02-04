# ProxyLLM Documentation

Welcome to the ProxyLLM documentation. This guide covers installation, configuration, and usage of ProxyLLM - a unified LLM gateway with enterprise features.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [API Reference](#api-reference)
5. [Enterprise Features](#enterprise-features)
6. [Deployment](#deployment)
7. [Troubleshooting](#troubleshooting)

## Getting Started

ProxyLLM is a unified API gateway that provides:

- **Multi-Provider Support**: Use OpenAI, Anthropic, Azure, Google, Groq, and more through a single API
- **Intelligent Routing**: Automatic load balancing and failover between providers
- **Cost Tracking**: Detailed spend tracking at organization, team, and API key levels
- **Enterprise RBAC**: Role-based access control with organizations and teams
- **Content Safety**: Built-in guardrails for PII, toxicity, and prompt injection
- **Admin Dashboard**: React-based UI for managing organizations and viewing analytics

### Quick Start

```bash
# Install
pip install deltallm

# Configure (create config.yaml)
deltallm init

# Start server
deltallm server

# Test
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Installation

### Requirements

- Python 3.10+
- PostgreSQL 14+ (for full features)
- Redis (optional, for caching)

### Standard Installation

```bash
pip install deltallm
```

### Development Installation

```bash
git clone https://github.com/your-org/deltallm.git
cd deltallm
pip install -e ".[dev]"
```

### Database Setup

```bash
# Create database
createdb deltallm

# Run migrations
alembic upgrade head

# Seed initial data
python -m deltallm.db.seed
```

## Configuration

ProxyLLM uses YAML configuration files. Here's a complete example:

```yaml
# config.yaml
general:
  master_key: "sk-master-your-secure-key"
  max_requests_per_minute: 100
  max_tokens_per_minute: 100000

router:
  routing_strategy: "simple"  # or "cost_optimized", "latency_optimized"
  num_retries: 3
  timeout: 60
  enable_cooldowns: true

model_list:
  - model_name: "gpt-4"
    litellm_params:
      model: "gpt-4"
      api_key: "${OPENAI_API_KEY}"
    
  - model_name: "claude-3-opus"
    litellm_params:
      model: "claude-3-opus-20240229"
      api_key: "${ANTHROPIC_API_KEY}"
    
  - model_name: "gemini-pro"
    litellm_params:
      model: "gemini-pro"
      api_key: "${GOOGLE_API_KEY}"

cache:
  mode: "redis"  # or "memory", "null"
  redis_url: "redis://localhost:6379/0"
  ttl: 3600

database:
  url: "postgresql://user:pass@localhost/deltallm"
  pool_size: 10

logging:
  level: "INFO"
  format: "json"
```

### Environment Variables

You can use environment variables in your config:

```yaml
api_key: "${OPENAI_API_KEY}"
```

Or set them directly:

```bash
export PROXYLLM_MASTER_KEY="sk-master-xxx"
export PROXYLLM_DATABASE_URL="postgresql://..."
```

## API Reference

### Authentication

All requests require an API key in the Authorization header:

```
Authorization: Bearer sk-xxx
```

### Chat Completions

```http
POST /v1/chat/completions
Content-Type: application/json
Authorization: Bearer sk-xxx

{
  "model": "gpt-4",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "temperature": 0.7,
  "max_tokens": 150
}
```

### Embeddings

```http
POST /v1/embeddings
Content-Type: application/json
Authorization: Bearer sk-xxx

{
  "model": "text-embedding-ada-002",
  "input": "Hello world"
}
```

### Organization Management

```http
# Create organization
POST /org/create
Authorization: Bearer sk-xxx

{
  "name": "Acme Corp",
  "slug": "acme-corp",
  "max_budget": 10000.00
}

# List organizations
GET /org/list
Authorization: Bearer sk-xxx

# Add member
POST /org/{id}/member/add
Authorization: Bearer sk-xxx

{
  "user_id": "uuid",
  "role": "admin"
}
```

### Team Management

```http
# Create team
POST /team/create
Authorization: Bearer sk-xxx

{
  "name": "Engineering",
  "slug": "engineering",
  "org_id": "org-uuid",
  "max_budget": 5000.00
}
```

### Budget Management

```http
# View org budget
GET /budget/org/{org_id}
Authorization: Bearer sk-xxx

# Set budget
POST /budget/org/{org_id}/set
Authorization: Bearer sk-xxx

{
  "max_budget": 15000.00
}

# View spend logs
GET /budget/logs?org_id=xxx&days=30
Authorization: Bearer sk-xxx
```

### Audit Logs

```http
GET /audit/logs?org_id=xxx&action=org:create
Authorization: Bearer sk-xxx
```

## Enterprise Features

### Organizations

Organizations are the top-level entity for multi-tenancy:

- **Budget Management**: Set spending limits at the org level
- **Member Management**: Add users with roles (owner, admin, member, viewer)
- **Team Creation**: Create teams within the organization
- **Audit Logging**: All actions are logged for compliance

### Teams

Teams allow for finer-grained access control:

- **Scoped Budgets**: Teams have their own budget limits
- **Team Members**: Add users to teams with specific roles
- **API Keys**: Create keys scoped to specific teams

### Role-Based Access Control

Built-in roles:

| Role | Permissions |
|------|-------------|
| superuser | Full system access |
| org_owner | Full org management |
| org_admin | Manage members, teams, budgets |
| org_member | Use API, view own data |
| org_viewer | Read-only access |
| team_admin | Manage team members and settings |
| team_member | Use team resources |

### Budget Enforcement

Hierarchical budget enforcement:

```
Organization Budget ($10,000)
├── Team A Budget ($5,000)
│   ├── Key 1 Budget ($1,000)
│   └── Key 2 Budget ($2,000)
└── Team B Budget ($3,000)
```

If any level exceeds its budget, requests are blocked.

### Content Guardrails

Built-in content filtering:

- **PII Detection**: Email, phone, SSN, credit cards
- **Toxicity Filtering**: Profanity and toxic language
- **Prompt Injection**: Detect bypass attempts
- **Custom Policies**: Define your own rules

```python
from deltallm.guardrails import GuardrailsManager, DEFAULT_POLICY

manager = GuardrailsManager(policy=DEFAULT_POLICY)
result = manager.check_prompt("User input here")

if not result.allowed:
    print(f"Blocked: {result.message}")
```

## Deployment

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["deltallm", "server", "--host", "0.0.0.0", "--port", "8000"]
```

### Docker Compose

```yaml
version: '3.8'

services:
  deltallm:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://deltallm:deltallm@db:5432/deltallm
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis
    volumes:
      - ./config.yaml:/app/config.yaml

  db:
    image: postgres:15
    environment:
      - POSTGRES_USER=deltallm
      - POSTGRES_PASSWORD=deltallm
      - POSTGRES_DB=deltallm
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
```

### Kubernetes

See `k8s/` directory for Kubernetes manifests.

## Troubleshooting

### Common Issues

**Database connection errors:**
```bash
# Check database is running
pg_isready -h localhost -p 5432

# Run migrations
alembic upgrade head
```

**Authentication failures:**
```bash
# Verify master key
export PROXYLLM_MASTER_KEY="your-key"

# Check key is valid
deltallm key validate sk-xxx
```

**Rate limiting:**
```yaml
# Increase limits in config
general:
  max_requests_per_minute: 1000
  max_tokens_per_minute: 1000000
```

### Logs

```bash
# View logs
deltallm logs

# Enable debug logging
export PROXYLLM_LOG_LEVEL=DEBUG
```

### Support

- GitHub Issues: https://github.com/your-org/deltallm/issues
- Documentation: https://docs.deltallm.io
- Email: support@deltallm.io
