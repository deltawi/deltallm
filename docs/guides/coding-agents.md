# Use coding agents

You can send coding-agent traffic, such as Claude Code or Codex, through DeltaLLM. DeltaLLM then
applies keys, budgets, rate limits, guardrails, and usage reports to each developer.

## Claude Code

Claude Code uses the Anthropic Messages API. Use a model that has a deployment with
`provider: anthropic`. DeltaLLM then sends each request to Anthropic unchanged, so
extended thinking, prompt caching, images, and beta features work. See
[Anthropic Messages](../api/messages.md#native-hop-anthropic-deployments).

1. Create an application key for the developer. Set a budget and an expiry date on the key.
   See [Application keys](../admin-ui/api-keys.md).
2. Give the key access to the Claude models that the developer can use.
3. Set these environment variables for the developer:

```bash
export ANTHROPIC_BASE_URL="https://your-gateway.example.com"
export ANTHROPIC_API_KEY="YOUR_DELTALLM_KEY"
# Use your DeltaLLM model names.
export ANTHROPIC_MODEL="claude-sonnet-4-6"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-haiku-4-5"
```

4. Start `claude`. Each request uses the DeltaLLM key.

Notes:

- Claude Code can read the model list from `GET /v1/models`. Set
  `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` to turn this on. Claude Code shows only
  model names that contain `claude` or `anthropic`.
- Set the model price `input_cost_per_token_cache_hit`. Claude Code reads most of its
  prompt from the Anthropic prompt cache, and DeltaLLM bills cache reads at this price.
- If the model also has deployments with other providers, those deployments use the
  translation hop. On those deployments, thinking and prompt caching are not available.
- Claude Code counts tokens with `POST /v1/messages/count_tokens`. DeltaLLM asks
  Anthropic for the count, or returns an estimate for other providers. DeltaLLM does not
  bill these calls. See [Count tokens](../api/messages.md#count-tokens).

## Codex

Codex uses the OpenAI Responses API. Use a model that has a deployment with
`provider: openai` on `api.openai.com`, or `provider: azure` with an `api_base` that ends
with `/openai/v1`. DeltaLLM then sends each request to the provider unchanged, so
reasoning, encrypted reasoning items, and all Codex tools work. See
[OpenAI Responses](../api/responses.md#native-hop-openai-and-azure-deployments).

Other deployments use the translation hop. Codex function tools work on this hop, but
`web_search` is not available and reasoning is not kept between turns.

1. Create an application key for the developer, as for Claude Code. The
   [Coding agents](../admin-ui/coding-agents.md) page also gives the setup text.
2. Add a DeltaLLM provider to `~/.codex/config.toml`:

```toml
# Use your DeltaLLM model name.
model = "gpt-5-codex"
model_provider = "deltallm"

[model_providers.deltallm]
name = "DeltaLLM"
base_url = "https://your-gateway.example.com/v1"
env_key = "DELTALLM_API_KEY"
wire_api = "responses"
```

3. Set `DELTALLM_API_KEY` to the DeltaLLM key, then start `codex`.

Notes:

- Codex sends `store: false` and the full conversation in each request. DeltaLLM does not
  support `previous_response_id`.
- DeltaLLM does not bill `web_search` calls. OpenAI charges the deployment's account for them.
