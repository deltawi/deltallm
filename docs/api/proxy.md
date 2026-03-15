# Proxy Endpoints

DeltaLLM's proxy API is OpenAI-compatible. In most clients, you only change the `base_url` and API key.

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

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/chat/completions` | Chat completions, including streaming |
| `POST /v1/completions` | Legacy prompt-style completions |
| `POST /v1/responses` | Responses API compatible subset |
| `POST /v1/embeddings` | Text embeddings |
| `POST /v1/images/generations` | Image generation |
| `POST /v1/audio/speech` | Text-to-speech |
| `POST /v1/audio/transcriptions` | Speech-to-text |
| `POST /v1/rerank` | Reranking |
| `GET /v1/models` | Available public model names |
| `POST /v1/files` | Upload batch input files |
| `GET /v1/files/{file_id}` | Inspect batch files |
| `GET /v1/files/{file_id}/content` | Download batch file content |
| `POST /v1/batches` | Create embeddings batches |
| `GET /v1/batches` | List batches |
| `GET /v1/batches/{batch_id}` | Inspect one batch |
| `POST /v1/batches/{batch_id}/cancel` | Cancel a batch |

## Text Endpoints

### Chat Completions

```text
POST /v1/chat/completions
```

This is the main endpoint most applications should start with.

Chat requests also support DeltaLLM-managed MCP tools through `tools: [{ "type": "mcp", ... }]` on non-streaming requests. See [MCP Gateway & Tooling](mcp.md).

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

DeltaLLM supports a compatible subset of the Responses API and translates these requests into chat completions internally.

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

### Audio Transcription

```text
POST /v1/audio/transcriptions
```

This endpoint accepts multipart form data.

```bash
curl http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "file=@sample.wav" \
  -F "model=whisper-large" \
  -F "response_format=json"
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

Inspect a batch:

```bash
curl http://localhost:8000/v1/batches/batch_123 \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Current behavior:

- batch endpoints are available only when `general_settings.embeddings_batch_enabled: true`
- the current implementation supports `endpoint: "/v1/embeddings"` only

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
