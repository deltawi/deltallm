# Mistral AI

Use provider ID `mistral` for chat and embeddings. The default API base is
`https://api.mistral.ai/v1`. Requests use `Authorization: Bearer` with your
Mistral API key.

## Configure a deployment

In **Models**, select **Mistral AI**. Attach a named credential with an API-key
secret reference. Select a chat or embedding model. The UI uses the provider's
default API base. For direct Admin API calls, include `api_base` in the named
credential or deployment parameters.

You can also use YAML to configure the initial deployments:

```yaml
model_list:
  - model_name: mistral-chat
    deltallm_params:
      provider: mistral
      model: mistral-small-latest
      api_key: os.environ/MISTRAL_API_KEY
    model_info:
      mode: chat
  - model_name: mistral-embeddings
    deltallm_params:
      provider: mistral
      model: mistral-embed
      api_key: os.environ/MISTRAL_API_KEY
    model_info:
      mode: embedding
```

Applications request `mistral-chat` or `mistral-embeddings` through DeltaLLM.
An explicit `api_base` takes priority over the default. Mistral deployments do
not inherit the platform's OpenAI key or API base.
The optional `mistral/` model prefix is removed before the upstream request.

Set input and output prices for your account before you use spend limits.
The catalog does not supply prices. Missing prices keep the existing unpriced
state. The [model catalog](capabilities.md) lists six model IDs and their sources.
You can enter another valid model ID.

## Chat requests

Chat supports text, streaming, function tools, and `response_format`.
Model support for tools and JSON output can differ. Omitted sampling fields
use Mistral defaults. The request contract supports one completion and the
parameters in the [compatible chat guide](compatible-chat.md#chat-parameters-and-reasoning).
Nonzero presence and frequency penalties and `user` are not supported.

Mistral sends stream usage without OpenAI's `stream_options` field. DeltaLLM
removes this field from upstream requests. Clients can still request a final
usage chunk with `stream_options: {"include_usage": true}`.
Tool messages keep the tool-call ID that Mistral returns.
After output starts, a stream failure cannot start a retry or failover.

## Embedding requests

Use `mistral-embed` for text or `codestral-embed` for code. Input must be a
string or an array of strings. Token arrays and the `user` parameter are not
supported. DeltaLLM maps OpenAI's `dimensions` field to Mistral's
`output_dimension` field. The selected model must support the requested size.

## Discovery and health

Live discovery and health checks use the authenticated `/v1/models` endpoint.
They use the separate control HTTP pool and the existing
[discovery authorization and outbound policy](compatible-chat.md#discovery-authorization-and-outbound-policy).
The limits are 2 MiB, 500 models, and ten seconds per operation.
Failed discovery keeps the catalog and shows a sanitized warning.
Health checks do not make billed inference calls.

Discovery uses `capabilities.completion_chat` for chat models. Mistral does
not supply an embedding capability flag. DeltaLLM uses catalog IDs and their
returned aliases to identify embedding models. Other model types and archived
models are excluded from live results. Enter an unknown embedding ID manually.
A successful health check confirms access to the models endpoint. Test the
exact deployment before you grant application access.

## Rollout and rollback

Update API replicas and batch workers before you configure Mistral deployments.
Before a rollback to an earlier release, disable these deployments and stop
their scheduled batch work. This integration needs no schema migration or new
infrastructure. It uses the existing credential, admission, accounting, and
retry services. Each inference attempt makes one upstream request.

References: [Chat API](https://docs.mistral.ai/api),
[Embeddings API](https://docs.mistral.ai/api/endpoint/embeddings), and
[Models API](https://docs.mistral.ai/api/endpoint/models).
