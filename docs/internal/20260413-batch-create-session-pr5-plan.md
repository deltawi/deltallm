# PR 5 Plan: Admin Session Visibility, Retry Controls, And Operational Telemetry

## Objective

PR 5 makes the new architecture operable.

Target outcome:

- operators can inspect failed or completed create sessions directly
- operators can retry or expire create sessions without touching executable batches
- create-session metrics and logs provide clear visibility into validation, staging, promotion, and cleanup
- batch admin semantics stop treating create failures as job repair problems conceptually, even if legacy endpoints still exist temporarily

This PR gives the new architecture an operational surface before legacy removal.

Related plans:

- [`20260413-batch-create-session-architecture-plan.md`](20260413-batch-create-session-architecture-plan.md)
- [`20260413-batch-create-session-pr1-plan.md`](20260413-batch-create-session-pr1-plan.md)
- [`20260413-batch-create-session-pr2-plan.md`](20260413-batch-create-session-pr2-plan.md)
- [`20260413-batch-create-session-pr3-plan.md`](20260413-batch-create-session-pr3-plan.md)
- [`20260413-batch-create-session-pr4-plan.md`](20260413-batch-create-session-pr4-plan.md)

## Branch And Merge Target

PR 5 should be implemented on:

- `pr/batch-create-session-5-admin-ops-surface`

PR 5 should be opened against:

- `feature/batch-create-session`
- not `main`

Stack rules:

1. Branch from the latest tip of `feature/batch-create-session` after PR 4 is merged there.
2. Merge back into `feature/batch-create-session`.
3. Do not delete legacy job-centric repair behavior yet.
4. New operator actions should target sessions, not executable jobs.
5. This PR should leave the system easier to operate before PR 6 starts deleting old paths.

## Scope

In scope for PR 5:

1. Add admin endpoints for create-session list/detail.
2. Add admin actions for retrying or expiring sessions.
3. Add UI authorization capability wiring if needed.
4. Add create-session metrics and structured logs.
5. Add audit actions for session retry / expire.
6. Add tests for admin scope, visibility, and operator actions.

Out of scope for PR 5:

1. Removing `repair-preparing`.
2. Removing old config flags.
3. Draining legacy staged jobs.
4. UI polish beyond functional operator surfaces.

## Design Decisions

### 1. Create failures are repaired through sessions only

Admin semantics should shift to:

- session retry
- session expire

not:

- batch repair

### 2. Retry should reuse staged data

Retrying a session should:

- reuse the existing staged artifact
- re-run promotion
- not revalidate from the original input file

### 3. Expire is an explicit operator retirement action

Expiring a session should:

- mark it terminal for create-session purposes
- delete the staged artifact if present
- never affect a completed executable batch

### 4. Add dedicated create-session telemetry

Do not overload existing batch metrics to represent session lifecycle.

Recommended metrics:

- create sessions by status
- promotion attempt count
- idempotency hit count
- staging write latency
- promotion latency
- cleanup deletion count

## Proposed Admin Contract

Recommended endpoints:

- `GET /ui/api/batch-create-sessions`
- `GET /ui/api/batch-create-sessions/{session_id}`
- `POST /ui/api/batch-create-sessions/{session_id}/retry`
- `POST /ui/api/batch-create-sessions/{session_id}/expire`

Recommended response fields:

- `session_id`
- `target_batch_id`
- `status`
- `endpoint`
- `input_file_id`
- `expected_item_count`
- `inferred_model`
- `requested_service_tier`
- `effective_service_tier`
- ownership fields
- `last_error_code`
- `last_error_message`
- `promotion_attempt_count`
- `created_at`
- `completed_at`
- `expires_at`

## Affected Files

Implementation:

- new or extended admin endpoint module under `src/api/admin/endpoints/`
- `src/services/ui_authorization.py`
- `src/audit/actions.py`
- `src/metrics/batch.py`
- `src/batch/create/service.py`
- `src/batch/create/session_repository.py`

Tests:

- new admin endpoint tests for create sessions
- audit tests if applicable
- metrics/logging tests where practical

## Test Plan

Required:

1. Admin can list and inspect sessions within scope.
2. Admin retry action succeeds only for retryable/promotable sessions.
3. Admin expire action cannot damage completed batches.
4. Session retry reuses staged artifact.
5. Audit events are emitted for retry and expire.
6. Metrics/log labels are exercised in focused tests.

## Acceptance Criteria

PR 5 is complete when:

1. Operators can debug and retry failed creates through sessions.
2. Session telemetry exists independently from executable batch telemetry.
3. No operator action needs to repair a live batch just to finish creation.

## Risks

Main risks:

- access-scope bugs in admin visibility
- operator actions that accidentally affect completed batches

Mitigation:

- keep session and batch resources distinct in repository and endpoint layers
- add explicit negative tests for completed-session / completed-batch safety

## Deliverable Summary

PR 5 should make the create-session architecture supportable in production before legacy cleanup begins.
