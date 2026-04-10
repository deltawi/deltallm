# Named Credentials

Named credentials let you store provider connection settings once and reuse them across multiple model deployments.

Use them when you want to:

- share one provider key across many deployments
- rotate a provider key in one place
- avoid repeating inline `api_key`, `api_base`, and similar connection fields in every model
- convert repeated inline credentials into a reusable shared object

This is especially useful for providers such as OpenAI-compatible gateways, Groq, Anthropic, Gemini, Azure OpenAI, and Bedrock.

## What a Named Credential Contains

A named credential stores:

- a stable `credential_id`
- a human-readable `name`
- the provider type
- provider connection fields in `connection_config`
- optional metadata

Typical connection fields include:

- `api_key`
- `api_base`
- `api_version`
- `auth_header_name`
- `auth_header_format`
- `region`
- `aws_access_key_id`
- `aws_secret_access_key`
- `aws_session_token`

Responses always redact secret-bearing values. You can read metadata and connection summaries, but not the raw stored secret back out of the API. For custom upstream auth, compact summaries may show labels such as `X-API-Key` or `Authorization (Token)`.

## Custom Upstream Auth Headers

For these OpenAI-compatible providers, named credentials can override the default `Authorization: Bearer ...` upstream auth behavior:

- `openai`
- `openrouter`
- `groq`
- `together`
- `fireworks`
- `deepinfra`
- `perplexity`
- `vllm`
- `lmstudio`
- `ollama`

Use:

- `auth_header_name` to choose the header key, such as `X-API-Key`
- `auth_header_format` to choose the header value template, such as `Token {api_key}`

Validation rules:

- `auth_header_format` must contain the exact `{api_key}` placeholder
- format specifiers or conversions such as `{api_key!r}` or `{api_key:>10}` are rejected
- escaped placeholders such as `{{api_key}}` are rejected
- reserved header names such as `Content-Type` are rejected

If you leave both fields unset, DeltaLLM keeps the default upstream behavior: `Authorization: Bearer {api_key}`.

## UI Workflow

### Create a named credential

1. Open **AI Gateway > Named Credentials**
2. Create a new credential
3. Choose the provider
4. Enter the shared connection settings for that provider
5. Save it

The page shows whether credentials are present and how many deployments currently reference the credential.

### Use it from the Models page

1. Open **AI Gateway > Models**
2. Create a new deployment or edit an existing one
3. Choose the credential source as **Named credential**
4. Select the credential that matches the deployment provider
5. Keep deployment-specific fields such as:
   - `model_name`
   - upstream `deltallm_params.model`
   - `model_info.mode`
   - pricing or request defaults
6. Save the deployment

The deployment keeps its own runtime identity, but shared provider connection settings come from the named credential.
If both the named credential and the deployment define overlapping connection fields, the named credential wins.

### Rotate a credential

1. Open **AI Gateway > Named Credentials**
2. Edit the credential
3. Replace the provider secret or endpoint fields
4. Save

If the credential is already linked to deployments, DeltaLLM reloads the runtime so linked deployments pick up the new connection settings.

### Convert repeated inline credentials

If multiple deployments already share the same inline provider credentials:

1. Open **AI Gateway > Named Credentials**
2. Use the inline-credentials report
3. Create a shared named credential from a repeated inline group
4. Link the selected deployments automatically

After conversion, the deployments keep their own `model_name` and provider model ID, but stop storing duplicate inline secrets.

## Programmatic Workflow

Admin endpoints accept either:

- `Authorization: Bearer $DELTALLM_MASTER_KEY`
- or an authenticated admin session cookie

### 1. Create the named credential

Example with a shared vLLM gateway that expects `X-API-Key`:

```bash
curl http://localhost:4002/ui/api/named-credentials \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "vLLM Shared Gateway",
    "provider": "vllm",
    "connection_config": {
      "api_key": "gateway-key",
      "api_base": "https://vllm.example/v1",
      "auth_header_name": "X-API-Key",
      "auth_header_format": "{api_key}"
    }
  }'
```

Example response shape:

```json
{
  "credential_id": "cred-123",
  "name": "vLLM Shared Gateway",
  "provider": "vllm",
  "connection_config": {
    "api_key": "***REDACTED***",
    "api_base": "https://vllm.example/v1",
    "auth_header_name": "X-API-Key",
    "auth_header_format": "{api_key}"
  },
  "credentials_present": true,
  "usage_count": 0
}
```

### 2. Create deployments that reference it

When a deployment uses a named credential, keep provider-specific connection fields out of the deployment payload. If both are present, the named credential connection config wins over deployment-local connection fields.

```bash
curl http://localhost:4002/ui/api/models \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "support-vllm",
    "named_credential_id": "cred-123",
    "deltallm_params": {
      "provider": "vllm",
      "model": "vllm/meta-llama/Llama-3.1-8B-Instruct"
    },
    "model_info": {
      "mode": "chat"
    }
  }'
```

Create as many deployments as needed with the same `named_credential_id`.

### 3. Rotate the shared credential

```bash
curl http://localhost:4002/ui/api/named-credentials/cred-123 \
  -X PUT \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "vLLM Shared Gateway",
    "provider": "vllm",
    "connection_config": {
      "auth_header_name": "Authorization",
      "auth_header_format": "Token {api_key}"
    }
  }'
```

That example switches the upstream request format from `X-API-Key: <api_key>` to `Authorization: Token <api_key>` without changing linked deployments.

Update semantics:

- omitted fields are preserved
- `null` removes a stored field
- provider type cannot be changed after creation
- if linked deployments exist, DeltaLLM reloads runtime config automatically

### 4. Verify usage and linkage

```bash
curl http://localhost:4002/ui/api/named-credentials/cred-123 \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

The detail response includes:

- redacted `connection_config`
- `usage_count`
- `linked_deployments`

## Bulk Conversion from Inline Credentials

To discover repeated inline credentials:

```bash
curl http://localhost:4002/ui/api/named-credentials/inline-report \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

To create a named credential from one repeated inline group and relink deployments:

```bash
curl http://localhost:4002/ui/api/named-credentials/convert-inline-group \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "OpenAI Shared",
    "provider": "openai",
    "fingerprint": "<fingerprint-from-inline-report>",
    "deployment_ids": ["dep-1", "dep-2"]
  }'
```

This removes duplicated inline connection fields from those deployments and replaces them with a shared `named_credential_id`.

## Operational Notes

- Deleting a named credential is blocked while deployments still reference it.
- Named credentials are resolved during bootstrap and runtime reload, not by adding database lookups to the request path.
- Provider model discovery can use a selected named credential.
- Provider validation is enforced per provider, so unsupported fields are rejected.
- Secret-ref values continue to work when the runtime secret resolver is configured.

## Related Pages

- [Models](models.md)
- [Admin Endpoints](../api/admin.md)
- [Model Deployments config reference](../configuration/models.md)
