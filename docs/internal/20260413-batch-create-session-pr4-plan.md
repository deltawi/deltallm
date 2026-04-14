# PR 4 Plan: Public Batch Create Cutover And Idempotent Session Resolution

## Objective

PR 4 switches the public batch-create path to the new create-session architecture.

Target outcome:

- `POST /v1/batches` uses validate -> stage -> session -> promote
- new creates no longer emit `preparing` or `validating`
- retries can safely resolve the same result when `Idempotency-Key` is supplied
- the old path remains behind a short-lived fallback flag only for rollback safety

This is the traffic cutover PR.

Related plans:

- [`20260413-batch-create-session-architecture-plan.md`](20260413-batch-create-session-architecture-plan.md)
- [`20260413-batch-create-session-pr1-plan.md`](20260413-batch-create-session-pr1-plan.md)
- [`20260413-batch-create-session-pr2-plan.md`](20260413-batch-create-session-pr2-plan.md)
- [`20260413-batch-create-session-pr3-plan.md`](20260413-batch-create-session-pr3-plan.md)

## Branch And Merge Target

PR 4 should be implemented on:

- `pr/batch-create-session-4-public-cutover`

PR 4 should be opened against:

- `feature/batch-create-session`
- not `main`

Stack rules:

1. Branch from the latest tip of `feature/batch-create-session` after PR 3 is merged there.
2. Merge back into `feature/batch-create-session`.
3. Keep rollback possible in this PR. Do not delete old create-path code yet.
4. Do not remove `repair-preparing` or legacy cleanup logic yet. That belongs later.
5. Public cutover and legacy deletion must remain separate.

## Scope

In scope for PR 4:

1. Switch public `POST /v1/batches` to the new create-session flow behind a config flag.
2. Add optional `Idempotency-Key` support and scope-aware resolution.
3. Rework `BatchService` so create-path logic moves into `src/batch/create/*`.
4. Ensure client-visible success and error responses remain compatible where intended.
5. Add audit coverage for idempotency key usage and session-driven create outcomes.
6. Add DB-backed tests for public cutover and retry behavior.

Out of scope for PR 4:

1. Admin session endpoints.
2. Removal of old `preparing` / `validating` logic.
3. Removal of staged repair worker.
4. UI changes.

## Review Discipline

PR 4 must follow the shared review checklist in:

- [`20260413-batch-create-session-review-checklist.md`](20260413-batch-create-session-review-checklist.md)

PR-specific invariants:

1. Public batch-create success responses remain normal batch objects.
2. With cutover enabled, new jobs are created directly as `queued`; the public path must not create `preparing` or `validating` jobs.
3. `Idempotency-Key` resolution must not use a namespace broader than the caller's real access boundary.
4. Idempotent payload mismatch returns `409` without creating a second session or batch.
5. New requests rejected for a full pending scope must fail before staged artifact or create-session persistence.
6. The promoter soft precheck stays advisory; only the promoter's authoritative path may mutate durable promotion state.
6. The old batch-create path remains available when the create-session service is not bound.
7. Audit events for `/v1/batches` include whether an idempotency key was present and whether the request used the create-session path.

Failure-mode pass for PR 4:

1. Matching idempotent retry after the original input file is no longer readable.
2. Same key with mismatched metadata.
3. Same key with a permanently failed session.
4. Same organization, different teams, same `Idempotency-Key`.
5. Early public capacity rejection after cutover.
6. Promotion-time capacity rejection after cutover.
5. Storage read failure while validating the uploaded batch input.
6. Rollback path with create-session cutover disabled.

## Design Decisions

### 1. Keep public response contract stable

Successful responses should still return a normal batch object.

This PR is an internal-architecture cutover, not a public API redesign.

### 2. Add optional `Idempotency-Key`

Recommended behavior:

- if the header is absent, create behaves as a normal non-idempotent request
- if present, the create-session layer must resolve retries safely

Scope key recommendation:

1. team id if present
2. else organization id if present
3. else api key

Pending-cap and scheduling scope recommendation:

1. team id if present
2. else api key

