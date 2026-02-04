# API Reference

Complete API reference for ProxyLLM.

## Base URL

```
Production: https://api.deltallm.io
Local: http://localhost:8000
```

## Authentication

All API requests require authentication via Bearer token:

```http
Authorization: Bearer {api_key}
```

## Response Format

All responses follow the standard format:

```json
{
  "success": true,
  "data": { ... },
  "error": null
}
```

Error responses:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable message",
    "details": { }
  }
}
```

## Core Endpoints

### Chat Completions

Create a chat completion.

```http
POST /v1/chat/completions
```

**Request Body:**

```json
{
  "model": "gpt-4",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "temperature": 0.7,
  "max_tokens": 150,
  "top_p": 1.0,
  "frequency_penalty": 0,
  "presence_penalty": 0,
  "stream": false
}
```

**Response:**

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "gpt-4",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "total_tokens": 30
  }
}
```

**Streaming:**

Set `stream: true` for Server-Sent Events (SSE) streaming.

### Embeddings

Create embeddings for text.

```http
POST /v1/embeddings
```

**Request Body:**

```json
{
  "model": "text-embedding-ada-002",
  "input": "The quick brown fox"
}
```

**Response:**

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [0.1, 0.2, ...]
    }
  ],
  "model": "text-embedding-ada-002",
  "usage": {
    "prompt_tokens": 5,
    "total_tokens": 5
  }
}
```

### List Models

```http
GET /v1/models
```

**Response:**

```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-4",
      "object": "model",
      "created": 1234567890,
      "owned_by": "openai"
    }
  ]
}
```

## Organization Endpoints

### Create Organization

```http
POST /org/create
```

**Request:**

```json
{
  "name": "Acme Corporation",
  "slug": "acme-corp",
  "description": "Main organization",
  "max_budget": 10000.00
}
```

**Response:**

```json
{
  "id": "uuid",
  "name": "Acme Corporation",
  "slug": "acme-corp",
  "max_budget": 10000.00,
  "spend": 0,
  "created_at": "2024-01-01T00:00:00Z"
}
```

### List Organizations

```http
GET /org/list?page=1&page_size=20
```

**Response:**

```json
{
  "organizations": [...],
  "total": 100,
  "page": 1,
  "page_size": 20
}
```

### Get Organization

```http
GET /org/{org_id}
```

### Update Organization

```http
POST /org/{org_id}/update
```

**Request:**

```json
{
  "name": "New Name",
  "max_budget": 15000.00
}
```

### Delete Organization

```http
DELETE /org/{org_id}
```

### Add Member

```http
POST /org/{org_id}/member/add
```

**Request:**

```json
{
  "user_id": "user-uuid",
  "role": "admin"
}
```

**Roles:** `owner`, `admin`, `member`, `viewer`

### Update Member Role

```http
POST /org/{org_id}/member/{user_id}/role
```

**Request:**

```json
{
  "role": "admin"
}
```

### Remove Member

```http
DELETE /org/{org_id}/member/{user_id}
```

## Provider Endpoints

Manage LLM provider configurations with API keys and settings.

### Create Provider

```http
POST /v1/providers
```

**Request:**

```json
{
  "name": "openai-prod",
  "provider_type": "openai",
  "api_key": "sk-xxx",
  "api_base": null,
  "org_id": "org-uuid",
  "is_active": true,
  "tpm_limit": 100000,
  "rpm_limit": 500,
  "settings": {}
}
```

**Response:**

```json
{
  "id": "uuid",
  "name": "openai-prod",
  "provider_type": "openai",
  "api_base": null,
  "org_id": "org-uuid",
  "is_active": true,
  "tpm_limit": 100000,
  "rpm_limit": 500,
  "settings": {},
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": null
}
```

**Permissions:**
- Global providers (`org_id: null`): Requires superuser
- Org-scoped providers: Requires org admin or owner

### List Providers

```http
GET /v1/providers?page=1&page_size=20&org_id=xxx&provider_type=openai&is_active=true
```

**Response:**

```json
{
  "total": 10,
  "page": 1,
  "page_size": 20,
  "pages": 1,
  "items": [...]
}
```

**Visibility:**
- Superusers see all providers
- Regular users see global providers + their organizations' providers

### Get Provider

```http
GET /v1/providers/{provider_id}
```

### Update Provider

```http
PATCH /v1/providers/{provider_id}
```

**Request:**

```json
{
  "name": "openai-prod-updated",
  "api_key": "sk-new-key",
  "is_active": false
}
```

### Delete Provider

```http
DELETE /v1/providers/{provider_id}?force=false
```

Use `force=true` to delete even if provider has active deployments (cascades).

### Test Provider Connectivity

```http
POST /v1/providers/{provider_id}/test
```

**Response:**

```json
{
  "success": true,
  "latency_ms": 234.5,
  "error_message": null,
  "model_list": ["gpt-4", "gpt-3.5-turbo"]
}
```

### Get Provider Health

```http
GET /v1/providers/{provider_id}/health
```

**Response:**

```json
{
  "provider_id": "uuid",
  "name": "openai-prod",
  "provider_type": "openai",
  "is_active": true,
  "is_healthy": true,
  "latency_ms": 234.5,
  "last_check": "2024-01-01T00:00:00Z",
  "error_message": null
}
```

### Grant Team Access to Provider

```http
POST /v1/providers/{provider_id}/teams/{team_id}
```

Grant a team access to use a provider. The team and provider must belong to the same organization (or provider must be global).

**Permissions:** Requires org admin of the team's organization.

**Response:**

```json
{
  "message": "Access granted to team Engineering"
}
```

**Errors:**
- `404` - Provider or team not found
- `400` - Provider and team belong to different organizations
- `403` - Not an org admin
- `409` - Team already has access

### Revoke Team Access to Provider

```http
DELETE /v1/providers/{provider_id}/teams/{team_id}
```

Revoke a team's access to a provider.

**Permissions:** Requires org admin of the team's organization.

**Errors:**
- `404` - Team does not have access to this provider
- `403` - Not an org admin

### List Teams with Provider Access

```http
GET /v1/providers/{provider_id}/teams
```

List all teams that have been granted access to a provider.

**Permissions:**
- Org-scoped providers: Requires org admin
- Global providers: Requires superuser

**Response:**

```json
{
  "items": [
    {
      "id": "uuid",
      "team_id": "team-uuid",
      "provider_config_id": "provider-uuid",
      "granted_by": "user-uuid",
      "granted_at": "2024-01-01T00:00:00Z",
      "team_name": "Engineering",
      "team_slug": "engineering"
    }
  ],
  "total": 1
}
```

---

## Model Deployment Endpoints

Manage model deployments that map public model names to provider configurations.

### Create Deployment

```http
POST /v1/deployments
```

Supports two modes:
1. **Linked mode**: Links to an existing provider configuration
2. **Standalone mode**: Stores API key directly (LiteLLM-style)

**Linked Deployment Request:**

```json
{
  "model_name": "gpt-4o",
  "provider_model": "gpt-4o-2024-08-06",
  "provider_config_id": "provider-uuid",
  "org_id": null,
  "is_active": true,
  "priority": 1,
  "tpm_limit": null,
  "rpm_limit": null,
  "timeout": 60.0,
  "settings": {}
}
```

**Standalone Deployment Request:**

```json
{
  "model_name": "my-custom-model",
  "provider_model": "gpt-4o",
  "provider_config_id": null,
  "provider_type": "openai",
  "api_key": "sk-xxx",
  "api_base": "https://custom-endpoint.com/v1",
  "org_id": "org-uuid",
  "is_active": true,
  "priority": 1
}
```

**Permissions:**
- Global deployments (`org_id: null`): Requires superuser
- Org-scoped deployments: Requires org admin

### List Deployments

```http
GET /v1/deployments?page=1&model_name=gpt-4o&provider_id=xxx&org_id=xxx&is_active=true
```

**Visibility:**
- Superusers see all deployments
- Regular users see global deployments + their organizations' deployments

### Get Deployment

```http
GET /v1/deployments/{deployment_id}
```

Returns full deployment details including provider information.

### Update Deployment

```http
PATCH /v1/deployments/{deployment_id}
```

**Request:**

```json
{
  "model_name": "gpt-4o-updated",
  "is_active": false,
  "priority": 2
}
```

### Delete Deployment

```http
DELETE /v1/deployments/{deployment_id}
```

### Enable Deployment

```http
POST /v1/deployments/{deployment_id}/enable
```

### Disable Deployment

```http
POST /v1/deployments/{deployment_id}/disable
```

### Get Deployments for Model

```http
GET /v1/deployments/model/{model_name}?only_active=true
```

Get all deployments for a specific model name, useful for understanding routing options.

---

## Team Endpoints

### Create Team

```http
POST /team/create
```

**Request:**

```json
{
  "name": "Engineering",
  "slug": "engineering",
  "org_id": "org-uuid",
  "description": "Engineering team",
  "max_budget": 5000.00
}
```

### List Teams

```http
GET /team/list?org_id=xxx
```

### Get Team

```http
GET /team/{team_id}
```

### Update Team

```http
POST /team/{team_id}/update
```

### Delete Team

```http
DELETE /team/{team_id}
```

### Add Team Member

```http
POST /team/{team_id}/member/add
```

**Request:**

```json
{
  "user_id": "user-uuid",
  "role": "member"
}
```

**Roles:** `admin`, `member`

## Budget Endpoints

### Get Organization Budget

```http
GET /budget/org/{org_id}
```

**Response:**

```json
{
  "entity_type": "organization",
  "entity_id": "uuid",
  "entity_name": "Acme Corp",
  "max_budget": 10000.00,
  "current_spend": 2500.50,
  "remaining_budget": 7499.50,
  "budget_utilization_percent": 25.0,
  "is_exceeded": false
}
```

### Get Full Budget Breakdown

```http
GET /budget/org/{org_id}/full
```

**Response:**

```json
{
  "org_budget": { ... },
  "team_budgets": [
    {
      "entity_type": "team",
      "entity_id": "uuid",
      "entity_name": "Engineering",
      ...
    }
  ]
}
```

### Set Organization Budget

```http
POST /budget/org/{org_id}/set
```

**Request:**

```json
{
  "max_budget": 15000.00
}
```

### Get Team Budget

```http
GET /budget/team/{team_id}
```

### Set Team Budget

```http
POST /budget/team/{team_id}/set
```

### List Spend Logs

```http
GET /budget/logs?org_id=xxx&days=30&limit=100
```

**Response:**

```json
{
  "total": 1000,
  "logs": [
    {
      "id": "uuid",
      "request_id": "req-xxx",
      "model": "gpt-4",
      "prompt_tokens": 100,
      "completion_tokens": 50,
      "spend": 0.015,
      "status": "success",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

### Get Spend Summary

```http
GET /budget/summary?org_id=xxx&days=30
```

**Response:**

```json
{
  "total_spend": 2500.50,
  "total_requests": 10000,
  "total_tokens": 500000,
  "successful_requests": 9950,
  "failed_requests": 50,
  "avg_latency_ms": 450,
  "top_models": [
    {"model": "gpt-4", "requests": 5000, "spend": 2000.00}
  ],
  "daily_breakdown": [
    {"date": "2024-01-01", "spend": 100.00, "requests": 400}
  ]
}
```

## Audit Log Endpoints

### List Audit Logs

```http
GET /audit/logs?org_id=xxx&action=org:create&limit=100
```

**Response:**

```json
{
  "total": 100,
  "logs": [
    {
      "id": "uuid",
      "action": "org:create",
      "resource_type": "organization",
      "resource_id": "org-uuid",
      "user": {
        "email": "admin@example.com"
      },
      "old_values": null,
      "new_values": {"name": "Acme Corp"},
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

### Get Available Actions

```http
GET /audit/actions
```

## Guardrails Endpoints

### List Policies

```http
GET /guardrails/policies
```

**Response:**

```json
[
  {
    "id": "default",
    "name": "Default Policy",
    "description": "Standard safety policy",
    "enable_pii_filter": true,
    "enable_toxicity_filter": true,
    "enable_injection_filter": true,
    "pii_action": "redact",
    "toxicity_action": "block",
    "injection_action": "block"
  }
]
```

### Check Content

```http
POST /guardrails/check
```

**Request:**

```json
{
  "content": "User input to check",
  "policy_id": "default"
}
```

**Response:**

```json
{
  "allowed": true,
  "action": "allow",
  "message": null,
  "filtered_content": null,
  "violations": []
}
```

### Get Guardrails Status

```http
GET /guardrails/status?org_id=xxx
```

### Set Organization Policy

```http
POST /guardrails/org/{org_id}/policy/{policy_id}
```

## API Key Endpoints

### Generate Key

```http
POST /key/generate
```

**Request:**

```json
{
  "key_alias": "Production Key",
  "org_id": "org-uuid",
  "team_id": "team-uuid",
  "models": ["gpt-4", "claude-3-opus"],
  "max_budget": 1000.00,
  "tpm_limit": 10000,
  "rpm_limit": 100,
  "expires_in_days": 90
}
```

**Response:**

```json
{
  "key": "sk-xxxxxxxxxxxx",
  "key_hash": "hash",
  "key_alias": "Production Key"
}
```

**Important:** The full key is only returned once. Store it securely.

### Get Key Info

```http
GET /key/info
```

### List Keys

```http
GET /key/list?org_id=xxx&team_id=xxx
```

### Delete Key

```http
DELETE /key/delete
```

## Authentication Endpoints

### Login

```http
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username=email@example.com&password=secret
```

**Response:**

```json
{
  "access_token": "jwt-token",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "email@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "is_superuser": false
  }
}
```

### Get Current User

```http
GET /auth/me
```

## Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `AUTHENTICATION_ERROR` | 401 | Invalid or missing API key |
| `AUTHORIZATION_ERROR` | 403 | Insufficient permissions |
| `RATE_LIMIT_ERROR` | 429 | Rate limit exceeded |
| `BUDGET_EXCEEDED` | 403 | Budget limit reached |
| `MODEL_NOT_FOUND` | 404 | Requested model unavailable |
| `PROVIDER_ERROR` | 502 | Upstream provider error |
| `TIMEOUT_ERROR` | 504 | Request timeout |
| `CONTENT_FILTERED` | 400 | Content blocked by guardrails |
| `VALIDATION_ERROR` | 400 | Invalid request parameters |
| `NOT_FOUND` | 404 | Resource not found |

## Rate Limits

Rate limits are enforced per API key:

- **Requests per minute**: Configurable (default: 60)
- **Tokens per minute**: Configurable (default: 10,000)
- **Concurrent requests**: Configurable (default: 10)

Rate limit headers are included in responses:

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1234567890
```

## Pagination

List endpoints support pagination:

```http
GET /org/list?page=1&page_size=20
```

**Response includes:**

```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "page_size": 20,
  "pages": 5
}
```

## Filtering

Many endpoints support filtering:

```http
GET /budget/logs?org_id=xxx&model=gpt-4&days=7
GET /audit/logs?action=org:create&org_id=xxx
```

## Webhooks (Coming Soon)

Subscribe to events:

- `budget.threshold.reached` - Budget threshold alert
- `org.member.added` - New member added
- `key.expiring` - API key expiring soon
