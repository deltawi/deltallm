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

Current implementation status:

- `BatchCreateSessionPromoter` is the internal promotion primitive
- `BatchJob.status` is validated in code and enforced through the Prisma schema so both `migrate deploy` and `db push` keep the closed set
- staged artifacts are pre-spooled outside the database transaction to keep the transactional window DB-focused
- promotion runs with explicit, configurable transaction `max_wait` and `timeout`
- a soft pre-spool pending-cap precheck can reject clearly saturated scopes before staged-artifact rereads without mutating session state, while still emitting an advisory rejection metric
- promotion currently accepts `staged` and `failed_retryable` sessions, and returns idempotently for `completed`
- the promoter is wired into bootstrap as an internal app-state seam only; public batch-create still uses the legacy path
- container startup now runs a migrate-only Prisma bootstrap helper from `src.prisma_bootstrap`, outside the `src.bootstrap` package import tree, so migrations do not depend on unrelated bootstrap imports

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

## PR 3 Invariants

1. No executable `BatchJob` row may exist for a create session until promotion commits.
2. Promoted jobs are inserted directly as `queued`; PR 3 must not reintroduce `preparing` or `validating`.
3. Promotion must be idempotent by `session_id` and `target_batch_id`.
4. Promotion failure must not leave partial `BatchJob` or `BatchItem` rows visible.
5. Pending-cap enforcement must use committed executable jobs only.
6. Session failure recording must not overwrite a concurrently completed session.
7. Staged artifact IO should happen outside the DB transaction whenever possible.
8. `BatchJob.status` must be a closed set in both code and the Prisma-managed database schema.
9. Promotion transaction timing must be configurable, not hard-coded.
10. The pre-spool capacity precheck is advisory only, must not mutate session state, and the locked in-transaction check remains authoritative.
11. Environments that previously reached the enum shape through `db push` must still be able to run the PR3 migration safely.
12. Advisory precheck rejections must remain visible in metrics.
13. Bootstrap wiring must remain internal-only; public API behavior stays unchanged in PR 3.
14. Container startup must never mask migration failure by falling back to `db push`.
15. Prisma bootstrap must remain isolated from `src.bootstrap` package imports.

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

Implemented contract:

1. staged artifact is read and normalized into a local spool before the DB transaction starts
2. promotion may run a soft capacity precheck before spooling to reject already-saturated scopes cheaply
3. a soft-precheck rejection does not change session status, error fields, or `promotion_attempt_count`
4. a soft-precheck rejection increments `promotion_precheck/rejected` in the create-session action metric
5. promotion then runs in one Prisma transaction with explicit configured `max_wait` and `timeout`
6. `BatchItem` rows are inserted in chunks through the existing repository bulk-insert path

### 4. `BatchJob` is born `queued`

Do not create:

- `preparing`
- `validating`
- hidden transitional execution rows

The final committed `BatchJob` row should be visible only as `queued`.

### 5. Promotion is idempotent by session, not by public API yet

If the promoter is called twice on the same already-completed session, it should return the existing `BatchJob` rather than creating a duplicate.

This is required before public idempotency is exposed in PR 4.

### 6. Failure recording is guarded

Promotion failures should only transition sessions that are still in promotable states.

This avoids stale retry/failure updates overwriting a concurrently completed session.

## Promotion Transaction Design

Recommended transaction steps:

Implemented flow:

1. fetch the session outside the transaction
2. if it is already `completed`, return the existing `BatchJob`
3. pre-read the staged artifact into a local spool and verify item-count integrity
4. start a Prisma transaction with explicit `max_wait` / `timeout`
5. lock `BatchCreateSession` by `session_id`
6. if the session is now `completed`, return the existing `BatchJob`
7. if a `BatchJob` already exists for `target_batch_id`, reconcile the session to `completed` and return it
8. acquire scope advisory lock
9. re-check pending executable cap
10. insert `deltallm_batch_job` directly as `queued`
11. bulk insert `deltallm_batch_item` rows from the pre-spooled artifact
12. mark session `completed` and increment `promotion_attempt_count`
13. commit

Failure behavior:

