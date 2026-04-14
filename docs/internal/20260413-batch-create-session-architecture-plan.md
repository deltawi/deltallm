# Batch Create Session Architecture Plan

## Objective

Redesign batch creation so:

1. `deltallm_batch_job` contains executable jobs only
2. workers never see half-created jobs
3. create-path recovery applies to a separate internal staging concept, not to live jobs
4. the architecture becomes simpler to reason about under crash, retry, restart, and concurrency
5. the system scales without reintroducing long-lived transaction leases, create heartbeats, or repair logic on the executable job state machine

This plan is intentionally opinionated.
It chooses one architecture and drives the codebase toward it instead of layering another round of local fixes onto the current `preparing` / `validating` approach.

## Branching And PR Strategy

Implement this redesign as a stacked series on a dedicated integration branch.

Feature integration branch:

- `feature/batch-create-session`

Per-PR working branches:

- `pr/batch-create-session-1-schema-foundation`
- `pr/batch-create-session-2-session-staging-cleanup`
- `pr/batch-create-session-3-promotion-engine`
- `pr/batch-create-session-4-public-cutover`
- `pr/batch-create-session-5-admin-ops-surface`
- `pr/batch-create-session-6-legacy-removal`

Rules of the stack:

1. Every PR must branch from the latest tip of `feature/batch-create-session`, not directly from `main`.
2. Every PR must target `feature/batch-create-session`, not `main`.
3. Only after the full stack is merged into `feature/batch-create-session` and validated should the integration branch be merged into `main`.
4. Schema additions should be front-loaded. Avoid adding schema changes in late cleanup PRs unless absolutely necessary.
5. Public traffic cutover and legacy deletion must not happen in the same PR.
6. After PR 4, rollback must still be possible without restoring deleted legacy code from scratch.
7. Starting in PR 3, every PR must include DB-backed integration coverage, not only unit tests.
8. Do not mix unrelated batch features into this stack. This stack is only for the create-session architecture redesign.
9. Do not use `Codex` in branch names, commit messages, PR titles, or PR descriptions.
10. Each PR should leave the branch in a deployable, reviewable state even if the full architecture is not yet cut over.

Companion PR plans:

- [`20260413-batch-create-session-pr1-plan.md`](20260413-batch-create-session-pr1-plan.md)
- [`20260413-batch-create-session-pr2-plan.md`](20260413-batch-create-session-pr2-plan.md)
- [`20260413-batch-create-session-pr3-plan.md`](20260413-batch-create-session-pr3-plan.md)
- [`20260413-batch-create-session-pr4-plan.md`](20260413-batch-create-session-pr4-plan.md)
- [`20260413-batch-create-session-pr5-plan.md`](20260413-batch-create-session-pr5-plan.md)
- [`20260413-batch-create-session-pr6-plan.md`](20260413-batch-create-session-pr6-plan.md)

Current stack status:

- PRs 1 through 4 are merged into `feature/batch-create-session`
- active implementation branch: `pr/batch-create-session-5-admin-ops-surface`
- PR 5 implementation is active in this branch
- PR 6 remains plan-only

## Decision Summary

### Core decision

Replace job-level create staging with a separate internal `BatchCreateSession` lifecycle.

The public `POST /v1/batches` request continues to return a normal batch object, but internally the flow becomes:

1. validate input and normalize it into an internal staged artifact
2. persist a `BatchCreateSession`
3. atomically promote that session into a real `queued` `BatchJob`
4. only then expose the batch to workers and clients

### What this means structurally

- `BatchJob` stops representing “a batch that might still be under construction”
- `BatchJob` becomes the execution contract only
- all “create in progress” semantics move into a new internal model
- all retry, cleanup, and debugging for create-path failures become session-centric
- the `preparing` and `validating` job statuses become legacy compatibility only, then are removed

## Why This Change Is Necessary

The current design direction is accumulating accidental complexity:

- interactive transaction timeout tuning
- create-time advisory-lock contention
- `preparing` job status
- creator lease ownership
- creator heartbeat
- staged repair worker
- admin `repair-preparing` endpoint
- legacy `validating` compatibility

That complexity exists because the system is trying to use `BatchJob` for two different responsibilities:

