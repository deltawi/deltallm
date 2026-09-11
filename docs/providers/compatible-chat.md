# DeepSeek, Z.ai, Qwen, Tencent, and MiniMax

These named integrations support the `chat` model mode through
`/v1/chat/completions`, including streaming and function tool calls.
Their upstream defaults control reasoning. Model support for individual
sampling parameters, tools, and structured output still varies.

| Provider ID | Service | Default API base |
| --- | --- | --- |
| `deepseek` | DeepSeek | `https://api.deepseek.com` |
| `zai` | Z.ai | `https://api.z.ai/api/paas/v4` |
| `qwen` | Alibaba Cloud Model Studio | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |
| `tencent` | Tencent TokenHub | `https://tokenhub-intl.tencentcloudmaas.com/v1` |
| `minimax` | MiniMax | `https://api.minimax.io/v1` |

## Configure a deployment

Choose the provider in **Models** and attach a named credential containing an
API-key secret reference. These integrations use `Authorization: Bearer`
authentication. Credentials remain server-side.
For direct Admin API model creation, include `api_base` in the named credential
or deployment parameters; the UI fills it from the selected provider preset.

A YAML bootstrap deployment can omit `api_base` to use the provider default:

```yaml
model_list:
  - model_name: direct-chat
    deltallm_params:
      provider: deepseek
      model: deepseek-v4-flash
      api_key: os.environ/DEEPSEEK_API_KEY
      timeout: 60
    model_info:
      mode: chat
```

Applications request `direct-chat`. Set the deployment's input, cached-input,
and output pricing before relying on spend calculations. The new curated
catalogs intentionally supply no prices: pricing depends on the vendor,
region, model, and contract. Missing prices retain the existing unpriced state.

The other provider IDs use the same configuration shape. Example model IDs are
`glm-5.3`, `qwen3.8-max`, `hy3`, and `MiniMax-M3`, respectively. The
[generated catalog](capabilities.md) records verification dates and official
sources. You can enter another valid upstream chat model ID.

## Regional endpoints

An explicit `api_base` overrides the default. For Qwen, use the endpoint and
API key for the same region/workspace. Workspace endpoints have the form
`https://{workspace}.{region}.maas.aliyuncs.com/compatible-mode/v1`.
Tencent also offers regional TokenHub endpoints. Use TokenHub API keys and the
model or enabled service ID supplied by Tencent; legacy Hunyuan SecretId/SecretKey
credentials are not the authentication mechanism for this integration.

DeltaLLM removes only the selected provider prefix: `deepseek/deepseek-v4-flash`
becomes `deepseek-v4-flash`. Vendor-owned IDs such as
`deepseek/deepseek-v4-flash` under the `tencent` provider remain intact.

