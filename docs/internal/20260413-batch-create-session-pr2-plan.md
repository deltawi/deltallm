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

## PR-Specific Invariants

PR 2 must maintain these invariants:

1. Staged artifacts are internal-only and never represented as `BatchFile` rows.
2. Orphan cleanup must use the full configured `scan_limit` whenever enough eligible artifacts exist.
3. Orphan selection must not starve a backend and must not waste cleanup capacity when another backend is sparse or empty.
4. Local orphan discovery work must scale with `limit`, not with the total number of files under the stage prefix.
5. Legacy flat local staged artifacts must remain discoverable until PR 2 explicitly removes compatibility.
6. Cleanup must remain scoped to create sessions and internal staged artifacts only.
7. Session cleanup must prefer recoverable leftover artifacts over deleting live or refreshed session state.

## Failure-Mode Pass

PR 2 review must explicitly cover:

1. artifact write succeeds but session insert fails
2. compensation delete fails and orphan sweep must recover later
3. cleanup races with a session refresh or retry
4. one backend is hot while another backend is empty
5. a large single-day shard on local storage
6. legacy flat artifacts mixed with newer sharded artifacts
7. day rollover with a cutoff earlier than the current day

## Implementation Status

Current branch status:

- implemented: create-session repository lifecycle methods for create, lookup, status transitions, summary, expiry listing, and delete
- implemented: normalized staged artifact backend with typed JSONL write, read, and delete over the existing local/S3 artifact storage layer
- implemented: write-side line-size enforcement so staged artifacts obey the same max-line contract on write and read
- implemented: a small create-session staging coordinator that compensates staged-artifact writes when session insertion fails, preventing orphaned internal artifacts
- implemented: dedicated create-session cleanup worker with bootstrap wiring, interruptible shutdown, conditional session-row deletion, staged-artifact deletion, orphan staged-artifact sweep, metrics refresh, and transient-iteration resilience
- implemented: retention-aware cleanup candidate selection driven by worker config rather than requiring every caller to precompute `expires_at`
- implemented: create-session status validation in application code and a DB-side status check constraint plus stage-artifact lookup index
- implemented: time-sharded staged artifact keys plus full-budget fair orphan selection across backends
- implemented: local sharded artifact listing that uses timestamp-prefixed filenames, stops at the configured limit, and keeps legacy flat artifacts discoverable
- implemented: dark-launch bootstrap seam so staged artifact storage is available whenever create sessions are enabled, even if cleanup remains disabled
- implemented: focused unit coverage for repository lifecycle, staging round-trips, cleanup behavior, and bootstrap wiring
- implemented: DB-backed tests for session lifecycle, staging compensation, orphan cleanup, and cleanup delete race semantics

Validation status on this branch:

- passed: focused PR 2 unit/bootstrap slice
- blocked in this environment: DB-backed PR 2 tests require a running Postgres on `localhost:5432`

Commands run:

- `/Users/mehditantaoui/Documents/Challenges/deltallm/.venv/bin/pytest tests/test_batch_storage.py tests/test_batch_create_session_repository.py tests/test_batch_create_staging.py tests/test_batch_create_session_cleanup.py tests/test_batch_db_integration.py`
  Result: `38 passed, 20 skipped`
- `/Users/mehditantaoui/Documents/Challenges/deltallm/.venv/bin/ruff check src/batch/create src/batch/storage.py tests/test_batch_storage.py tests/test_batch_create_session_repository.py tests/test_batch_create_staging.py tests/test_batch_create_session_cleanup.py tests/test_batch_db_integration.py`
  Result: `All checks passed!`

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

### 4. Staging compensation is part of the durability boundary

The system must not rely on the cleanup worker to discover artifacts for sessions that never committed.

That means:

- staged artifact write and session insert are still separate operations
- but the coordinator that performs them must best-effort delete the artifact if session insertion fails
- cleanup worker discovery remains row-driven and only applies to sessions that actually exist
- if best-effort compensation still fails, a bounded orphan sweep on the staged-artifact prefix provides eventual recovery without adding another persistence object
- orphan sweeping must be fair across configured backends so one hot backend does not starve another during migration or rollback windows

### 5. Keep write format append-friendly and streaming-friendly

The staging abstraction should support:

- streaming writes from validation
- streaming reads for later promotion
- backend reuse across local and S3 artifact stores

It must also enforce the same max-line contract at write time that promotion will enforce at read time.

### 6. Failure classification starts here

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

Implemented storage layout on this branch:

- `batch-create-stage/YYYY/MM/DD/<timestamp>-<uuid>-<filename>`
- the sharded layout keeps local orphan discovery bounded by day directories instead of full-tree scans
- local listing uses the timestamp embedded in sharded filenames to stop at the configured limit without statting every file in a busy day shard
- cross-backend orphan selection merges the oldest candidates after asking each backend for up to the full global limit
- legacy flat local artifacts remain discoverable while PR 2 compatibility is still required

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
2. delete session rows only if the cleanup snapshot still matches the persisted row
3. delete staged artifacts for rows cleanup actually won
3. mark or delete session rows according to retention policy
4. emit cleanup metrics/logs

Retention recommendations:

- completed session retention: 24 hours
- retryable failure retention: 24 hours
- permanent failure retention: 7 days

Retention behavior implemented on this branch:

- `expires_at` remains an explicit override when present
- otherwise cleanup derives candidates from status-specific retention windows
- `staged` sessions are not auto-cleaned in PR 2
- old unreferenced staged artifacts are also swept by prefix after a configurable orphan grace period
- row-driven cleanup now deletes the session row first with a null-safe snapshot match and lets orphan sweep handle any later artifact-delete failure

Status integrity implemented on this branch:

- create-session status is validated in the dataclasses and repository update path
- parsed DB rows fail fast on unknown status values
- the database now rejects unknown statuses with a check constraint

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
6. Orphan sweep can consume the full configured `scan_limit` even when backends are uneven.
7. Local sharded orphan discovery remains bounded by `limit`, and legacy flat artifacts remain discoverable.

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
