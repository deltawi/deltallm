# SGLang

[SGLang](https://docs.sglang.ai/) is a self-hosted inference server with an
OpenAI-compatible API. DeltaLLM routes chat and embedding deployments to it with
`provider: sglang`.

| Mode | DeltaLLM endpoint | SGLang endpoint |
| --- | --- | --- |
| Chat | `/v1/chat/completions`, plus `/v1/completions`, `/v1/responses` and `/v1/messages` through the chat path | `{api_base}/chat/completions` |
| Embedding | `/v1/embeddings` | `{api_base}/embeddings` |

Streaming, tool calls and `response_format` JSON schemas are passed to SGLang
unchanged. Whether they work depends on the model and the SGLang version.
DeltaLLM asks SGLang for the final stream usage chunk, so streamed calls are
billed on the token counts SGLang reports.

## Start the server

```bash
python -m sglang.launch_server \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 --port 30000 \
  --api-key "$SGLANG_API_KEY"
```

`--api-key` is optional. For an embedding model, add `--is-embedding`.

## Add the model

Admin UI: **Models > Add model**, provider **SGLang**:

- **Upstream model**: the model ID the server reports, for example
  `meta-llama/Llama-3.1-8B-Instruct`. **Refresh provider models** reads it from
  `{api_base}/models`.
- **API Base URL**: the server address with `/v1`, for example `http://sglang:30000/v1`.
- **API Key**: the server's `--api-key`. DeltaLLM needs a key for every
  OpenAI-compatible deployment, so enter any value when the server runs without one.

When the live model list is loaded, the model's context window (`max_model_len`)
fills the **Context Window** field.

Config file:

```yaml
model_list:
  - model_name: llama-local
    deltallm_params:
      provider: sglang
      model: meta-llama/Llama-3.1-8B-Instruct
      api_base: http://sglang:30000/v1
      api_key: os.environ/SGLANG_API_KEY
    model_info:
      mode: chat
      max_tokens: 131072
```

The model can also be written as `sglang/meta-llama/Llama-3.1-8B-Instruct`;
DeltaLLM removes the `sglang/` prefix before calling the server.

## Cached tokens

SGLang reuses the KV cache for prompts that start with the same text (RadixAttention).
To report the reused tokens, start the server with `--enable-cache-report`:

```bash
python -m sglang.launch_server \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 --port 30000 \
  --enable-cache-report
```

SGLang then sends `usage.prompt_tokens_details.cached_tokens`. DeltaLLM bills these
tokens at the model's cache-hit input price. Set **Cached Input Cost / Token** on the
model to give a discount. When the price is not set, cached tokens use the normal
input price. Without `--enable-cache-report`, all prompt tokens use the normal input
price.

## Credentials

An SGLang deployment never inherits the gateway's `OPENAI_API_KEY` or
`OPENAI_BASE_URL`. Set `api_base` and `api_key` on the deployment or on a
[named credential](../admin-ui/named-credentials.md). A gateway in front of SGLang that
expects another header can use `auth_header_name` and `auth_header_format`
([custom upstream auth headers](../configuration/models.md#custom-upstream-auth-headers)).

When DeltaLLM runs in Docker and SGLang runs on the host, use
`http://host.docker.internal:30000/v1`.

## Not supported

- Rerank, image generation, audio and realtime modes.
- vLLM's `guided_json` parameter. Use `response_format` with a JSON schema instead.
- SGLang-only routes such as `/generate` and `/get_model_info`.
