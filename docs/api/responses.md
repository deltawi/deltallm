# OpenAI Responses Endpoint

DeltaLLM provides an OpenAI Responses API-compatible endpoint at `POST /v1/responses`.
It uses the same authentication, prompts, guardrails, model access, budgets, rate limits,
routing, failover, and accounting as `/v1/chat/completions`.

Coding agents such as Codex use this endpoint. See
[Use coding agents](../guides/coding-agents.md#codex).

The provider hop depends on the deployment that serves the request:

| Deployment | Provider hop |
| --- | --- |
| `provider: openai` with `api_base` on `api.openai.com` (or no `api_base`) | **Native.** DeltaLLM sends your Responses body to OpenAI and returns the OpenAI response unchanged. |
| `provider: azure` with an `api_base` that ends with `/openai/v1` | **Native**, as for OpenAI. |
| Any other deployment | **Translation.** DeltaLLM converts the request to chat format and converts the answer back to Responses format. |

## Example

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

The `model` value is a DeltaLLM public model name. The gateway resolves it through its
routing configuration, as for any other endpoint.

## Native hop (OpenAI and Azure deployments)

DeltaLLM forwards the request body as an open list. New Responses fields work without a
DeltaLLM change. DeltaLLM changes only these fields:

- `model`: the deployment's upstream model.
- `max_output_tokens`: the value after DeltaLLM output policy.
- `stream`: the request mode.
- `metadata` and `service_tier`: DeltaLLM removes them. `metadata` holds DeltaLLM
  controls, and `service_tier` changes the token price, which DeltaLLM pricing cannot show.

Everything else goes to the provider unchanged. This includes `instructions`, `developer`
messages, `reasoning`, `include` (for example `reasoning.encrypted_content`), encrypted
`reasoning` items, `text`, `prompt_cache_key`, `parallel_tool_calls`, function, custom and
namespace tools, and `web_search`.

Responses:

- A non-streaming response is the provider JSON, unchanged.
- A streaming response relays each provider SSE event in order.

### Guardrails on the native hop

Guardrails and pre-call hooks read the text of the request. If a guardrail changes text
(for example, PII redaction), DeltaLLM writes the new text back into the same text parts
of the body.

DeltaLLM uses the translation hop for the request when a hook adds or removes messages,
or changes tools, `tool_choice`, tool call arguments, `temperature` or `top_p`. If that
request also has content that only the native hop supports (for example, a
`web_search_call` item or a `local_shell` tool), DeltaLLM sends it only to native
deployments. If the model has no native deployment, DeltaLLM rejects the request with a
`400 invalid_request_error`.

## Translation hop (other providers)

The translation hop supports:

- `input` as a string or as a list of items: messages with `user`, `assistant`, `system`
  or `developer` roles (`developer` becomes a system message), `input_text`,
  `output_text` and `input_image` parts.
- `function_call` and `custom_tool_call` items, and their `*_output` items.
- `instructions`.
- `function` tools, `custom` tools, and `namespace` tools. A function in a namespace
  goes to the provider as `<namespace>__<name>`, and comes back with its `namespace` field.
- `tool_choice`, `text.format` (JSON mode and JSON schema), `temperature`, `top_p`, and
  `max_output_tokens`.
- Streaming, with Responses events: `response.created`, `response.in_progress`,
  `response.output_item.added` and `done`, `response.content_part.added` and `done`,
  `response.output_text.delta` and `done`, `response.reasoning_text.delta` and `done`,
  `response.function_call_arguments.delta` and `done`,
  `response.custom_tool_call_input.delta` and `done`, and `response.completed` (or
  `response.incomplete`) with usage.

The translation hop ignores `reasoning`, `reasoning` items from earlier turns,
`parallel_tool_calls`, `include`, `store`, and `prompt_cache_key`. A model's
`reasoning_effort` default parameter still applies. It removes `web_search` tools, because the provider has no hosted search.

The translation hop rejects other hosted tools and input items that it cannot carry with a
`400 invalid_request_error`.

## Usage and cost

On the native hop, DeltaLLM records:

- Prompt tokens: `input_tokens`.
- Completion tokens: `output_tokens`, which include reasoning tokens.

DeltaLLM bills cached input tokens (`input_tokens_details.cached_tokens`) at the normal
input price. This is the same as `/v1/chat/completions` for OpenAI and Azure deployments.

DeltaLLM does not bill hosted tool calls such as `web_search`. OpenAI charges the
deployment's account for them.

## Rejected fields

DeltaLLM rejects these fields with a `400 invalid_request_error`. They refer to objects in
the deployment's provider account, which all DeltaLLM tenants share, or they start work
that DeltaLLM cannot bill:

- `previous_response_id`, `conversation`, and stored `prompt` templates. Send the full
  conversation in `input` instead, with `store: false`.
- `background`.
- `item_reference` input items, and content with a `file_id`. Send file content inline.
- `file_search`, `code_interpreter`, `image_generation`, and hosted `shell` tools, and
  `mcp` tools with `server_url` or `connector_id`.

DeltaLLM gateway MCP tools (`{"type": "mcp", "server": "..."}`) work and use the
translation hop. See [MCP Gateway & Tooling](mcp.md).

## Errors

Errors use the OpenAI error envelope, as on `/v1/chat/completions`.

On the native hop, a provider `invalid_request_error` with status `400`, `413`, or `422`
keeps the provider message text. The text describes the client's own request. DeltaLLM
never forwards other provider messages or response bodies.

If a provider fails after a streaming `200` response has started, DeltaLLM does not retry
or replace the response. It sends one sanitized `response.failed` event (or the provider's
`error` event, sanitized) and closes the stream without `response.completed`.

`response.completed` delivery follows the shared
[streaming accounting contract](proxy.md#streaming-accounting).

## Limits

- `GET /v1/responses/{id}`, `DELETE /v1/responses/{id}`, `/v1/responses/input_tokens`, and
  `/v1/responses/compact` are not available.
- The response cache key includes the full request body.
- When a model has native and translation deployments, a failover between them can
  change the reasoning items that the client receives. Keep the deployments of one model
  on the same hop.
