# Providers

DeltaLLM supports native provider adapters and OpenAI-compatible upstreams. Configure a model
deployment with a provider, upstream model ID, credentials, and optional API base; applications
then call the deployment name or route group through DeltaLLM.

## Choose an integration path

| Upstream type | Configuration path |
| --- | --- |
| OpenAI, Anthropic, Azure OpenAI, Gemini, Bedrock, or ElevenLabs | Use the named provider adapter and its native authentication fields |
| OpenRouter, Groq, Together AI, Fireworks AI, DeepInfra, or Perplexity | Use the named OpenAI-compatible preset |
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

## Support boundaries

- Capability is declared per configured model mode, not inferred from marketing names.
- Provider-specific parameters are forwarded only where the adapter explicitly supports them.
- Unknown model IDs may work even when they are absent from the curated catalog; test them
  against the selected adapter.
- Model catalogs record their verification date and source links. Upstream availability and
  pricing can change after that date.
- Provider credentials are write-only operational secrets and should never appear in logs,
  documentation examples, client code, or public error bodies.
