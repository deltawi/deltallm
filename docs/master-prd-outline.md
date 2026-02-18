# Master PRD Outline - DeltaLLM: LiteLLM Replication Program

## 1. Document Control
- **Version:** 1.0 (Complete PRD)
- **Date:** 2026-02-13
- **Owner:** Lead (consolidation), with domain research from CoreProxy, RouteCacheGuard, ObsMetricsBilling, ProviderAPI
- **Status:** Complete - All sections researched and drafted

## 2. Program Goal and Scope
Build a production-grade, OpenAI-compatible LLM gateway that replicates LiteLLM core proxy capabilities first, then extends to routing/caching/guardrails, observability/metrics/billing, and deployment/ops/integrations.

**Target Feature Parity:** LiteLLM v1.30-v1.60+ (OSS tier), with architectural foundations enabling enterprise features.

**Key Numbers:**
- 100+ LLM providers supported
- 15+ API endpoint categories
- 20+ admin/management API endpoints
- 10+ logging/observability integrations
- 6+ routing strategies
- 8+ guardrail provider integrations
- 5+ authentication mechanisms

## 3. Product Principles
- OpenAI API compatibility at client edge.
- Config-driven provider/model abstraction.
- Strong governance: auth, keys, quotas, budgets.
- Reliability-by-default: retries, timeouts, deterministic failover.
- Multi-tenant attribution for spend and access control.
- Graceful degradation when optional dependencies (Redis, external services) are unavailable.
- Extensibility through callbacks, custom guardrails, and pass-through endpoints.

## 4. Baseline Feature Matrix (Program-Wide)

