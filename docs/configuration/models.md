# Model Deployments

Model deployments tell DeltaLLM how to reach a real provider-backed model.

In simple terms:

- clients call a public model name such as `gpt-4o-mini`
- DeltaLLM resolves that name to one or more concrete deployments
- each deployment contains the provider details, credentials, mode, and optional pricing metadata

## Quick Success Path

For most teams, use this model lifecycle:

1. Keep `general_settings.model_deployment_source: db_only`
2. Create deployments through the Admin UI or Admin API
3. Use one-time config bootstrap only to seed the first models into the database
4. After models exist in the database, manage them there instead of editing `config.yaml`

## First Working Example

```yaml
model_list:
  - model_name: gpt-4o-mini
    deltallm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
      api_base: https://api.openai.com/v1
      timeout: 60
```

This creates one public model name, `gpt-4o-mini`, backed by one OpenAI deployment.

## Choose a Storage Mode

Runtime deployments are stored in the `deltallm_modeldeployment` table. DeltaLLM can read model definitions from the database, from `config.yaml`, or both.

| Mode | What it does | When to use it |
|------|--------------|----------------|
| `db_only` | Read deployments only from the database | Recommended for normal runtime operations |
| `hybrid` | Prefer database, fall back to `model_list` if the DB is empty or unavailable | Transitional setups |
| `config_only` | Read only from `model_list` | Static or file-managed setups |

```yaml
general_settings:
  model_deployment_source: db_only
```

### Bootstrap Behavior

Use config bootstrap only when you want to seed `model_list` into an empty database.

```yaml
general_settings:
  model_deployment_source: db_only
  model_deployment_bootstrap_from_config: true
```

After the initial seed, set `model_deployment_bootstrap_from_config` back to `false`.

## Fields You Usually Need

| Field | Required | What it means |
|-------|----------|---------------|
| `model_name` | Yes | Public model name clients send in API requests |
| `deltallm_params.model` | Yes | Provider-prefixed upstream model ID, such as `openai/gpt-4o-mini` |
| `deltallm_params.api_key` | Yes | Provider API key |
| `deltallm_params.api_base` | No | Custom provider base URL |
| `deltallm_params.auth_header_name` | No | Custom upstream auth header key for supported OpenAI-compatible providers |
| `deltallm_params.auth_header_format` | No | Custom upstream auth header value template, must include `{api_key}` |
| `deltallm_params.timeout` | No | Upstream timeout in seconds |
| `deltallm_params.weight` | No | Relative weight when multiple deployments share one public model |
| `model_info.mode` | No | Runtime workload type such as `chat`, `embedding`, or `rerank` |
| `model_info.access_groups` | No | Authorization groups attached to the public callable target |
| `model_info.tags` | No | Routing tags for deployment selection; not authorization |

## Custom Upstream Auth Headers

These `deltallm_params` fields are available for the OpenAI-compatible providers that support custom upstream auth headers:

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

- `auth_header_name` for the header key, such as `X-API-Key`
- `auth_header_format` for the value template, such as `Token {api_key}`

If you omit both fields, DeltaLLM uses the default `Authorization: Bearer {api_key}` upstream auth pattern.

Example config-file deployment for a vLLM gateway that expects `X-API-Key`:

```yaml
model_list:
  - model_name: support-vllm
    deltallm_params:
      model: vllm/meta-llama/Llama-3.1-8B-Instruct
      api_key: os.environ/VLLM_GATEWAY_KEY
      api_base: https://vllm.example/v1
      auth_header_name: X-API-Key
      auth_header_format: "{api_key}"
    model_info:
      mode: chat
```

Validation rules:

- `auth_header_format` must contain the exact `{api_key}` placeholder
- extra placeholders, format specifiers, and conversions are rejected
- reserved header names such as `Content-Type` are rejected

## Multiple Deployments Behind One Public Model

You can attach more than one deployment to the same public model name by repeating `model_name`.

```yaml
model_list:
  - model_name: gpt-4o
    deltallm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

  - model_name: gpt-4o
    deltallm_params:
      model: azure/gpt-4o-deployment
      api_key: os.environ/AZURE_API_KEY
      api_base: https://your-resource.openai.azure.com
```

DeltaLLM then routes requests for `gpt-4o` across those deployments using the active routing strategy.

For day-to-day operations, the preferred workflow is still to manage explicit route groups in the Admin UI.

## Set the Workload Type

DeltaLLM supports several runtime modes. Use `model_info.mode` when the deployment is not a standard chat model.

| Mode | Used by |
|------|---------|
| `chat` | `/v1/chat/completions`, `/v1/completions`, `/v1/responses` |
| `embedding` | `/v1/embeddings` |
| `image_generation` | `/v1/images/generations` |
| `audio_speech` | `/v1/audio/speech` |
| `audio_transcription` | `/v1/audio/transcriptions` |
| `rerank` | `/v1/rerank` |

```yaml
model_list:
  - model_name: whisper-large
    deltallm_params:
      model: groq/whisper-large-v3-turbo
      api_key: os.environ/GROQ_API_KEY
    model_info:
      mode: audio_transcription
```

## Access Groups and Routing Tags

Use `model_info.access_groups` when you want to grant model access by group instead of binding every model name separately.

```yaml
model_list:
  - model_name: gpt-4o-mini
    deltallm_params:
      provider: openai
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      mode: chat
      access_groups:
        - beta
        - support
      tags:
        - low-latency
```

