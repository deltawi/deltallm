# OpenAI-compatible endpoints

Use the `openai_compatible` provider for any service that speaks the OpenAI API
and has no named preset in DeltaLLM. Examples are xAI, Cerebras, SambaNova,
Nvidia NIM, Moonshot, a LiteLLM or other gateway, or a server you host yourself.

The provider has no default address. You always enter the API base URL.

## Add a model in the dashboard

1. Open **Models** and choose **Add model**.
2. Pick **OpenAI-compatible** as the provider.
3. Enter the provider model exactly as the service names it, for example
   `grok-4` or `meta-llama/Llama-3.3-70B-Instruct`.
4. Enter the **API Base URL**, usually ending in `/v1`, for example
   `https://api.x.ai/v1`. Use a named credential to share it across models.
5. Enter the **API Key**. If the service needs none, enter any value:
   DeltaLLM always sends a key.
6. Optional: change **Auth Header Name** and **Auth Header Format** when the
   service does not use `Authorization: Bearer <key>`.

After you add the key and URL, **Refresh provider models** reads the service's
`GET /models` list. Context windows are filled when the service reports them.

## Configure it in YAML

```yaml
model_list:
  - model_name: grok
    deltallm_params:
      provider: openai_compatible
      model: grok-4
      api_base: https://api.x.ai/v1
      api_key: os.environ/XAI_API_KEY
    model_info:
      mode: chat
```

## What DeltaLLM sends

| Model type | Endpoint on the service |
| --- | --- |
| Chat | `POST /chat/completions` |
| Embeddings | `POST /embeddings` |
| Images | `POST /images/generations` |
| Speech | `POST /audio/speech` |
| Transcription | `POST /audio/transcriptions` |
| Rerank | `POST /rerank` |

Pick a model type only when the service really serves that endpoint.

Streaming chat requests ask for usage with `stream_options.include_usage`, so
spend is based on the token counts the service reports. Most compatible
servers accept this field. Prefer a named preset when one exists, because
presets carry provider-specific fixes.

## Safety

An `openai_compatible` deployment never uses the gateway's own OpenAI key or
`OPENAI_BASE_URL`. A missing API base or key fails the request instead of
reaching OpenAI with the platform's credentials.

DeltaLLM removes only an `openai_compatible/` prefix from the model name.
Model names such as `meta-llama/Llama-3.3-70B-Instruct` stay intact.

Set prices on the model before relying on spend reports. DeltaLLM has no
catalog prices for this provider.

When the service sends `usage.prompt_tokens_details.cached_tokens`, DeltaLLM bills
those prompt tokens at the model's cache-hit input price
(`input_cost_per_token_cache_hit`). When the price is not set, they use the normal
input price.