1. construction-time state
2. execution-time state

Those responsibilities have different correctness rules:

- construction wants validation, normalization, retryability, and garbage collection
- execution wants queueing, leases, cancellation, finalization, and terminal retention

They should not share the same lifecycle object.

## Current-State Anchors

Current create and execution behavior lives primarily in:

- `src/api/v1/endpoints/batches.py`
- `src/models/requests.py`
- `src/batch/service.py`
- `src/batch/worker.py`
- `src/batch/cleanup.py`
- `src/batch/repository.py`
- `src/batch/repositories/job_repository.py`
- `src/batch/repositories/item_repository.py`
- `src/batch/repositories/maintenance_repository.py`
- `src/api/admin/endpoints/batches.py`
- `src/bootstrap/batch.py`
- `src/config.py`
- `src/metrics/batch.py`
- `prisma/schema.prisma`

Current persistence already mixes execution and staging concerns in `DeltaLLM_BatchJob`, while `DeltaLLM_BatchItem` is directly tied to `batch_id`.

That is exactly the boundary this plan fixes.

## Design Principles

1. `BatchJob` is executable-only.
2. Create-path failures must never require repairing a live job.
3. Recovery must be idempotent and keyed by a durable create-session identity.
4. Expensive validation and normalization must happen outside the executable queue.
5. Public batch semantics should remain synchronous and backward-compatible where practical.
6. Retry after ambiguous client/network failure must be supported explicitly.
7. Cleanup semantics must be safe without creator heartbeats.
8. Operational tooling should expose failed create sessions separately from real batches.
9. Request admission and executable backlog should be controlled separately.
10. The design should preserve the current execution worker model as much as possible.

## Non-Goals

This plan does not attempt to:

- replace the existing batch execution worker
- replace `DeltaLLM_BatchItem` with a pointer-based artifact execution model
- move to provider-native async batch APIs
- change the public `GET /v1/batches` or `POST /v1/batches/{id}/cancel` contract
- introduce a new public “202 Accepted, poll session later” contract

Those may be worth doing later, but they are not required to fix the current architectural boundary.

## Target Lifecycle Model

### Executable lifecycle

`BatchJob` statuses should be limited to executable and terminal states:

- `queued`
- `in_progress`
- `finalizing`
- `completed`
- `failed`
- `cancelled`
- `expired`

`preparing` and `validating` must not be used for new jobs after cutover.

### Create-session lifecycle

Introduce a new internal lifecycle:

- `staged`
- `completed`
- `failed_retryable`
- `failed_permanent`
- `expired`

Definitions:

- `staged`: normalized internal artifact exists and the session may be promoted into a real batch
- `completed`: batch job was created successfully and `batch_id` is durable
- `failed_retryable`: promotion failed due to transient or policy reasons that may succeed later or on explicit retry
- `failed_permanent`: validation or policy failure that must not be retried automatically
- `expired`: session and artifact are no longer eligible for retry and can be garbage-collected

Important:

- there is no create heartbeat state
- there is no `promoting` state persisted across transactions
- there is no live executable job until promotion commits

## Recommended Architecture

### Chosen design

Use:

1. a new internal `BatchCreateSession` table
2. an internal normalized staged artifact stored in the configured batch artifact backend
3. a synchronous promotion step that atomically creates the executable batch
4. optional idempotency-key support so ambiguous create responses can be recovered cleanly

### Explicitly rejected designs

#### Rejected: keep `preparing` on `BatchJob`

Reason:

- it continues to blur construction and execution
- it forces repair and admin logic into the execution object
- it makes worker-visible state depend on create-path correctness

#### Rejected: stage normalized items in `deltallm_batch_item` before `BatchJob` exists

Reason:

- it conflicts with the current `BatchItem -> BatchJob` foreign-key model
- it weakens data integrity
- it pushes staging semantics into the execution table indirectly

#### Rejected: create a second staging item table as the primary design

Reason:

- it doubles DB write amplification for every request body
- it duplicates large JSON payload storage temporarily
- it makes promotion a DB-to-DB copy problem instead of an artifact-to-DB promotion problem

This remains a fallback option only if artifact-based promotion proves too slow in benchmarks.

## Proposed Data Model