### 3. Soft precheck remains advisory

Early saturation prechecks may stay, but the authoritative backlog decision remains promotion-time only.

### 4. Keep old path behind a temporary flag

Recommended config:

- `embeddings_batch_create_sessions_enabled`

Behavior:

- `false`: old path
- `true`: new path

This flag should exist only until PR 6 removes the old architecture.

## Public Flow After Cutover

Recommended request sequence:

1. load input file and enforce ownership
2. stream-validate input into normalized staged artifact
3. resolve or create session
4. promote session
5. return resulting batch

With `Idempotency-Key`:

- existing completed session returns existing batch
- existing retryable session retries promotion
- existing permanent failure returns the stored failure
- payload mismatch returns `409`

## Affected Files

Implementation:

- `src/api/v1/endpoints/batches.py`
- `src/models/requests.py` only if request metadata or helper types need small changes
- `src/batch/service.py`
- `src/batch/create/service.py`
- `src/batch/create/__init__.py`
- `src/batch/create/session_repository.py`
- `src/batch/create/promoter.py`
- `src/bootstrap/batch.py`

Tests:

- `tests/test_batch_create_service.py`
- `tests/test_batch_service.py`
- `tests/test_batch_db_integration.py`
- `tests/test_data_plane_audit_extended.py`
- `tests/bootstrap/test_optional_bootstrap.py`

## Test Plan

Required:

1. Public create with new flag enabled returns a normal batch object.
2. No new `preparing` / `validating` jobs are created when cutover flag is enabled.
3. Same idempotency key returns the same batch after ambiguous retry.
4. Payload mismatch under same idempotency key returns `409`.
5. Promotion failure returns correct client-visible error without creating a partial batch.
6. Existing old-path behavior still works when the flag is off.
7. Matching idempotent retry can resolve an existing session before reloading the input file.
8. Audit coverage records `idempotency_key_present` and the create-path outcome.
9. Different teams in the same organization do not collide on the same `Idempotency-Key`.
10. Full pending scope returns `429` before staged artifact or create-session persistence for new requests.

## Acceptance Criteria

PR 4 is complete when:

1. Public batch creation can run end-to-end on the create-session path.
2. New jobs are born `queued`, not `preparing` / `validating`.
3. Idempotent retry is supported when requested.
4. Rollback to the old path is still possible by config.
5. The focused PR4 validation slice passes on unit tests, and DB-backed cutover tests exist for environments with Postgres available.
6. Cross-team org callers do not share an idempotency namespace.

## Risks

Main risks:

- subtle public behavior drift
- audit gaps
- idempotency edge cases around payload matching

Mitigation:

- keep response contract unchanged
- add explicit request/response/audit tests
- keep old path behind flag until legacy removal

## Deliverable Summary

PR 4 should turn live create traffic onto the new architecture without yet deleting the fallback.

## Implementation Status

Implemented in this branch:

1. `BatchService` delegates public create requests to `src/batch/create/service.py` when the create-session service is bound.
2. `/v1/batches` forwards `Idempotency-Key` and emits cutover-aware audit metadata.
3. Bootstrap wires `BatchCreateSessionService` behind `embeddings_batch_create_sessions_enabled`.
4. Focused unit and audit tests cover the cutover path.
5. DB-backed cutover tests cover queued-job creation, idempotent retry reuse, and idempotency mismatch rejection.
6. Shared batch-scope helpers keep idempotency and pending-cap semantics aligned across the public service, legacy service, and promoter.
7. The public cutover path restores the cheap pending-cap reject before staged artifact creation.

Validation currently run in this worktree:

1. `tests/test_batch_service.py`
2. `tests/test_batch_create_service.py`
3. `tests/test_data_plane_audit_extended.py`
4. `tests/bootstrap/test_optional_bootstrap.py`
5. Targeted PR4 DB-backed cutover tests in `tests/test_batch_db_integration.py`

Note:

- the DB-backed PR4 tests are present and collect cleanly, but they skip locally unless `DATABASE_URL` points to a live Postgres instance.
