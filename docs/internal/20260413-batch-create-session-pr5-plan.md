# PR 5 Plan: Session-Centric Admin Visibility And Safe Operator Actions

## Objective

PR 5 makes the create-session architecture operable without turning it into a second batch-control system.

Target outcome:

- operators can inspect create sessions directly
- operators can retry promotable sessions through the existing promoter
- operators can explicitly retire non-completed sessions without touching executable batches
- operator-visible metrics, logs, and audit events make create-session failures explainable
- batch repair and create-session repair stay conceptually separate even while legacy batch repair endpoints still exist

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
6. Do not let PR 5 grow into UI redesign or another generalized workflow engine.

## Scope

In scope for PR 5:

1. Add a dedicated admin endpoint module for create-session list/detail.
2. Add admin actions for retrying or expiring sessions.
3. Add a small create-session admin service so retry/expire semantics do not live in route handlers.
4. Add session-specific UI authorization capability wiring.
5. Complete operator-facing create-session telemetry using the existing metrics family, plus minimal structured logs where needed.
6. Add audit actions for session retry and expire.
7. Add tests for admin scope, visibility, operator actions, and completed-batch safety.

Out of scope for PR 5:

1. Removing legacy batch repair endpoints.
2. Removing old config flags.
3. Draining legacy staged jobs.
4. Revalidating from the original input file on retry.
5. Bulk session operations.
6. UI polish beyond functional operator surfaces.
7. New metric families unless a real observability gap cannot be covered by the existing create-session metric family.

## Review Discipline

PR 5 must follow the shared review checklist in:

- [`20260413-batch-create-session-review-checklist.md`](20260413-batch-create-session-review-checklist.md)

PR-specific invariants:

1. Create-session admin endpoints must operate on sessions, not on executable `BatchJob` state, except by invoking the existing promoter for retry.
2. Retry must reuse the staged artifact already referenced by the session; it must not re-read or revalidate the original input file.
3. Expire must never mutate or delete a completed executable batch.
4. Completed create sessions remain read-only from the admin mutation surface.
5. Session visibility and mutation permissions must follow the same team/org ownership boundary used for the session itself.
6. Endpoint handlers must not encode lifecycle transitions directly; retry/expire semantics must live in one create-session admin service.
7. Session telemetry must reuse the existing create-session metrics family unless a distinct semantic gap is proven.
8. Admin list/detail responses must expose capabilities derived from status, scope, and real runtime action availability, not from hard-coded UI assumptions.
9. Operator mutations must be auditable with stable resource ids and action names.
10. PR 5 must not enlarge [src/api/admin/endpoints/batches.py](/tmp/deltallm-batch-create-session-pr5/src/api/admin/endpoints/batches.py) into a mixed batch/session control module.
11. Rolling public create back to the legacy path must not strand existing create sessions without retry or expire support.

Failure-mode pass for PR 5:

1. Retry on a `failed_retryable` session whose staged artifact is missing or unreadable.
2. Retry on a `completed` session.
3. Expire on a `completed` session that already produced an executable batch.
4. Expire on an already `expired` session.
5. Session read or mutation by a different team in the same organization.
6. Concurrent cleanup and admin retry against the same session.
7. Concurrent admin expire and promoter completion against the same session.
8. Operator retry after repeated transient promotion failure.
9. Missing staged artifact delete during operator expire.
10. Metrics and audit visibility for operator-triggered retry and expire.
11. Public create cutover disabled while existing failed sessions still need operator retry or expire.

## Design Decisions

### 1. Keep the admin surface session-native and separate from batch repair

Create-session debugging and repair should live in a dedicated admin module and a small create-session admin service.

Do not keep extending [src/api/admin/endpoints/batches.py](/tmp/deltallm-batch-create-session-pr5/src/api/admin/endpoints/batches.py) with session-specific logic. That file should stay job-centric.

### 2. Retry delegates to the existing promoter

Admin semantics should shift to:

- session retry
- session expire

not:

- batch repair

Retrying a session should:

- reuse the existing staged artifact
- re-run promotion through the existing promoter
- not revalidate from the original input file
- not create a second session row

Allowed retry statuses should stay narrow:

- `staged`
- `failed_retryable`

Retry should be rejected for:

- `completed`
- `failed_permanent`
- `expired`

### 3. Expire is a terminal operator retirement action for non-completed sessions only

Expiring a session should:

- mark it terminal for create-session purposes
- delete the staged artifact if present
- never affect a completed executable batch
- never delete the session row in the mutation path

Allowed expire statuses should stay narrow:

- `staged`
- `failed_retryable`
- `failed_permanent`

Expire should be rejected for:

- `completed`
- `expired`

### 4. Reuse the existing create-session metrics family

PR 2, PR 3, and PR 4 already introduced create-session metrics.