### 1. New internal table: `DeltaLLM_BatchCreateSession`

Add a new Prisma model mapped to something like `deltallm_batch_create_session`.

Recommended fields:

| Field | Type | Purpose |
| --- | --- | --- |
| `session_id` | `String @id @default(uuid())` | internal create-session identity |
| `target_batch_id` | `String @unique` | preallocated final batch id for idempotent promotion |
| `status` | `String` | `staged`, `completed`, `failed_retryable`, `failed_permanent`, `expired` |
| `endpoint` | `String` | requested endpoint, initially `/v1/embeddings` |
| `input_file_id` | `String` | original user-facing input file |
| `staged_storage_backend` | `String` | backend for normalized internal artifact |
| `staged_storage_key` | `String` | internal artifact key |
| `staged_checksum` | `String?` | normalized artifact checksum |
| `staged_bytes` | `Int` | artifact size |
| `expected_item_count` | `Int` | validated item count |
| `inferred_model` | `String?` | resolved model from validation |
| `metadata` | `Json?` | original batch metadata snapshot |
| `requested_service_tier` | `String?` | caller request |
| `effective_service_tier` | `String?` | resolved policy result to carry into final job |
| `service_tier_source` | `String?` | `default`, `requested`, `admin_override` |
| `scheduling_scope_key` | `String?` | same scheduling/fair-share key that final job will use |
| `priority_quota_scope_key` | `String?` | if priority queueing policy is active |
| `idempotency_scope_key` | `String?` | scope namespace for idempotency |
| `idempotency_key` | `String?` | caller-provided idempotency key |
| `last_error_code` | `String?` | machine-readable failure code |
| `last_error_message` | `String?` | operator-visible error |
| `promotion_attempt_count` | `Int @default(0)` | promotion retry accounting |
| `created_by_api_key` | `String?` | ownership |
| `created_by_user_id` | `String?` | ownership |
| `created_by_team_id` | `String?` | ownership |
| `created_by_organization_id` | `String?` | ownership |
| `created_at` | `DateTime @default(now())` | creation time |
| `completed_at` | `DateTime?` | successful promotion time |
| `last_attempt_at` | `DateTime?` | latest promotion attempt |
| `expires_at` | `DateTime?` | session retention / GC deadline |

Recommended indexes:

- `@@index([status, created_at])`
- `@@index([created_by_team_id, status, created_at])`
- `@@index([created_by_organization_id, status, created_at])`
- `@@index([created_by_api_key, status, created_at])`
- `@@index([expires_at])`
- `@@unique([idempotency_scope_key, idempotency_key])` with nullable semantics handled carefully

### 2. Keep `DeltaLLM_BatchJob` executable-only

Do not add new create-staging fields to `BatchJob`.

Retain existing execution and scheduling fields such as:

- `status`
- `queued_at`
- `effective_service_tier`
- `scheduling_scope_key`
- `priority_quota_scope_key`
- execution counters
- lease fields

But remove new create-session responsibilities from it.

### 3. Keep `DeltaLLM_BatchFile` user-facing only

Do not store internal normalized staging artifacts as public `BatchFile` rows.

Reason:

- internal create artifacts are not user-visible files
- they should not appear in public file list/read endpoints
- they have different retention and security semantics

Store staged artifact metadata directly on `BatchCreateSession` instead.

### 4. Keep `DeltaLLM_BatchItem` unchanged as the execution table

`BatchItem` remains the execution work table.
Rows are inserted only when promotion to `BatchJob` succeeds.

That preserves:

- worker logic
- item lease logic
- completion outbox logic
- finalization logic

## Public API Contract

### `POST /v1/batches`

Keep the existing public contract:

- request body remains `input_file_id`, `endpoint`, `completion_window`, `metadata`, `service_tier`
- successful response remains a normal batch object

### Add optional idempotency support

Recommended addition:

- support `Idempotency-Key` request header on `POST /v1/batches`

Behavior:

1. same scope + same key + already completed session:
   - return existing batch
2. same scope + same key + retryable staged/failed session:
   - retry promotion instead of revalidating input
3. same scope + same key + permanent failure:
   - return the stored failure
4. same scope + same key + request payload mismatch:
   - return `409`

