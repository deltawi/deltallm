# Proxy Endpoints

DeltaLLM's proxy API is OpenAI-compatible. In most clients, you only change the `base_url` and API key.

Each change is tested with the official OpenAI Python SDK. See [OpenAI SDK compatibility](openai-sdk-compatibility.md).

## Quick Start

Use the same auth header for every proxy endpoint: `Authorization: Bearer YOUR_API_KEY`

Check which models are available:

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Send a chat request:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Hello from DeltaLLM"}
    ]
  }'
```

## Endpoint Map

Chat Completions accepts `max_tokens` or `max_completion_tokens`. Supply one. When a caller has an `output_tpm_limit`, the gateway checks recorded output before the request and counts actual output after each provider attempt. Admitted calls can exceed the remaining quota; subsequent calls are blocked until the fixed UTC minute resets. Output TPM does not require or change generation caps. See [Output TPM limits and headers](../features/rate-limiting.md#output-tokens-per-minute).

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/chat/completions` | Chat completions, including streaming |
| `POST /v1/messages` | Anthropic Messages API-compatible subset, see [Anthropic Messages](messages.md) |
| `POST /v1/messages/count_tokens` | Input-token count for a Messages request, see [Count tokens](messages.md#count-tokens) |
| `POST /v1/completions` | Legacy prompt-style completions |
| `POST /v1/responses` | OpenAI Responses API. See [OpenAI Responses](responses.md). |
| `POST /v1/embeddings` | Text embeddings |
| `POST /v1/images/generations` | Image generation |
| `POST /v1/audio/speech` | Text-to-speech |
| `POST /v1/audio/transcriptions` | Speech-to-text |
| `POST /v1/rerank` | Reranking |
| `GET /v1/models` | Available public model names |
| `GET /v1/models/{model}` | One model, if your key can call it |
| `GET /v1/key` | Budget, spend and limits of the calling key, see [Key information](#key-information) |
| `GET /model/info`, `GET /v1/model/info` | Per-deployment prices, context window, mode and capabilities (LiteLLM-compatible) |
| `GET /model_group/info` | The same details aggregated per public model name (LiteLLM-compatible) |
| `POST /v1/files` | Upload batch input files |
| `GET /v1/files/{file_id}` | Inspect batch files |
| `GET /v1/files/{file_id}/content` | Download batch file content |
| `POST /v1/batches` | Create embedding or non-streaming chat completion batches |
| `GET /v1/batches` | List batches |
| `GET /v1/batches/{batch_id}` | Inspect one batch |
| `POST /v1/batches/{batch_id}/cancel` | Cancel a batch |

## Response cost

Successful non-streaming answers from Chat Completions, Completions, Responses, Messages,
Embeddings, Images, Audio and Rerank carry the cost DeltaLLM recorded for the request, in USD:

```http
x-deltallm-response-cost: 0.000412
```

It is the same amount the request log and spend reports show for the request, after tier pricing.
Cache hits carry the amount charged for the hit. The header is left out when the model has no
price configured. Streaming answers do not carry it, because headers are sent before the answer
ends; read their cost from request logs or the Usage page.

```python
response = client.chat.completions.with_raw_response.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.headers.get("x-deltallm-response-cost"))
completion = response.parse()
```

## Key information

```text
GET /v1/key
```

An application can read its own key's budget, spend and limits with the key itself. No admin
session or master key is needed, and the response never contains the secret.

```bash
curl http://localhost:8000/v1/key \
  -H "Authorization: Bearer YOUR_API_KEY"
```

```json
{
  "object": "key",
  "name": "billing-service",
  "team_id": "team-1",
  "organization_id": "org-1",
  "created_at": "2026-10-01T00:00:00+00:00",
  "expires_at": null,
  "budget": {
    "max_budget": 10.0,
    "spend": 2.5,
    "remaining": 7.5,
    "duration": "1mo",
    "reset_at": "2026-11-01T00:00:00+00:00"
  },
  "limits": {"rpm": 60, "tpm": 100000, "output_tpm": null, "rph": null, "rpd": null, "tpd": null}
}
```

Amounts are in USD. `remaining` is `null` when the key has no budget. These are the key's own
budget and limits. Team, organization and tier limits still apply on top; the `x-ratelimit-*`
headers on each answer report the limits that applied to that request. A master key, JWT or
custom credential gets `400`, because it is not a DeltaLLM API key.

## Text Endpoints

### Chat Completions

```text
POST /v1/chat/completions
```

This is the main endpoint most applications should start with.

#### Request parameters

DeltaLLM forwards the chat parameters you send, including ones it does not
declare itself, such as `seed`, `reasoning_effort`, `logprobs`, `top_logprobs`,
`parallel_tool_calls`, `service_tier` or vendor fields like vLLM's
`chat_template_kwargs` (sent with an SDK's `extra_body`):

```python
client.chat.completions.create(
    model="gpt-5-mini",
    messages=[{"role": "user", "content": "Hello"}],
    reasoning_effort="low",
    seed=7,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)
```

Where they go depends on the deployment's provider:

| Provider | Parameters DeltaLLM does not declare |
| --- | --- |
| OpenAI, Azure OpenAI, and other OpenAI-compatible servers (vLLM, Ollama, Groq, OpenRouter, ...) | Sent to the provider unchanged. The provider rejects ones it does not know. |
| DeepSeek, Mistral, Qwen, Z.ai, MiniMax, Tencent | Left out. Their declared parameters are checked as before. |
| Anthropic, Gemini, Bedrock | Left out. These requests are translated from the declared fields. |

Parameters you leave out are not sent. The provider's own defaults apply, or the
model's configured `default_params` when an operator set them. An operator can
remove a parameter for one model with `drop_params`, or force a value with
`override_params`. See [Request shaping](../configuration/request-shaping.md).

The response cache keys on these parameters too, so answers for different
`reasoning_effort` or `seed` values are cached separately.

#### Usage details

When the provider reports them, chat answers keep `usage.prompt_tokens_details`
(for example `cached_tokens`) and `usage.completion_tokens_details` (for example
`reasoning_tokens`), plus answer fields such as `service_tier` and
`message.refusal`. These breakdowns are informational. They do not change how
DeltaLLM prices the request.

Chat requests also support DeltaLLM-managed MCP tools through `tools: [{ "type": "mcp", ... }]` on non-streaming requests. See [MCP Gateway & Tooling](mcp.md).

OpenAI-compatible client-managed tool histories may omit assistant `content`, or set it to
`null`, when that assistant message contains non-empty `tool_calls`. Follow the assistant turn
with the matching tool result:

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "user", "content": "Search the documentation."},
    {
      "role": "assistant",
      "tool_calls": [
        {
          "id": "call_1",
          "type": "function",
          "function": {"name": "search", "arguments": "{\"query\":\"DeltaLLM\"}"}
        }
      ]
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "Search result"}
  ]
}
```

System, user, and tool messages still require non-null `content`. Use
`POST /v1/chat/completions`, not the legacy `POST /v1/completions`, for tool-calling requests.

Provider error bodies have size limits. DeltaLLM never returns them verbatim.
The gateway does not decompress encoded error bodies.
It classifies these errors with trusted status and `Retry-After` metadata.
Bounded identity bodies can also supply provider-specific classifications.
Before response commit, DeltaLLM uses the sanitized classification for retry and failover.

After a streaming response sends content, DeltaLLM does not retry it.
A provider failure sends a final `data: {"error": {...}}` frame and closes the stream without a
`[DONE]` marker. The OpenAI SDKs raise an error on that frame.
Clients must treat a missing terminal marker as an incomplete response.

#### Streaming accounting

For successful text streams, DeltaLLM awaits the configured answer-accounting
writer and required audit before forwarding `[DONE]` (or the translated
`message_stop` event). Clients may close at that marker without draining the HTTP
connection to EOF. Content tokens are still delivered immediately. This ordering
applies to Chat, Completions, Responses, and the Messages-compatible wrapper.

Model-router selectors require `general_settings.spend_ingestion_mode: outbox`.
In this mode, durable accounting must accept the answer charge before the terminal marker.
The stream ends without a successful terminal marker if this acceptance or required audit fails.
This also occurs if finalization exceeds the request deadline.
The provider can still charge for tokens already generated.
Durable acceptance does not mean that the background worker completed each reporting or ledger update.

The default `legacy` spend mode is also awaited, but its existing writer logs and
swallows database-write failures and does not atomically update all ledgers.
Consequently, `[DONE]` or `message_stop` in legacy mode is **not proof that the
charge was persisted**. Awaiting this writer fixes close-at-terminal cancellation,
not its historical durability limitations. Use outbox mode when durable charge
acceptance is required.

### Completions (Legacy)

```text
POST /v1/completions
```

Use this only if you still have prompt-based clients. DeltaLLM translates `prompt` into a chat-style user message internally.

Unsupported request fields currently return `400`:

- `echo`
- `best_of > 1`
- `logprobs`
- `suffix`

### Responses

```text
POST /v1/responses
```

DeltaLLM sends Responses requests to OpenAI and Azure deployments unchanged. For other
deployments, it translates the request to chat completions and translates the answer back.
See [OpenAI Responses](responses.md) for tools, input items, streaming events, and limits.

```bash
curl http://localhost:8000/v1/responses \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "input": "Write a one-line summary of DeltaLLM.",
    "stream": false
  }'
