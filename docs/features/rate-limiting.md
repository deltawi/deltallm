# Rate Limiting

DeltaLLM can enforce request and token limits before traffic reaches the provider.

## Quick Path

For most teams, start with limits on API keys and teams:

1. Set `rpm_limit` on the key
2. Set `tpm_limit` on the key if token usage matters
3. Add team or organization limits only when you want shared caps across multiple keys

Example API key:

```bash
curl -X POST http://localhost:8000/ui/api/keys \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_name": "rate-limited-key",
    "rpm_limit": 60,
    "tpm_limit": 100000
  }'
```

## What DeltaLLM Enforces

DeltaLLM checks these limits:

| Limit | Meaning |
| --- | --- |
| `rpm_limit` | Requests per minute |
| `tpm_limit` | Estimated tokens per minute |

The gateway estimates token usage from the request payload before the provider call.

## Scope Order

RPM and TPM can be enforced at four levels:

```text
Organization -> Team -> User -> API Key
```

One request must pass every configured scope. If any scope is over its limit, DeltaLLM returns `429`.

## Common Configurations

Team limit:

```bash
curl -X POST http://localhost:8000/ui/api/teams \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "team_alias": "engineering",
    "rpm_limit": 300,
    "tpm_limit": 500000
  }'
```

Organization limit:

```bash
curl -X POST http://localhost:8000/ui/api/organizations \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "organization_name": "Acme Corp",
    "rpm_limit": 1000,
    "tpm_limit": 2000000
  }'
```

## Enforcement Behavior

- Limits are checked atomically so partial counter updates are avoided
- The gateway returns `Retry-After` when it knows how long the client should wait
- Multipart requests such as file uploads still enforce RPM limits, with a minimal TPM estimate
- If a limit is unset, that scope does not apply a restriction

A typical error looks like this:

```json
{
  "error": {
    "message": "Rate limit exceeded",
    "type": "rate_limit_error",
    "param": null,
    "code": null
  }
}
```

## Advanced Notes

- Redis is the normal backend for shared limit tracking.
- If Redis is unavailable and the runtime is in degraded mode, fallback behavior depends on your platform settings.
- Routing and rate limiting are separate concerns. You can combine request limits with `rate-limit-aware` routing when you also configure deployment-level RPM and TPM metadata.

## Related Pages

- [API Keys](../admin-ui/api-keys.md)
- [Teams](../admin-ui/teams.md)
- [Organizations](../admin-ui/organizations.md)
- [Routing & Failover](routing.md)