Recommendation:

- make the header optional for public compatibility
- make it mandatory for internal gateways/SDK wrappers as soon as feasible

Without idempotency, ambiguous client/network failures after commit can never be resolved cleanly.
This is a reliability requirement, not a nice-to-have.

## Detailed Request Flow

### Step 0: Soft admission / early rejection

Before heavy work:

1. load the input file record and enforce ownership
2. perform a best-effort precheck of executable batch backlog
3. apply generic request rate limits / concurrency limits

Important:

- this soft precheck is an optimization only
- the strict backlog decision still happens at promotion time

Reason:

- we want to reject obvious saturation early
- but we do not want create-session bookkeeping to become part of the executable backlog model

### Step 1: Validate and normalize input outside the executable queue

Read the input artifact and stream-validate it exactly as today, but write a normalized internal artifact format:

```json
{"line_number":1,"custom_id":"req-1","request_body":{"model":"m1","input":"..."}}
```

The staged artifact should be:

- immutable
- fully validated
- normalized to exactly the fields needed for final `BatchItem` insertion
- stored under a dedicated internal prefix, for example `batch-create-stage/`

Validation responsibilities:

- endpoint match
- schema validation
- duplicate `custom_id`
- item count limit
- line size limit
- model visibility / callable-target policy
- service-tier resolution snapshot if needed during validation

If validation fails:

- no `BatchJob` is created
- no `BatchCreateSession` is required
- temporary spool data is deleted
- return the client-visible 4xx immediately

### Step 2: Persist the create session

After normalized staging artifact write succeeds:

1. allocate `target_batch_id`
2. compute session ownership and scheduling fields
3. upsert or create `BatchCreateSession`

If `Idempotency-Key` is present:

- first resolve by `(idempotency_scope_key, idempotency_key)`
- if an existing session is found, use it instead of creating a new one

Recommended session creation rules:

- do not create the session before staging artifact is durable
- do not store partial validation progress in the session
- session should represent a fully staged, retryable promotion candidate

This keeps session cleanup simple and avoids create heartbeats entirely.

### Step 3: Promote session to executable batch

Promotion must be the only place where `BatchJob` is created.

Recommended promotion transaction:

1. `SELECT ... FOR UPDATE` the `BatchCreateSession`
2. ensure status is promotable (`staged` or `failed_retryable`)
3. acquire the scope advisory lock
4. re-check executable backlog cap against `deltallm_batch_job` only
5. insert final `deltallm_batch_job` row directly as `queued`
6. bulk insert `deltallm_batch_item` rows from the staged artifact
7. update `BatchCreateSession` to `completed` and store `completed_at`
8. commit

If any step fails before commit:

- no `BatchJob` exists
- no live `BatchItem` rows exist
- the session remains `staged` or transitions to `failed_retryable` / `failed_permanent`

### Promotion implementation recommendation

Do not implement promotion with Prisma interactive transactions.

Recommended repository capability:

- a dedicated promotion path using a connection-level DB transaction suitable for bulk insertion

Target characteristics:

- no interactive 5-second timeout coupling
- ability to run bulk insert efficiently
- ability to hold the scope lock only for the actual promotion transaction

Preferred bulk insertion strategies, in order:

1. native PostgreSQL `COPY`-style ingestion into `deltallm_batch_item`
2. chunked multi-row insert with repository-controlled batch size

The artifact-to-DB promotion path should be benchmarked against maximum supported batch size before rollout.

## Failure Model

### Validation failure

- no session needed
- return permanent 4xx

### Staging artifact write failure

- no session or a permanent failed session if idempotency lookup already exists
- return 5xx / 503

### Backlog capacity exceeded during promotion

Recommended behavior:

- mark session `failed_retryable`
- set `last_error_code = pending_batch_limit_exceeded`
- return `429`

Reason:

- the normalized artifact already exists
- an idempotent retry should not have to revalidate the input file

### Transient DB failure during promotion

- session remains promotable or becomes `failed_retryable`
- no `BatchJob` is created unless commit succeeds

### Client disconnect after promotion commit

- if `Idempotency-Key` is present, retry returns the created batch
- if absent, the batch exists but the client may not know `batch_id`