| Domain | Feature | Priority | Status | Owner | Notes |
|---|---|---|---|---|---|
| **Core Proxy** | OpenAI-compatible endpoints (`/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `/v1/models`) | P0 | Drafted | CoreProxy | |
| Core Proxy | Provider abstraction adapters + param mapping | P0 | Drafted | CoreProxy | Initial: OpenAI/Azure/Anthropic |
| Core Proxy | Model registry (`model_list`) + model groups | P0 | Drafted | CoreProxy | Client model maps to group |
| Core Proxy | Request lifecycle pipeline | P0 | Drafted | CoreProxy | auth -> limits -> route -> provider -> usage write |
| Core Proxy | Auth baseline (`master_key`, virtual keys) | P0 | Drafted | CoreProxy | DB + cache lookup path |
| Core Proxy | Core limits (key/user/team rate + budget) | P0 | Drafted | CoreProxy | enforcement + reset semantics |
| Core Proxy | Reliability basics (retries, timeout, fallback) | P0 | Drafted | CoreProxy | deterministic policy order |
| Core Proxy | OpenAI SDK/client compatibility | P0 | Drafted | CoreProxy | base_url swap model |
| Core Proxy | Alias layers (`model_group_alias`, per-key alias) | P1 | Drafted | CoreProxy | backward compatibility |
| Core Proxy | Route controls and access tiers | P1 | Drafted | CoreProxy | admin/public restrictions |
| Core Proxy | Advanced auth/RBAC parity | P2 | Drafted | CoreProxy | enterprise-gap risk |
| **Routing/Caching/Guardrails** | Simple shuffle + least-busy LB | P0 | Drafted | RouteCacheGuard | |
| Routing/Caching/Guardrails | Model group routing + failover + cooldowns | P0 | Drafted | RouteCacheGuard | |
| Routing/Caching/Guardrails | Redis + in-memory cache backends | P0 | Drafted | RouteCacheGuard | |
| Routing/Caching/Guardrails | Cache key composition + TTL | P0 | Drafted | RouteCacheGuard | |
| Routing/Caching/Guardrails | Custom guardrail hook framework | P0 | Drafted | RouteCacheGuard | |
| Routing/Caching/Guardrails | Latency/cost/usage-based routing | P1 | Drafted | RouteCacheGuard | |
| Routing/Caching/Guardrails | Tag-based + priority routing | P1 | Drafted | RouteCacheGuard | |
| Routing/Caching/Guardrails | Per-request cache control + streaming cache | P1 | Drafted | RouteCacheGuard | |
| Routing/Caching/Guardrails | Presidio PII + Lakera prompt injection | P1 | Drafted | RouteCacheGuard | |
| Routing/Caching/Guardrails | Semantic cache (Qdrant) | P2 | Drafted | RouteCacheGuard | |
| Routing/Caching/Guardrails | Streaming guardrails (during_call) | P2 | Drafted | RouteCacheGuard | |
| **Observability/Metrics/Billing** | Callback system (success/failure/stream hooks) | P0 | Drafted | ObsMetricsBilling | |
| Observability/Metrics/Billing | Prometheus metrics + labels | P0 | Drafted | ObsMetricsBilling | |
| Observability/Metrics/Billing | Token cost calculation + spend ledger | P0 | Drafted | ObsMetricsBilling | |
| Observability/Metrics/Billing | Budget enforcement (hard + soft) + alerts | P0 | Drafted | ObsMetricsBilling | |
| Observability/Metrics/Billing | Langfuse + OpenTelemetry integrations | P1 | Drafted | ObsMetricsBilling | |
| Observability/Metrics/Billing | Spend query APIs + export | P1 | Drafted | ObsMetricsBilling | |
| Observability/Metrics/Billing | Custom pricing overrides | P1 | Drafted | ObsMetricsBilling | |
| Observability/Metrics/Billing | Lago billing integration | P2 | Drafted | ObsMetricsBilling | |
| Observability/Metrics/Billing | Cost anomaly detection | P2 | Drafted | ObsMetricsBilling | |
| **Deployment/Ops/Integrations** | Docker + K8s deployment topologies | P0 | Drafted | DeployOps | |
| Deployment/Ops/Integrations | PostgreSQL + Redis state dependencies | P0 | Drafted | DeployOps | |
| Deployment/Ops/Integrations | Health endpoints (liveness/readiness/model) | P0 | Drafted | DeployOps | |
| Deployment/Ops/Integrations | YAML config + env var interpolation | P0 | Drafted | DeployOps | |
| Deployment/Ops/Integrations | SSO (OAuth2/OIDC) + JWT auth | P1 | Drafted | DeployOps | |
| Deployment/Ops/Integrations | DB config hot-reload + model hot-add | P1 | Drafted | DeployOps | |
| Deployment/Ops/Integrations | Secret manager integration (AWS/GCP/Azure) | P1 | Drafted | DeployOps | |
| Deployment/Ops/Integrations | Admin UI dashboard | P1 | Drafted | DeployOps | |
| Deployment/Ops/Integrations | Enterprise features (audit log, advanced RBAC) | P2 | Drafted | DeployOps | |
| **Provider/API Surface** | 100+ LLM provider adapters | P0 | Drafted | ProviderAPI | |
| Provider/API Surface | Chat completions + embeddings + models endpoints | P0 | Drafted | ProviderAPI | |
| Provider/API Surface | Streaming (SSE) support | P0 | Drafted | ProviderAPI | |
| Provider/API Surface | Tool/function calling translation | P0 | Drafted | ProviderAPI | |
| Provider/API Surface | Responses API (`/v1/responses`) | P1 | Drafted | ProviderAPI | |
| Provider/API Surface | Image generation + audio + rerank endpoints | P1 | Drafted | ProviderAPI | |
| Provider/API Surface | Pass-through endpoints | P1 | Drafted | ProviderAPI | |
| Provider/API Surface | Admin management APIs (keys/users/teams/models) | P0 | Drafted | ProviderAPI | |

---

## 5. Detailed Requirements - Core Proxy (Baseline)

### 5.1 API Compatibility Surfaces
#### P0 Requirements
- Accept OpenAI-like payloads for chat/completions, responses, embeddings.
- Provide models discovery endpoint compatible with common OpenAI SDK flows.

#### Acceptance Criteria
1. Existing OpenAI Python/JS clients work by setting `base_url` to proxy endpoint.
2. Request/response payloads preserve expected top-level fields used by mainstream SDKs.
3. Unsupported endpoint usage returns consistent, documented error schema.

### 5.2 Provider Abstraction
#### P0 Requirements
- Implement adapter interface to translate canonical request into provider-specific requests.
- Support per-deployment provider params via config.

#### Acceptance Criteria
1. Same client-facing request schema executes against at least 3 provider families.
2. Provider-specific settings are isolated to config, not client payload contract.
3. Adapter errors map to proxy-standard error types.

### 5.3 Model Registry and Aliases
#### P0 Requirements
- `model_list` registry with request model mapped to model group/deployments.

#### P1 Requirements
- Add alias mechanisms (`model_group_alias`, per-key alias maps).

#### Acceptance Criteria
1. Multiple backends can back one public model name/group.
2. Alias changes can remap model requests without client change.
3. Resolved model/group appears in request metadata/logs.

### 5.4 Request Lifecycle
#### P0 Requirements
- Canonical pipeline: auth -> policy checks -> routing -> provider execution -> async usage logging.

#### Acceptance Criteria
1. Every request has a unique request ID propagated through logs/events.
2. Denied requests fail before provider invocation.
3. Successful calls enqueue usage/spend accounting update.

### 5.5 Auth and Key Management
#### P0 Requirements
- Master key protected admin routes.
- Virtual key generation and validation with persistence.

#### P1 Requirements
- Custom auth header support and scoped route permissions.

#### Acceptance Criteria
1. Admin APIs reject non-master credentials.
2. Generated virtual keys can scope allowed models and expiry.
3. Key validation path supports cache-first with DB fallback.

### 5.6 Virtual Keys / Projects
#### P0 Requirements
- Associate keys to `user_id` and optionally `team_id/project` for attribution and control.

#### Acceptance Criteria
1. Spend and usage are queryable by key, user, and team/project dimensions.
2. Team/project policy can override individual defaults when defined.

### 5.7 Rate Limits and Budgets
#### P0 Requirements
- Enforce rpm/tpm/parallel limits and max budget at core scopes.
- Support reset windows/durations for budget policies.

#### P1 Requirements
- Budget templates/tiers reusable across users/teams.

#### Acceptance Criteria
1. Requests breaching limits fail deterministically with machine-readable error metadata.
2. Budget reset windows apply predictably and are auditable.
3. Limit counters remain accurate in multi-instance mode with shared state backend.

### 5.8 Config Formats
#### P0 Requirements
- YAML config supporting `model_list`, `router_settings`, `litellm_settings`, `general_settings`, `environment_variables`.

#### P1 Requirements
- Config composition/include support and startup validation diagnostics.

#### Acceptance Criteria
1. Invalid config fails fast with actionable validation errors.
2. Effective loaded model groups/settings can be inspected at startup/admin path.

### 5.9 SDK/Client Compatibility
#### P0 Requirements
- Interoperate with OpenAI SDK patterns and OpenAI-compatible frameworks.

#### Acceptance Criteria
1. OpenAI Python and JS examples run with `base_url`/key change only.
2. At least one orchestration framework integration path is validated.

### 5.10 Error Handling, Retries, Timeouts, Fallback Basics
#### P0 Requirements
- Standardized proxy error schema with provider error mapping.
- Configurable retries, global/per-model timeout, and fallback chains.

#### Acceptance Criteria
1. Retry policy does not retry non-retryable classes.
2. Timeout behavior is deterministic for both standard and streaming flows.
3. Fallback chain order and termination rules are observable and documented.

### 5.11 Migration Considerations
#### P0 Requirements
- Provide migration guidance for base URL swap, model alias staging, and key transition.

#### Acceptance Criteria
1. Migration guide includes compatibility table and known deviations.
2. Rollout pattern supports shadow/test mode before strict enforcement.

---

## 6. Detailed Requirements - Routing, Caching, Guardrails

### 6.1 Routing - Load Balancing Strategies

#### P0 Requirements

**Simple Shuffle (Default)**
- Random selection across healthy deployments within a model group.
- All deployments have equal probability unless weight-modified.

**Least Busy**
- Route to deployment with fewest in-flight (active) requests.
- Track active request counts in-memory or Redis (multi-instance).

**Failover / Fallback Chains**
- Sequential retry through ordered deployment list on failure.
- Model-group-level fallbacks: `fallbacks` config maps primary model group to fallback model groups.
- Context window fallbacks: `context_window_fallbacks` routes to larger-context model when input exceeds primary model's window.
- Content policy fallbacks: `content_policy_fallbacks` for provider content filter rejections.

#### P1 Requirements

**Latency-Based Routing**
- Route to deployment with lowest observed average latency (rolling window).
- Weighted moving average using configurable time decay.

**Cost-Based Routing**
- Route to cheapest deployment first based on `input_cost_per_token` / `output_cost_per_token`.

**Usage-Based Routing**
- Balance across deployments based on TPM/RPM utilization.
- Track current-window usage per deployment, route to least-utilized.

**Tag-Based Routing**
- Deployments labeled with tags (e.g., `"tags": ["premium", "eu-region"]`).
- Requests specify `metadata.tags` to filter eligible deployments.

**Priority-Based Routing**
- Deployments assigned integer priority (0 = highest).
- All priority-0 deployments tried first; fallback to priority-1 only when all priority-0 exhausted.

**Weighted Routing**
- `weight` parameter per deployment controls traffic distribution ratio.

**Rate-Limit Aware Routing (RPM/TPM)**
- `enable_pre_call_checks: true` in `router_settings`.
- Before routing, check remaining RPM/TPM capacity per deployment.
- Skip deployments near rate limits.

#### P2 Requirements
- Usage-Based Routing v2 (smoother distribution algorithm).

#### Configuration
```yaml
router_settings:
  routing_strategy: "simple-shuffle"  # or least-busy, latency-based-routing, cost-based-routing, usage-based-routing
  num_retries: 3
  retry_after: 5
  timeout: 300
  cooldown_time: 60
  allowed_fails: 3
  enable_pre_call_checks: true

litellm_settings:
  fallbacks:
    - {"gpt-4": ["gpt-3.5-turbo"]}
  context_window_fallbacks:
    - {"gpt-4": ["gpt-4-32k"]}
  content_policy_fallbacks:
    - {"claude-3": ["gpt-4"]}
```

#### Acceptance Criteria
1. Each routing strategy selects deployments according to its documented algorithm.
2. Unhealthy/cooled-down deployments are excluded from selection.
3. Fallback chains execute in declared order and terminate on success or exhaustion.
4. Strategy is configurable per proxy instance via `router_settings.routing_strategy`.
5. Tag/priority routing correctly filters the candidate deployment pool before applying the LB strategy.

---

### 6.2 Routing - Cooldown Mechanisms

#### P0 Requirements
- When a deployment returns an error, it enters a cooldown period.
- `cooldown_time` in `router_settings` (default: 60 seconds).
- `allowed_fails` threshold before cooldown activates (default: 0 = immediate).
- Cooldown state stored in Redis (shared across instances) or in-memory (single instance).
- During cooldown, the deployment is skipped by the router.

#### Acceptance Criteria
1. A deployment enters cooldown after `allowed_fails + 1` consecutive failures.
2. Cooldown duration matches `cooldown_time` setting.
3. In multi-instance mode with Redis, cooldown state is shared across all instances.
4. Deployment automatically re-enters the healthy pool after cooldown expires.
5. `cooldown_deployment` alert fires when a deployment enters cooldown.

---

### 6.3 Routing - Health Checks

#### P1 Requirements

**Background Health Checks**
- `background_health_checks: true` in `general_settings`.
- `health_check_interval: <seconds>` (default: 300).
- Background task sends lightweight test request to each deployment periodically.
- Failed deployments marked unhealthy and excluded from routing.
- `health_check_model` optionally specifies a lightweight model for cost-efficient health probes.

**Passive Health Tracking**
- Real request failures increment failure counters.
- Deployments record last error, last success time, consecutive failure count.

#### Acceptance Criteria
1. Background health checks run at configured interval.
2. Unhealthy deployments are excluded from routing until next successful health check.
3. `/health` endpoint returns per-deployment health status including last error and success timestamps.
4. Health check cost is minimized via configurable health check model.

---

### 6.4 Caching - Backends

#### P0 Requirements

**In-Memory Cache**
- Simple dictionary-based cache for single-instance/development deployments.
- No persistence across restarts.

**Redis Cache**
- Production cache backend.
- Configuration: `redis_host`, `redis_port`, `redis_password` or `redis_url`.
- Supports Redis Cluster and Redis Sentinel topologies.
- SSL/TLS support via `redis_ssl: true`.

#### P1 Requirements
- Redis Cluster (`redis_cluster_nodes`) and Redis Sentinel (`redis_sentinel_nodes`, `redis_sentinel_master_name`).
- S3 cache backend for large/persistent cache storage.

#### P2 Requirements
- Qdrant semantic cache (vector similarity for semantically similar queries).
- Disk cache backend for local filesystem caching.

#### Configuration
```yaml
litellm_settings:
  cache: true
  cache_params:
    type: "redis"            # redis, redis-semantic, s3, local, qdrant-semantic, disk
    host: "localhost"
    port: 6379
    password: "os.environ/REDIS_PASSWORD"
    ttl: 3600                # default TTL in seconds
    # For semantic cache:
    # similarity_threshold: 0.8
    # qdrant_api_base: "http://localhost:6333"
```

#### Acceptance Criteria
1. In-memory cache provides functional caching for single-instance deployments.
2. Redis cache is shared across proxy instances.
3. Cache backend is swappable via `cache_params.type` configuration.
4. Cache operations degrade gracefully (log + skip) when backend is unavailable.

---

### 6.5 Caching - Key Composition and TTL

#### P0 Requirements
- Cache key composed from: `model`, `messages`, `temperature`, `top_p`, `max_tokens`, `n`, `stop`, `tools`, `tool_choice`, and other deterministic parameters.
- Keys are SHA256 hashed for consistent length and Redis compatibility.
- Global TTL via `cache_params.ttl` (default: 3600 seconds).

#### P1 Requirements
- Per-request TTL override via `Cache-TTL` header or `metadata.cache_ttl`.
- `caching_groups` parameter for configurable cache key field composition.
- Per-request cache control directives: `no-cache` (skip read), `no-store` (skip write).
- Custom cache key override via `metadata.cache_key`.

#### Acceptance Criteria
1. Identical requests produce cache hits.
2. Requests differing in any cache key component produce cache misses.
3. Global TTL applies when no per-request TTL is specified.
4. `no-cache` skips cache read but writes the new response.
5. `no-store` skips cache write but reads from cache if hit exists.

---

### 6.6 Caching - Streaming Behavior

#### P1 Requirements
- Cache hits for streaming requests return reconstructed SSE stream from cached complete response.
- Streaming responses are fully assembled from all chunks before cache write.
- Partially streamed responses (client disconnect) are NOT cached.

#### Acceptance Criteria
1. Cache hits for streaming requests return properly formatted SSE chunks.
2. Only complete streaming responses are written to cache.
3. Incomplete streams do not pollute the cache.

#### Risks
- Reconstructed SSE stream timing may differ from real-time stream.
- Memory pressure from assembling large streaming responses before cache write.

---

### 6.7 Caching - Metrics

#### P1 Requirements
- `x-litellm-cache-hit: true/false` response header.
- Cache hit/miss events emitted through callback system (Prometheus, Langfuse, custom).
- Cache key available in response metadata for debugging.

#### Acceptance Criteria
1. Every response includes cache hit/miss header.
2. Cache hit ratio is calculable from emitted Prometheus metrics.

---

### 6.8 Caching - Supported Endpoints

| Endpoint | Cacheable | Priority | Notes |
|---|---|---|---|
| `/chat/completions` | Yes | P0 | Primary caching target |
| `/completions` | Yes | P0 | Legacy text completions |
| `/embeddings` | Yes | P0 | Deterministic, excellent cache candidates |
| `/responses` | Yes | P1 | OpenAI Responses API |
| `/audio/transcriptions` | Yes | P2 | Audio transcription caching |
| `/images/generations` | No | - | Non-deterministic |

---

### 6.9 Guardrails - Architecture

#### P0 Requirements

**Custom Guardrail Framework**
- `CustomGuardrail` base class with lifecycle hooks:
  - `async_pre_call_hook(user_api_key_dict, cache, data, call_type)` - inspect/modify input, raise to block
  - `async_post_call_success_hook(data, user_api_key_dict, response)` - inspect output, raise to block
  - `async_post_call_failure_hook(request_data, original_exception, user_api_key_dict)` - error handling
  - `async_moderation_hook(data, user_api_key_dict, call_type)` - moderation-specific check
- Registration via YAML config or programmatic `litellm.callbacks`.

**Enforcement Points**
1. **Pre-call (input):** Before LLM provider call. For prompt injection, PII masking, content policy.
2. **Post-call (output):** After LLM response, before returning to client. For content filtering, PII detection.
3. **During-call (streaming):** On streaming chunks. P2 scope.

#### Configuration
```yaml
litellm_settings:
  guardrails:
    - guardrail_name: "my-custom-guard"
      litellm_params:
        guardrail: "path.to.module.MyCustomGuardrail"
        mode: "pre_call"             # pre_call, post_call, during_call
        default_on: true             # apply to all requests by default
```

#### Acceptance Criteria
1. Custom guardrail hooks receive correct request/response data at each lifecycle point.
2. Exceptions in pre_call hooks block the request before provider invocation.
3. Exceptions in post_call hooks block the response before client delivery.
4. Custom guardrails are configurable via YAML.

---

### 6.10 Guardrails - Built-in Integrations

| Integration | Type | Priority | Description |
|---|---|---|---|
| **Presidio** | Pre+Post | P1 | Microsoft PII detection/anonymization (names, emails, phones, SSNs, credit cards, etc.) |
| **Lakera Guard** | Pre-call | P1 | Prompt injection detection, PII, content moderation (SaaS API) |
| **Aporia AI** | Pre+Post | P1 | Content filtering, prompt injection, topic control, data leakage prevention |
| **AWS Bedrock Guardrails** | Pre+Post | P1 | AWS-native content filtering, denied topics, word filters, sensitive info filters |
| **Google Text Safety** | Pre+Post | P2 | Toxicity scoring, identity attack, profanity, threat detection |
| **LLM Guard** | Pre+Post | P2 | Open-source: prompt injection, toxicity, bias, PII, regex patterns |
| **OpenAI Moderation** | Pre-call | P2 | Content classification (hate, self-harm, sexual, violence) |
| **Azure Content Safety** | Pre+Post | P2 | Azure AI text moderation |

---

### 6.11 Guardrails - Enforcement Modes

#### P1 Requirements

| Mode | Behavior | Use Case |
|---|---|---|
| **Block** (default) | Guardrail raises exception, request/response rejected | Production enforcement |
| **Log** | Guardrail logs violation but allows request to proceed | Shadow mode, monitoring, gradual rollout |

#### Acceptance Criteria
1. Block mode returns error to client with appropriate status code and guardrail name.
2. Log mode allows the request to proceed while recording the violation.
3. Mode is configurable per guardrail instance.

---

### 6.12 Guardrails - Per-Key Assignment

#### P1 Requirements
- Virtual keys can specify `guardrails` list on creation (`POST /key/generate`).
- Keys without explicit assignment use guardrails with `default_on: true`.
- Per-request guardrail override via `metadata.guardrails`.

#### Acceptance Criteria
1. Key-level guardrail assignments are enforced for that key's requests.
2. Default guardrails apply when no key-specific assignment exists.
3. Per-request overrides work when permitted by key policy.

---

### 6.13 Cross-Module Integration: Request Lifecycle with All Modules

```
Client Request
  |
  v
1. Auth/Key Validation (cache-first, DB fallback)
  |
  v
2. Rate Limit Check (RPM/TPM/parallel)
  |
  v
3. Pre-call Guardrails (PII masking, prompt injection, content filter)
  |
  v
4. Cache Lookup
  |-- Cache HIT --> 7 (skip routing + provider)
  |-- Cache MISS --> 5
  v
5. Router: select deployment (LB strategy, cooldowns, tags, priorities)
  |
  v
6. Provider Call (with retries/failover across deployments)
  |
  v
7. Post-call Guardrails (output content filter, PII detection)
  |
  v
8. Cache Write (if cache miss path)
  |
  v
9. Usage/Spend Logging (async, non-blocking)
  |
  v
Client Response
```

---

### 6.14 Routing/Caching/Guardrails - Out of Scope (Current)
- ML-based anomaly detection for routing decisions.
- Custom routing strategy plugin API (use code-level extension).
- Multi-region cache replication.
- Real-time guardrail model training/fine-tuning.

### 6.15 Routing/Caching/Guardrails - Risks and Unknowns

**Routing Risks:**
1. Multi-instance cooldown/latency state requires Redis for consistency. In-memory fallback only works single-instance.
2. RPM/TPM estimation before completion is approximate, may cause suboptimal routing.
3. Tag routing + fallback interaction: fallback model groups may not have matching tags.
4. Priority routing with small pools: no LB within a single-deployment priority tier.

**Caching Risks:**
1. No built-in cache invalidation beyond TTL. Model updates may serve stale responses until TTL expires.
2. Semantic cache false positives: similar but contextually different queries may return wrong cached response.
3. Streaming cache memory pressure under high concurrency.
4. Redis as single point of failure for cache. Need graceful degradation (disable cache or fallback to in-memory).

**Guardrails Risks:**
1. Latency overhead: each guardrail adds latency, external API guardrails (Lakera, Aporia) add network round-trip.
2. Streaming guardrail buffering negates streaming latency benefits.
3. False positive rates in content filtering may block legitimate requests.
4. External guardrail service unavailability: need fail-open vs fail-closed policy.
5. PII masking before provider may degrade response quality when personal context is needed.
6. Guardrail ordering across multiple hooks on same point needs deterministic order.

---

## 7. Detailed Requirements - Observability, Metrics, Billing

### 7.1 Callback System Architecture

#### P0 Requirements

**Callback Registration**
- `success_callback`: List of callback names triggered on successful LLM calls.
- `failure_callback`: List of callbacks triggered on failed LLM calls.
- `callbacks`: General callbacks/hooks for both success and failure paths.
- Support both built-in integrations (string names) and custom classes (Python module paths).

**CustomLogger Base Class**
```python
class CustomLogger:
    def log_pre_api_call(self, model, messages, kwargs): ...
    def log_post_api_call(self, kwargs, response_obj, start_time, end_time): ...
    def log_success_event(self, kwargs, response_obj, start_time, end_time): ...
    def log_failure_event(self, kwargs, response_obj, start_time, end_time): ...
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time): ...
    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time): ...
    async def async_log_stream_event(self, kwargs, response_obj, start_time, end_time): ...
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type): ...
    async def async_post_call_success_hook(self, data, user_api_key_dict, response): ...
    async def async_post_call_failure_hook(self, request_data, original_exception, user_api_key_dict): ...
