# Anthropic Messages Endpoint

DeltaLLM exposes an Anthropic Messages API-compatible endpoint at `POST /v1/messages`. Requests are converted to the gateway's canonical chat format and routed through the same deployment routing, budgets, rate limits, and failover as `/v1/chat/completions`, so the target model can be served by any configured provider — not only Anthropic.

## Authentication

The endpoint uses the same authentication as every other proxy endpoint: `Authorization: Bearer YOUR_API_KEY`.

!!! note
    The Anthropic SDKs send the API key in an `x-api-key` header by default, which this endpoint does **not** accept. Configure your client to send a bearer token instead. With the Python SDK: `Anthropic(auth_token="YOUR_API_KEY", base_url="http://localhost:8000")`.

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

Gateway-level errors (rate limits, budget exhaustion, upstream failures) return the same status codes as the OpenAI-compatible endpoints.