This is the strongest reason to add idempotency support.

## Cleanup Model

### Session cleanup

Replace “repair preparing jobs” with session cleanup.

Add a new cleanup worker dedicated to `BatchCreateSession` records.

Responsibilities:

1. expire old `staged` / `failed_retryable` / `failed_permanent` sessions
2. delete internal staged artifacts
3. delete completed sessions after idempotency retention window passes

Recommended defaults:

- completed session retention: 24 hours
- retryable failed session retention: 24 hours
- permanent failure retention: 7 days
- cleanup interval: 5 minutes

### Executable cleanup

Keep `BatchRetentionCleanupWorker` focused on:

- terminal `BatchJob` retention
- output/error artifact retention

It should not know anything about create-session staging.

## Admin And Operator Surface

### Remove job-centric create repair

After cutover:

- remove `repair-preparing` from batch admin actions
- remove batch capability logic for `preparing`
- remove job-centric create repair metrics

### Add session-centric operator visibility

Recommended admin endpoints:

- `GET /ui/api/batch-create-sessions`
- `GET /ui/api/batch-create-sessions/{session_id}`
- `POST /ui/api/batch-create-sessions/{session_id}/retry`
- `POST /ui/api/batch-create-sessions/{session_id}/expire`

Recommended session fields in admin UI:

- session id
- target batch id
- status
- endpoint
- input file id
- expected item count
- inferred model
- service tier
- ownership scope
- last error code/message
- promotion attempts
- created at / completed at / expires at

### Operational policy

Operators should repair create failures by acting on sessions, not batches.

That gives a clean distinction:

- session problems are create-path problems
- batch problems are execution-path problems

## Observability Plan

Add separate create-session metrics alongside executable batch metrics.

Recommended metrics:

### Gauges

- `batch_create_sessions{status}`
- `batch_create_staged_bytes_total` or per-session aggregate gauge if useful

### Counters

- create-session promotion attempts
- create-session idempotency hits
- create-session capacity rejections
- create-session permanent validation failures
- create-session artifact cleanup actions

### Histograms

- validation latency
- staged artifact write latency
- promotion latency

### Structured logs

Every create request should log:

- request id
- idempotency key
- session id
- target batch id
- input file id
- scope key
- expected item count
- outcome

Every promotion attempt should log:

- session id
- target batch id
- result
- promotion latency
- inserted item count
- cap decision result

## Configuration Plan

Add create-session-specific settings.

Recommended fields in `src/config.py`:

- `embeddings_batch_create_sessions_enabled`
- `embeddings_batch_create_session_cleanup_enabled`
- `embeddings_batch_create_session_cleanup_interval_seconds`
- `embeddings_batch_create_session_completed_retention_seconds`
- `embeddings_batch_create_session_retryable_retention_seconds`
- `embeddings_batch_create_session_failed_retention_seconds`
- `embeddings_batch_create_soft_precheck_enabled`
- `embeddings_batch_create_idempotency_enabled`
- `embeddings_batch_create_promotion_insert_chunk_size`

Remove after cutover:

- `embeddings_batch_preparing_lease_seconds`
- `embeddings_batch_preparing_heartbeat_interval_seconds`
- `embeddings_batch_staged_repair_enabled`
- `embeddings_batch_staged_repair_interval_seconds`
- `embeddings_batch_staged_repair_scan_limit`
- legacy `embeddings_batch_preparing_stale_after_seconds`

## Module Boundary Plan

Do not keep expanding `src/batch/service.py` as a single class.

Recommended split:

- `src/batch/create/service.py`
  - public create orchestration
- `src/batch/create/session_repository.py`
  - create-session persistence
- `src/batch/create/staging.py`
  - normalized staged artifact read/write
- `src/batch/create/promoter.py`
  - session-to-batch promotion transaction
- `src/batch/create/cleanup.py`
  - session cleanup worker
- `src/batch/service.py`
  - keep only file APIs and thin orchestration, or split further if needed

Keep execution concerns where they already belong:

- `src/batch/worker.py`
- `src/batch/repositories/item_repository.py`
- `src/batch/repositories/job_repository.py`
- `src/batch/completion_outbox.py`

## Prisma / Schema Plan

### New model

