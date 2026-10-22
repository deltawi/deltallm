# Request shaping

Request shaping changes what DeltaLLM sends to a provider for one model deployment. Use it when a provider needs a parameter fixed, removed or added, needs a header from the client, or must be reached through a proxy.

| Field (`deltallm_params`) | What it does | Applies to |
| --- | --- | --- |
| `override_params` | Sets body fields. The value always wins over the client's value. Objects are merged. | Chat routes |
| `drop_params` | Removes body fields before the call. A dotted path removes a nested field, for example `chat_template_kwargs.extra`. | Chat routes |
| `forward_headers` | Copies these headers from the client request to the provider when the client sends them. | Chat routes |
| `extra_headers` | Sends these headers with a fixed value. | Chat routes |
| `session_header` | Always sends this header. The value is the client's, or a stable session ID from the gateway. | Chat routes |
| `outbound_proxy` | Sends every call and health check for this deployment through a named proxy. | All routes |

`model_info.default_params` still fills values that the client left out. The order is: provider translation, then `default_params`, then `override_params`, then `drop_params`.

Chat routes are `/v1/chat/completions`, `/v1/completions`, `/v1/messages`, `/v1/responses`, streaming, MCP tool loops and batch jobs that run chat models.

## Example: OpenCode Go

OpenCode Go rejects calls without `x-opencode-session` (`MissingSessionID`). This deployment always sends one and passes the project header through:

```yaml
model_list:
  - model_name: kimi-k2-go
    deltallm_params:
      provider: openai
      model: kimi-k2
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_GO_API_KEY
      session_header: x-opencode-session
      forward_headers: [x-opencode-project]
    model_info:
      mode: chat
```

When the client sends `x-opencode-session`, DeltaLLM forwards it. When it does not, DeltaLLM sends a stable ID for the conversation:

- If the request carries a gateway session ID (the one sticky routing uses), the header is derived from it.
- Otherwise it is derived from the API key, the user, the system prompt and the first user message, so each turn of a conversation gets the same ID.
- If neither exists, DeltaLLM sends a random ID.

The derived ID is a one-way hash shaped like a UUID. It never contains the client's key or prompt.

## Example: fix and remove parameters

```yaml
model_list:
  - model_name: qwen-no-think
    deltallm_params:
      provider: vllm
      model: Qwen/Qwen3-32B
      api_base: https://vllm.internal/v1
      override_params:
        chat_template_kwargs:
          enable_thinking: false
      drop_params: [logit_bias, parallel_tool_calls]
```

## Safety rules

DeltaLLM rejects a deployment that breaks these rules, both in `config.yaml` and in the admin API:

- Auth, credential and transport headers are never forwarded or set: `Authorization`, `Proxy-Authorization`, `Cookie`, `X-Api-Key`, `Api-Key`, `Host`, `Content-Length`, `Content-Type`, `Transfer-Encoding`, `Connection`, `X-Forwarded-*`, `X-Amz-*`, `X-Deltallm-*`, and any name that contains `auth`, `token`, `secret`, `key`, `cookie`, `credential`, `password` or `signature`.
- A header that the provider request already sets (for example the provider's auth header) is never replaced.
- Header values are at most 1024 bytes and cannot contain line breaks or control characters. A forwarded client value that breaks this rule is skipped.
- `model`, `messages`, `input`, `prompt`, `stream`, `stream_options`, `n`, `max_tokens`, `max_completion_tokens` and `max_output_tokens` cannot be overridden or dropped. They control admission and billing. The same applies to provider limit fields inside objects, such as `generationConfig.maxOutputTokens` or `inferenceConfig.maxTokens`.
- A field cannot be both overridden and dropped. At most 16 headers and 32 parameters per deployment.

`override_params` values are visible to anyone who can read the deployment. Do not put secrets in them. Use [provider credentials](../admin-ui/named-credentials.md) or `api_key` instead.

## Outbound proxies

An operator defines proxies by name in `general_settings`. A deployment can only pick one of these names:

```yaml
general_settings:
  upstream_proxies:
    corp-egress: os.environ/CORP_EGRESS_PROXY_URL   # http://user:pass@proxy.internal:3128
    eu-socks: socks5h://10.0.0.5:1080

model_list:
  - model_name: eu-mistral
    deltallm_params:
      provider: mistral
      model: mistral-large-latest
      api_key: os.environ/MISTRAL_API_KEY
      outbound_proxy: eu-socks
```

- Names use lowercase letters, digits, `-` and `_`. Supported schemes are `http`, `https`, `socks5` and `socks5h`. Cloud metadata hosts are rejected. At most 16 proxies.
- Proxy URLs are secret. The admin UI and API show them masked and list only the names (`GET /ui/api/models/outbound-proxies`).
- `upstream_proxies` is a startup setting. Change it in `config.yaml` or Helm values and restart. The settings API refuses a change with `409 restart_required`.
- A named proxy takes precedence over `HTTPS_PROXY` and `HTTP_PROXY` for that deployment.
- If a deployment names a proxy that the running gateway does not have, its calls fail with `503 outbound_proxy_unavailable` instead of going direct.
- Live model discovery in the admin UI does not use the named proxy.

Each named proxy has its own connection pool of `upstream_http_max_connections`. See [provider connection tuning](../deployment/upstream-http.md#named-outbound-proxies) for the capacity effect.

## Related pages

- [Model deployments](models.md)
- [Models UI](../admin-ui/models.md#parameters-headers-and-network)
- [Coding agents](../admin-ui/coding-agents.md)