```

**Standard Logging Payload (`kwargs`)**

| Field | Type | Description |
|---|---|---|
| `model` | str | Requested model name |
| `messages` | list | Input messages array |
| `response_obj` | ModelResponse | Full response object |
| `response_cost` | float | Calculated cost in USD |
| `usage` | Usage | Token usage (prompt, completion, total) |
| `call_type` | str | "completion", "embedding", etc. |
| `stream` | bool | Whether streaming was used |
| `litellm_call_id` | str | Unique call ID |
| `request_id` | str | Proxy request ID |
| `api_key` | str | Hashed API key |
| `user` | str | User ID |
| `team_id` | str | Team ID |
| `metadata` | dict | User-provided metadata |
| `cache_hit` | bool | Cache hit status |
| `start_time` | datetime | Request start |
| `end_time` | datetime | Request end |

#### Configuration
```yaml
litellm_settings:
  success_callback: ["langfuse", "prometheus", "s3"]
  failure_callback: ["langfuse", "sentry"]
  callbacks: ["custom_module.MyCallback"]
```

#### Acceptance Criteria
1. All registered callbacks receive the standard payload on success/failure events.
2. Async callbacks are non-blocking to response delivery.
3. Callback failures do not affect the client response.
4. Custom callbacks integrate via Python module path or programmatic registration.

---

### 7.2 Built-in Logging Integrations

| Integration | Callback String | Priority | Description |
|---|---|---|---|
| **Langfuse** | `"langfuse"` | P1 | Tracing, prompt management, cost tracking |
| **OpenTelemetry** | `"otel"` | P1 | OTLP HTTP/gRPC export, W3C trace context |
| **Prometheus** | `"prometheus"` | P0 | Metrics endpoint at `/metrics` |
| **Datadog** | `"datadog"` | P1 | DogStatsD/APM integration |
| **LangSmith** | `"langsmith"` | P2 | LangChain observability |
| **S3** | `"s3"` | P1 | Log storage to S3 buckets |
| **GCS Bucket** | `"gcs_bucket"` | P2 | Log storage to GCS |
| **Sentry** | `"sentry"` | P2 | Error tracking |
| **Helicone** | `"helicone"` | P2 | LLM observability |
| **Lunary** | `"lunary"` | P2 | LLM observability |
| **Braintrust** | `"braintrust"` | P2 | LLM evaluation |

---

### 7.3 Log Redaction / PII in Logs

#### P1 Requirements
- `turn_off_message_logging: true` in `litellm_settings` suppresses message/response content from all callbacks.
- Per-key control via `permissions.turn_off_message_logging: true`.
- When enabled, `messages` and `response` fields replaced with `"redacted-by-litellm"`.
- Does NOT affect the response to the client -- only logging/callbacks.

#### Acceptance Criteria
1. Redaction setting prevents message content from reaching any logging integration.
2. Metadata, cost, and usage data remain available in logs.
3. Per-key redaction overrides global setting for that key's requests.

---

### 7.4 Correlation IDs and Tracing

#### P0 Requirements
- Every request gets a unique `litellm_call_id` (UUID).
- `x-litellm-call-id` response header returned to client.
- `request_id` can be passed by client or auto-generated.

#### P1 Requirements
- Metadata supports arbitrary trace correlation: `trace_id`, `span_id`, `generation_name`, `generation_id`.
- OpenTelemetry integration propagates W3C trace context headers.
- Langfuse `trace_id` connects multiple calls into a single trace.

#### Acceptance Criteria
1. Every response includes a unique correlation ID header.
2. All logging callbacks receive the correlation ID.
3. Client-provided trace IDs are propagated through the callback system.

---

### 7.5 Prometheus Metrics

#### P0 Requirements

**Request Metrics:**

| Metric | Type | Description |
|---|---|---|
| `litellm_requests_metric` | Counter | Total LLM API requests |
| `litellm_request_total_latency_metric` | Histogram | End-to-end request latency |
| `litellm_llm_api_latency_metric` | Histogram | Provider API latency only |
| `litellm_request_failures_metric` | Counter | Total failed requests |

**Token & Cost Metrics:**

| Metric | Type | Description |
|---|---|---|
| `litellm_input_tokens_metric` | Counter | Total input tokens |
| `litellm_output_tokens_metric` | Counter | Total output tokens |
| `litellm_spend_metric` | Counter | Total spend in USD |

**Cache Metrics:**

| Metric | Type | Description |
|---|---|---|
| `litellm_cache_hit_metric` | Counter | Cache hits |
| `litellm_cache_miss_metric` | Counter | Cache misses |

**Deployment Health:**

| Metric | Type | Description |
|---|---|---|
| `litellm_deployment_state` | Gauge | 0=healthy, 1=partial, 2=degraded |
| `litellm_deployment_latency_per_output_token` | Gauge | Latency per output token |
| `litellm_remaining_team_budget_metric` | Gauge | Remaining budget per team |
| `litellm_remaining_api_key_budget_metric` | Gauge | Remaining budget per key |

**Common Labels:** `model`, `api_provider`, `user`, `team`, `api_key` (hashed), `status_code`, `cache_hit`.

**Cardinality Controls:**
```yaml
litellm_settings:
  prometheus_label_settings:
    disable_end_user_label: true
    disable_api_base_label: true
