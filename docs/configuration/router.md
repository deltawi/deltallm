# Router Settings

Use `router_settings` for the gateway-wide defaults that control deployment selection, retries, timeouts, and aliases.

## Quick Path

Start with a small, predictable config:

```yaml
router_settings:
  routing_strategy: simple-shuffle
  num_retries: 1
  retry_after: 1
  timeout: 600
  cooldown_time: 60
  allowed_fails: 0
```

That is enough for most first deployments.

Tip: check the effective `allowed_fails` value in the config your deployment actually loads. Depending on how you run DeltaLLM, the source of truth is usually your `config.yaml` from `DELTALLM_CONFIG_PATH`, your Helm `values.yaml`, or the rendered Kubernetes ConfigMap.

## Reference

| Setting | Default | What it controls |
| --- | --- | --- |
| `routing_strategy` | `simple-shuffle` | Global default strategy for choosing a deployment |
| `num_retries` | `0` | Extra retry attempts after the first failure |
| `retry_after` | `0` | Base backoff delay in seconds |
| `timeout` | `600` | Request timeout in seconds |
| `cooldown_time` | `60` | Seconds a failing deployment stays out of rotation |
| `allowed_fails` | `0` | Failures allowed before cooldown starts |
| `enable_pre_call_checks` | `false` | Skip deployments already over configured RPM or TPM metadata |
| `model_group_alias` | `{}` | Friendly names that map to real model groups |
| `route_groups` | `[]` | File-defined route groups and membership |

Each file-defined route group should declare one workload mode.
For compatibility, DeltaLLM can infer an omitted mode from enabled, resolvable members with the same deployment mode.
An empty or unresolved legacy group defaults to `chat`.
DeltaLLM rejects mixed member modes.
Declare `mode` explicitly for stable configuration without warnings:

```yaml
router_settings:
  route_groups:
    - key: search-embeddings
      mode: embedding
      strategy: weighted
      context:
        mode: smallest-sufficient
        unknown_capacity: allow
        safety_margin_tokens: 256
      members:
        - deployment_id: embeddings-primary
          weight: 3
        - deployment_id: embeddings-secondary
          weight: 1
```

A request whose endpoint workload does not match the route group is rejected before shared
routing-state reads. Invalid group/member combinations fail runtime snapshot validation and do not
replace the last valid live registry.

`allowed_fails: 0` starts cooldown on the first health-affecting provider failure. After
`cooldown_time` expires, the router admits one shared-Redis half-open request for that deployment;
successful recovery restores normal routing and failed recovery re-enters cooldown. Request-side
validation, policy, budget, guardrail, cancellation, and local gateway-capacity failures are
health-neutral. Background health checks are optional because request traffic can perform the
bounded recovery transition.

Operator-triggered health checks use a short-lived manual probe claim. After cooldown expiry they
may own the same fenced recovery transition as request traffic or the background checker. During an
active manual cooldown, a successful provider probe reports that the provider responded but does
not restore routing health. A concurrent owned recovery returns HTTP `409` instead of running a
second recovery probe.

Health hashes use rolling 30-day retention, extended when a longer cooldown requires it. Mutable
provider-health keys include an opaque deployment generation derived from the provider
configuration and its durable incarnation. A late result from a retired provider configuration can
therefore update only the retired generation. Runtime reload atomically publishes the new registry
generation before attempting bounded, exact cleanup of retired health keys. Cleanup failure is
reported as degraded maintenance and does not make a persisted model mutation fail. Metadata-only
changes such as weight and priority retain the existing health generation and state.

Router state uses the standalone Redis topology created at application startup.
Its multi-key admission and health-transition scripts do not support Redis Cluster.
Each router key uses `deltallm:<app_env>:v1:<router-capability>:<identifiers>`.
This schema replaces the previous unscoped temporary router keys.

Before namespaced replicas receive traffic, drain replicas that run the previous binary.
Use the same drain procedure for rollback.
Do not run the two key schemas concurrently. They would use separate admission and cooldown ownership.
For Helm, use the [router Redis v1 schema cutover](../deployment/router-state-schema-cutover.md).
Chart upgrades require drain acknowledgement and `strategy.type=Recreate`.