```

Responses requests also support DeltaLLM-managed MCP tools on non-streaming requests. See [MCP Gateway & Tooling](mcp.md).

### Embeddings

```text
POST /v1/embeddings
```

```bash
curl http://localhost:8000/v1/embeddings \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "text-embedding-3-small",
    "input": "The quick brown fox"
  }'
```

## Multimodal and Specialized Endpoints

### Image Generation

```text
POST /v1/images/generations
```

```bash
curl http://localhost:8000/v1/images/generations \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "dall-e-3",
    "prompt": "A sunset over mountains",
    "size": "1024x1024"
  }'
```

### Audio Speech

```text
POST /v1/audio/speech
```

This endpoint returns audio bytes, not JSON.

Some providers stream WAV output with placeholder sizes in the file header (for example Groq Orpheus).
When `response_format` is `wav`, DeltaLLM rewrites those placeholders with the real sizes so strict players accept the file.

Public speech requests remain OpenAI-compatible even when the selected deployment is ElevenLabs. For ElevenLabs, DeltaLLM maps `input` to native `text`, resolves the ElevenLabs voice ID from request `voice` or deployment `model_info.default_params.voice_id`, and calls `POST /v1/text-to-speech/{voice_id}` upstream with `xi-api-key`.

```bash
curl http://localhost:8000/v1/audio/speech \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tts-1",
    "input": "Hello world",
    "voice": "alloy",
    "response_format": "mp3"
  }' \
  --output speech.mp3
