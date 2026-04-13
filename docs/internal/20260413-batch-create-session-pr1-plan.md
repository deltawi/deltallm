# PR 1 Plan: Batch Create Session Schema, Contract, And Dark-Launch Foundation

## Objective

PR 1 establishes the persistent and module-level foundation for the batch create-session architecture without changing runtime behavior of `POST /v1/batches`.

Target outcome:

- the repo has a first-class `BatchCreateSession` persistence model
- the batch create-session architecture is documented and linked from the implementation plan
- create-session config, metrics placeholders, and module scaffolding exist
- the app can boot with the new schema and new modules present
- the public batch create flow still behaves exactly as it does on `main`

This PR should be low-risk and deploy-safe.
It should introduce structure, not cut over traffic.

Related master plan:

- [`20260413-batch-create-session-architecture-plan.md`](20260413-batch-create-session-architecture-plan.md)

## Implementation Status

Current branch status:

- implemented locally on `pr/batch-create-session-1-schema-foundation`
- still dark-launched
- not yet merged into `feature/batch-create-session`

Delivered in this branch:

1. Added the `DeltaLLM_BatchCreateSession` Prisma model and migration.
2. Added internal create-session types and repository scaffolding under `src/batch/create/`.
3. Added an enforced `input_file_id -> BatchFile.file_id` relation and made file GC session-aware.
4. Enforced the create-session idempotency pair contract at the model, repository, and migration levels.
5. Added dark-launch config for session enablement, retention, idempotency, and promotion chunk sizing.
6. Centralized create-session cleanup default values so config and scaffolded cleanup runtime share one source of truth.
7. Added create-session metric placeholders and publication helpers.
8. Added a no-op create-session cleanup worker seam with full bootstrap, status, and shutdown lifecycle wiring.
9. Added focused tests for bootstrap wiring, metric exports, create-session repository scaffolding, and file-GC/default/idempotency consistency.

Still intentionally deferred to later PRs:

1. staged artifact persistence behavior
2. promotion into `BatchJob`
3. public endpoint cutover
4. admin session endpoints and operator actions

## Branch And Merge Target

PR 1 should be implemented on:

- `pr/batch-create-session-1-schema-foundation`

PR 1 should be opened against:

- `feature/batch-create-session`
- not `main`

Stack rules:

1. Create this branch from the latest tip of `feature/batch-create-session`.
2. Merge it back into `feature/batch-create-session` after approval.
3. Do not merge directly to `main`.
4. Do not introduce public behavior changes in this PR.
5. Do not mix create-session promotion or public API cutover into this PR.

Naming rule:

- do not use `Codex` in commit messages, PR titles, or PR descriptions
- use direct feature language such as `Add batch create session schema and dark-launch scaffolding`

## Scope

In scope for PR 1:

1. Add the `DeltaLLM_BatchCreateSession` schema and migration.
2. Add create-session model mappings and repository scaffolding.
3. Add internal create-session module scaffolding under `src/batch/create/`.
4. Add config flags for create-session enablement, cleanup, idempotency, and insert batching.
5. Add create-session metrics scaffolding and no-op publication helpers if needed.
6. Add bootstrap wiring placeholders for future create-session cleanup worker registration.
7. Update the master architecture doc with stacked PR strategy and related links.
8. Add unit tests proving the app can initialize with the new config and schema-facing modules.

Out of scope for PR 1:

1. Writing normalized staged artifacts.
2. Promoting a session into a real `BatchJob`.
3. Switching `POST /v1/batches` to the new path.
4. Admin session endpoints.
5. Removing `preparing` / `validating` logic.

## Design Decisions

### 1. Schema first, behavior later

PR 1 should own the new table and supporting config so later PRs can build behavior incrementally without reopening persistence decisions.

### 2. `BatchCreateSession` is internal-only

Do not expose session ids or session objects through public API contracts in this PR.

The session model is an internal durability boundary, not a new public-facing resource.

### 3. Keep `BatchJob` untouched for runtime behavior

Do not change:

- current `POST /v1/batches` behavior
- worker claim logic
- staged-job repair behavior
- public batch response shapes

The goal is to land the foundation without destabilizing the current branch.

### 4. Add config now even if dark-launched

Recommended config fields to land now:

- `embeddings_batch_create_sessions_enabled`
- `embeddings_batch_create_session_cleanup_enabled`
- `embeddings_batch_create_session_cleanup_interval_seconds`
- `embeddings_batch_create_session_completed_retention_seconds`
- `embeddings_batch_create_session_retryable_retention_seconds`
- `embeddings_batch_create_session_failed_retention_seconds`
- `embeddings_batch_create_soft_precheck_enabled`
- `embeddings_batch_create_idempotency_enabled`
- `embeddings_batch_create_promotion_insert_chunk_size`