References: [Qwen endpoints](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope),
[Tencent protocols and regions](https://www-sg.tencentcloud.com/ind/document/product/1300/80632).

## Chat parameters and reasoning

The initial request contract forwards `messages`, `stream`, `max_tokens`,
`temperature`, `top_p`, `stop`, `tools`, `tool_choice`, and
`response_format`. Omitted sampling fields use provider defaults. Only one
completion is supported. Nonzero presence/frequency penalties and `user`
are rejected by these integrations; neutral values are omitted.
DeltaLLM metadata stays inside the gateway.

Copy the complete assistant message into the next request when continuing a
tool call, including `tool_calls`, `reasoning_content`, and
`reasoning_details` when present. Reasoning strings are limited to 1,048,576
characters per assistant message; reasoning-details arrays allow 128 JSON objects.
These fields participate in cache identity and prompt estimation.
MiniMax requests use `reasoning_split: true` so returned reasoning is separate
from answer content. No arbitrary provider-specific request body is forwarded.

Streaming preserves reasoning and tool deltas. Usage is requested internally
where supported; clients can request a final usage chunk with
`stream_options: {"include_usage": true}`. Z.ai uses its native stream usage
behavior. Provider cache counters are normalized to `usage.prompt_tokens_cached`;
completion token totals already include reasoning and are not increased again.
Once reasoning or answer output has started, failures cannot trigger failover.

## Model discovery and health

DeepSeek, Tencent, and MiniMax use their authenticated models-list endpoint.
Qwen uses its native `/api/v1/models` endpoint and fetches one page of up to
100 entries; a warning indicates truncation. All new discovery paths allow at
most 2 MiB of response data and 500 models, use a ten-second total deadline,
and do not follow redirects. A discovery failure retains the curated catalog
and displays a sanitized warning.

Z.ai has no documented models-list endpoint in the reference used for this
integration. Its discovery returns catalog entries with a warning, and its
health probe reports unsupported without changing routing health. Automatic
health checks never generate billed inference as a substitute.

Successful model-list probes establish endpoint/authentication availability;
they do not certify every configured model or tool. Test the exact deployment
before granting application access.

## Rollback

Upgrade API replicas and batch workers before configuring these provider IDs.
Before rolling back to an older release, disable their deployments and stop
scheduling their batch work. No schema migration or additional infrastructure
is required. Response-cache schema version 5 combines reasoning-history support
with selector-policy identity and prevents reuse of entries from either earlier
branch format. Old entries expire under their existing TTLs.

## Discovery authorization and outbound policy

Live model discovery and manual health checks require platform-administrator
permission, including completed MFA when enabled. Master-key headers and master
sessions remain supported. Anonymous requests receive 401; other authenticated
roles receive 403 before credential/deployment lookup or any DNS/HTTP work.
Provider presets and ordinary model reads retain their existing access policy.

New-provider discovery and scheduled health checks share a bootstrap-owned
outbound policy. Public HTTPS on port 443 is allowed by default. Configure
`general_settings.provider_discovery_allow_http`,
`provider_discovery_allowed_ports`, and `provider_discovery_allowed_private_cidrs`
in startup YAML for controlled development or private/VPC endpoints. These fields
require a restart; named credentials and discovery request bodies cannot override
them. Provider and batch-webhook allowances are independent. Metadata services
remain denied even within an explicitly allowed CIDR.

All DNS answers must pass policy. The connection uses the validated IP with the
original Host, TLS SNI, and certificate verification. Redirects are rejected.
Discovery uses direct egress, bypassing environment proxies; other control-client
callers retain their existing proxy routing and CA trust. The control transport
uses HTTP/1.1 and zero idle connections to prevent TLS reuse across hostnames
sharing an IP. This adds one TCP/TLS setup per control request; inference pooling
is unchanged. There is no switch to restore unsafe reuse.

Each process admits at most 32 operations and 32 waiters, with a 100 ms queue
limit, at most 2 seconds for DNS, and a 10-second total operation deadline. Pool
acquisition uses at most 80% of the deadline remaining after admission and DNS,
respecting any shorter configured pool timeout. This lets local pool exhaustion
be classified before the total deadline expires. The direct control pool remains
limited to 100 active connections. Maximum discovery concurrency is
`32 × API processes × maximum API replicas`. Responses are limited
to 2 MiB after decompression and 500 models (Qwen: one page of 100). Policy or
local-capacity denial returns the catalog with a warning and does not penalize
deployment health. Z.ai discovery remains catalog-only with zero DNS/HTTP calls.

Monitor `deltallm_provider_discovery_total{outcome}` and
`deltallm_provider_discovery_seconds` for policy/capacity failures and latency.
Labels never contain destinations, credentials, or provider bodies.

Roll out authorization and transport changes together before enabling private
allowances. Restrict these two operator actions at ingress during mixed-version
rollouts if non-admin sessions can reach old replicas. To roll back, disable the
affected deployments and discovery/health actions before reverting the expansion.
This change secures the five new profiles' discovery/health paths. Legacy provider
inference, legacy discovery, and MCP endpoint transport require their own egress
migration; this release does not certify those destinations.
