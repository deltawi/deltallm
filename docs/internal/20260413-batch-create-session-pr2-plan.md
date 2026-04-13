# PR 2 Plan: Session Repository, Normalized Staging Artifacts, And Session Cleanup

## Objective

PR 2 implements durable create-session staging and cleanup, still without cutting public batch creation over to the new path.

Target outcome:

- normalized staged artifacts can be written and read through a dedicated staging abstraction
- `BatchCreateSession` records can be created, updated, queried, and expired
- a session cleanup worker can remove expired session records and staged artifacts
- all of this remains dark-launched and unused by public traffic by default

This PR should make the new durability boundary real, but not yet authoritative.

Related plans:

- [`20260413-batch-create-session-architecture-plan.md`](20260413-batch-create-session-architecture-plan.md)
- [`20260413-batch-create-session-pr1-plan.md`](20260413-batch-create-session-pr1-plan.md)

## Branch And Merge Target

PR 2 should be implemented on:

- `pr/batch-create-session-2-session-staging-cleanup`

PR 2 should be opened against:

- `feature/batch-create-session`
- not `main`

Stack rules:

1. Branch from the latest tip of `feature/batch-create-session` after PR 1 is merged there.
2. Merge back into `feature/batch-create-session`.
3. Do not cut over `POST /v1/batches` yet.
4. Do not implement promotion into `BatchJob` yet.
5. This PR may add cleanup runtime, but it must only manage create sessions and staged artifacts.

## Scope

In scope for PR 2:

1. Implement the create-session repository.
2. Implement normalized staged artifact write/read/delete abstractions.
3. Define the normalized artifact record format.
4. Implement create-session cleanup worker and bootstrap wiring.
5. Add create-session metrics/logs for staging and cleanup.
6. Add DB-backed tests for session creation, lookup, update, expiry, and artifact cleanup.

Out of scope for PR 2:

1. Creating a real `BatchJob` from a session.
2. Public request idempotency behavior.
3. Public API cutover.
4. Admin session endpoints.
5. Legacy staged-job deletion.

## Design Decisions

### 1. Staged artifact is the retry boundary

The normalized staged artifact should contain exactly the data needed to create `BatchItem` rows later:

```json
{"line_number":1,"custom_id":"req-1","request_body":{"model":"m1","input":"..."}}
```

It should not contain:

- public file metadata
- dynamic worker lease fields
- mutable execution state

### 2. Staged artifacts are internal-only

Do not register staged artifacts as `DeltaLLM_BatchFile` records.

They are internal implementation details and should not appear in:

- public file list/read endpoints
- admin batch artifact views

### 3. Cleanup worker owns only session artifacts

PR 2 should introduce a separate cleanup worker for create sessions rather than extending terminal batch GC.

Reason:

- different lifecycle object
- different retention windows
- different failure semantics

### 4. Keep write format append-friendly and streaming-friendly

The staging abstraction should support:

- streaming writes from validation
- streaming reads for later promotion
- backend reuse across local and S3 artifact stores

### 5. Failure classification starts here

The session repository should support:

- `staged`
- `completed`
- `failed_retryable`
- `failed_permanent`
- `expired`

Even before promotion exists, the code should carry those states explicitly.

## Normalized Artifact Design

Recommended storage prefix:

- `batch-create-stage/`

Recommended staged artifact properties:

- immutable once written
- UTF-8 JSONL
- one record per validated request line
- line-oriented for stream-friendly read during promotion

Recommended writer responsibilities:

- write validated normalized lines
- compute bytes and checksum
- return `(backend, key, checksum, bytes)`

Recommended reader responsibilities:

- iterate normalized lines as strongly typed records
- enforce max line sizes using the same artifact protections as batch files

## Session Repository Responsibilities

Implement methods such as:

- `create_session(...)`
- `get_session(session_id)`
- `get_session_by_idempotency(...)`
- `mark_session_completed(...)`
- `mark_session_failed_retryable(...)`
- `mark_session_failed_permanent(...)`
- `mark_session_expired(...)`
- `list_expirable_sessions(...)`
- `delete_session(...)`

Recommended behavior:

- no implicit retries
- no background promotion
- repository should only persist and query lifecycle state

## Cleanup Worker Design

Recommended new worker:

- `BatchCreateSessionCleanupWorker`

Responsibilities:

1. list expirable sessions
2. delete staged artifacts if still present
3. mark or delete session rows according to retention policy
4. emit cleanup metrics/logs

Retention recommendations:

- completed session retention: 24 hours
- retryable failure retention: 24 hours
- permanent failure retention: 7 days

## Affected Files

Implementation:

- `src/batch/create/session_repository.py`
- `src/batch/create/staging.py`
- `src/batch/create/cleanup.py`
- `src/batch/create/models.py`
- `src/batch/__init__.py`
- `src/bootstrap/batch.py`
- `src/config.py`
- `src/metrics/batch.py`

Possibly:

- `src/batch/storage.py` or related storage abstractions if helper reuse is needed

Tests:

- new tests for create-session repository
- new tests for staging read/write
- new tests for create-session cleanup worker
- `tests/bootstrap/test_optional_bootstrap.py`

## Test Plan

Required:

1. Unit tests for staged artifact write/read/delete.
2. Unit tests for session repository lifecycle transitions.
3. DB-backed tests for session persistence and cleanup.
4. Bootstrap tests for cleanup worker wiring.
5. Existing batch execution tests remain green.

## Acceptance Criteria

PR 2 is complete when:

1. A create session can be created and persisted with staged artifact metadata.
2. A normalized staged artifact can be written and read without using `BatchFile`.
3. A cleanup worker can expire sessions and delete internal staged artifacts.
4. No public batch-create behavior changes.
5. No executable `BatchJob` behavior changes.

## Risks

Main risks:

- storing large staged payloads inefficiently
- cleanup logic accidentally touching public batch artifacts

Mitigation:

- keep staged artifacts under a dedicated prefix
- keep session cleanup worker separate from batch retention worker
- add explicit tests proving `BatchFile` rows are untouched

## Deliverable Summary

PR 2 should make create-session staging durable and operable, but still dark-launched.
