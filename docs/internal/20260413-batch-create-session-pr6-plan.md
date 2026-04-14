# PR 6 Plan: Final Cutover, Executable-Only `BatchJob`, And Compatibility Removal

## Objective

PR 6 should finish the create-session redesign by removing the remaining architectural fallback and executable-job compatibility that still exist after PRs 1 through 5.

Target outcome:

- public batch creation always uses the create-session architecture when embeddings batching is enabled
- `DeltaLLM_BatchJob` is execution-only in both schema and runtime code
- `validating` is removed from executable batch semantics, admin filters, and tests
- the codebase keeps the new session admin and cleanup surfaces without carrying the old create-path compatibility
- PR 6 is a final simplification pass, not another redesign

This PR should be intentionally narrower than earlier drafts. Most of the original “remove preparing / leases / repair-preparing” work is already gone from the codebase. The remaining job is to remove the actual leftover compatibility, not to relitigate the whole architecture.

Related plans:

- [`20260413-batch-create-session-architecture-plan.md`](20260413-batch-create-session-architecture-plan.md)
- [`20260413-batch-create-session-pr1-plan.md`](20260413-batch-create-session-pr1-plan.md)
- [`20260413-batch-create-session-pr2-plan.md`](20260413-batch-create-session-pr2-plan.md)
- [`20260413-batch-create-session-pr3-plan.md`](20260413-batch-create-session-pr3-plan.md)
- [`20260413-batch-create-session-pr4-plan.md`](20260413-batch-create-session-pr4-plan.md)
- [`20260413-batch-create-session-pr5-plan.md`](20260413-batch-create-session-pr5-plan.md)

## Branch And Merge Target

PR 6 should be implemented on:

- `pr/batch-create-session-6-legacy-removal`

PR 6 should be opened against:

- `feature/batch-create-session`
- not `main`

Stack rules:

1. Branch from the latest tip of `feature/batch-create-session` after PR 5 is merged there.
2. Merge back into `feature/batch-create-session`.
3. Keep this PR focused on final cutover and compatibility removal only.
4. Do not turn this PR into a broad bootstrap rewrite, UI redesign, or execution-worker refactor.
5. Remove architectural rollback paths, but keep operational controls that still have clear value.

## Current Post-PR5 Reality

After PR 5, the old architecture is already mostly gone.

What is already true:

1. New public creates can use the create-session flow.
2. Session staging, promotion, cleanup, and admin operations exist as first-class runtime components.
3. There is no remaining runtime `preparing` state machine, creator heartbeat, or `repair-preparing` endpoint in the active code path.
4. Session-centric ops are now available.

What actually remains as legacy compatibility:

1. `embeddings_batch_create_sessions_enabled` still preserves a public-create fallback path.
2. `DeltaLLM_BatchJobStatus` still includes `validating`.
3. Some repository defaults, admin filters, fixtures, and docs still assume executable jobs may be `validating`.
4. A few tests and internal docs still describe PR 6 as if it needs to remove much more than the code now contains.

PR 6 should target those remaining surfaces directly.

## Preconditions

Before PR 6 starts, all of the following should be true:

1. PRs 1 through 5 are merged into `feature/batch-create-session`.
2. The create-session public path has been exercised successfully in staging or an equivalent controlled environment.
3. Session-centric admin and cleanup behavior has been validated operationally.
4. All target shared environments are ready to stop relying on `embeddings_batch_create_sessions_enabled=false` as a rollback strategy.
5. All target shared environments have no live executable jobs in `validating`.

Required preflight query:

```sql
SELECT COUNT(*) AS validating_jobs
FROM deltallm_batch_job
WHERE status = 'validating';
```

If that count is non-zero, PR 6 is not ready to deploy. Those rows must be explicitly retired or migrated with a separate operational step before removing the enum label.

## Scope

In scope for PR 6:

1. Remove the public-create rollback flag `embeddings_batch_create_sessions_enabled`.
2. Remove the legacy branch in `BatchService` that falls back to the old direct-create path.
3. Make create-session public create binding unconditional whenever embeddings batching is enabled.
4. Remove `validating` from the executable `BatchJob` status set in code.
5. Remove `validating` from the Prisma enum and add the migration required to do that safely.
6. Remove `validating` compatibility from admin filtering, repository defaults, mappers, fixtures, and tests.
7. Update internal docs so PR 6 reflects the real final-cutover scope instead of the older broader draft.
8. Remove dead public-create configuration that only existed for the deleted legacy direct-create path.

Out of scope for PR 6:

1. New create-session feature work.
2. Refactoring session cleanup or promotion semantics.
3. Removing operational flags that still serve a real runtime purpose.
4. Refactoring the batch execution worker for style or consistency only.
5. UI/product enhancements unrelated to legacy removal.
6. Bulk schema cleanup outside the `BatchJob.status` compatibility boundary.

