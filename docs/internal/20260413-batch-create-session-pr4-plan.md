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

## Design Decisions

### 1. Keep public response contract stable

Successful responses should still return a normal batch object.

This PR is an internal-architecture cutover, not a public API redesign.

### 2. Add optional `Idempotency-Key`

Recommended behavior:

- if the header is absent, create behaves as a normal non-idempotent request
- if present, the create-session layer must resolve retries safely

Scope key recommendation:

1. organization id if present
2. else team id
3. else api key

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
- `src/batch/create/session_repository.py`
- `src/batch/create/promoter.py`
- `src/routers/audit_helpers.py` or related audit helpers if extra metadata is emitted

Tests:

- `tests/test_batch_service.py`
- `tests/test_batch_db_integration.py`
- request/audit tests if present for public batch endpoints

## Test Plan

Required:

1. Public create with new flag enabled returns a normal batch object.
2. No new `preparing` / `validating` jobs are created when cutover flag is enabled.
3. Same idempotency key returns the same batch after ambiguous retry.
4. Payload mismatch under same idempotency key returns `409`.
5. Promotion failure returns correct client-visible error without creating a partial batch.
6. Existing old-path behavior still works when the flag is off.

## Acceptance Criteria

PR 4 is complete when:

1. Public batch creation can run end-to-end on the create-session path.
2. New jobs are born `queued`, not `preparing` / `validating`.
3. Idempotent retry is supported when requested.
4. Rollback to the old path is still possible by config.

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