```

Example using an ElevenLabs-backed public model:

```bash
curl http://localhost:8000/v1/audio/speech \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "elevenlabs-tts",
    "input": "Hello from ElevenLabs through DeltaLLM",
    "voice": "21m00Tcm4TlvDq8ikWAM",
    "response_format": "mp3"
  }' \
  --output elevenlabs-speech.mp3
```

### Audio Transcription

```text
POST /v1/audio/transcriptions
```

This endpoint accepts multipart form data.

`response_format` accepts `json` (default), `verbose_json`, `text`, `srt` and `vtt`.
As in the OpenAI API, `json` and `verbose_json` return a JSON object, and `text`, `srt` and `vtt`
return the transcript itself as `text/plain` (`text/vtt` for `vtt`).

Public transcription requests remain OpenAI-compatible when the selected deployment is ElevenLabs.
DeltaLLM sends uploaded audio to the native `POST /v1/speech-to-text` endpoint.
It maps public `language` to `language_code` and maps `temperature`.
It converts the provider response to the requested public response format.

```bash
curl http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "file=@sample.wav" \
  -F "model=whisper-large" \
  -F "response_format=json"
```

Example using an ElevenLabs-backed public model:

```bash
curl http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "file=@sample.wav" \
  -F "model=elevenlabs-stt" \
  -F "language=en" \
  -F "response_format=verbose_json"
```

### Rerank

```text
POST /v1/rerank
```

```bash
curl http://localhost:8000/v1/rerank \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "rerank-english-v2.0",
    "query": "What is machine learning?",
    "documents": [
      "Machine learning is a subset of AI.",
      "The weather is sunny today.",
      "Deep learning uses neural networks."
    ],
    "top_n": 2
  }'
```

## Batch Endpoints

### Files

```text
POST /v1/files
GET /v1/files/{file_id}
GET /v1/files/{file_id}/content
```

Use files as the input and output artifacts for batch jobs.

```bash
curl http://localhost:8000/v1/files \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "purpose=batch" \
  -F "file=@input.jsonl"
```

### Batches

```text
POST /v1/batches
GET /v1/batches
GET /v1/batches/{batch_id}
POST /v1/batches/{batch_id}/cancel
```

Create a batch:

```bash
curl http://localhost:8000/v1/batches \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input_file_id": "file_123",
    "endpoint": "/v1/embeddings",
    "completion_window": "24h"
  }'
```

DeltaLLM supports the OpenAI-compatible `24h` batch completion window. Missing or null `completion_window` values default to `24h`; unsupported values return HTTP 400.

Inspect a batch:

```bash
curl http://localhost:8000/v1/batches/batch_123 \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Current behavior:

- batch endpoints are available only when `general_settings.embeddings_batch_enabled: true`
- supported endpoints are `"/v1/embeddings"` and `"/v1/chat/completions"`
- public batch responses include OpenAI-compatible `completion_window`, `errors`, `expires_at`, `failed_at`, and `expired_at` fields
- chat batch requests must be non-streaming; `stream: true` is rejected
- MCP tools are not supported in chat batch requests yet