- if commit fails, no visible batch should exist
- artifact-corruption failures are marked `failed_permanent`
- transactional or capacity failures are marked `failed_retryable`
- failure updates are guarded to promotable statuses only

## Repository And Module Plan

Likely modules:

- `src/batch/create/promoter.py`
- `src/batch/create/session_repository.py`
- additions to `src/batch/repository.py`
- additions to `src/batch/repositories/job_repository.py`

Recommended promoter API shape:

```python
async def promote_session(session_id: str) -> BatchCreatePromotionResult
```

## Affected Files

Implementation:

- `src/batch/create/promoter.py`
- `src/batch/create/session_repository.py`
- `src/batch/create/staging.py`
- `src/batch/repository.py`
- `src/batch/repositories/job_repository.py`
- `src/batch/repositories/item_repository.py`
- `src/bootstrap/batch.py`

Tests:

- new DB-backed tests in `tests/test_batch_db_integration.py`
- unit tests for promoter edge cases

## Failure-Mode Pass

Before PR 3 is considered complete, validate these cases explicitly:

1. an invalid `BatchJob.status` from a future caller fails before persistence
2. a raw invalid `deltallm_batch_job.status` insert is rejected in environments bootstrapped by `db push`
3. an environment where `DeltaLLM_BatchJobStatus` already exists from `db push` can still apply the PR3 migration safely
4. a clearly saturated scope fails before staged-artifact respooling, leaves the session unchanged, and emits a precheck rejection metric
5. a scope that looks available during the soft precheck but becomes saturated before commit is rejected by the locked in-transaction check and recorded as retryable
6. promotion still succeeds on slower databases when transaction timing is raised through config rather than code edits
7. Prisma bootstrap retries only on transient database-connectivity failures
8. Prisma bootstrap exits hard on migration/schema errors instead of reconciling schema with `db push`
9. Importing the Prisma bootstrap entrypoint must not import `src.bootstrap` or its submodules

## Test Plan

Required DB-backed scenarios:

1. successful promotion creates exactly one `queued` batch and all expected items
2. promotion failure leaves no executable batch
3. same-scope concurrent promotion enforces pending cap correctly
4. repeated promotion of a completed session returns the same batch
5. workers cannot claim a batch before promotion commit
6. bulk insert path handles large item counts correctly
7. Prisma bootstrap helper retries transient connectivity failures and fails fast on real migration errors
8. Prisma bootstrap import-isolation regression test passes

Required unit scenarios:

1. completed session returns the existing batch without re-reading staged records
2. unpromotable statuses fail without mutating session state
3. soft precheck rejects saturated scopes before staged-artifact reads
4. the locked in-transaction cap check still rejects contention races after spooling
5. promoter transactions use configured `max_wait` and `timeout`
6. invalid `BatchJob.status` values are rejected before SQL

## Acceptance Criteria

PR 3 is complete when:

1. A staged session can be promoted into a `queued` batch.
2. No partial executable batch is visible after promotion failure.
3. Strict pending cap is enforced at promotion time.
4. Promotion is idempotent at the session level.
5. Invalid `BatchJob.status` values cannot be persisted in either `migrate deploy` or `db push` environments.
6. Promotion tx settings are configurable through app config.
7. Saturated-scope soft-precheck rejections do not reread staged artifacts, do not mutate session state, and remain visible in metrics.
8. DB-backed concurrency tests pass.

Current validation:

- focused unit tests for promoter, session repository, scaffolding, bootstrap, batch service, batch repository, and metrics pass locally
- promoter unit coverage now includes soft-precheck short-circuiting, configured tx timing, and the locked recheck path
- DB-backed promotion tests are present and collect cleanly, but they still skip in this worktree unless `DATABASE_URL` points at a live Postgres instance

## Risks

Main risks:

- bulk insert performance under large batches
- awkward transaction support in the current repository stack

Mitigation:

- keep the transaction DB-focused by pre-spooling artifacts before entering it
- benchmark insert chunk sizes in this PR
- keep the promoter isolated so low-level transaction changes do not spread through the codebase

## Deliverable Summary

PR 3 should deliver the atomic promotion primitive that makes the architecture viable.