PR 5 should extend operator visibility primarily by:

- reusing `deltallm_batch_create_session_actions_total`
- ensuring session summary gauges are refreshed after operator mutations where appropriate
- adding minimal structured logs with `session_id`, `target_batch_id`, `action`, `actor`, and `result`

Do not add another generalized metric family unless a real gap remains after using the existing create-session metrics.

### 5. Add a session capability builder instead of reusing batch capabilities

Create-session actions depend on session status, not job status.

PR 5 should add a dedicated capability builder in [src/services/ui_authorization.py](/tmp/deltallm-batch-create-session-pr5/src/services/ui_authorization.py) rather than overloading `build_batch_capabilities()`.

## Proposed Admin Contract

Recommended endpoints:

- `GET /ui/api/batch-create-sessions`
- `GET /ui/api/batch-create-sessions/{session_id}`
- `POST /ui/api/batch-create-sessions/{session_id}/retry`
- `POST /ui/api/batch-create-sessions/{session_id}/expire`

Recommended list filters:

- `status`
- `search` over `session_id`, `target_batch_id`, and `input_file_id`
- `limit`
- `offset`

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
- `capabilities`

Recommended retry response:

- `session_id`
- `target_batch_id`
- `status`
- `retried`
- `promotion_result`

Recommended expire response:

- `session_id`
- `target_batch_id`
- `status`
- `expired`
- `artifact_deleted`

## Affected Files

Implementation:

- new dedicated admin endpoint module under `src/api/admin/endpoints/`
- `src/api/admin/router.py`
- `src/batch/create/admin_service.py`
- `src/services/ui_authorization.py`
- `src/audit/actions.py`
- `src/metrics/batch.py`
- `src/batch/create/promoter.py`
- `src/batch/create/session_repository.py`

Tests:

- new admin endpoint tests for create sessions
- UI authorization tests for create-session capabilities
- audit tests for retry and expire
- metrics/logging tests where practical
- DB-backed tests for scope enforcement and completed-batch safety

## Test Plan

Required:

1. Admin can list and inspect sessions within scope.
2. Session capabilities vary correctly by scope and status.
3. Admin retry succeeds only for `staged` and `failed_retryable` sessions.
4. Admin retry reuses the staged artifact and delegates to the existing promoter.
5. Admin retry does not create a second session row.
6. Admin expire cannot damage completed batches.
7. Admin expire is rejected for `completed` and `expired` sessions.
8. Audit events are emitted for retry and expire.
9. Metrics and log labels are exercised in focused tests.
10. DB-backed scope tests prove a different team in the same org cannot mutate or read another team-owned session.

## Acceptance Criteria

PR 5 is complete when:

1. Operators can debug create failures through session list and detail endpoints.
2. Operators can retry promotable sessions without touching executable batch repair endpoints.
3. Operators can explicitly retire non-completed sessions without mutating completed executable batches.
4. Session capabilities are scope-aware and status-aware.
5. Session capabilities are also runtime-aware: the admin surface must not advertise retry or expire when the session admin service is unavailable.
6. Rolling public create back to the legacy path leaves create-session admin retry and expire available for already-existing sessions.
7. Operator-visible create-session telemetry uses the existing create-session metrics family and audit surface cleanly.
8. The focused PR5 validation slice passes on unit tests, and DB-backed scope and safety coverage exists for Postgres-enabled environments.

## Risks

Main risks:

- access-scope bugs in admin visibility or mutation
- operator actions that accidentally affect completed batches
- endpoint handlers duplicating lifecycle rules already owned by the promoter or repository

Mitigation:

- keep session and batch resources distinct in endpoint and service layers
- add a dedicated create-session admin service instead of embedding mutation semantics in handlers
- add explicit negative tests for completed-session and completed-batch safety
- keep capability projection and mutation authorization centralized

## Deliverable Summary

PR 5 should make the create-session architecture supportable in production before legacy cleanup begins, without expanding the system into a second generalized repair plane.

## Implementation Status

Implemented in this branch:

1. Added a dedicated create-session admin endpoint module at `src/api/admin/endpoints/batch_create_sessions.py`.
2. Added a small create-session admin service at `src/batch/create/admin_service.py` to own retry and expire semantics.
3. Wired the session admin runtime through bootstrap and app state independently from the public create cutover flag.
4. Added session-specific UI capability projection in `src/services/ui_authorization.py`, including runtime-aware retry and expire controls.
5. Added audit actions for session retry and expire.
6. Reused the existing create-session metrics family for operator actions instead of adding a new metric family.
7. Added focused unit tests, admin endpoint tests, bootstrap wiring tests, and a DB-backed PR5 slice that skips when Postgres is unavailable.

Still pending in this branch:

1. Final code review and any resulting hardening fixes.
2. Full DB-backed execution in an environment with a live `DATABASE_URL`.