## Current Implementation Status

Current branch: `pr/batch-create-session-6-legacy-removal`

Implemented in this branch:

1. Public batch creation now fails closed unless a create-session service is bound, and bootstrap now binds that service unconditionally whenever embeddings batching is enabled.
2. `BatchJobStatus` no longer exposes `validating` in Python or Prisma schema code.
3. Repository defaults now create executable jobs as `queued`.
4. Admin batch filtering no longer accepts `validating`.
5. The old public-create compatibility flag has been removed from config.
6. The dead `embeddings_batch_create_buffer_size` knob has been removed with the legacy direct-create path.
7. A dedicated enum-removal migration now fails clearly if any `deltallm_batch_job.status = 'validating'` rows still exist.
8. Bootstrap now probes create-session schema readiness before starting batch workers or binding the public create-session path.
9. The older batch-job enum migration has been hardened to tolerate both the pre-PR6 enum shape and the already-final enum shape.

Validation completed so far:

1. Focused unit slice passed:
   - `tests/test_batch_service.py`
   - `tests/test_batch_create_service.py`
   - `tests/test_batch_repository.py`
   - `tests/bootstrap/test_optional_bootstrap.py`
   - `tests/test_data_plane_audit_extended.py`
2. Focused `ruff` slice passed on the touched runtime and test files.
3. `prisma validate --schema=./prisma/schema.prisma` passed.

Validation still pending in this environment:

1. DB-backed PR 6 migration/status checks require a live Postgres on `localhost:5432`; the targeted spot check could not run here because no database server was reachable.

## Review Discipline

PR 6 must follow the shared review checklist in:

- [`20260413-batch-create-session-review-checklist.md`](20260413-batch-create-session-review-checklist.md)

PR-specific invariants:

1. Public batch creation must always go through the create-session architecture when `embeddings_batch_enabled` is true.
2. `BatchJob` must not accept or emit `validating` after this PR.
3. The Prisma schema, Python status constants, repository defaults, and admin filters must all agree on the same executable status set.
4. Session admin and session cleanup must continue to work after the public-create rollback flag is removed.
5. PR 6 must remove architectural fallback, not operational safety knobs.
6. If a remaining toggle still has operational value, keep it and document why.
7. The PR must not widen into unrelated cleanup just because code is nearby.
8. Enum migration must fail clearly if shared environments still contain `validating` rows.
9. Batch execution behavior must stay unchanged for `queued`, `in_progress`, `finalizing`, and terminal states.
10. The codebase should be simpler after PR 6, but simplicity must come from removing dead compatibility, not from risky incidental rewrites.
11. Bootstrap must not report batching ready unless the create-session schema is actually usable.
12. The migration chain must tolerate both the old enum shape and the already-final enum shape during rollout.

Failure-mode pass for PR 6:

1. Environment contains one or more `deltallm_batch_job.status = 'validating'` rows before migration.
2. Admin batch list still receives `status=validating`.
3. Boot with embeddings batching enabled after `embeddings_batch_create_sessions_enabled` is removed.
4. Session cleanup disabled while public create remains enabled.
5. Batch create request during startup before the session service is fully wired.
6. Raw DB insert or stale fixture still attempts to persist `validating`.
7. DB-backed tests still assume a `validating` executable job.
8. Internal docs and rollout notes still describe a broader “preparing” removal than the code actually requires.
9. Database already has the final `DeltaLLM_BatchJobStatus` enum shape before `_prisma_migrations` catches up.
10. Embeddings batching is enabled while `deltallm_batch_create_session` is missing.

## Design Decisions

### 1. Remove the architectural rollback flag, keep meaningful operational controls

`embeddings_batch_create_sessions_enabled` should be removed in PR 6 because it preserves a second public-create architecture. That is exactly the kind of long-lived fallback that turns into permanent complexity.

By contrast, these should remain unless there is a separate reason to remove them:

- `embeddings_batch_create_session_cleanup_enabled`
- create-session cleanup retention settings
- create-session promotion timeout settings
- create-session soft precheck and idempotency controls if they still serve product or rollout needs

Reason:

- architectural rollback and operational tuning are not the same thing
- deleting both in one PR makes late-stack cleanup much riskier than it needs to be

### 2. `BatchJob` becomes strictly executable-only

After PR 6, the executable batch status set should be:

- `queued`
- `in_progress`
- `finalizing`
- `completed`
- `failed`
- `cancelled`
- `expired`

`validating` should not survive in:

- `src/batch/models.py`
- `src/batch/repository.py`
- `src/batch/repositories/job_repository.py`
- `src/batch/repositories/mappers.py`
- `src/api/admin/endpoints/batches.py`
- `prisma/schema.prisma`
- DB migrations
- fixtures and tests

