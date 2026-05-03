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
      provider: openai
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

| Field | Status | What it means |
|-------|--------|---------------|
| `model_name` | Required | Public model name clients send in API requests |
| `deltallm_params.provider` | Recommended for new config; required by the admin UI/API | Authoritative provider identity for routing visibility, dashboards, spend, callbacks, and metrics |
| `deltallm_params.model` | Required | Upstream model ID sent to the provider; provider prefixes remain supported as legacy/import fallback |
| `deltallm_params.api_key` | Required | Provider API key |
| `deltallm_params.api_base` | Optional | Custom provider base URL |
| `deltallm_params.auth_header_name` | Optional | Custom upstream auth header key for supported OpenAI-compatible providers |
| `deltallm_params.auth_header_format` | Optional | Custom upstream auth header value template, must include `{api_key}` |
| `deltallm_params.timeout` | Optional | Upstream timeout in seconds |
| `deltallm_params.weight` | Optional | Relative weight when multiple deployments share one public model |
| `model_info.mode` | Optional | Runtime workload type such as `chat`, `embedding`, or `rerank` |
| `model_info.access_groups` | Optional | Authorization groups attached to the public callable target |
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
      provider: vllm
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
      provider: openai
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

  - model_name: gpt-4o
    deltallm_params:
      provider: azure_openai
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
      provider: groq
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
      provider: openai
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

## Chat Batch Worker Execution

Chat batch jobs default to `concurrent` execution when `deltallm_params.chat_batching`
is omitted. That means the worker sends one ordinary upstream chat request per
batch item, bounded by worker concurrency. This is the recommended default for
vLLM because vLLM performs continuous batching inside the serving engine.

```yaml
model_list:
  - model_name: llama-3.1-8b
    deltallm_params:
      provider: vllm
      model: meta-llama/Llama-3.1-8B-Instruct
      api_base: https://vllm.example/v1
      api_key: os.environ/VLLM_API_KEY
      chat_batching:
        mode: concurrent
        max_in_flight: 32
```

Supported modes:

- `disabled`: execute each chat item as an ordinary upstream request; no microbatch grouping
- `concurrent`: execute per-item upstream requests with worker and deployment concurrency limits
- `sync_microbatch`: group compatible items into one upstream provider call when a microbatch executor is available

`native_async_batch` is reserved for a future provider-managed async batch API and
is not accepted by config validation.

For providers with a proven sync chat microbatch API, opt in explicitly:

```yaml
model_list:
  - model_name: provider-chat
    deltallm_params:
      provider: example_provider
      model: example-chat-model
      api_base: https://provider.example/v1
      api_key: os.environ/PROVIDER_API_KEY
      chat_batching:
        mode: sync_microbatch
        upstream_max_batch_size: 8
        max_total_input_tokens: 32000
        require_homogeneous_params: true
```

Sync chat microbatching is conservative:

- grouping happens after routing, so only items resolved to the same deployment can be batched
- non-message request parameters must match exactly; `require_homogeneous_params` must remain `true` when provided
- streaming, MCP tools, function tools, complex response formats, and multiple choices fall back to per-item execution
- whole microbatch calls use the normal failover path; each served deployment must have `mode: sync_microbatch`, support the chunk size and token cap, and expose a sync microbatch executor
- if a served deployment does not satisfy sync microbatch requirements and there was no earlier retryable health-affecting microbatch failure, the worker degrades that chunk to bounded per-item execution without marking the unsupported deployment unhealthy
- if the primary sync microbatch already failed with a retryable health-affecting error and failover then reaches an unsupported deployment, the worker preserves the primary failure and requeues the chunk instead of hiding that failure behind per-item fallback
- successful provider results must include per-item usage; aggregate-only usage is rejected for chat billing
- mixed provider results persist successful items and fail or retry only the affected failed items; structured per-item `429`, `408`, and `5xx` provider errors are retryable under the normal batch retry policy

`max_in_flight` is enforced per worker replica. When running multiple Kubernetes
replicas, multiply the configured value by the worker replica count when sizing
provider limits and vLLM capacity.

## Add Pricing and Defaults

Pricing metadata powers spend tracking.

```yaml
model_list:
  - model_name: gpt-4o-mini
    deltallm_params:
      provider: openai
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
      provider: openai
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      default_params:
        temperature: 0.7
        max_tokens: 1024
```

User-supplied request values still take priority over these defaults.

## Provider Prefix Fallback

For new deployments, set `deltallm_params.provider`. Provider prefixes in `deltallm_params.model` remain supported for legacy config/import fallback, but they are not the source of truth when `provider` is present. The admin UI/API requires an explicit provider for created or updated deployments.

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
