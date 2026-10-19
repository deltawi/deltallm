# Providers

DeltaLLM supports native provider adapters and OpenAI-compatible upstreams.
Configure a model deployment with a provider, upstream model ID, credentials, and optional API base.
Applications then call the deployment name or route group through DeltaLLM.

## Choose an integration path

| Upstream type | Configuration path |
| --- | --- |
| OpenAI, Anthropic, Azure OpenAI, Gemini, Bedrock, or ElevenLabs | Use the named provider adapter and its native authentication fields |
| OpenRouter, Groq, Together AI, Fireworks AI, DeepInfra, or Perplexity | Use the named OpenAI-compatible preset |
| DeepSeek, Z.ai, Qwen, Tencent TokenHub, or MiniMax | Use a named [compatible chat integration](compatible-chat.md) |
| Mistral AI | Use the [Mistral AI integration](mistral.md) for chat and embeddings |
| vLLM, LM Studio, or Ollama | Supply the reachable API base for the self-hosted server |
| Another OpenAI-compatible service | Configure its provider/model prefix and explicit API base, then verify the required endpoint and parameters |

The generated [capability and model catalog](capabilities.md) is derived from the runtime
provider registry. It is the source of truth for the provider modes DeltaLLM currently routes.

## Minimal deployment

```yaml
model_list:
  - model_name: app-chat
    deltallm_params:
      provider: openai
      model: gpt-5-mini
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      mode: chat
```

Applications request `app-chat`; the upstream model and credential remain server-side. In
production, prefer named credential records or an external secret resolver over embedding
provider secrets in a committed configuration file.

Continue to [Model deployments](../configuration/models.md) for database-managed definitions,
credential behavior, pricing metadata, and model modes. Use [Routing and failover](../features/routing.md)
to combine deployments behind one route group.

## vLLM JSON constraints

For a deployment with `provider: vllm`, `/v1/chat/completions` accepts a
top-level `guided_json` field. Supply a JSON Schema object or a string that
contains a JSON Schema object. This also supports clients that use LiteLLM's
`extra_body={"guided_json": schema}`.

```json
{
  "model": "private-chat",
  "messages": [{"role": "user", "content": "Return an invoice number and total."}],
  "guided_json": {
    "type": "object",
    "properties": {
      "invoice_number": {"type": "string"},
      "total": {"type": "number"}
    },
    "required": ["invoice_number", "total"],
    "additionalProperties": false
  }
}
```

The vLLM adapter sends this constraint as:

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {"name": "guided_json", "schema": "<the complete schema object>"}
  }
}
```

The `schema` value above represents the original object. DeltaLLM does not
remove or change its properties. vLLM documents this format in
[version 0.9.2](https://docs.vllm.ai/en/v0.9.2/features/structured_outputs.html)
and [version 0.12.0](https://docs.vllm.ai/en/v0.12.0/features/structured_outputs/).
Version 0.12.0 removed the upstream `guided_json` field. The translation avoids
that removed field. Use a vLLM version and model that support JSON Schema output.

Do not supply both `guided_json` and a non-null `response_format`.
Invalid field types, invalid JSON strings, and conflicting fields return HTTP
400. Other provider adapters reject a non-null `guided_json` with HTTP 400.
A null `guided_json` adds no constraint. Existing `response_format` requests
keep their current behavior. A vLLM rejection returns an error; DeltaLLM does
not retry the request without its constraint.

The same translation applies to normal and streaming requests. It uses no
additional database, Redis, or HTTP calls. The request owns the schema; no
shared schema state is added. Cache identity includes `guided_json`, including
when a caller supplies a custom cache key or configured field list. Cache
namespace `v7` prevents use of earlier entries that could have lost the schema.
Existing authentication and tenant cache scope still apply.

### Check a real vLLM deployment

Configure a test model alias with `provider: vllm`. Use a test key that can call
that alias. Set `DELTALLM_TEST_API_KEY`, then run:

```bash
uv run python scripts/check_vllm_guided_json.py \
  --gateway-url http://localhost:4000 \
  --vllm-url http://localhost:8000 \
  --model private-chat
```

The vLLM URL is the server root, without `/v1`. The check reads `/version`, then
sends three real generation requests through DeltaLLM: `guided_json` as an
object, `guided_json` as a string, and standard `response_format`. The prompt
asks for plain text. Each result must contain only the two required fields
with the values fixed by the schema. Cached responses are bypassed. A failed
request or schema check stops the command with a nonzero exit status.
Run this check against the exact vLLM version and model before deployment.

## Support boundaries

- Capability is declared per configured model mode, not inferred from marketing names.
- Provider-specific parameters are forwarded only where the adapter explicitly supports them.
- Unknown model IDs may work even when they are absent from the curated catalog; test them
  against the selected adapter.
- Model catalogs record their verification date and source links. Upstream availability and
  pricing can change after that date.
- Provider credentials are write-only operational secrets and should never appear in logs,
  documentation examples, client code, or public error bodies.