### 3. Remove the fallback path without forcing an unnecessary service-construction rewrite

PR 6 should delete the legacy create branch in `BatchService`.

It does not need to rewrite bootstrap wiring more than necessary. If keeping `bind_create_session_service(...)` is the simplest initialization seam, that is acceptable, as long as:

- the service is always bound when batching is enabled
- no runtime fallback path remains

### 4. Prefer explicit migration failure over silent status remapping

If shared environments still contain `validating` rows, the migration should fail clearly.

Do not silently rewrite:

- `validating -> queued`
- `validating -> failed`

inside the migration unless there is an explicitly approved data-migration strategy outside this PR.

Reason:

- silent remapping hides operational state assumptions
- PR 6 should simplify architecture, not make up state transitions for historical rows

## Affected Files

Implementation:

- `src/batch/service.py`
- `src/bootstrap/batch.py`
- `src/config.py`
- `src/batch/models.py`
- `src/batch/repository.py`
- `src/batch/repositories/job_repository.py`
- `src/batch/repositories/mappers.py`
- `src/api/admin/endpoints/batches.py`
- `prisma/schema.prisma`
- new migration under `prisma/migrations/`

Tests:

- `tests/test_batch_service.py`
- `tests/test_batch_repository.py`
- `tests/bootstrap/test_optional_bootstrap.py`
- `tests/test_batch_db_integration.py`
- `tests/test_data_plane_audit_extended.py`
- any fixture-based tests still returning or asserting `validating`

Docs:

- `docs/internal/20260413-batch-create-session-pr6-plan.md`
- `docs/internal/20260413-batch-create-session-architecture-plan.md`
- any rollout/internal docs that still describe PR 6 as broader “preparing” cleanup

## Removal Checklist

Delete or refactor:

1. `embeddings_batch_create_sessions_enabled` from config, bootstrap wiring, and tests.
2. The legacy non-session create branch in `BatchService.create_embeddings_batch_result(...)`.
3. `BatchJobStatus.VALIDATING` in Python.
4. `validating` from `BATCH_JOB_STATUSES`.
5. Repository defaults that still create jobs as `validating`.
6. Mapper fallbacks that default missing/invalid batch job rows to `validating`.
7. Admin batch list filter support for `validating`.
8. Stale fixtures and audit stubs that still model executable jobs as `validating`.
9. PR6 doc language that talks about already-removed `preparing` repair systems as if they still exist.

Keep unless a separate concrete reason emerges:

1. session cleanup worker and its enablement flag
2. create-session admin runtime and endpoints
3. promotion timeout settings
4. create-session soft precheck and idempotency flags if they are still intentional rollout/product knobs

## Test Plan

Required:

1. Public create path still succeeds with embeddings batching enabled after the public cutover flag is removed.
2. `BatchService` no longer has a legacy direct-create fallback branch.
3. Bootstrap always wires the create-session service when embeddings batching is enabled.
4. Admin batch list no longer accepts or returns `validating` as a supported executable status.
5. Runtime code rejects invalid `BatchJob.status` values that include the removed legacy label.
6. DB-backed tests confirm executable jobs use the reduced enum set only.
7. DB-backed migration validation fails cleanly if `validating` rows still exist.

Focused regression expectations:

1. No create-session admin or cleanup behavior regresses because the public cutover flag was removed.
2. No operator surface still advertises executable batches as `validating`.
3. No fixture-based test silently depends on the removed legacy label.

## Acceptance Criteria

PR 6 is complete when:

1. `embeddings_batch_create_sessions_enabled` is removed.
2. Public batch create always uses the create-session architecture.
3. `DeltaLLM_BatchJobStatus` no longer contains `validating`.
4. No runtime code, admin filtering, or focused tests treat `validating` as a valid executable batch status.
5. The enum migration handles the shared-environment safety case explicitly.
6. The codebase is simpler because the remaining compatibility is actually gone, not just hidden.

## Risks

Main risks:

- removing the cutover flag before all target environments are comfortable without the old public path
- deploying the enum change while `validating` rows still exist
- over-cleaning and deleting useful operational controls in the name of simplification

Mitigation:

- keep the PR narrowly scoped to actual remaining compatibility
- require preflight verification that `validating` rows are gone
- fail fast in migration rather than remapping historical rows silently
- keep operational controls that still have a clear runtime purpose
- run focused unit coverage plus DB-backed batch tests

## Deliverable Summary

PR 6 should be the final architectural cleanup PR, not another round of feature work.

If PR 6 is done well, the system should look like this afterward:

- one public create architecture
- one executable batch status model
- one session-centric create failure and ops surface
- fewer compatibility branches, not fewer operational safeguards
