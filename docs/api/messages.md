# Anthropic Messages Endpoint

DeltaLLM provides an Anthropic Messages API-compatible endpoint at `POST /v1/messages`.
DeltaLLM converts requests to its standard chat format.
It uses the same deployment routing, budgets, rate limits, and failover as `/v1/chat/completions`.
The target model can use a configured provider other than Anthropic.

## Authentication

The endpoint uses the same authentication as every other proxy endpoint: `Authorization: Bearer YOUR_API_KEY`.

The Anthropic SDKs send the API key in an `x-api-key` header by default.
This endpoint does not accept that header.

Configure your client to send a bearer token.
For the Python SDK, use `Anthropic(auth_token="YOUR_API_KEY", base_url="http://localhost:8000")`.

## Example

```bash
curl http://localhost:8000/v1/messages \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello from DeltaLLM"}
    ]
  }'
```

The `model` value is a DeltaLLM public model name, resolved through the gateway's routing configuration like any other endpoint.

## Supported

- Text conversations with `system` prompts (string or text-block form)
- `text`, `tool_use`, and `tool_result` content blocks, including the `is_error` flag on `tool_result` (forwarded as an `Error: ` prefix on the tool message content)
- Tool calling: `tools`, `tool_choice` (`auto`, `any`, `tool`, `none`), including full pass-through to Anthropic and Bedrock deployments in their native formats
- Streaming via `"stream": true` — responses use Anthropic SSE event types (`message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`), including streamed tool use
- `max_tokens` (required), `temperature`, `top_p`, `stop_sequences`, `metadata`

## Not Supported

Unsupported input is rejected with a `400 invalid_request_error` — never silently dropped. This includes:

- `image` and `document` content blocks (and any other non-text, non-tool block types)
- `top_k`
- Extended thinking (top-level `thinking` field), prompt caching (`cache_control` on any block), citations (`citations` on text blocks), and any other unrecognized top-level field or block field

## Errors

Errors use the Anthropic error envelope:

```json
{
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "message": "messages[0].content[1]: unsupported content block type 'image'; this endpoint only forwards text and tool blocks"
  }
}
```

Each failure before the response starts uses this envelope.
This includes authentication, request validation, rate limits, budget exhaustion, and upstream or provider failures.
DeltaLLM never forwards provider response bodies or messages.
It maps the gateway status to an Anthropic error type:

- `authentication_error`.
- `permission_error`.
- `not_found_error`.
- `rate_limit_error`.
- `overloaded_error`.
- `api_error`.

For rate limits, DeltaLLM keeps a valid `Retry-After` header.

Gateway errors use the same HTTP status codes as the OpenAI-compatible endpoints.
An unclassified upstream `401`, `403`, or `404` is a deployment or configuration failure.
DeltaLLM can try another healthy deployment.
After all failover attempts fail, the client receives a sanitized `503 overloaded_error`.
A trusted content-policy or context-window classification remains a terminal `400 invalid_request_error`.

If a provider fails after a streaming `200` response has already emitted content, DeltaLLM does not
retry or replace the response. It emits one sanitized Anthropic `event: error` frame and closes the
stream without a `message_stop` event.

`message_stop` delivery follows the shared text endpoint's configured accounting
writer and required audit. Closing at that event does not require draining the
HTTP response to EOF. In `spend_ingestion_mode: outbox`, failed durable charge
acceptance leaves the stream incomplete without `message_stop`; it does not cause
a second provider attempt. Required audit failure or finalization timeout also
withholds the terminal event.

The default `legacy` writer can swallow database-write failures, so `message_stop`
in that mode does not prove that billing was persisted. See the shared
[streaming accounting contract](proxy.md#streaming-accounting) for the mode-specific guarantees.
