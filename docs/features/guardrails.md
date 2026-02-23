# Guardrails

DeltaLLM includes a guardrail framework for content safety, running checks before or after LLM calls.

## Built-in Guardrails

### Presidio PII Detection

Detects and optionally anonymizes personally identifiable information (PII) in requests.

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
          - CREDIT_CARD
```

| Setting | Description |
|---------|-------------|
| `mode` | `pre_call` (check input) or `post_call` (check output) |
| `default_on` | Apply to all requests by default |
| `default_action` | `block` (reject request) or `warn` (log only) |
| `anonymize` | Replace PII with placeholders instead of blocking |
| `threshold` | Confidence threshold (0.0-1.0) for entity detection |
| `entities` | List of PII entity types to detect |

### Lakera Prompt Injection

Detects prompt injection attacks using the Lakera Guard API.

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

| Setting | Description |
|---------|-------------|
| `api_key` | Lakera Guard API key |
| `threshold` | Score threshold for blocking (0.0-1.0) |
| `fail_open` | If `true`, allow requests when Lakera is unreachable |

## Managing Guardrails in the Admin UI

The Guardrails page lets you configure content safety policies and manage scoped assignments through the web interface.

![Guardrails Page](../admin-ui/images/guardrails.png)

## Scoped Guardrail Assignments

Guardrails can be assigned at different scope levels, allowing fine-grained control over which guardrails apply to specific organizations, teams, or API keys.

### Scope Hierarchy

```
Global → Organization → Team → API Key
```

Each scope can either **inherit** from its parent or **override** with its own configuration.

### Assignment Modes

| Mode | Behavior |
|------|----------|
| `inherit` | Use parent scope's guardrails plus any additions |
| `override` | Replace parent scope's guardrails entirely |

### Managing Scoped Assignments

Use the admin API to manage guardrail assignments:

```bash
# Set guardrails for an organization
curl -X PUT http://localhost:8000/ui/api/guardrails/scope/organization/org-123 \
  -H "Authorization: Bearer MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "override",
    "include": ["presidio-pii"],
    "exclude": ["lakera-prompt-injection"]
  }'

# View current assignments
curl http://localhost:8000/ui/api/guardrails/scope/organization/org-123 \
  -H "Authorization: Bearer MASTER_KEY"

# Remove scope assignment (reverts to inherit)
curl -X DELETE http://localhost:8000/ui/api/guardrails/scope/organization/org-123 \
  -H "Authorization: Bearer MASTER_KEY"
```

### Resolution Order

When a request arrives, DeltaLLM resolves the active guardrails by walking up the scope chain:

1. Check the API key's guardrail config
2. If inheriting, check the team's config
3. If inheriting, check the organization's config
4. If inheriting, use the global config

The first scope with `override` mode stops the chain and uses only its configuration.