## Model Discovery

### List Models

```text
GET /v1/models
```

DeltaLLM returns the public model names that clients can request. This list is built from the current runtime model registry.

```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-4o-mini",
      "object": "model",
      "owned_by": "deltallm"
    }
  ]
}
```

### Retrieve a Model

```text
GET /v1/models/{model}
```

Returns the OpenAI model object for one public name, or `404 model_not_found` when the name does not exist or your key cannot call it. Both cases return the same error, so the endpoint does not reveal models outside your access.

### Model Info

```text
GET /model/info
GET /v1/model/info
GET /model_group/info
```

These endpoints follow LiteLLM's response shapes, so tools written for LiteLLM's `/model/info` and `/model_group/info` work against DeltaLLM. Both only list names your key can call, with the same rules as `GET /v1/models` (key, team, organization, tier and creator access). They accept the key as `Authorization: Bearer ...` or `x-api-key`.

Provider credentials and endpoints are never returned. `litellm_params` only carries `model`, `custom_llm_provider`, `rpm`, `tpm`, `max_tokens`, `timeout` and `stream_timeout`; `api_key`, `api_base`, AWS credentials, custom auth headers, `default_params` and access groups stay on the server.

`/model/info` returns one row per deployment behind each public name. A deployment that serves a route group is listed under the route group name too. Pass `?litellm_model_id=<deployment id>` to get one deployment; an unknown or inaccessible id returns `404`.

```json
{
  "data": [
    {
      "model_name": "gpt-4o-mini",
      "litellm_params": {
        "model": "openai/gpt-4o-mini",
        "custom_llm_provider": "openai"
      },
      "model_info": {
        "id": "dep_123",
        "mode": "chat",
        "litellm_provider": "openai",
        "max_tokens": null,
        "max_input_tokens": 128000,
        "max_output_tokens": 16384,
        "input_cost_per_token": 1.5e-07,
        "output_cost_per_token": 6e-07,
        "cache_read_input_token_cost": 7.5e-08,
        "rpm": null,
        "tpm": null,
        "chat_capabilities": {"tools": true, "json_object": true, "json_schema": true, "image": true, "audio": false, "file": false, "streaming": true, "multiple_choices": true},
        "supports_function_calling": true,
        "supports_vision": true,
        "supports_audio_input": false,
        "supports_pdf_input": false,
        "supports_response_schema": true,
        "supports_native_streaming": true
      }
    }
  ]
}
```

Prices are the gateway prices configured on the model (what DeltaLLM charges), not the provider's list price. `cache_read_input_token_cost` is the model's cache-hit input price. Other configured prices (batch, audio, image, per character, per second, per request) are included when set.

Capability flags come only from the model's declared chat capabilities (`model_info.chat_capabilities`, see [model capability metadata](../configuration/models.md#model-router-capability-and-price-metadata)); DeltaLLM does not guess them from the model name. When a model has no declared capabilities the `supports_*` flags are `null`, meaning unknown.

`/model_group/info` returns one row per public name, including route groups (`target_type: "route_group"`). Pass `?model_group=<name>` to get one; a name you cannot call returns an empty `data` list.

```json
{
  "data": [
    {
      "model_group": "gpt-4o-mini",
      "target_type": "model",
      "providers": ["azure", "openai"],
      "mode": "chat",
      "max_input_tokens": 64000,
      "max_output_tokens": 16384,
      "input_cost_per_token": 2e-07,
      "output_cost_per_token": 6e-07,
      "cache_read_input_token_cost": 7.5e-08,
      "rpm": 150,
      "tpm": null,
      "supports_function_calling": true,
      "supports_parallel_function_calling": null,
      "supports_vision": false,
      "supports_audio_input": false,
      "supports_pdf_input": false,
      "supports_response_schema": false,
      "supports_native_streaming": true,
      "supports_reasoning": null,
      "supports_web_search": null,
      "supported_openai_params": null,
      "is_public_model_group": false
    }
  ]
}
```

A request to a public name can land on any of its deployments, so the group row is conservative where LiteLLM reports the best case:

- `max_input_tokens` and `max_output_tokens` are the smallest known value across deployments
- prices are the highest known value
- a `supports_*` flag is `true` only when every deployment declares it, `false` when any deployment lacks it, and `null` when some are undeclared
- `rpm` and `tpm` are summed only when every deployment has a limit

Differences from LiteLLM: `include_team_models`, `teamId` and `healthy_only` are accepted but ignored, since results are always scoped to the calling key. `/v2/model/info` and the public model hub are not available; admins use the [Models](../admin-ui/models.md) page and admin API instead. `supported_openai_params`, `supports_reasoning` and `supports_web_search` are always `null`.