In this example, granting the `beta` or `support` access group to an organization, team, key, or runtime user can make the `gpt-4o-mini` callable target visible to that scope. The `low-latency` tag is separate routing metadata: requests can use `metadata.tags` to prefer matching deployments, but tags do not grant access.

Important behavior:

- Access groups grant callable targets such as `gpt-4o-mini`, not individual deployments.
- Access group keys are normalized to lowercase and may contain letters, digits, `.`, `_`, and `-`.
- If multiple deployments share one `model_name`, give every deployment the same `model_info.access_groups`; conflicting values disable group expansion for that public model.
- Route groups are callable targets too. Keep route-group keys and public model names unique so grants are unambiguous.
- Newly added models become available to scopes already granted a matching access group after the runtime reloads its model and governance snapshots.
- In Kubernetes, admin writes publish governance invalidation events so other replicas refresh their in-memory snapshots asynchronously.
- Organization-level grants can reference an access group before any model currently belongs to it. This is useful when you want a tenant to receive future models as they are added to that group.

Recommended rollout:

1. Label models with access groups that describe authorization intent, such as `support`, `finance`, or `beta`.
2. Grant access groups to organizations first; organizations define the parent access universe.
3. Use team, key, or user `restrict` mode only when you need to narrow access below the organization.
4. Do not migrate routing tags blindly into access groups. Tags describe routing preferences; access groups describe who can call a target.

## Embedding Batch Worker Micro-batching

`model_info.upstream_max_batch_inputs` only applies to compatible embedding items
processed by the async batch worker. It does not change synchronous
`/v1/embeddings` behavior or make realtime embedding requests batch together.

```yaml
model_list:
  - model_name: text-embedding-3-large
    deltallm_params:
      model: openai/text-embedding-3-large
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      mode: embedding
      output_vector_size: 3072
      upstream_max_batch_inputs: 8
```

Rollout guidance:

- leave the field unset or set it to `1` to disable batch-worker micro-batching
- start with small values such as `4` or `8`
- validate provider behavior, error rates, and throughput before increasing further

Operational note:

- when DeltaLLM groups multiple embedding items into one upstream request, the provider usually returns usage at the grouped-call level
- DeltaLLM allocates per-item usage and cost from that aggregate usage so total usage and total cost stay consistent
- per-item usage is therefore allocated from aggregate upstream usage, not exact provider-native attribution for each grouped item

## Add Pricing and Defaults

Pricing metadata powers spend tracking.

```yaml
model_list:
  - model_name: gpt-4o-mini
    deltallm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      input_cost_per_token: 0.00000015
      output_cost_per_token: 0.0000006
```

Default parameters let you inject request defaults for a deployment.

```yaml
model_list:
  - model_name: gpt-4o-mini
    deltallm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      default_params:
        temperature: 0.7
        max_tokens: 1024
```

User-supplied request values still take priority over these defaults.

## Provider Prefixes

Use a provider prefix in `deltallm_params.model`.

| Provider | Prefix | Example |
|----------|--------|---------|
| OpenAI | `openai/` | `openai/gpt-4o` |
| Anthropic | `anthropic/` | `anthropic/claude-3-5-sonnet-latest` |
| Azure OpenAI | `azure/` or `azure_openai/` | `azure/gpt-4o-deployment` |
| OpenRouter | `openrouter/` | `openrouter/openai/gpt-4o-mini` |
| Groq | `groq/` | `groq/llama-3.1-8b-instant` |
| Together AI | `together/` | `together/meta-llama/Llama-3.1-8B-Instruct-Turbo` |
| Fireworks AI | `fireworks/` | `fireworks/accounts/fireworks/models/llama-v3p1-8b-instruct` |
| DeepInfra | `deepinfra/` | `deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct` |
| Perplexity | `perplexity/` | `perplexity/sonar` |
| Gemini | `gemini/` | `gemini/gemini-2.0-flash` |
| AWS Bedrock | `bedrock/` | `bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0` |
| vLLM | `vllm/` | `vllm/meta-llama/Llama-3.1-8B-Instruct` |
| LM Studio | `lmstudio/` | `lmstudio/qwen2.5-7b-instruct` |
| Ollama | `ollama/` | `ollama/llama3.1` |

## Capability Matrix

| Provider | Chat | Embedding | Image | TTS | STT | Rerank |
|----------|------|-----------|-------|-----|-----|--------|
| OpenAI | Y | Y | Y | Y | Y | N |
| Anthropic | Y | N | N | N | N | N |
| Azure OpenAI | Y | Y | Y | Y | Y | N |
| OpenRouter | Y | Y | Y | N | N | N |
| Groq | Y | Y | N | Y | Y | N |
| Together AI | Y | Y | Y | N | N | N |
| Fireworks AI | Y | Y | Y | N | N | N |
| DeepInfra | Y | Y | Y | N | N | N |
| Perplexity | Y | N | N | N | N | N |
| Gemini | Y | N | N | N | N | N |
| AWS Bedrock | Y | N | N | N | N | N |
| vLLM | Y | Y | Y | Y | Y | N |
| LM Studio | Y | Y | N | N | N | N |
| Ollama | Y | Y | N | N | N | N |

!!! note
    `rerank` exists as a DeltaLLM mode, but no provider is currently marked as rerank-capable in the backend capability matrix.

## Related Pages

- [Router Settings](router.md)
- [Quick Start](../getting-started/quickstart.md)
- [Models UI](../admin-ui/models.md)
