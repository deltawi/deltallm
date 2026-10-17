# Anthropic Messages Endpoint

DeltaLLM provides an Anthropic Messages API-compatible endpoint at `POST /v1/messages`.
It uses the same authentication, prompts, guardrails, model access, budgets, rate limits,
routing, failover, and accounting as `/v1/chat/completions`.

The provider hop depends on the deployment that serves the request:

| Deployment | Provider hop |
| --- | --- |
| `provider: anthropic` | **Native.** DeltaLLM sends your Messages body to Anthropic unchanged and returns the Anthropic response unchanged. |
| Any other provider | **Translation.** DeltaLLM converts the request to chat format and converts the answer back. |

Use the native hop for coding agents such as Claude Code. See
[Use coding agents](../guides/coding-agents.md).

## Authentication

Send the DeltaLLM key in one of these headers:

- `Authorization: Bearer YOUR_API_KEY`
- `x-api-key: YOUR_API_KEY` (the Anthropic SDK default)

If a request has both headers, DeltaLLM uses `Authorization`.
DeltaLLM accepts `x-api-key` only on `/v1/messages` and `/v1/models`.

## Example

```bash
curl http://localhost:8000/v1/messages \
  -H "x-api-key: YOUR_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello from DeltaLLM"}
    ]
  }'
```

The `model` value is a DeltaLLM public model name, resolved through the gateway's routing configuration like any other endpoint.

## Native hop (Anthropic deployments)

DeltaLLM forwards the request body as an open list. New Messages fields work without a
DeltaLLM change. DeltaLLM changes only these fields:

- `model`: the deployment's upstream model.
- `max_tokens`: the value after DeltaLLM output policy.
- `stream`: the request mode.
- `metadata`: DeltaLLM keeps only `user_id`. Other keys are DeltaLLM controls.

Everything else goes to Anthropic unchanged. This includes `system` in block form,
`cache_control`, `thinking` and signed thinking blocks, `image` and `document` blocks,
`top_k`, `output_config`, `context_management`, and server tools.

Headers:

- DeltaLLM forwards the client `anthropic-beta` header unchanged.
- The client `anthropic-version` header replaces the deployment's `api_version`.

Responses:

- A non-streaming response is the Anthropic JSON, unchanged.
- A streaming response relays each Anthropic SSE event in order, including `ping`,
  `thinking_delta`, and `signature_delta` events.

### Guardrails on the native hop

Guardrails and pre-call hooks read the text of the request. If a guardrail changes text
(for example, PII redaction), DeltaLLM writes the new text back into the text blocks of
that message. The changed message keeps one text block that holds all of its text.

If a hook adds or removes messages (for example, a prompt from the prompt registry),
DeltaLLM uses the translation hop for that request. If that request also has content that
only the native hop supports, DeltaLLM rejects it with a `400 invalid_request_error`.

### Usage and cost

Anthropic reports `input_tokens`, `cache_creation_input_tokens`, and
`cache_read_input_tokens` separately. DeltaLLM records:

- Prompt tokens: the sum of the three values.
- Cached prompt tokens: `cache_read_input_tokens`.

Cache reads use the model's `input_cost_per_token_cache_hit` price when it is set.
Other prompt tokens, which include cache writes, use `input_cost_per_token`.

## Translation hop (other providers)

The translation hop supports:

- `system` prompts (string or text-block form).
- `text`, `tool_use`, and `tool_result` content blocks, including the `is_error` flag on `tool_result` (forwarded as an `Error: ` prefix on the tool message content).
- Tool calling: `tools` and `tool_choice` (`auto`, `any`, `tool`, `none`).
- Streaming via `"stream": true`, with Anthropic SSE event types.
- `max_tokens` (required), `temperature`, `top_p`, `stop_sequences`, and `metadata`.

The translation hop ignores fields that have no chat equivalent and do not change the
content: `thinking`, earlier `thinking` blocks, `cache_control`, `citations`, `top_k`, and
other unknown fields.

The translation hop rejects content that it cannot carry with a `400 invalid_request_error`.
This includes `image` and `document` blocks and server tools such as `web_search`.

## Errors

Errors use the Anthropic error envelope:

```json
{
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "message": "messages[0].content[1]: content block type 'image' is supported only when the model is served by an Anthropic deployment"
  }
}
```

Each failure before the response starts uses this envelope.
This includes authentication, request validation, rate limits, budget exhaustion, and upstream or provider failures.
It maps the gateway status to an Anthropic error type:

- `authentication_error`.
- `permission_error`.
- `not_found_error`.
- `rate_limit_error`.
- `overloaded_error`.
- `api_error`.

On the native hop, an Anthropic `invalid_request_error` with status `400`, `413`, or `422`
keeps the Anthropic message text. The text describes the client's own request, and
Anthropic clients use it to recover automatically (for example, Claude Code turns off a
capability that the model rejects). DeltaLLM never forwards other provider messages or
response bodies.

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

## Limits

- DeltaLLM rejects `mcp_servers`, `container`, and content with a `file` source
  (`file_id`). These refer to objects in the deployment's Anthropic account, which all
  DeltaLLM tenants share. Send file content inline instead.
- DeltaLLM does not bill Anthropic server tools such as `web_search`. Anthropic charges
  the deployment's account for them.
- `/v1/messages/count_tokens` is not available. Claude Code then estimates context size.
- Amazon Bedrock Claude deployments use the translation hop.
- Anthropic `anthropic-ratelimit-*` response headers are not forwarded.