```

#### Acceptance Criteria
1. `/metrics` endpoint returns Prometheus-compatible exposition format.
2. All listed metrics are emitted with correct labels.
3. Cardinality controls allow disabling high-cardinality labels.

---

### 7.6 Token Cost Calculation

#### P0 Requirements
- Internal model cost map with per-model pricing: `input_cost_per_token`, `output_cost_per_token`.
- Support for cached token pricing (`input_cost_per_token_cache_hit`).
- `completion_cost()` function calculates cost from model + usage.
- Cost calculated on every successful request and included in callback payload as `response_cost`.

#### P1 Requirements
- Custom pricing overrides per deployment in config.
- `model_info.input_cost_per_token` / `output_cost_per_token` in `model_list` entries.
- `litellm.register_model()` for runtime pricing registration.

#### Acceptance Criteria
1. Cost is calculated for every successful LLM call.
2. Custom pricing overrides take precedence over default model pricing.
3. Cost includes correct handling of cached tokens at discounted rate.

---

### 7.7 Spend Tracking and Ledger

#### P0 Requirements

**Spend Tracking Hierarchy:** Organization > Team > Key > End User.

**Database Tables (Prisma schema):**

- `LiteLLM_SpendLogs` -- Per-request spend ledger:
  - `request_id`, `call_type`, `api_key`, `spend`, `total_tokens`, `prompt_tokens`, `completion_tokens`, `startTime`, `endTime`, `model`, `api_base`, `user`, `team_id`, `end_user`, `metadata`, `cache_hit`, `cache_key`, `request_tags`

- `LiteLLM_VerificationToken` -- API keys with cumulative `spend`, `max_budget`, `budget_duration`, `budget_reset_at`.

- `LiteLLM_UserTable` -- Users with `spend`, `max_budget`, `budget_duration`.

- `LiteLLM_TeamTable` -- Teams with `spend`, `max_budget`, `model_max_budget` (per-model caps).

- `LiteLLM_OrganizationTable` -- Organizations with `spend`, `max_budget`.

- `LiteLLM_EndUserTable` -- End-user spend tracking.

- `LiteLLM_BudgetTable` -- Reusable budget configurations.

**Spend Write Path:**
1. LLM call completes -> cost calculated via `completion_cost()`.
2. Cost + usage written to `LiteLLM_SpendLogs`.
3. Cumulative `spend` incremented on key, user, team, org rows.
4. Budget checks compare updated `spend` against `max_budget` / `soft_budget`.

#### Acceptance Criteria
1. Every successful request creates a spend log entry.
2. Cumulative spend is accurately incremented at all hierarchy levels.
3. Spend is queryable by key, user, team, org, model, tag, and date range.

---

### 7.8 Budget Enforcement

#### P0 Requirements

**Hard Budget (`max_budget`):**
- `spend >= max_budget` -> subsequent requests blocked with HTTP 400.
- Checked in pre-call auth middleware.
- Applies at key, user, team, org levels. Most restrictive wins.

**Budget Duration Reset:**
- `budget_duration`: `"1h"`, `"1d"`, `"7d"`, `"30d"`, `"1mo"`.
- `budget_reset_at` stores next reset timestamp.
- Background job resets spend to 0 and recalculates next reset.

#### P1 Requirements

**Soft Budget (`soft_budget`):**
- `spend >= soft_budget` -> alert triggered (Slack/webhook/email) but requests continue.
- Available on keys and teams.

**Model-Level Budget (`model_max_budget`):**
- Per-model caps within a team/key: `{"gpt-4": 100.0, "gpt-3.5-turbo": 50.0}`.
- Blocks requests to specific models when sub-budget exceeded.

#### Acceptance Criteria
1. Hard budget blocks requests deterministically with clear error message.
2. Soft budget triggers alerts without blocking.
3. Budget reset occurs automatically at configured intervals.
4. Model-level budgets enforce independently within the entity's overall budget.

---

### 7.9 Budget Alerts

#### P1 Requirements

**Alert Configuration:**
```yaml
general_settings:
  alerting: ["slack", "email", "webhook"]
  alerting_threshold: 80          # percentage of budget
  alert_types:
    - budget_alerts
    - daily_reports
    - llm_exceptions
    - llm_too_slow
    - cooldown_deployment
    - outage_alerts
    - db_exceptions
  alerting_args:
    slack_webhook_url: "https://hooks.slack.com/..."
    daily_report_frequency: 43200
    budget_alert_ttl: 86400