Add `DeltaLLM_BatchCreateSession`.

### Existing model changes

No new create-path state should be added to `DeltaLLM_BatchJob`.

After migration/drain:

- stop using `preparing`
- stop using `validating` for new jobs

If the enum-like status values are documented externally, keep backward read compatibility until cleanup is complete.

### Migration ordering

1. add new table and indexes
2. deploy code that can read legacy jobs and new sessions
3. switch new creates to session path behind flag
4. drain legacy staged jobs
5. remove staged job logic from runtime and admin

## Rollout Plan

### Phase 0: Contract freeze and instrumentation

Scope:

- finalize this architecture
- add missing metrics and logs needed to compare old vs new path

Deliverables:

- design doc approved
- benchmarks defined
- migration and rollback plan agreed

### Phase 1: Schema and internal create-session scaffolding

Scope:

- add `BatchCreateSession`
- add staging artifact abstraction
- add session repository and cleanup worker
- no traffic cutover yet

Files:

- `prisma/schema.prisma`
- `src/batch/create/*`
- `src/bootstrap/batch.py`
- `src/config.py`
- `src/metrics/batch.py`
- tests

Acceptance:

- schema is deployed
- cleanup worker can manage sessions and internal artifacts
- no behavior change to public create path yet

### Phase 2: New create path behind feature flag

Scope:

- implement validation -> staged artifact -> session -> promotion flow
- wire optional idempotency support
- keep old path available behind fallback flag

Files:

- `src/api/v1/endpoints/batches.py`
- `src/batch/service.py`
- `src/batch/create/*`
- `src/models/requests.py` only if request-level changes are needed

Acceptance:

- new create path returns normal batch responses
- no `preparing` jobs are created when flag is on
- same-scope contention only affects promotion, not validation

### Phase 3: Admin and operator cutover

Scope:

- add session admin visibility and retry/expire actions
- deprecate `repair-preparing`

Files:

- `src/api/admin/endpoints/batches.py`
- new admin endpoint module for create sessions if preferred
- `src/services/ui_authorization.py`
- admin UI files if UI support is desired in same phase

Acceptance:

- operators debug failed create sessions without touching live batches
- repair semantics move from jobs to sessions

### Phase 4: Legacy staged-job drain

Scope:

- drain or manually retire existing `preparing` / `validating` jobs
- stop generating those statuses
- remove staged repair worker from bootstrap

Acceptance:

- no non-executable jobs remain in `deltallm_batch_job`
- all new creates use session path

### Phase 5: Cleanup and simplification

Scope:

- delete create heartbeat logic
- delete job-centric staged repair logic
- remove obsolete config
- remove legacy tests

Acceptance:

- executable batch runtime contains execution logic only
- create-path reliability is handled entirely via sessions

## File-Level Implementation Plan

### `prisma/schema.prisma`

Add:

- `DeltaLLM_BatchCreateSession`

Possibly adjust comments / indexes on `DeltaLLM_BatchJob` to clarify executable-only contract.

### `src/batch/create/service.py`

New.

Responsibilities:

- validate input file
- normalize staged artifact
- resolve session/idempotency
- call promoter
- translate failures to public HTTP behavior

### `src/batch/create/staging.py`

New.

Responsibilities:

- write normalized internal artifact
- read normalized artifact for promotion
- delete staged artifact on cleanup

### `src/batch/create/session_repository.py`

New.

Responsibilities:

- create session
- get session by id
- get session by idempotency key
- mark completed
- mark retryable/permanent failure
- list expirable sessions

### `src/batch/create/promoter.py`

New.

Responsibilities:

- lock session
- enforce strict executable backlog cap
- create executable `BatchJob`
- bulk insert `BatchItem`s
- finalize session

### `src/batch/service.py`

Refactor.

Keep:

- file APIs
- batch get/list/cancel APIs

Remove create-path complexity that belongs in `batch/create/*`.

### `src/batch/cleanup.py`

Refactor.

Keep:

- terminal `BatchJob` retention

Remove:

- `repair_preparing`
- staged repair worker
- legacy staged status reasoning

### `src/bootstrap/batch.py`

Replace staged repair worker wiring with create-session cleanup worker wiring.

### `src/api/admin/endpoints/batches.py`

