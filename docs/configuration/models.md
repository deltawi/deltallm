# Model Deployments

The `model_list` section defines which LLM models are available through the gateway. Each entry maps a public model name to a specific provider deployment.

## Storage Source

Runtime deployments are persisted in the `deltallm_modeldeployment` database table.

- `general_settings.model_deployment_source: db_only` — read deployments only from DB (recommended)
- `general_settings.model_deployment_source: hybrid` — prefer DB, fallback to `model_list` when DB is empty/unavailable
- `general_settings.model_deployment_source: config_only` — read only from `model_list`

### Bootstrap Behavior

Use `general_settings.model_deployment_bootstrap_from_config: true` only to seed DB from `model_list` when the deployment table is empty.

After startup, runtime model lifecycle is managed via Admin UI/API (`/ui/api/models`). Changes to `config.yaml` are not the primary runtime write path in `db_only`.

## Basic Model Definition

```yaml
model_list:
  - model_name: gpt-4o-mini
    deltallm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
      api_base: https://api.openai.com/v1
      timeout: 60
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `model_name` | Yes | Public name clients use to request this model |
| `deltallm_params.model` | Yes | Provider-prefixed model identifier (e.g., `openai/gpt-4o`, `anthropic/claude-3-sonnet`) |
| `deltallm_params.api_key` | Yes | Provider API key (use `os.environ/` for env vars) |
| `deltallm_params.api_base` | No | Override the provider API base URL |
| `deltallm_params.timeout` | No | Request timeout in seconds (default: 600) |
| `deltallm_params.weight` | No | Routing weight for load balancing (default: 1) |

## Model Groups

Multiple deployments can share the same `model_name` to create a model group. Requests are distributed across the group based on the configured [routing strategy](router.md).

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

In this example, requests for `gpt-4o` are routed across both OpenAI and Azure deployments.

## Model Types

DeltaLLM supports multiple model types. Set the `mode` in `model_info` to specify the type:

| Mode | Description | Endpoints |
|------|-------------|-----------|
| `chat` | Chat completion models (default) | `/v1/chat/completions` |
| `embedding` | Text embedding models | `/v1/embeddings` |
| `image_generation` | Image generation models | `/v1/images/generations` |
| `audio_speech` | Text-to-speech models | `/v1/audio/speech` |
| `audio_transcription` | Speech-to-text models | `/v1/audio/transcriptions` |
| `rerank` | Reranking models | `/v1/rerank` |

```yaml
model_list:
  - model_name: whisper-large
    deltallm_params:
      model: groq/whisper-large-v3-turbo
      api_key: os.environ/GROQ_API_KEY
    model_info:
      mode: audio_transcription
```

## Pricing Configuration

Set per-token costs for spend tracking:

```yaml
model_list:
  - model_name: gpt-4o-mini
    deltallm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      input_cost_per_token: 0.00000015
      output_cost_per_token: 0.0000006
      max_tokens: 128000
```

## Default Parameters

Inject default parameters into every request for a deployment. User-provided values always take precedence.

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

## Supported Providers

The backend source of truth is `src/providers/resolution.py` (`PROVIDER_CAPABILITIES` and `PROVIDER_PRESETS`).

### Provider Prefixes

| Provider | Model Prefix | Example |
|----------|-------------|---------|
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

### Capability Matrix

Legend: `Y` = supported, `N` = not supported in backend compatibility matrix.

| Provider | Chat | Embedding | Image | TTS (`audio_speech`) | STT (`audio_transcription`) | Rerank |
|----------|------|-----------|-------|----------------------|-----------------------------|--------|
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
    `rerank` mode is available in DeltaLLM, but no provider is explicitly marked as `rerank`-capable in the current backend provider matrix.

!!! note
    Unknown/custom providers are allowed by default at validation time (`provider_supports_mode()` returns true when provider is not listed), so custom integrations can still be configured.