## Supported Strategies

These strategy names are valid today:

- `simple-shuffle`
- `least-busy`
- `latency-based-routing`
- `cost-based-routing`
- `usage-based-routing`
- `tag-based-routing` (deprecated compatibility alias for `weighted`)
- `priority-based-routing`
- `weighted`
- `rate-limit-aware`

Short version:

- `simple-shuffle`: best default when deployments are equivalent
- `weighted`: use for planned traffic splits
- `priority-based-routing`: use for primary and standby routing
- `least-busy`: use for burst balancing
- `latency-based-routing`: use for latency-sensitive traffic
- `cost-based-routing`: use for lowest-cost routing
- `usage-based-routing`: use to spread quota usage
- `rate-limit-aware`: use to avoid hot deployments near RPM or TPM caps
- `tag-based-routing`: accepted for existing configuration only; migrate to `weighted` because tag
  eligibility is applied before every strategy

For non-text workloads, usage-aware routing can also use these deployment fields when they are configured:

- `image_pm_limit`
- `audio_seconds_pm_limit`
- `char_pm_limit`
- `rerank_units_pm_limit`

See [Routing & Failover](../features/routing.md) for the full behavior and setup examples.

## Route-Group Policy Support

Route-group policies currently support:

- `mode` (deprecated input alias only)
- `strategy`
- `members`
- `timeouts.global_ms` or `timeouts.global_seconds`
- `retry.max_attempts`
- `retry.retryable_error_classes`
- `context.mode`
- `context.unknown_capacity`
- `context.default_output_tokens`
- `context.safety_margin_tokens`
- optional version 3 `selector` and member `lane` fields for the model-router contract

Legacy mode aliases accepted on input:

- `weighted` maps to `weighted`
- `fallback` maps to `priority-based-routing`

Do not treat `conditional` or `adaptive` as active runtime policy behaviors today.

`strategy` is the canonical routing field. The policy `mode` field remains accepted as a deprecated
input shortcut (`weighted` or `fallback`) and produces a warning; normalized new writes omit it. It
is separate from the route group's workload `mode`. Existing policy history is not rewritten and
remains readable and rollback-safe. When `members` is omitted, the policy inherits the group's
enabled members. Newly saved policies treat an explicit list as authoritative. Policies created
before this semantics version retain their legacy widening behavior, including when rolled back. A
policy can disable an eligible member but cannot reactivate a group member disabled by an operator.

### Context-capacity routing

Context routing is disabled unless a route-group policy contains a `context` object. It uses the
existing deployment `model_info.max_tokens`, `model_info.max_input_tokens`, and
`model_info.max_output_tokens` fields.

For upgrade compatibility, existing non-positive values in these fields mean unknown capacity.
New admin API and UI writes require positive integers.
When an operator opens and saves a legacy deployment, the UI removes a non-positive sentinel.
The operator can supply a positive replacement.

Policy writes distinguish omission from explicit deletion. Omitting `context` preserves the
currently stored block so older clients do not erase policy they do not understand. Send
`"context": null` to disable context routing and remove the stored block. Non-negative token
settings must be exact integers; fractional JSON numbers are rejected rather than truncated.

Context policies are valid only for route groups whose workload mode is `chat` or `embedding`.
Configuration and admin-policy validation reject context policies for image, audio, and rerank
groups rather than accepting an inactive policy.

Example:

```json
{
  "strategy": "least-busy",
  "context": {
    "mode": "smallest-sufficient",
    "unknown_capacity": "allow",
    "default_output_tokens": 1024,
    "safety_margin_tokens": 256
  }
}
```

`eligible-only` removes deployments that cannot fit the estimated request and then preserves the
configured strategy's order. `smallest-sufficient` additionally prefers the smallest known
sufficient context tier while retaining larger eligible deployments for failover. `unknown_capacity`
defaults to `allow` for upgrade compatibility; use `exclude` only after every member has accurate
capacity metadata.

The gateway computes the input estimate once from the final normalized payload.
This occurs after prompt rendering, pre-call hooks, and guardrails.
It uses a fast character-based token estimate, not a provider tokenizer.
Keep a non-zero safety margin.

