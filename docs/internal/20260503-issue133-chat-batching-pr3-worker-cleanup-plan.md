# Issue 133 PR3 Plan: Batch Worker Execution Cleanup

## Objective

Continue the batch worker decomposition after PR2 without changing public Batch API behavior, retry semantics, spend accounting, passive health hooks, or provider failover behavior.

PR2 should stop at the low-risk extraction of chat and embedding execution paths into dedicated worker mixins. PR3 should handle the remaining shared infrastructure cleanup once the feature behavior has landed.

## Current State After PR2

Expected module ownership after PR2:

- `src/batch/worker_execution.py`: orchestration, shared persistence, shared failure handling, runtime hooks, retry scheduling, and backpressure coordination
- `src/batch/chat_worker_execution.py`: chat item preparation, concurrent chat execution, sync chat microbatch execution, and chat planning
- `src/batch/embedding_worker_execution.py`: embedding item preparation, embedding single-item execution, embedding microbatch execution, and embedding planning
- `src/batch/chat_batching.py`: pure chat batching config, eligibility, grouping, and result normalization helpers

This reduces the large worker file enough for PR2 review while avoiding a broad architecture rewrite.

## Remaining Problems

1. `worker_execution.py` still mixes independent shared concerns:
   - retry classification application
   - per-item failure persistence
   - model-group backpressure deferrals
   - passive health hooks
   - spend failure logging
   - completion outbox persistence
   - generic worker concurrency runner
2. The chat and embedding mixins still depend on many implicit methods on `self`.
3. Test helper setup in `tests/test_batch_worker.py` is large and makes targeted worker behavior harder to read.
4. There is no narrow unit boundary around the shared failure/backpressure policy.

## Non-Goals

- Do not change Batch API request/response shapes.
- Do not change artifact output or error JSONL format.
- Do not change retry categories, retry delays, or max-attempt behavior.
- Do not change Redis/Postgres synchronization semantics.
- Do not add new provider batching modes.
- Do not refactor router failover internals.

## Proposed Changes

### 1. Extract Failure And Retry Policy

Create `src/batch/worker_failure_handling.py`.

Move:

- `_mark_item_failed`
- `_build_failure_error_payload`
- `_retry_terminal_reason`
- `_can_retry_item`
- `_record_item_failure_decision`
- `_retry_delay_seconds`

Keep behavior identical. The extracted code should still use the existing `classify_batch_retry()` result and existing repository methods.

Add focused tests for:

- retryable item failure schedules retry
- terminal item failure records terminal reason
- max attempts exhaustion
- batch expiration behavior
- provider `Retry-After` capped by configured max delay

### 2. Extract Backpressure Coordination

Create `src/batch/worker_backpressure.py`.

Move:

- `_batch_backpressure_coordinator`
- `_resolve_item_model_group`
- `_get_model_group_deferral`
- `_raise_if_model_group_deferred`
- `_record_model_group_deferral`
- `_record_model_group_deferred_item`
- `_maybe_defer_model_group_for_retry`

Keep Redis-backed model-group deferral behavior unchanged and retain the in-process fallback behavior from the existing coordinator.

Add focused tests for:

- no coordinator is a no-op
- active deferral raises `BatchModelGroupDeferred`
- no-healthy-deployments retry creates model-group deferral
- coordinator failure does not fail the item path

### 3. Extract Runtime Hooks

Create `src/batch/worker_runtime_hooks.py`.

Move:

- `_record_upstream_success_runtime_hooks`
- `_record_upstream_failure_runtime_hook`
- `_record_failure_runtime_hooks`

Keep hook failures non-fatal. Preserve issue 108 behavior: passive health and router usage hooks must remain best-effort and must not break batch item persistence.

Add focused tests for:

- success hook records passive health and router usage
- failure hook records passive health and spend failure
- hook exceptions are logged and swallowed
- sync chat microbatch whole-call failure is not double-counted

### 4. Extract Completion Persistence

Create `src/batch/worker_persistence.py`.

Move:

- `_build_completion_outbox_payload`
- `_persist_completion_rows_with_outbox`
- `_renew_item_lease_once`
- `_observe_item_execution_latency`

Keep the outbox write coupled to item completion persistence so spend logging remains exactly-once from the worker perspective.

Add focused tests for:

- completed item plus outbox success
- lost ownership returns false
- transient persistence failure retries once
- persistence failure requeues item IDs

### 5. Add A Generic Bounded Runner Helper

Create a small internal helper for bounded concurrent item execution, only if it reduces duplication without hiding control flow.

Candidate shape:

```python
async def run_bounded_work_units(
    *,
    worker_id: str,
    capacity: int,
    work_units: Sequence[Callable[[], Awaitable[None]]],
) -> None:
    ...
```

This helper must keep saturation metrics accurate and must not introduce shared mutable global state. Keep per-deployment semaphore logic in chat execution unless a clean generic abstraction appears.

### 6. Clean Test Helpers

Split reusable worker test fixtures into `tests/batch_worker_fakes.py` or `tests/helpers/batch_worker.py`.

Move:

- fake repository
- fake storage
- spend recorder
- passive health recorder
- common chat job/item builders
- common worker builder

Do this only after behavior-focused tests exist so fixture extraction does not obscure regressions.

## Implementation Order

1. Extract failure/retry policy first because it has the clearest boundary.
2. Extract backpressure coordination next because failure handling depends on it.
3. Extract runtime hooks after failure handling is stable.
4. Extract completion persistence.
5. Evaluate whether the generic bounded runner is still worth it.
6. Clean test helpers last.

## Validation

Run:

```bash
uv run ruff check src/batch tests
uv run pytest tests/test_batch_retry.py tests/test_batch_worker.py tests/config/test_settings.py tests/test_ui_models.py
uv run pytest tests/test_batch_service.py tests/test_batch_repository.py
npm run build
```

Also run any PR2-specific regression tests that cover:

- issue 108 passive health behavior
- chat sync microbatch failover
- structured retryable provider item errors
- embedding microbatch retry reduction

## Acceptance Criteria

- `src/batch/worker_execution.py` becomes a thin orchestration class, ideally below 500 lines.
- Chat and embedding execution modules remain behavior-focused and do not grow shared policy code.
- All extracted shared modules have focused tests.
- No public API, persistence schema, config, Helm, Redis, or Postgres behavior changes.
- Existing PR2 tests remain green.