Do not delete old `preparing`-related config in this PR.

### 5. Add module boundaries before implementation

Create the package layout now so later PRs can fill it in without large file-move diffs.

Recommended new modules:

- `src/batch/create/__init__.py`
- `src/batch/create/models.py`
- `src/batch/create/session_repository.py`
- `src/batch/create/staging.py`
- `src/batch/create/promoter.py`
- `src/batch/create/cleanup.py`

Initial content can be lightweight, but the intended ownership of each module should be obvious.

## Proposed Schema

### New model: `DeltaLLM_BatchCreateSession`

Recommended initial fields in PR 1:

| Field | Type | Notes |
| --- | --- | --- |
| `session_id` | `String @id @default(uuid())` | primary key |
| `target_batch_id` | `String @unique` | preallocated final batch id |
| `status` | `String` | `staged`, `completed`, `failed_retryable`, `failed_permanent`, `expired` |
| `endpoint` | `String` | endpoint snapshot |
| `input_file_id` | `String` | user-facing input file |
| `staged_storage_backend` | `String` | internal artifact backend |
| `staged_storage_key` | `String` | internal artifact key |
| `staged_checksum` | `String?` | optional integrity check |
| `staged_bytes` | `Int` | staged artifact size |
| `expected_item_count` | `Int` | validated item count |
| `inferred_model` | `String?` | validation result |
| `metadata` | `Json?` | original metadata snapshot |
| `requested_service_tier` | `String?` | preserved if queueing policy exists |
| `effective_service_tier` | `String?` | preserved if queueing policy exists |
| `service_tier_source` | `String?` | future-compatible |
| `scheduling_scope_key` | `String?` | future-compatible |
| `priority_quota_scope_key` | `String?` | future-compatible |
| `idempotency_scope_key` | `String?` | nullable until cutover |
| `idempotency_key` | `String?` | nullable until cutover |
| `last_error_code` | `String?` | retry classification |
| `last_error_message` | `String?` | operator message |
| `promotion_attempt_count` | `Int @default(0)` | retry accounting |
| `created_by_api_key` | `String?` | ownership |
| `created_by_user_id` | `String?` | ownership |
| `created_by_team_id` | `String?` | ownership |
| `created_by_organization_id` | `String?` | ownership |
| `created_at` | `DateTime @default(now())` | lifecycle |
| `completed_at` | `DateTime?` | lifecycle |
| `last_attempt_at` | `DateTime?` | lifecycle |
| `expires_at` | `DateTime?` | cleanup |

Recommended indexes:

- `@@index([status, created_at])`
- `@@index([created_by_team_id, status, created_at])`
- `@@index([created_by_organization_id, status, created_at])`
- `@@index([created_by_api_key, status, created_at])`
- `@@index([expires_at])`
- `@@unique([idempotency_scope_key, idempotency_key])`

## Affected Files

Schema and model layer:

- `prisma/schema.prisma`
- new migration under `prisma/migrations/`
- `src/batch/create/models.py`
- `src/batch/repositories/mappers.py` if session row mapping is shared there

Runtime/config/metrics:

- `src/config.py`
- `src/bootstrap/batch.py`
- `src/metrics/batch.py`
- `src/metrics/__init__.py`

New module scaffolding:

- `src/batch/create/__init__.py`
- `src/batch/create/session_repository.py`
- `src/batch/create/staging.py`
- `src/batch/create/promoter.py`
- `src/batch/create/cleanup.py`

Docs:

- `docs/internal/20260413-batch-create-session-architecture-plan.md`

Tests:

- `tests/bootstrap/test_optional_bootstrap.py`
- new focused tests for session repository/model wiring if appropriate

## Test Plan

Required in PR 1:

1. schema validation / migration generation works
2. app bootstrap tolerates new config fields
3. metrics module imports cleanly
4. create-session repository scaffolding imports cleanly
5. existing batch tests still pass unchanged

Do not add fake behavior-only tests for not-yet-implemented promotion.

## Acceptance Criteria

PR 1 is complete when:

1. `DeltaLLM_BatchCreateSession` exists in Prisma schema and migration output.
2. The app boots with the new config fields.
3. No public batch endpoint changes behavior.
4. No worker behavior changes.
5. The master architecture doc includes stack rules and links to all PR plans.

## Risks

Main risk:

- schema shape chosen here constrains every later PR

Mitigation:

- keep schema wide enough for idempotency and queueing carry-through now
- do not overfit to the current `preparing` design

## Deliverable Summary

PR 1 should land a clean, dark-launched foundation for the create-session redesign and nothing more.