Remove or deprecate:

- `repair-preparing`

Add:

- session-centric operator actions or route to a dedicated create-session admin module

### `src/services/ui_authorization.py`

Remove batch capability:

- `repair_preparing`

Add session capability if the admin UI exposes create sessions.

### `src/metrics/batch.py`

Split metrics conceptually into:

- executable batch metrics
- create-session metrics

### Tests

Add or replace:

- unit tests for session repository
- unit tests for promoter idempotency
- DB-backed tests for same-scope promotion race
- tests for retryable vs permanent create failures
- tests for cleanup of expired sessions and artifacts
- tests proving no `BatchJob` exists before promotion commit

## Testing Strategy

### Unit tests

Cover:

- validation normalization
- idempotency resolution
- failure classification
- session cleanup policy

### DB integration tests

Cover:

1. same-scope concurrent promotions serialize correctly
2. only one completed batch exists for a given idempotency key
3. failed promotion does not create a partial job
4. promotion inserts all items before batch becomes visible
5. session cleanup deletes internal artifacts safely

### Failure-injection tests

Inject faults at:

- input artifact read
- normalized artifact write
- session create
- advisory lock wait
- batch job insert
- mid-bulk item insert
- session completion update
- client disconnect after commit

### Load tests

Separate:

1. validation throughput
2. promotion throughput
3. execution throughput

Do not collapse them into one metric.

Measure:

- p50/p95 create latency
- promotion latency by item count
- error rate under same-scope contention
- executable backlog stability

## Reliability Acceptance Criteria

The redesign is acceptable only if all of the following are true:

1. no non-executable `BatchJob` statuses are used for new creates
2. no live `BatchJob` requires a repair action to finish creation
3. a failed create cannot leave behind a worker-visible batch
4. same request retried with idempotency key returns the same batch after ambiguous success
5. create-session cleanup can remove abandoned staged artifacts without touching executable batches
6. batch workers and finalizers operate unchanged on executable jobs
7. `POST /v1/batches` no longer depends on create heartbeats or staged-job repair workers

## Scalability Expectations

This architecture should improve scalability in four concrete ways:

1. validation and normalization are removed from the executable job lifecycle
2. workers never branch on create-path states
3. strict backlog admission is enforced only at promotion time, not across the entire validation flow
4. cleanup and recovery complexity is isolated to lightweight session records and internal artifacts

The main remaining scalability bottleneck becomes promotion insert speed for very large batches.

That should be addressed with:

- bulk insert benchmarking
- native transaction control
- COPY-style insertion if needed

not with additional job-level states.

## Open Design Choices And Recommended Answers

### Should create sessions auto-promote in the background after the request fails?

Recommended answer:

- no

Reason:

- the public API is synchronous
- if the client never received success, creating a batch later without an idempotent retry is surprising
- background workers should clean or inspect sessions, not create user-visible batches autonomously

### Should create sessions count toward pending batch cap?

Recommended answer:

- no

Reason:

- pending batch cap is an executable backlog control
- request amplification should be controlled with request rate limits and soft prechecks
- combining the two creates the same complexity that caused the current `preparing` design to sprawl

### Should staged artifacts be stored in DB rows?

Recommended answer:

- no for the primary design

Reason:

- avoid duplicate DB payload storage
- preserve DB for execution tables
- keep staging artifact cleanup independent from execution data

## Migration Away From `preparing` And `validating`

### Legacy handling during rollout

During migration:

- existing `preparing` / `validating` jobs may still need compatibility handling
- do not add more features to those states
- treat them as legacy debt to drain, not part of the target model

### Drain strategy

1. stop creating new legacy staged jobs
2. inspect existing rows
3. complete or retire them explicitly
4. verify zero rows remain
5. remove code paths, admin actions, and configs related to them

## Final Recommendation

Implement this redesign as a dedicated batch-create architecture refactor, not as another incremental fix on top of the current staged-job model.

The winning simplification is:

- `BatchJob` is execution
- `BatchCreateSession` is construction
- internal staged artifact is the durable retry boundary
- promotion is the only bridge between them

That boundary is the cleanest path to a batch system that is supportable, reliable under failure, and scalable under concurrency.
