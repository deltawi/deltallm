# Rate Limiting

DeltaLLM enforces rate limits at multiple levels of the resource hierarchy. Limits are tracked in Redis using atomic operations.

## Limit Types

| Limit | Description |
|-------|-------------|
| **RPM** | Requests per minute |
| **TPM** | Tokens per minute |

## Hierarchy

Rate limits can be set at four levels. A request must satisfy limits at every level:

```
Organization → Team → User → API Key
```

If any level's limit is exceeded, the request is rejected with HTTP 429.

## Setting Limits

### API Key Limits

```bash
curl -X POST http://localhost:8000/ui/api/keys \
  -H "Authorization: Bearer MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_name": "rate-limited-key",
    "rpm_limit": 60,
    "tpm_limit": 100000
  }'
```

### Team Limits

```bash
curl -X POST http://localhost:8000/ui/api/teams \
  -H "Authorization: Bearer MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "team_alias": "Engineering",
    "rpm_limit": 200,
    "tpm_limit": 500000
  }'
```

### Organization Limits

```bash
curl -X POST http://localhost:8000/ui/api/organizations \
  -H "Authorization: Bearer MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "organization_name": "Acme Corp",
    "rpm_limit": 500,
    "tpm_limit": 1000000
  }'
```

## Enforcement Behavior

- Limits are enforced atomically — if a request would exceed any scope, no counters are incremented
- Counters reset on a rolling 60-second window
- When rate-limited, the response includes:

```json
{
  "error": {
    "message": "Rate limit exceeded",
    "type": "rate_limit_error",
    "code": 429
  }
}
```

The `Retry-After` header indicates how many seconds to wait before retrying.

## Null Limits

If a limit is `null` (not set) at any level, that level imposes no restriction. For example, a team with `rpm_limit: null` does not limit requests per minute at the team level.
