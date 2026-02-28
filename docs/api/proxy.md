# Proxy Endpoints

These endpoints are fully compatible with the OpenAI API. Point any OpenAI SDK or client library at DeltaLLM by changing the `base_url`.

## Chat Completions

```
POST /v1/chat/completions
```

Create a chat completion. Supports streaming.

## Completions (Legacy)

```
POST /v1/completions
```

Legacy prompt-based completions API. Supports streaming.

Compatibility notes:
- `prompt` is mapped to a chat `user` message.
- Unsupported fields currently return `400`: `echo`, `best_of > 1`, `logprobs`, `suffix`.

## Responses

```
POST /v1/responses
```

Unified Responses API (compatible subset) with `input` and optional `instructions`.

Compatibility notes:
- Requests are translated to chat completions internally.
- Supported input forms: plain text and simple message/text blocks.
- Advanced Responses item/tool state graph features are not fully implemented yet.

**Request:**

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "temperature": 0.7,
  "max_tokens": 1024,
  "stream": false
}
```

**Response:**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 10,
    "total_tokens": 30
  }
}
```

## Embeddings

```
POST /v1/embeddings
```

Create text embeddings.

**Request:**

```json
{
  "model": "text-embedding-ada-002",
  "input": "The quick brown fox"
}
```

## Image Generation

```
POST /v1/images/generations
```

Generate images from text prompts.

**Request:**

```json
{
  "model": "dall-e-3",
  "prompt": "A sunset over mountains",
  "size": "1024x1024"
}
```

## Audio Speech (TTS)

```
POST /v1/audio/speech
```

Convert text to speech.

**Request:**

```json
{
  "model": "tts-1",
  "input": "Hello world",
  "voice": "alloy"
}
```

## Audio Transcription (STT)

```
POST /v1/audio/transcriptions
```

Transcribe audio to text. Accepts multipart form data with an audio file.

## Rerank

```
POST /v1/rerank
```

Rerank a list of documents by relevance to a query.

**Request:**

```json
{
  "model": "rerank-english-v2.0",
  "query": "What is machine learning?",
  "documents": [
    "Machine learning is a subset of AI.",
    "The weather is sunny today.",
    "Deep learning uses neural networks."
  ]
}
```

## List Models

```
GET /v1/models
```

List all available models. Returns an OpenAI-compatible model list.

**Response:**

```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-4o-mini",
      "object": "model",
      "created": 1700000000,
      "owned_by": "deltallm"
    }
  ]
}
```