```

**Alert Types:**
- `budget_alerts` -- spend approaching/exceeding budget thresholds
- `daily_reports` -- daily usage/spend summary
- `llm_exceptions` -- LLM API errors
- `llm_too_slow` -- response time exceeding `alerting_threshold`
- `cooldown_deployment` -- deployment entering cooldown
- `outage_alerts` -- all deployments for a model group down

#### Acceptance Criteria
1. Alerts fire to configured destinations (Slack, email, webhook) at threshold crossing.
2. Alert content includes entity identifier, current spend, budget, percentage.
3. Duplicate alerts are suppressed within `budget_alert_ttl` window.

---

### 7.10 Spend Query APIs

#### P1 Requirements

| Endpoint | Method | Description |
|---|---|---|
| `GET /spend/logs` | GET | Query spend logs with filters (api_key, user_id, team_id, model, date range) |
| `GET /spend/tags` | GET | Spend grouped by request tags |
| `GET /global/spend` | GET | Global spend summary |
| `GET /global/spend/report` | GET | Detailed report with model/provider breakdown |
| `GET /global/spend/keys` | GET | Spend per API key |
| `GET /global/spend/teams` | GET | Spend per team |
| `GET /global/spend/end_users` | GET | Spend per end-user |
| `GET /global/spend/models` | GET | Spend per model |
| `GET /global/activity` | GET | Activity metrics (request counts, tokens) |

**Request Tags for Custom Attribution:**
- Clients pass `metadata.tags: ["project-alpha", "team-ml"]` in requests.
- Tags stored in `LiteLLM_SpendLogs.request_tags`.
- Queryable via `/spend/tags` for arbitrary cost attribution.

#### Acceptance Criteria
1. All spend query endpoints return accurate, filtered results.
2. Date range filtering works correctly.
3. Tag-based attribution provides arbitrary cost grouping beyond key/user/team hierarchy.

---

### 7.11 Observability/Metrics/Billing - Out of Scope (Current)
- ML-based cost anomaly detection (use Prometheus/Grafana alerting rules instead).
- Invoice generation (integrate with Lago or external billing system).
- Real-time cost prediction before completion.

### 7.12 Observability/Metrics/Billing - Risks and Unknowns
1. Spend write path performance: every request triggers DB writes to spend logs + cumulative spend updates. Need batching/buffering strategy under high throughput.
2. Budget enforcement accuracy in multi-instance: spend increments need atomic operations or accept eventual consistency window.
3. Cost map staleness: model pricing changes require model cost map updates. Need update mechanism.
4. Prometheus cardinality explosion with many models/keys/users: cardinality controls are essential.
5. Log volume management: `LiteLLM_SpendLogs` grows unbounded. Need retention/archival policy.

---

## 8. Detailed Requirements - Deployment/Ops & Integrations

### 8.1 Deployment Topologies

#### P0 Requirements

**Single Instance**
- Docker container or direct Python process.
- In-memory caching, local rate limiting.
- Optional PostgreSQL for key/spend persistence.
- Suitable for development and low-traffic production.

**Multi-Instance / HA**
- Multiple proxy instances behind a load balancer.
- Shared Redis for: cache, rate limit counters, cooldown state, config pub/sub.
- Shared PostgreSQL for: keys, users, teams, spend, config.
- No sticky sessions required (stateless proxy).

**Docker Deployment:**
```bash
docker run -d --name deltallm \
  -p 4000:4000 \
  -v /path/to/config.yaml:/app/config.yaml \
  -e OPENAI_API_KEY=sk-... \
  -e DATABASE_URL=postgresql://... \
  deltallm:latest \
  --config /app/config.yaml --port 4000
