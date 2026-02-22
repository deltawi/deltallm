# Model Deployments

The `model_list` section defines which LLM models are available through the gateway. Each entry maps a public model name to a specific provider deployment.

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

| Provider | Model Prefix | Example |
|----------|-------------|---------|
| OpenAI | `openai/` | `openai/gpt-4o` |
| Anthropic | `anthropic/` | `anthropic/claude-3-sonnet-20240229` |
| Azure OpenAI | `azure/` | `azure/gpt-4o-deployment` |
| Groq | `groq/` | `groq/llama-3.1-8b-instant` |
| AWS Bedrock | `bedrock/` | `bedrock/anthropic.claude-3-sonnet` |
| Google Vertex AI | `vertex_ai/` | `vertex_ai/gemini-pro` |
| Cohere | `cohere/` | `cohere/command-r-plus` |
| Mistral | `mistral/` | `mistral/mistral-large-latest` |
