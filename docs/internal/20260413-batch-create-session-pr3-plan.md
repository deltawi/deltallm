# PR 3 Plan: Session Promotion Engine And Atomic Executable Batch Materialization

## Objective

PR 3 implements the core promotion engine that turns a staged create session into an executable `queued` batch atomically.

Target outcome:

- a staged create session can be promoted into a real executable batch
- `BatchJob` is created only as `queued`
- `BatchItem` rows are bulk inserted during the same promotion transaction
- failed promotion never leaves a worker-visible partial batch
- the promoter can be called programmatically and tested, but the public API still does not use it by default

This PR is the correctness center of the redesign.

Related plans:

- [`20260413-batch-create-session-architecture-plan.md`](20260413-batch-create-session-architecture-plan.md)
- [`20260413-batch-create-session-pr1-plan.md`](20260413-batch-create-session-pr1-plan.md)
- [`20260413-batch-create-session-pr2-plan.md`](20260413-batch-create-session-pr2-plan.md)

## Branch And Merge Target

PR 3 should be implemented on:

- `pr/batch-create-session-3-promotion-engine`

PR 3 should be opened against:

- `feature/batch-create-session`
- not `main`

Stack rules:

1. Branch from the latest tip of `feature/batch-create-session` after PR 2 is merged there.
2. Merge back into `feature/batch-create-session`.
3. Do not switch public `POST /v1/batches` to this promoter yet.
4. DB-backed integration tests are mandatory in this PR.
5. If a fallback path is needed, it must be internal-only and not leak new behavior to callers.

## Scope

In scope for PR 3:

1. Implement session locking and promotability checks.
2. Implement strict promotion-time pending-batch cap enforcement.
3. Implement advisory-lock acquisition during promotion.
4. Implement atomic `BatchJob` creation as `queued`.
5. Implement bulk `BatchItem` insertion from the staged artifact.
6. Mark the session `completed` on success.
7. Mark retryable/permanent promotion failures explicitly.
8. Add DB-backed tests for contention, failure rollback, and atomic visibility.

Out of scope for PR 3:

1. Public endpoint cutover.
2. Public idempotency behavior.
3. Admin UI/session endpoints.
4. Legacy staged-job removal.

## Design Decisions

### 1. Promotion is the only bridge into `BatchJob`

No code path outside the promoter should create executable jobs from staged session data.

That invariant is the foundation of the redesign.

### 2. Enforce executable cap only at promotion time

Do not count create sessions as pending executable jobs.

Strict backlog control should check only:

- `deltallm_batch_job`
- executable statuses

### 3. Use a real transaction suited for bulk insertion

Do not rely on Prisma interactive-transaction defaults for this path if they remain too restrictive.

Recommended order of preference:

1. transaction path that can support efficient bulk insert safely
2. chunked multi-row insert if `COPY` is not practical in this PR

### 4. `BatchJob` is born `queued`

Do not create:

- `preparing`
- `validating`
- hidden transitional execution rows

The final committed `BatchJob` row should be visible only as `queued`.

### 5. Promotion is idempotent by session, not by public API yet

If the promoter is called twice on the same already-completed session, it should return the existing `BatchJob` rather than creating a duplicate.

This is required before public idempotency is exposed in PR 4.

## Promotion Transaction Design

Recommended transaction steps:

1. lock `BatchCreateSession` by `session_id`
2. ensure session status is promotable
3. if session already completed, return the existing `BatchJob`
4. acquire scope advisory lock
5. re-check pending executable cap
6. insert `deltallm_batch_job` row with:
   - status `queued`
   - `queued_at = now()`
   - ownership and service-tier fields copied from session
7. bulk insert `deltallm_batch_item` rows from staged artifact
8. mark session `completed`
9. commit

Failure behavior:

- if commit fails, no visible batch should exist
- session remains staged or becomes `failed_retryable` / `failed_permanent`

## Repository And Module Plan

Likely modules:

- `src/batch/create/promoter.py`
- `src/batch/create/session_repository.py`
- additions to `src/batch/repository.py`
- additions to batch repositories for batch/job/item insertion coordination

Recommended promoter API shape:

```python
async def promote_session_to_batch(*, session_id: str) -> BatchJobRecord
```

or an equivalent service-level abstraction returning the batch plus promotion metadata.

## Affected Files

Implementation:

- `src/batch/create/promoter.py`
- `src/batch/create/session_repository.py`
- `src/batch/create/staging.py`
- `src/batch/repository.py`
- `src/batch/repositories/job_repository.py`
- `src/batch/repositories/item_repository.py`
- possibly a dedicated low-level transaction helper if Prisma abstraction becomes too limiting

Tests:

- new DB-backed tests in `tests/test_batch_db_integration.py`
- unit tests for promoter edge cases

## Test Plan

Required DB-backed scenarios:

1. successful promotion creates exactly one `queued` batch and all expected items
2. promotion failure leaves no executable batch
3. same-scope concurrent promotion enforces pending cap correctly
4. repeated promotion of a completed session returns the same batch
5. workers cannot claim a batch before promotion commit
6. bulk insert path handles large item counts correctly

## Acceptance Criteria

PR 3 is complete when:

1. A staged session can be promoted into a `queued` batch.
2. No partial executable batch is visible after promotion failure.
3. Strict pending cap is enforced at promotion time.
4. Promotion is idempotent at the session level.
5. DB-backed concurrency tests pass.

## Risks

Main risks:

- bulk insert performance under large batches
- awkward transaction support in the current repository stack

Mitigation:

- benchmark insert chunk sizes in this PR
- keep the promoter isolated so low-level transaction changes do not spread through the codebase

## Deliverable Summary

PR 3 should deliver the atomic promotion primitive that makes the architecture viable.
