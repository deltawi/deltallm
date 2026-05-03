# Issue 133 PR2 Plan: Chat Provider Batch Optimization

## Objective

Add provider-aware chat batch optimization after the `/v1/chat/completions` Batch API MVP is stable.

PR1 makes chat batch jobs correct and reliable by executing one non-streaming upstream chat request per batch item with bounded concurrency. PR2 adds explicit provider/deployment capabilities so DeltaLLM can use upstream chat batching where it is safe and measurable.

Related issue:

- [#133](https://github.com/deltawi/deltallm/issues/133)

Depends on:

- PR1: Chat Batch API MVP

## Problem Statement

Some providers expose chat batching capabilities, but those capabilities are not uniform.

There are at least three different meanings of chat batching:

1. DeltaLLM internal batch orchestration.
2. Synchronous upstream microbatching where multiple prompts are sent in one provider request.
3. Provider-native async batch APIs where files are uploaded, jobs are polled, and results are downloaded later.

Embeddings already have an upstream max batch size because embeddings have a simple multi-input request shape and predictable per-item response mapping. Chat does not have the same universal shape.

This PR should add provider batching without weakening correctness, billing, retry behavior, or artifact compatibility.

## Scope

In scope:

1. Add explicit deployment-level chat batching capability configuration.
2. Keep default chat batch behavior as bounded concurrency.
3. Treat vLLM OpenAI-compatible chat as concurrent execution by default.
4. Add a safe internal grouping layer for sync chat microbatch-capable providers.
5. Enforce conservative grouping rules.
6. Require per-item response mapping and per-item usage attribution.
7. Fall back to per-item execution when batching is not safe.
8. Add metrics to compare batched versus non-batched chat execution.
9. Document supported modes and provider requirements.

Out of scope:

1. Provider-native async batch job handoff.
2. Changing public Batch API semantics.
3. Changing artifact format.
4. Changing embeddings microbatch behavior.
5. Guessing provider capabilities from provider name alone.
6. Using aggregate-only provider usage for per-item billing.

## Design Principles

### 1. Capabilities must be explicit

Do not infer chat batching behavior only from provider names.

Providers and deployment adapters differ, and even the same provider can expose different behavior across endpoints, models, versions, or API bases.

Capability config should live on deployment parameters so operators can opt in intentionally.

### 2. vLLM should usually use concurrent mode

For vLLM's OpenAI-compatible chat endpoint, the gateway should generally send multiple normal chat requests concurrently. vLLM performs continuous batching inside the serving engine.

That means the useful controls for vLLM are:

- max in-flight chat requests
- request timeout
- worker concurrency
- optional token pressure limits

Not a synthetic upstream chat array request unless a specific vLLM-compatible API shape is introduced and validated.

### 3. Sync chat microbatching requires strict safety rules

Only batch together chat requests when the provider adapter can prove:

- response order or IDs map back to input items
- usage is available per item
- partial failures can be mapped per item
- request parameters are compatible
- retry behavior does not duplicate successful item spend

If any condition is uncertain, fall back to per-item execution.

### 4. Billing correctness wins over throughput

Do not use provider chat batching when the provider only reports aggregate usage unless DeltaLLM can allocate usage with a provider-documented, deterministic, and auditable rule.

For MVP optimization, require per-item usage.

## Proposed Capability Config

Recommended shape:

```yaml
deltallm_params:
  provider: vllm
  model: llama-3.1-8b
  chat_batching:
    mode: concurrent
    max_in_flight: 32
```

For sync microbatch-capable providers:

```yaml
deltallm_params:
  provider: example_provider
  model: example-chat-model
  chat_batching:
    mode: sync_microbatch
    upstream_max_batch_size: 8
    max_total_input_tokens: 32000
    require_homogeneous_params: true
```

Supported modes:

- `disabled`: execute one upstream request per item.
- `concurrent`: execute one upstream request per item with bounded concurrency.
- `sync_microbatch`: group compatible items into one upstream provider call.

Reserved future mode:

- `native_async_batch`: provider-managed async jobs; design only, not implemented in this PR.

Backward compatibility:

- Missing `chat_batching` config should behave like `concurrent` for internal chat batch jobs, using existing worker concurrency limits.
- Existing embeddings batch config remains unchanged.

## Config Model Changes

Primary files:

- `src/config.py`
- `src/batch/worker_execution.py`
- docs under `docs/configuration/`

Add a typed config object for chat batching.

Recommended fields:

- `mode: Literal["disabled", "concurrent", "sync_microbatch"]`
- `max_in_flight: int | None`
- `upstream_max_batch_size: int | None`
- `max_total_input_tokens: int | None`
- `require_homogeneous_params: bool = true`

Validation rules:

1. `sync_microbatch` requires `upstream_max_batch_size >= 2`.
2. `max_total_input_tokens`, when set, must be positive.
3. `max_in_flight`, when set, must be positive.
4. Unknown modes should fail config validation.
5. Native async mode should not be accepted until implemented.
6. `sync_microbatch` currently requires `require_homogeneous_params: true`; heterogeneous request-parameter grouping remains out of scope until a provider adapter proves a safe contract.

## Worker Architecture

Primary files:

- `src/batch/worker_execution.py`
- `src/batch/worker_types.py`
- new `src/batch/chat_batching.py` if the grouping logic grows

Recommended flow:

1. Prepare chat items using the PR1 path.
2. Group prepared items by resolved deployment.
3. For each deployment group, inspect chat batching capability.
4. Execute according to mode:
   - `disabled`: per-item execution
   - `concurrent`: per-item execution with deployment concurrency bounds
   - `sync_microbatch`: group compatible items into provider microbatches
5. Persist results per item.
6. Fall back to per-item execution on unsupported grouping cases.

Do not group before routing. Batching must happen after items resolve to the same deployment.

## Sync Microbatch Grouping Rules

Items can share one upstream chat microbatch only when all of these match:

1. Deployment ID.
2. Provider.
3. API base.
4. Auth/credential context.
5. Public model name.
6. Deployment model.
7. Compatible sampling parameters.
8. Compatible response format.
9. Compatible tool settings.
10. No streaming.
11. No MCP tools.
12. Estimated total input tokens within configured cap.
13. Item count within `upstream_max_batch_size`.

Recommended first implementation:

- require exact equality for non-message request parameters
- allow messages to differ per item
- do not batch requests with tools until a provider adapter proves safe tool-call mapping
- do not batch requests with response format complexity until tested

This is intentionally conservative.

## Provider Adapter Contract

Add a narrow contract for sync chat microbatch execution.

Suggested shape:

```python
class ChatMicrobatchExecutor(Protocol):
    async def execute_chat_microbatch(
        self,
        *,
        requests: list[ChatCompletionRequest],
        deployment: ModelDeployment,
        request_context: BatchRequestContext,
    ) -> list[ChatMicrobatchResult]:
        ...
```

Each result must include:

- original item index or custom id
- response body on success
- normalized error on failure
- per-item usage on success
- provider latency information if available

Hard requirement:

- returned results must be mappable to input items without ambiguity

If a provider returns aggregate-only usage, the adapter should reject microbatch mode for that deployment.

## Cost and Usage Attribution

Primary files:

- `src/billing/cost.py`
- `src/batch/worker_execution.py`
- `src/batch/completion_outbox.py`

Rules:

1. Each completed item must have its own usage object.
2. Cost calculation remains per item.
3. Spend outbox remains per item.
4. Router usage can include additional metadata showing batched execution.
5. Provider cost and billed cost are still persisted per item.

Add optional internal metadata:

- `batch_execution_mode`: `concurrent` or `sync_microbatch`
- `microbatch_size`
- `microbatch_id`

Do not expose this metadata in public batch artifacts unless explicitly documented.

## Failure Handling

Sync microbatch failure cases:

1. Whole upstream request fails before item-level results are available.
2. Provider returns mixed per-item successes and failures.
3. Provider returns malformed or incomplete result mapping.
4. Provider returns aggregate-only usage.

Rules:

- If the whole microbatch fails transiently, retry each item according to existing retry rules.
- Whole microbatch execution must run through the existing router failover manager. If a fallback deployment serves the grouped call, persistence and spend metadata must use the fallback deployment's provider, API base, model, and pricing.
- If mixed results are returned, persist successful items and retry/fail only failed items.
- Structured per-item provider errors should preserve retry classification. `429`, `408`, and `5xx` item errors are retryable; unknown or unstructured item errors remain terminal invalid-request failures.
- If result mapping is ambiguous, fail the affected items permanently unless retrying per item can safely recover.
- If usage is missing for a successful item, treat the item as failed for accounting safety.

Avoid double-charging:

- durable spend outbox should be written only after per-item success persistence
- retry logic must not re-log successful items
- passive health failure hooks should be emitted once per affected item; do not also emit a duplicate chunk-level failure for the same failed request group

## Metrics

Add or extend metrics so operators can see whether provider batching helps.

Useful dimensions:

- endpoint: `/v1/chat/completions`
- batch execution mode: `concurrent` or `sync_microbatch`
- provider
- deployment
- microbatch size
- success/failure

Suggested metrics:

- chat batch items executed
- chat microbatches executed
- chat microbatch size histogram
- microbatch provider latency
- fallback-to-per-item count
- aggregate token throughput if already available

Keep metric cardinality bounded:

- avoid custom IDs
- avoid raw API bases
- avoid request IDs

## vLLM Strategy

Default vLLM mode:

```yaml
chat_batching:
  mode: concurrent
  max_in_flight: 32
```

Rationale:

- vLLM performs continuous batching internally
- OpenAI-compatible chat requests remain standard
- per-item usage and response mapping remain simple
- the gateway avoids provider-specific request shapes

Optional future vLLM work:

- token pressure limiter based on estimated prompt tokens
- deployment-level max in-flight defaults
- adapter for a documented vLLM-compatible sync microbatch API if one is adopted

## Implementation Phases

### Phase 1: Config and capability plumbing

1. Add typed `chat_batching` deployment config.
2. Validate config at startup/load time.
3. Expose resolved capability to the batch worker.
4. Add config tests.
5. Document defaults and allowed modes.

### Phase 2: Execution mode dispatch

1. Add chat execution mode resolution.
2. Keep PR1 per-item path as `concurrent`.
3. Add `disabled` mode.
4. Add tests proving missing config preserves PR1 behavior.

### Phase 3: Grouping layer

1. Group prepared chat items by resolved deployment.
2. Add compatibility-key calculation for request params.
3. Enforce max item count.
4. Enforce optional max estimated input tokens.
5. Add tests for grouping and fallback cases.

### Phase 4: Sync microbatch adapter contract

1. Add protocol or small adapter interface.
2. Add one test/dummy adapter implementation.
3. Wire the worker to call the adapter only when mode is `sync_microbatch`.
4. Require per-item result mapping and usage.

### Phase 5: Accounting and failure semantics

1. Persist per-item successful responses.
2. Persist per-item usage and costs.
3. Handle mixed success/failure results.
4. Add spend outbox payload metadata for execution mode.
5. Add tests for partial failure and missing usage.

### Phase 6: Metrics and docs

1. Add low-cardinality execution mode metrics.
2. Document provider requirements.
3. Document vLLM as concurrent by default.
4. Document why aggregate-only usage is unsupported.

## Test Plan

Config tests:

```bash
uv run pytest tests/test_config.py
```

Batch worker and grouping tests:

```bash
uv run pytest tests/test_batch_worker.py tests/test_batch_worker_microbatch.py
```

New expected tests:

- chat batching config defaults
- invalid chat batching modes
- sync microbatch grouping by deployment
- fallback when request params differ
- fallback when token cap would be exceeded
- mixed success/failure provider result handling
- missing per-item usage rejection
- vLLM concurrent mode does not call microbatch adapter

Broader validation:

```bash
uv run pytest tests/test_batch_*.py tests/test_chat.py
uv run ruff check src/batch src/config.py tests
```

## Acceptance Criteria

1. Deployments can explicitly configure chat batching behavior.
2. Missing config preserves PR1 behavior.
3. vLLM deployments use concurrent execution by default.
4. Sync microbatching is used only for deployments that opt in.
5. Grouping never combines incompatible requests.
6. Per-item usage is mandatory for per-item spend.
7. Mixed microbatch results are persisted correctly.
8. Existing embeddings microbatch behavior is unchanged.
9. Operators can observe chat batch execution mode and fallback behavior.

## Rollout Notes

Recommended rollout:

1. Deploy PR1 first and observe chat batch stability.
2. Deploy PR2 with all deployments defaulting to `concurrent`.
3. Enable `sync_microbatch` on one non-production deployment.
4. Verify usage attribution and artifacts.
5. Enable provider by provider.

Rollback behavior:

- deployments with `sync_microbatch` config should fall back to `concurrent` after rollback only if the older code ignores unknown nested config safely
- if config validation in older versions rejects the field, remove `chat_batching` config before rollback

Operational warning:

- increasing microbatch size can improve provider throughput but may increase tail latency
- for chat, tail latency and output token variance matter more than embeddings
- tune per provider, not globally
