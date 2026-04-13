# PR 6 Plan: Legacy Staged-Job Drain, Codepath Removal, And Simplification

## Objective

PR 6 removes the rejected architecture once the new create-session flow has been proven.

Target outcome:

- no new or existing batch creation depends on `preparing` / `validating`
- creator leases, creator heartbeats, and staged repair worker are removed
- `repair-preparing` is removed from the admin surface
- obsolete config, metrics, and tests are deleted
- `BatchJob` is executable-only in both code and data

This PR should leave the batch runtime materially simpler than before the redesign started.

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
3. Do not start this PR until legacy staged jobs are drained or explicitly retired.
4. Remove fallback flags and dead code in this PR; do not preserve both architectures afterward.
5. This PR should be the only PR that deletes the old create-path architecture.

## Preconditions

Before PR 6 starts, all of the following should be true:

1. PRs 1 through 5 are merged into `feature/batch-create-session`.
2. The new public create path has been running successfully in test/staging or controlled rollout.
3. Existing `preparing` / `validating` jobs have been inspected and drained or explicitly retired.
4. Operators have session-centric tools available.

If those are not true, PR 6 is premature.

## Scope

In scope for PR 6:

1. Remove old create-path code from `BatchService`.
2. Remove `preparing` / `validating` creation logic.
3. Remove creator lease and heartbeat logic.
4. Remove staged repair worker and related cleanup behavior.
5. Remove `repair-preparing` admin endpoint and capability.
6. Remove obsolete config flags and metrics.
7. Simplify tests to reflect the new executable-only `BatchJob` contract.

Out of scope for PR 6:

1. New create-session feature work.
2. UI/product enhancements unrelated to legacy removal.
3. Additional queueing or billing changes.

## Design Decisions

### 1. Delete, do not deprecate-in-place

Once the new architecture is proven, the old one should be removed rather than hidden behind long-term flags.

Reason:

- keeping both models alive destroys the simplification benefit

### 2. `BatchJob` status set becomes executable-only

After this PR, new code should treat valid execution statuses as:

- `queued`
- `in_progress`
- `finalizing`
- `completed`
- `failed`
- `cancelled`
- `expired`

Any remaining references to `preparing` or `validating` should be removed or limited to one-time migration utilities outside the runtime path.

### 3. Cleanup workers separate cleanly

After PR 6:

- create-session cleanup worker manages create sessions and staged artifacts
- batch retention cleanup worker manages executable batches and final artifacts

There should be no mixed repair logic left in batch cleanup.

## Affected Files

Implementation:

- `src/batch/service.py`
- `src/batch/cleanup.py`
- `src/bootstrap/batch.py`
- `src/api/admin/endpoints/batches.py`
- `src/services/ui_authorization.py`
- `src/config.py`
- `src/metrics/batch.py`
- `src/batch/models.py`
- any legacy repository methods that only existed for `preparing` / `validating`

Tests:

- `tests/test_batch_service.py`
- `tests/test_batch_cleanup.py`
- `tests/test_admin_batch_repairs.py`
- `tests/bootstrap/test_optional_bootstrap.py`
- DB-backed tests updated to assert executable-only jobs

Docs:

- architecture doc may need a post-cutover cleanup note
- remove or update any old internal docs that still recommend job-centric staged repair

## Removal Checklist

Delete or refactor:

1. creator lease settings
2. creator heartbeat settings
3. staged repair settings
4. staged repair worker bootstrap
5. `repair-preparing` admin route
6. `repair_preparing` capability wiring
7. repository methods used only for `preparing` job repair
8. tests asserting `preparing` creation behavior

## Test Plan

Required:

1. No public create path emits `preparing` / `validating`.
2. Batch runtime boots without old staged-repair config.
3. No admin surface exposes `repair-preparing`.
4. Session cleanup and batch cleanup continue to work independently.
5. Existing batch execution and finalization tests remain green.
6. DB-backed create tests confirm executable-only job lifecycle.

## Acceptance Criteria

PR 6 is complete when:

1. The old staged-job architecture is removed from runtime code.
2. `BatchJob` is execution-only in code and operational semantics.
3. Session cleanup fully replaces staged-job repair.
4. The codebase is simpler than before the redesign began.

## Risks

Main risks:

- deleting a legacy compatibility path before data is drained
- missing hidden references to `preparing` / `validating`

Mitigation:

- require preconditions before starting
- use aggressive grep-based cleanup and focused review
- run full batch-focused test suite plus DB-backed checks

## Deliverable Summary

PR 6 should complete the architecture shift and remove the old create-state model entirely.
