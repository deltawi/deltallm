# Guardrails

Guardrails let DeltaLLM inspect requests or responses and block, warn, or sanitize content before it reaches the client.

## Quick Path

For a fast first rollout:

1. Start with one pre-call guardrail
2. Keep `default_on: true` so it protects every request
3. Use `default_action: block` for strict enforcement or `warn` while evaluating impact
4. Add scoped overrides later for specific organizations, teams, or API keys

Example with built-in PII detection:

```yaml
deltallm_settings:
  guardrails:
    - guardrail_name: presidio-pii
      deltallm_params:
        guardrail: src.guardrails.presidio.PresidioGuardrail
        mode: pre_call
        default_on: true
        default_action: block
        anonymize: true
        threshold: 0.5
        entities:
          - EMAIL_ADDRESS
          - PHONE_NUMBER
          - US_SSN
```

## Built-In Guardrails

DeltaLLM currently ships with two built-in guardrail integrations.

### Presidio PII Detection

Use this when you want to detect or redact sensitive personal data in prompts or outputs.

Common settings:

- `mode`: `pre_call`, `post_call`, or `during_call`
- `default_on`: enable by default for all traffic
- `default_action`: `block` or `warn`
- `anonymize`: replace detected PII instead of failing the request
- `threshold`: detection confidence threshold
- `entities`: specific PII types to inspect

### Lakera Prompt Injection

Use this when you want to detect prompt injection or jailbreak-style content.

```yaml
deltallm_settings:
  guardrails:
    - guardrail_name: lakera-prompt-injection
      deltallm_params:
        guardrail: src.guardrails.lakera.LakeraGuardrail
        mode: pre_call
        default_on: true
        default_action: block
        api_key: os.environ/LAKERA_API_KEY
        threshold: 0.5
        fail_open: false
```

Common settings:

- `api_key`: Lakera Guard API key
- `threshold`: score threshold for blocking
- `fail_open`: allow traffic through if the external guardrail service is unavailable

## How Scope Resolution Works

Guardrails can be assigned at these levels:

```text
Global -> Organization -> Team -> API Key
```

DeltaLLM starts with the global default set, then applies scoped changes from top to bottom.

Each scope can use one of two modes:

| Mode | Meaning |
| --- | --- |
| `inherit` | Start from the parent scope, then add or remove guardrails |
| `override` | Replace the parent result with the local list |

This makes it easy to keep one safe platform default while giving a specific team or key a narrower or broader policy.

## Admin UI and Admin API

The [Guardrails](../admin-ui/guardrails.md) page is the easiest way to manage policy. The same capability is available through the admin API and requires platform-admin access.

![Guardrails Page](../admin-ui/images/guardrails.png)

Read a scoped assignment:

```bash
curl http://localhost:8000/ui/api/guardrails/scope/organization/org-123 \
  -H "Authorization: Bearer YOUR_MASTER_KEY"
```

Set a scoped assignment:

```bash
curl -X PUT http://localhost:8000/ui/api/guardrails/scope/organization/org-123 \
  -H "Authorization: Bearer YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "guardrails_config": {
      "mode": "inherit",
      "include": ["presidio-pii"],
      "exclude": []
    }
  }'
```

Remove a scoped assignment:

```bash
curl -X DELETE http://localhost:8000/ui/api/guardrails/scope/organization/org-123 \
  -H "Authorization: Bearer YOUR_MASTER_KEY"
```

## Advanced Notes

- If no org, team, or key override exists, DeltaLLM uses only the global defaults marked `default_on: true`.
- A key can still use a direct guardrail list, but scoped config is the clearer long-term pattern.
- Guardrail violations are returned as structured proxy errors, including the guardrail name.
- Use `warn` during rollout if you want visibility before enforcement.

## Related Pages

- [Admin UI: Guardrails](../admin-ui/guardrails.md)
- [Admin Endpoints](../api/admin.md)
- [Authentication & SSO](authentication.md)