```

**Kubernetes:**
- Helm chart with Deployment, Service, ConfigMap, Secrets.
- Liveness probe: `GET /health/liveliness`.
- Readiness probe: `GET /health/readiness`.
- HPA based on CPU/request metrics.

#### Acceptance Criteria
1. Single-instance deployment works without Redis or PostgreSQL (degraded mode).
2. Multi-instance deployment shares state correctly via Redis + PostgreSQL.
3. Docker image starts with config file and environment variables.
4. Kubernetes probes correctly reflect instance health.

---

### 8.2 State Dependencies

#### P0 Requirements

**PostgreSQL (Persistent State):**
- Stores: keys, users, teams, orgs, spend logs, config, audit logs, budgets.
- Connection string via `DATABASE_URL` env var or `database_url` in `general_settings`.
- `database_connection_pool_limit` (default: 100).
- `database_connection_timeout` (default: 60 seconds).
- Prisma ORM for schema management and migrations.
- Migrations are additive (no destructive changes). `prisma db push` on startup.

**Redis (Ephemeral Shared State):**
- Stores: API key validation cache, rate limit counters, response cache, routing state (cooldowns, health), config change pub/sub, distributed locks.
- Configuration: `redis_host`, `redis_port`, `redis_password` or `redis_url`.
- Redis Sentinel and Redis Cluster support.
- SSL/TLS via `redis_ssl: true`.

**Graceful Degradation:**
- If Redis unavailable: caching disabled, rate limiting falls back to in-memory (per-instance), cooldown state local only.
- If PostgreSQL unavailable: key validation fails (unless cached in Redis), spend logging queues locally.

#### Acceptance Criteria
1. Proxy starts successfully with only PostgreSQL (no Redis).
2. Proxy operates in degraded mode when Redis is temporarily unavailable.
3. Connection pool limits are enforced.
4. Migrations run automatically on startup without data loss.

---

### 8.3 Health Check Endpoints

#### P0 Requirements

| Endpoint | Auth | Description |
|---|---|---|
| `GET /health/liveliness` | None | Returns `{"status": "healthy"}` if process alive. No dependency checks. K8s liveness probe. |
| `GET /health/readiness` | None | Checks DB + Redis connectivity. Returns 503 if critical deps unreachable. K8s readiness probe. |
| `GET /health` | Required | Deep health check including per-model deployment health. Accepts `?model=<name>` filter. |

#### Acceptance Criteria
1. Liveness probe responds within 100ms.
2. Readiness probe accurately reflects DB and Redis connectivity.
3. Health endpoint returns per-deployment status (last error, last success, consecutive failures).

---

### 8.4 Configuration Lifecycle

#### P0 Requirements

**YAML Config Loading:**
- Config path via `--config` CLI flag or `LITELLM_CONFIG_PATH` env var.
- Top-level sections: `model_list`, `router_settings`, `litellm_settings`, `general_settings`, `environment_variables`.
- Fail-fast on invalid config with actionable error messages.

**Environment Variable Interpolation:**
- `os.environ/<VARNAME>` syntax anywhere a string value is expected.
- Unresolvable references cause startup failure.

**Secret Manager Integration (P1):**
- `aws_secret_manager/<secret_name>` for AWS Secrets Manager.
- `google_kms/<secret_name>` for Google Secret Manager.
- `azure_key_vault/<secret_name>` for Azure Key Vault.

#### P1 Requirements

**DB-Based Dynamic Config:**
- `POST /config/update` stores config in `LiteLLM_Config` table.
- DB config merged with file config (DB takes precedence).
- Redis pub/sub notifies all instances to reload.

**Model Hot-Add/Remove:**
- `POST /model/new` adds deployment without restart.
- `POST /model/delete` removes deployment without restart.

#### Acceptance Criteria
1. Invalid config fails fast with descriptive error message.
2. Environment variables resolve correctly in all config fields.
3. DB config updates propagate to all instances within seconds.
4. Model additions/removals take effect without proxy restart.

---

### 8.5 Security & Authentication

#### P0 Requirements

**Master Key:**
- Set via `LITELLM_MASTER_KEY` env var or `master_key` in `general_settings`.
- Required for all admin APIs (key/user/team/config management).
- Never stored in DB (memory/env only).
- Keys stored as SHA-256 hashes in DB. `LITELLM_SALT_KEY` adds salt.

**Virtual Key Authentication:**
- `POST /key/generate` creates virtual keys with scoped permissions.
- Key params: `models`, `max_budget`, `rpm_limit`, `tpm_limit`, `duration`, `user_id`, `team_id`, `tags`, `permissions`.
- Validation: Redis cache first -> DB fallback. Check expiry, budget, model access.
- Custom auth header via `litellm_key_header_name`.

#### P1 Requirements

**SSO (OAuth2/OIDC):**
- Microsoft Entra ID, Google OAuth, Okta, Generic OIDC provider support.
- `PROXY_ADMIN_EMAIL_LIST` for SSO -> admin role mapping.
- `default_team_id` for auto-assigning SSO users.

**JWT Token Validation:**
- `enable_jwt_auth: true` in `general_settings`.
- `jwt_public_key_url` for JWKS endpoint.
- JWT claims mapping to team/user identities.

**Custom Auth Handlers:**
- `custom_auth` setting pointing to Python function: `async def custom_auth(api_key, request) -> UserAPIKeyAuth`.

#### P2 Requirements

**RBAC Roles:**

| Role | Permissions |
|---|---|
| `proxy_admin` | Full admin access |
| `proxy_admin_viewer` | Read-only admin |
| `team_admin` | Manage own team's keys/members |
| `internal_user` | Create own keys within team constraints |
| `internal_user_viewer` | Read-only, view own keys/spend |
| `end_user` | Tracked for spend attribution, no direct auth |

**IP Allowlisting:**
- `allowed_ips` in `general_settings`.
- Enterprise: CIDR support.

**Audit Logging (Enterprise):**
- `LiteLLM_AuditLog` table: records all admin actions with `changed_by`, `action`, `before_value`, `updated_values`.

#### Acceptance Criteria
1. Master key is required for all admin operations.
2. Virtual keys enforce model access, budget, and rate limits.
3. SSO login flow works end-to-end with role assignment.
4. JWT validation supports configurable claims mapping.

---

### 8.6 Admin UI / Dashboard

#### P1 Requirements
- React-based dashboard at `/ui` path.
- Admin UI requires master key or SSO login.
- **Features:**
  - Model management: view, add, edit, delete deployments with health status.
  - Key management: create, edit, delete, regenerate keys with full parameter control.
  - Usage/spend dashboards: per-key, per-user, per-team, per-model, per-tag with time-series charts.
  - Team/user management: create teams, assign members with roles, view spend.
  - Log viewer: filterable request logs with detail view.
  - Settings management: view/edit proxy configuration.
  - Self-serve key portal: SSO users generate keys within team constraints (when `allow_user_auth: true`).
- Customization: `custom_ui_logo`, `custom_ui_message`, `proxy_server_name`.

#### Acceptance Criteria
1. Dashboard is accessible at `/ui` with proper authentication.
2. All CRUD operations on keys, users, teams, models work from UI.
3. Spend dashboards display accurate, filterable data.
4. Self-serve portal enforces `upperbound_key_generate_params`.

---

### 8.7 Additional Enterprise Features

#### P2 Requirements

**Multi-Tenancy Hierarchy:** Organization > Team > Key > End User.
- Budgets and model access enforced at each level (most restrictive wins).

**Config Hot-Reload:**
- DB config changes propagate via Redis pub/sub.
- YAML file changes require restart (or external file-watcher).

**Request/Response Controls:**
- `max_request_size_mb` / `max_response_size_mb` in `general_settings`.
- `allowed_origins` for CORS configuration.
- Rate limit headers: `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, etc.
- OpenAPI docs at `/swagger` and `/redoc`.

**Email Notifications:**
- SMTP config: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SENDER_EMAIL`.
- Key creation confirmations, budget threshold alerts, expiry reminders.

---

### 8.8 Deployment/Ops - Out of Scope (Current)
- Multi-region active-active replication.
- Full SAML support (use OIDC bridge).
- Automated database backup/restore tooling.
- Custom UI white-labeling beyond logo/message.

### 8.9 Deployment/Ops - Risks and Unknowns
1. Database migration strategy for production upgrades: Prisma's additive-only approach may accumulate unused columns.
2. Redis as single point of shared state: Redis Sentinel/Cluster adds operational complexity.
3. SSO provider diversity: each OIDC provider has quirks in claims format.
4. Config drift between YAML file and DB config: need clear precedence rules and reconciliation strategy.
5. Secret rotation for master key requires coordinated restart of all instances.

---

## 9. Detailed Requirements - Provider Support & API Surface

### 9.1 Provider Support

#### P0 Requirements (Initial Providers)
- **OpenAI** -- GPT-4, GPT-3.5-Turbo, GPT-4o, o1, o3, embeddings, DALL-E, Whisper, TTS
- **Azure OpenAI** -- All Azure-hosted OpenAI models, deployment-based routing
- **Anthropic** -- Claude 3/3.5/4 family, native API

#### P1 Requirements (Priority Providers)
- **AWS Bedrock** -- Claude, Llama, Titan, Mistral, Cohere on Bedrock
- **Google Vertex AI** -- Gemini, PaLM, embeddings
- **Mistral AI** -- Mistral/Mixtral models
- **Groq** -- Fast inference endpoints
- **Cohere** -- Command R/R+, embeddings, rerank
- **Ollama** -- Local model hosting
- **Together AI** -- Open-source model hosting

#### P2 Requirements (Extended Providers)
- vLLM, Replicate, Fireworks AI, AI21, Deepseek, Hugging Face Inference, Perplexity, Anyscale, Voyage AI, and any OpenAI-compatible endpoint.

**Provider Configuration:**
```yaml
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
      api_key: os.environ/OPENAI_API_KEY
  - model_name: gpt-4
    litellm_params:
      model: azure/gpt-4-deployment
      api_base: https://my-azure.openai.azure.com/
      api_key: os.environ/AZURE_API_KEY
      api_version: "2024-02-15-preview"
  - model_name: claude-3
    litellm_params:
      model: anthropic/claude-3-sonnet-20240229
      api_key: os.environ/ANTHROPIC_API_KEY