Output demand uses the first available value in this order:

1. The request's explicit `max_tokens`.
2. The output limit that the selected provider adapter must send.
   This includes Anthropic's deployment `deltallm_params.max_tokens` or its 1024-token protocol default.
3. The deployment's `model_info.default_params.max_tokens`.
4. The policy default.

Embeddings use zero output demand.
MCP chat requests with multiple model phases compute demand again between those phases.
They invalidate only their request-local candidate plan.

Known insufficient capacity enters the existing `context_window_fallbacks` chain before a provider call.
The initiating route group's context policy applies to each deployment in that chain.
This includes fallback groups without a context block.
If no configured context fallback is eligible, the gateway returns `400` with code `context_length_exceeded`.
If a capable deployment exists but is unhealthy or in cooldown, the existing no-healthy-deployments `503` behavior applies.

The filter adds no SQL, Redis, or network calls.
It uses the policy snapshot and deployment metadata in memory after the router's existing batched state reads.

Review ownership, migration, rollback, and latency before you enable
context-capacity routing in production.

### Model-router policy contract

Version 3 defines a bounded `llm-tier` selector for chat Route Groups. It classifies a request into
an allowlisted lane; it does not choose a deployment. Lanes have unique contiguous ranks starting at
zero, and an omitted `default_lane` normalizes to the highest-ranked lane. Selector policies require
an explicit answer-member list, a concrete chat classifier (inside or outside the group), and one valid lane for every enabled
answer member.

For an existing version 3 selector draft, omitting `selector` preserves it. A member-only update
may omit existing lane assignments; DeltaLLM preserves those lanes by deployment ID while treating
the submitted member list as authoritative. Use `"selector": null` to disable selection. That
removes the selector and all member lanes without discarding the member list, enabled flags,
weights, priorities, or server-owned member metadata.

Publishing a qualified selector policy activates it for real-time Chat Completions and Responses,
including streaming. File-configured groups use the same qualification at runtime load. There is
no shadow mode or background classification of production requests. New operations pin one complete
routing generation; in-flight operations finish against their original policy and credentials.

Before activation:

- Use a supported concrete chat deployment ID as the classifier, not an alias or another group.
  It need not be an answer member. Every enabled
  answer member must declare positive context capacity and explicit
  [`model_info.chat_capabilities`](models.md#model-router-capability-and-price-metadata).
- Set group `context.unknown_capacity: exclude`. The classifier needs explicit input/output token
  prices (zero is allowed when genuinely free) and positive `rpm_limit` and `tpm_limit` metadata.
- Enable `general_settings.spend_ingestion_mode: outbox` and
  `spend_ingestion_worker_enabled: true`, with healthy PostgreSQL and shared Redis admission.
  Missing accounting readiness fails activation/admission; it never becomes a free default call.

The classifier only needs capabilities/context for its bounded textual classification request,
not the answer's streaming, tools or full context window. Only explicitly assigned answer
members can answer. Shared selectors retain one physical deployment's health and capacity
identity across groups and standalone traffic. Choosing a selector does not grant callers
standalone access to it.

For example, add this block alongside the `selector` and `members` in a policy:

```yaml
context:
  mode: smallest-sufficient
  unknown_capacity: exclude
  safety_margin_tokens: 256
```

The execution sequence is:

1. Normalized prompt, hook, and guardrail processing.
2. Authentication and caller admission.
3. Whole-response cache lookup.
4. Deterministic member filtering.
5. Bounded selector admission and classification.
6. Normal answer placement, retry, and failover.

A cache hit skips classification and selector accounting.
The selector receives a bounded subset of the final prompt, recent context, and structural features.
It does not receive credentials, unrestricted metadata, tool schemas, or attachment contents.
This prompt content goes to the configured classifier's provider.
Before publication, qualify that provider's data-handling policy and the necessary routing tags.

Classification makes at most one provider attempt per external operation. Provider/parse/timeout
or capacity failures select the configured safe lane; authentication, accounting and parent-deadline
failures do not. The selected minimum rank is reused across retries, stream setup, MCP continuations
and later selector-bearing fallback groups, even if lane names differ. A first selector reached
through fallback runs only when needed. Normal strategy/context ordering applies independently
within eligible lanes, with upward-only escalation. Selector-free fallback members retain their
existing equivalence contract. Client disconnect cancels and joins selector work before streaming
headers are sent. A disconnect terminates locally with `client_disconnected` (499), without opening
the answer stream. Selector and answer share the original deadline; receipt/lease cleanup has a
small bounded grace period and never replays a provider call.

Customers pay the selector's actual provider cost, with no selector markup. Its linked, idempotent
spend component is attributed to the caller even if the answer later fails or disconnects. Public
OpenAI-compatible `usage` remains answer-only. Reported receipts use frozen exact pricing; unknown
dispatch usage remains pending reconciliation, not zero. Compare savings as avoided answer spend
minus selector spend; include cache/model-switch effects when evaluating.

Budgets remain **soft admission checks**, not strict spending reservations. The selector's
conservative estimate uses configured classifier input capacity plus its 64-token output bound;
concurrently admitted requests may overshoot. Actual reported usage is still charged, including
above that estimate. Ordinary traffic gains no new reservation calls. The classifier consumes
shared provider RPM/TPM/concurrency capacity, while caller RPM is charged once. TPM admission uses
the conservative context allowance, so configure enough TPM for the intended selector throughput.
If a team-model budget is configured, its maintained spend counter must exist. Missing counter
data fails selector admission as accounting unavailable; repair/reconcile it through the existing
billing owner before retrying. Admission never rebuilds counters by scanning spend history.

Use the existing policy validate, draft, publish, history, and rollback API endpoints.
Invalid activation does not archive the working policy.
Active member and model changes receive qualification again in the transaction.

To disable selection, publish `{"selector": null}` or roll back to a revision without a selector.
Rollback to a revision with a selector validates and activates it again.
Use a separately authorized group for the canary.
Before binary rollback to a version from before PR 4, remove active selectors.
Drain in-flight operations before that rollback.

Disposable runtime-cache envelopes use a new version and reload from the durable policy source.
Equivalent policy reloads keep response-cache identity.
Selector, lane, capability, or fallback-dependency changes invalidate that identity.

Internal Batch chat items select independently. Items whose group or configured
normal/context/content-policy fallback topology contains a selector execute individually
within the existing worker concurrency limit; selector-free microbatching is unchanged.
The first reached selector is lazy, and its saved decision survives answer retries and
worker reclaim. Customers pay the classifier's regular provider cost; answer pricing still
uses the existing Batch tier. Public Batch usage contains only answer tokens.
An uncertain or incompatible saved decision returns `batch_selector_checkpoint_unavailable`
through bounded Batch retry handling, never another paid classification. See
[Batch selector rollout and tradeoffs](../features/batching.md#model-selectors-in-chat-batches).
Non-chat workloads reject selector policies.
Deterministic policy simulation does not run selectors. The guided editor and optional fixture
evaluation are described in [Model router administration](model-router-admin.md).
Streaming with managed MCP tools retains its existing unsupported error.

Monitor bounded selector decision, default, termination, duration, terminal rank, and escalation metrics.
Also monitor the selector's contribution to streaming TTFT.
Detailed lane and policy identity stay in protected routing telemetry. Public selector headers do not expose them.

For high default rates, examine classifier capacity, provider health, context and capability metadata, and strict lane output.
For missing spend, examine the durable pending-reconciliation state.
Do not substitute a zero value for unresolved spend.

The complete design, bounds, compatibility behavior, rollout, and rollback order
are part of the route-group model router contract.

## Fallback Configuration

Fallback chains live under `deltallm_settings`, not `router_settings`:

```yaml
deltallm_settings:
  fallbacks:
    - gpt-4o:
        - gpt-4o-mini
  context_window_fallbacks:
    - gpt-4o-mini:
        - gpt-4o
  content_policy_fallbacks:
    - gpt-4o:
        - claude-3-sonnet
```

Provider adapters classify context-window and content-policy failures from known error envelope fields.
This applies to chat, embeddings, images, rerank, speech, and transcription.
OpenAI-compatible and Azure OpenAI responses or stream events with `finish_reason: content_filter` are content-policy failures.
Raw exception text and malformed provider bodies never activate a specialized chain.

Explicit custom providers use generic status mapping unless declared as supported OpenAI-compatible providers.
An omitted provider keeps the existing implicit OpenAI-compatible behavior.
HTTP status controls the public error type and health impact.
A recognized context or policy classification on a `5xx` response still affects health.
It can try its specialized chain first.
An unclassified `5xx` uses the general chain. A `429` remains a rate-limit failure regardless of envelope text.

Malformed JSON or response schemas with a nominally successful provider status are provider failures that affect health.
They can use the general `fallbacks` map. The client never receives the upstream payload.
Examples include empty chat choices, missing or mismatched embedding and rerank results, and empty speech audio.

Unclassified provider client errors such as `400`, `409`, or `422` do not affect health.
They skip retries on the same deployment and continue through remaining eligible deployments and configured general fallback groups.
If all candidates reject the request, DeltaLLM returns the final sanitized `400`.
Known authentication, permission, missing-model, timeout, and rate-limit statuses keep their specialized behavior.

Anthropic Messages classifies `refusal` as content policy and `model_context_window_exceeded` as context window before it returns a nominal success.
Gemini accepts only documented success terminal reasons. Policy terminals use the content-policy chain.
Unsupported, malformed, or unknown terminal reasons fail closed through the general chain.
For Bedrock streams, the documented exception type is authoritative.
Message text cannot change the classification of throttling or service exceptions.
Validation exceptions can use the bounded provider-specific context and content markers.

No fallback starts after the gateway sends a streaming response frame.
A bounded pre-commit buffer holds provider role and metadata events until output or a valid terminal event establishes a real response.
For OpenAI-compatible streams, non-empty `reasoning`, `reasoning_content`, and `reasoning_details` deltas count as output.
Content, refusals, and tool calls also count as output.
The first such delta commits the response and releases buffered metadata in its original order.
The gateway cannot then replay the request on another deployment.

Before output, classified terminal events can select a specialized fallback for OpenAI-compatible, Azure OpenAI, Anthropic, and Bedrock providers.
Empty, terminal-only, and truncated streams before output are malformed successes. They can use the general fallback chain.
After partial output commits a response, a classified stop ends that stream with the compatible `content_filter` or `length` finish reason.
It does not start another provider attempt.
The gateway aborts other malformed committed streams and marks the deployment unhealthy.
It never caches them as complete responses.

Well-formed, unrecognized metadata can exceed the bounded pre-commit buffer.
This is a gateway compatibility failure. It returns the sanitized provider-response error without a deployment cooldown.
A clean `[DONE]` marker after only unrecognized, non-empty delta fields has the same health-neutral result.
Before it sends a response frame, the router skips retries on the same deployment and tries the next eligible deployment once.

Each eligible deployment can return only unrecognized output fields and reach one of these boundaries.
If all do, the request fails with the sanitized provider-response error. None of those deployments becomes unhealthy.
A buffer filled only with known metadata, such as repeated role-only deltas, is a malformed provider stream.
That failure affects health.

For OpenAI-compatible streams, DeltaLLM asks OpenAI-family and vLLM deployments for a final usage chunk.
It does this even when the client did not request one. The internal chunk is hidden from the client.
If the provider omits usage, fallback estimation removes duplicate textual `reasoning`, `reasoning_content`, and `reasoning_details` aliases within each delta.
It then sums the deltas.
Opaque reasoning details use a conservative payload-size estimate. Usage metadata marks these estimates incomplete.

Each replica serializes durable config loads, subscriber application, publication, and rollback.
Runtime reloads publish all three immutable maps as one generation, and publication is fenced by
the complete generation identity rather than only the route revision. A failed or superseded reload
therefore cannot expose mixed fallback configuration or overwrite a newer authorization snapshot.

## Model Aliases

Aliases let clients request a stable name while you map it to the real group:

```yaml
router_settings:
  model_group_alias:
    best-model: gpt-4o
    fast-model: gpt-4o-mini
```

## Related Pages

- [Routing & Failover](../features/routing.md)
- [Model Deployments](models.md)