```

**Provider Adapter Interface:**
- Translate canonical request schema to provider-specific format.
- Map provider-specific response back to OpenAI-compatible format.
- Handle provider-specific error codes and map to standard error types.
- Support `drop_params: true` to silently drop unsupported parameters per provider.

#### Acceptance Criteria
1. Same client request schema works against all supported providers.
2. Provider-specific settings are isolated to config.
3. Adding a new OpenAI-compatible provider requires only config, no code change.
4. Provider errors map to standardized proxy error types.

---

### 9.2 API Endpoints

#### P0 Endpoints

**Chat Completions: `POST /v1/chat/completions`**
- Full OpenAI-compatible request/response schema.
- Streaming (SSE) and non-streaming modes.
- Tool/function calling with cross-provider translation.
- Vision (multimodal) support for capable models.
- Response format control (`json_object`, `json_schema`).

**Embeddings: `POST /v1/embeddings`**
- OpenAI-compatible embedding request/response.
- Provider translation for non-OpenAI embedding providers.

**Models: `GET /v1/models`**
- Returns configured model groups visible to the authenticated key.
- Filters based on key/team model access permissions.

**Admin/Management APIs:**
- Key: `POST /key/generate`, `POST /key/update`, `POST /key/delete`, `GET /key/info`, `GET /key/list`, `POST /key/regenerate`
- User: `POST /user/new`, `GET /user/info`, `POST /user/update`, `POST /user/delete`
- Team: `POST /team/new`, `GET /team/info`, `POST /team/update`, `POST /team/member_add`, `POST /team/member_delete`
- Model: `POST /model/new`, `GET /model/info`, `POST /model/delete`
- Config: `GET /config/yaml`, `POST /config/update`
- Health: `GET /health`, `GET /health/liveliness`, `GET /health/readiness`

#### P1 Endpoints

**Responses API: `POST /v1/responses`**
- OpenAI Responses API format with `input`, `instructions`, `tools`, `previous_response_id`.
- Built-in tool types: `web_search_preview`, `file_search`, `code_interpreter`.
- Streaming events: `response.created`, `response.output_text.delta`, `response.completed`.

**Image Generation: `POST /v1/images/generations`**
- DALL-E and compatible providers.

**Audio: `POST /v1/audio/transcriptions`, `POST /v1/audio/speech`**
- Whisper-compatible transcription, TTS.

**Reranking: `POST /v1/rerank`**
- Cohere-compatible reranking.

**Pass-Through Endpoints:**
- `POST /vertex_ai/*`, `/bedrock/*`, `/anthropic/*`, `/azure/*`, `/openai/*`
- Custom pass-through via `general_settings.pass_through_endpoints`.

**Organization/Budget Management:**
- `POST /organization/new`, `POST /budget/new`, spend query endpoints.

#### Acceptance Criteria
1. All P0 endpoints accept OpenAI-compatible request format.
2. Responses follow OpenAI response schema with `choices`, `usage`, `model`, `id`.
3. Streaming returns properly formatted SSE with `data: [DONE]` terminator.
4. Admin APIs require master key authentication.

---

### 9.3 Input/Output Contracts

**Standard Request Schema:**
```json
{
  "model": "string (required)",
  "messages": [{"role": "system|user|assistant|tool", "content": "string|array"}],
  "temperature": "float 0-2",
  "max_tokens": "integer",
  "top_p": "float 0-1",
  "stream": "boolean",
  "tools": [{"type": "function", "function": {"name": "", "parameters": {}}}],
  "tool_choice": "auto|none|required|{...}",
  "response_format": {"type": "text|json_object|json_schema"},
  "user": "string (end-user ID for tracking)",
  "metadata": {"tags": [], "trace_id": "", "generation_name": ""}
}
```

**Standard Response Schema:**
```json
{
  "id": "chatcmpl-<unique-id>",
  "object": "chat.completion",
  "created": 1677652288,
  "model": "gpt-4",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "string", "tool_calls": []},
    "finish_reason": "stop|length|tool_calls|content_filter"
  }],
  "usage": {"prompt_tokens": 9, "completion_tokens": 12, "total_tokens": 21}
}
```

**Error Response Schema:**
```json
{
  "error": {
    "message": "string",
    "type": "invalid_request_error|authentication_error|rate_limit_error|server_error|timeout_error|budget_exceeded|model_not_found",
    "param": "string|null",
    "code": "string|null"
  }
}
```

**Error Mapping:**

| HTTP Status | Error Type | Description |
|---|---|---|
| 400 | `invalid_request_error` | Bad request / Budget exceeded |
| 401 | `authentication_error` | Invalid API key |
| 403 | `permission_denied` | Insufficient permissions |
| 404 | `model_not_found` | Model not configured |
| 408 | `timeout_error` | Request timeout |
| 429 | `rate_limit_error` | Rate limit exceeded |
| 500 | `server_error` | Internal server error |
| 503 | `service_unavailable` | Provider unavailable |

---

### 9.4 Tool/Function Calling Translation

#### P0 Requirements
- Accept OpenAI-format `tools` array and `tool_choice`.
- Translate to provider-specific format:
  - Anthropic: `tool_use` content blocks
  - Vertex AI: function declaration format
  - Bedrock: converse API tool format
  - Cohere: `tools` parameter
- Normalize all provider responses back to OpenAI `tool_calls` format.
- Handle parallel tool calls where supported; map to sequential for providers that don't support it.

#### Acceptance Criteria
1. Tool definitions in OpenAI format work with all supported providers.
2. Tool call responses are normalized to OpenAI format regardless of provider.
3. Providers without native tool support receive graceful error or prompt-based fallback.

---

### 9.5 CLI Tool

#### P0 Requirements
```bash
deltallm --config /path/to/config.yaml
deltallm --config config.yaml --port 4000 --host 0.0.0.0
deltallm --config config.yaml --num_workers 4
deltallm --config config.yaml --detailed_debug
deltallm --model gpt-4  # Quick single-model proxy
deltallm --config config.yaml --test  # Test model connectivity
deltallm --config config.yaml --health  # Health check all models
```

**Key CLI Flags:**

| Flag | Description |
|---|---|
| `--config <path>` | Path to YAML config file |
| `--port <int>` | Port (default: 4000) |
| `--host <str>` | Bind host (default: 0.0.0.0) |
| `--num_workers <int>` | Uvicorn workers |
| `--model <name>` | Quick single-model mode |
| `--debug` / `--detailed_debug` | Debug logging |
| `--test` | Test completion after starting |
| `--health` | Health check all configured models |
| `--drop_params` | Drop unsupported params silently |
| `--max_budget <float>` | Set global budget |

#### Acceptance Criteria
1. Proxy starts with config file and environment variables.
2. Quick single-model mode works with just `--model` flag.
3. Test mode validates model connectivity and reports failures.

---

## 10. Key Configuration Settings Reference

### `general_settings` (proxy-level)
| Setting | Type | Default | Description |
|---|---|---|---|
| `master_key` | string | None | Master API key |
| `database_url` | string | None | PostgreSQL connection URL |
| `database_connection_pool_limit` | int | 100 | Max DB connections |
| `custom_auth` | string | None | Custom auth function path |
| `litellm_key_header_name` | string | "Authorization" | Custom API key header |
| `allowed_ips` | list | None | IP allowlist |
| `allowed_origins` | list | ["*"] | CORS origins |
| `public_routes` | list | [] | Unauthenticated routes |
| `enable_jwt_auth` | bool | false | Enable JWT validation |
| `background_health_checks` | bool | false | Enable periodic health checks |
| `health_check_interval` | int | 300 | Health check interval (seconds) |
| `alerting` | list | [] | Alert destinations [slack, email, webhook] |
| `alerting_threshold` | int | 300 | Slow request threshold (seconds) |
| `enable_rate_limit_headers` | bool | false | Return rate limit headers |
| `max_request_size_mb` | int | None | Max request body size |
| `allow_user_auth` | bool | false | Enable user self-auth |
| `pass_through_endpoints` | list | None | Custom pass-through routes |

### `router_settings`
| Setting | Type | Default | Description |
|---|---|---|---|
| `routing_strategy` | string | "simple-shuffle" | Load balancing strategy |
| `num_retries` | int | 0 | Retry attempts |
| `retry_after` | int | 0 | Seconds between retries |
| `timeout` | float | None | Global request timeout |
| `cooldown_time` | int | 60 | Deployment cooldown (seconds) |
| `allowed_fails` | int | 0 | Failures before cooldown |
| `enable_pre_call_checks` | bool | false | Check RPM/TPM before routing |
| `model_group_alias` | dict | {} | Model name aliases |

### `litellm_settings`
| Setting | Type | Default | Description |
|---|---|---|---|
| `drop_params` | bool | false | Drop unsupported params |
| `cache` | bool | false | Enable response caching |
| `cache_params` | dict | {} | Cache configuration |
| `success_callback` | list | [] | Logging callbacks |
| `failure_callback` | list | [] | Error callbacks |
| `callbacks` | list | [] | General callbacks/hooks |
| `default_fallbacks` | list | [] | Default fallback models |
| `request_timeout` | float | 600 | Default request timeout |
| `num_retries` | int | None | Default retries |
| `turn_off_message_logging` | bool | false | Disable message content logging |
| `guardrails` | list | [] | Guardrail configurations |

---

## 11. Program-Level Out-of-Scope (Current)
- Full parity with every enterprise-only LiteLLM capability unless explicitly licensed/scoped.
- Full UI/dashboard clone in initial gateway build (basic admin UI is in scope at P1).
- Non-core protocol ecosystems (MCP/A2A/etc.) before proxy parity milestones.
- ML-based anomaly detection or predictive routing.
- Multi-region active-active replication.
- Full SAML support (OIDC bridge recommended).
- Automated database backup/restore tooling.
- Invoice generation (integrate with Lago/Stripe).

## 12. Program-Level Risks and Unknowns
1. **OSS vs enterprise boundary:** Feature scope could shift if targeting enterprise parity.
2. **Distributed state accuracy:** Rate limit and spend consistency across instances require careful Redis architecture.
3. **Compatibility drift:** Upstream OpenAI/provider API evolution requires ongoing adapter maintenance.
4. **Spend write path performance:** High-throughput scenarios need batched DB writes.
5. **Provider adapter maintenance:** 100+ providers means ongoing translation layer work.
6. **Cache invalidation:** No built-in mechanism beyond TTL; model updates may serve stale data.
7. **Guardrail latency overhead:** Multiple guardrails compound latency; external API guardrails add network hops.
8. **Secret rotation complexity:** Master key rotation requires coordinated restart.
9. **Config drift:** File + DB config dual-source needs clear precedence and reconciliation.
10. **Database migration strategy:** Additive-only Prisma approach may accumulate unused columns over time.

## 13. Consolidated Implementation Sequencing

### Phase 1: Core Foundation
1. Freeze API contract and error taxonomy.
2. Build core proxy path: auth middleware, request lifecycle, provider abstraction (OpenAI/Azure/Anthropic).
3. Implement model registry, model groups, basic routing (simple shuffle).
4. Virtual key management with DB persistence.
5. Rate limit enforcement (RPM/TPM/budget) with Redis.
6. CLI tool with config loading.

### Phase 2: Reliability & Routing
7. Retries, timeouts, and fallback chains.
8. Cooldown mechanisms and health checks.
9. Advanced routing strategies (least-busy, latency-based, cost-based).
10. Tag-based and priority routing.
11. Migration tooling and alias layers.

### Phase 3: Caching & Guardrails
12. Redis + in-memory cache backends with cache key composition.
13. Per-request cache control and streaming cache.
14. Custom guardrail framework (pre-call + post-call hooks).
15. Built-in guardrail integrations (Presidio PII, Lakera prompt injection).

### Phase 4: Observability & Billing
16. Callback system with standard logging payload.
17. Prometheus metrics exposition.
18. Spend ledger and cost calculation.
19. Budget enforcement (hard + soft) with alerts.
20. Spend query APIs and export.
21. Langfuse and OpenTelemetry integrations.

### Phase 5: Operations & UI
22. Docker deployment with health check endpoints.
23. DB-based dynamic config and model hot-add/remove.
24. SSO/JWT authentication integration.
25. Admin UI dashboard (keys, models, spend, logs).
26. Self-serve key portal.
27. Secret manager integration (AWS/GCP/Azure).

### Phase 6: Extended Surface
28. Additional provider adapters (Bedrock, Vertex, Mistral, Groq, etc.).
29. Responses API, image generation, audio, rerank endpoints.
30. Pass-through endpoints for provider-specific APIs.
31. Enterprise features (audit log, advanced RBAC, IP allowlisting).

## 14. Primary Source References

### Core Proxy
- https://docs.litellm.ai/docs/
- https://docs.litellm.ai/docs/simple_proxy
- https://docs.litellm.ai/docs/supported_endpoints
- https://docs.litellm.ai/docs/providers
- https://docs.litellm.ai/docs/proxy/configs
- https://docs.litellm.ai/docs/proxy/config_settings
- https://docs.litellm.ai/docs/proxy/architecture
- https://docs.litellm.ai/docs/proxy/virtual_keys
- https://docs.litellm.ai/docs/proxy/users
- https://docs.litellm.ai/docs/proxy/model_access_guide
- https://docs.litellm.ai/docs/proxy/reliability
- https://docs.litellm.ai/docs/proxy/timeout
- https://docs.litellm.ai/docs/proxy/token_auth
- https://docs.litellm.ai/docs/proxy/access_control
- https://docs.litellm.ai/docs/proxy/public_routes

### Routing/Caching/Guardrails
- https://docs.litellm.ai/docs/routing
- https://docs.litellm.ai/docs/proxy/load_balancing
- https://docs.litellm.ai/docs/proxy/tag_routing
- https://docs.litellm.ai/docs/caching/all_caches
- https://docs.litellm.ai/docs/proxy/guardrails/quick_start
- https://docs.litellm.ai/docs/proxy/guardrails/custom_guardrail

### Observability/Metrics/Billing
- https://docs.litellm.ai/docs/proxy/logging
- https://docs.litellm.ai/docs/proxy/prometheus
- https://docs.litellm.ai/docs/proxy/cost_tracking
- https://docs.litellm.ai/docs/proxy/custom_callback
- https://docs.litellm.ai/docs/proxy/budget_alerts
- https://docs.litellm.ai/docs/completion_cost
- https://docs.litellm.ai/docs/proxy/billing
- https://docs.litellm.ai/docs/observability/langfuse_integration

### Deployment/Ops
- https://docs.litellm.ai/docs/proxy/deploy
- https://docs.litellm.ai/docs/proxy/prod
- https://docs.litellm.ai/docs/proxy/docker_quick_start
- https://docs.litellm.ai/docs/proxy/health
- https://docs.litellm.ai/docs/proxy/db
- https://docs.litellm.ai/docs/proxy/self_serve
- https://docs.litellm.ai/docs/proxy/enterprise
- https://docs.litellm.ai/docs/proxy/email

### Provider/API Surface
- https://docs.litellm.ai/docs/providers
- https://docs.litellm.ai/docs/completion/input
- https://docs.litellm.ai/docs/completion/output
- https://docs.litellm.ai/docs/completion/stream
- https://docs.litellm.ai/docs/proxy/pass_through
- https://docs.litellm.ai/docs/proxy/ui
- https://docs.litellm.ai/docs/proxy/cli
- https://docs.litellm.ai/docs/response_api
- https://github.com/BerriAI/litellm
