# Tiers UX redesign: code-review remediation plan v2

> Status: implementation-ready planning document. No production code has been changed. This revision incorporates both code-review rounds, resolves the five follow-up findings, and supersedes conflicting decisions in `tiers-ux-redesign-mapping.md` and earlier revisions of this document.

## 0. Follow-up review closure

These corrections are mandatory. Later sections expand them into schema, repository, API, UI, rollout, and test work.

| Follow-up finding | Authoritative correction | Closure gate |
| --- | --- | --- |
| There is no Tiers implementation diff and the worktree branch is stale | Treat branch correction and a clean baseline as Slice 0; do not start application edits until it passes | `HEAD` is based on freshly fetched `origin/main`, the duplicate fair-share patch is absent, planning files are committed, and the only remaining changes are intentional Tiers work |
| The bootstrap key is not claimed before resource creation | Serialize `(principal_scope, idempotency_key)` with a transaction-scoped PostgreSQL advisory lock before the replay lookup or any insert | Concurrent identical requests converge on one Tier/v1; a mismatched replay conflicts; no generic uniqueness error is used as the normal idempotency path |
| Requiring a body on the existing publish endpoint would break the current UI | Add a new guarded `/activate` endpoint first; migrate the UI; only then require guards on or retire legacy `/publish` | There is no deployment in which the active UI calls an endpoint contract the active backend rejects |
| Optional revision checks let legacy replacement callers bypass concurrency safety | Make revision preconditions mandatory at the GA enforcement gate; do not claim the feature is concurrency-safe while unguarded writes remain enabled | Unversioned replacement requests and bodyless legacy publish requests return `428`, and every remaining mutation enters its shared guard |
| Row IDs were not explicitly scoped to the Tier/version URL | Lock the path Tier/version and mutate with compound `(row_id, tier_version_id)` predicates; missing/mismatched rows produce `404` without a revision bump | Cross-tier and cross-version mutation tests prove no row or revision changes |

## 1. Decisions changed by the review

| Earlier assumption | Reviewed decision |
| --- | --- |
| Keep the branch on `e1e7420c` | Rebase onto current `origin/main`; the branch commit is patch-equivalent to an already merged commit |
| Paginate policies and pools only in the browser | Add server pagination and row-level CRUD; browser pagination alone does not reduce payload or save cost |
| Preserve full-array replacement saves | Move the UI to revision-checked row mutations; deprecate replacement endpoints |
| Create tier and then Draft v1 in two UI requests | Add one atomic bootstrap endpoint and serialize each principal-scoped idempotency key before lookup or creation |
| Automatically continue the newest draft | Continue automatically only when unambiguous; show a chooser when multiple drafts exist |
| Use existing `updated_at` values | Add a configuration revision and bump the version timestamp on every configuration mutation |
| Load up to 500 deployments to infer pricing mode | Return model mode with callable-target catalog data, including an explicit conflict/unknown state |
| Change the existing `/publish` contract in the additive API slice | Add `/activate` with mandatory guards, migrate the UI, then enforce guards on legacy `/publish` |
| Let legacy replacement callers omit revisions during the completed rollout | Permit this only during a measured migration stage; GA requires a revision on every surviving mutation route |
| Address policy/pool rows by ID alone | Require the row ID, version ID, and path Tier relationship to agree inside the locked transaction |

The visual information architecture, compact Live/Draft badges, blue application theme, version restoration by cloning, capacity-health view, full field parity, and accessibility requirements remain valid.

## 2. Review findings and target fixes

| Finding | Target fix | Completion evidence |
| --- | --- | --- |
| No Tiers code is present to review; branch is stale and carries a duplicate patch | Make branch correction a hard implementation gate and rebase onto freshly fetched `origin/main` before application changes | Ahead/behind is expected for only intentional redesign commits; `git cherry origin/main` has no duplicate fair-share patch; a source diff contains Tiers files only after implementation begins |
| Full-array saves can overwrite another admin | Add `configuration_revision`; every mutation requires `expected_revision` and stale writes return structured `409` | Two concurrent clients cannot both save revision N; the second performs no database mutation |
| Client pagination does not scale | Add paginated policy, pricing, pool, and archived-version reads plus row CRUD and a bulk-limits endpoint | A single row edit sends and writes one row regardless of total collection size |
| Configuration timestamps are stale | Increment revision and update the parent version timestamp in the same transaction as every row mutation | Version and catalog activity timestamps change after policy/pool mutations |
| Draft v1 creation is not atomic or idempotent | Add an atomic bootstrap transaction plus principal-scoped `Idempotency-Key` serialization and replay handling | Retry after a lost response returns the same tier and v1; simultaneous requests converge; mismatched replay returns `409` |
| “Continue newest draft” can open another admin's work | Record creator/source attribution and require a chooser when drafts are ambiguous | Multiple drafts show creator, time, and source; no draft is silently chosen |
| Pricing mode inference stops at 500 deployments | Enrich callable targets with resolved model mode | A callable beyond deployment 500 receives the correct profile; conflicting modes require explicit choice |
| Guarding the existing publish endpoint would break the bodyless current client | Introduce a new guarded activation endpoint and use a staged migration/enforcement sequence | Backend-additive, UI-migration, and enforcement contract tests all pass; current and redesigned clients are never incompatible with the deployed stage |
| Legacy replacement endpoints can bypass optimistic concurrency | Require `expected_revision` after UI migration and return `428` when absent | No enabled endpoint can change draft configuration without a revision precondition at GA |
| Row CRUD ownership is underspecified | Scope every read-for-mutation and DML predicate to the locked version and verify that version belongs to the path Tier | Wrong Tier/version/row combinations return `404`, mutate nothing, and do not increment revision |

## 3. Phase 0: correct the worktree foundation

Do this before editing application code. Slice 0 is a blocking gate, not administrative cleanup that can be deferred.

1. Record the preflight state from the Tiers worktree:
   - `git status --short --branch`;
   - `git rev-parse HEAD origin/main`;
   - `git rev-list --left-right --count origin/main...HEAD`;
   - `git cherry -v origin/main HEAD`;
   - `git diff --name-status origin/main...HEAD`.
2. Commit only the two planning documents as an intentional documentation commit. Do not stage source-checkout `plans/`, `ui/design/`, or unrelated files from another worktree.
3. Fetch `origin` and record the refreshed `origin/main` SHA.
4. Re-run `git cherry -v origin/main HEAD` and confirm `e1e7420c` is patch-equivalent to merged commit `9d7933d3`.
5. Rebase `feature/tiers-ux-redesign` onto the refreshed `origin/main`. Let Git skip the duplicate fair-share patch; do not force-replay it.
6. Verify:
   - the worktree remains `/Users/mehditantaoui/Documents/Challenges/deltallm/.worktrees/tiers-ux-redesign`;
   - the branch remains `feature/tiers-ux-redesign`;
   - both planning files remain present and tracked;
   - `git merge-base HEAD origin/main` equals the refreshed main SHA;
   - `git cherry -v origin/main HEAD` lists only intentional planning/redesign commits;
   - `git diff --name-status origin/main...HEAD` contains no batch-test patch or unrelated source-checkout files.
7. Install dependencies only if absent, then record a corrected-base test baseline:
   - `uv run pytest tests/test_ui_tiers_api.py tests/db/test_tier_catalog_repository.py tests/db/test_tier_policy_repository.py tests/db/test_tier_version_repository.py tests/db/test_tier_version_clone_repository.py`;
   - `npm --prefix ui run test:unit`;
   - `npm --prefix ui run build`;
   - `git diff --check origin/main...HEAD`.
8. If an existing baseline test fails, record the command, failure, and whether it reproduces on `origin/main`. Do not hide it inside redesign commits.

No Tiers implementation review or implementation-complete claim is valid until this gate passes and a subsequent diff actually contains the intended application changes.

Do not use a hard reset. If the rebase encounters an unexpected conflict, stop and inspect rather than discarding either side.

## 4. Target data model

### 4.1 Tier version concurrency and provenance

Add these fields to `DeltaLLM_TierVersion`:

```text
configuration_revision   INTEGER NOT NULL DEFAULT 0
created_by_account_id    TEXT NULL
created_by_kind          TEXT NOT NULL DEFAULT 'unknown'
source_tier_version_id   TEXT NULL
```

Constraints and indexes:

- `configuration_revision >= 0` check constraint.
- `created_by_kind IN ('account', 'master_key', 'system', 'unknown')` check constraint.
- `created_by_account_id` foreign key to `DeltaLLM_PlatformAccount`, `ON DELETE SET NULL`.
- `source_tier_version_id` self-reference, `ON DELETE SET NULL`.
- Indexes on `created_by_account_id` and `source_tier_version_id`.
- Update the Prisma inverse relations on `DeltaLLM_PlatformAccount` and `DeltaLLM_TierVersion`.

Semantics:

- A newly created blank version starts at revision `0`.
- A cloned/restored version also starts at revision `0`; its copied contents are its initial configuration.
- Each successful policy/pool create, update, delete, or bulk mutation increments revision exactly once.
- Lifecycle-only changes such as publish/archive update `updated_at` but do not increment configuration revision.
- `created_by_account_id` is populated for platform-session admins.
- Master-key creation records `created_by_kind='master_key'` with no account ID.
- Existing rows are backfilled as `created_by_kind='unknown'`; do not infer a creator from the publisher.
- Clone operations store the source version ID. Blank versions leave it null.

### 4.2 Idempotent tier bootstrap

Add a dedicated table rather than exposing idempotency internals on the Tier model:

```text
DeltaLLM_TierCreationRequest
  tier_creation_request_id  TEXT PRIMARY KEY
  principal_scope           TEXT NOT NULL
  idempotency_key           TEXT NOT NULL
  request_hash              TEXT NOT NULL
  tier_id                   TEXT NOT NULL UNIQUE
  created_at                TIMESTAMP NOT NULL DEFAULT NOW()

  UNIQUE (principal_scope, idempotency_key)
```

- Foreign key `tier_id -> DeltaLLM_Tier`, `ON DELETE CASCADE`.
- Validate a bounded, nonblank idempotency key (recommended maximum: 200 characters).
- Derive `principal_scope` on the server from the authenticated, non-secret actor identity: `account:<account_id>` for a platform session and the stable singleton scope `master_key` for master-key authentication. Never accept it from the request body, and never put the raw key or a reversible credential value in this column, logs, or audit data.
- The UI generates a UUID per submit attempt and reuses it for retries until a terminal result.
- Hash the normalized tier fields and initial-draft intent. Reusing a key with different input returns `409`.
- Keep the request row for the lifetime of the tier; deleting the tier removes it.
- The uniqueness constraint is a durable replay record, not the concurrency primitive. The transaction-scoped advisory lock described below serializes the first request before this row exists.
- Use a stable database hash such as `hashtextextended('tier-bootstrap:v1:' || principal_scope || ':' || idempotency_key, 0)` for the advisory lock. Do not use Python's process-randomized `hash()`.
- A rare advisory-lock hash collision may serialize unrelated bootstrap requests but cannot merge their replay records because the table's composite unique constraint and request hashes remain authoritative.

### 4.3 Accurate activity timestamps

- Every configuration mutation updates `DeltaLLM_TierVersion.updated_at` in the same transaction that increments `configuration_revision`.
- Tier metadata updates continue to update `DeltaLLM_Tier.updated_at`.
- The list query exposes `last_activity_at = GREATEST(tier.updated_at, MAX(version.updated_at))`.
- The catalog uses `last_activity_at`; the version rail uses each version's `updated_at`.
- Do not update the parent Tier row for every policy edit. This avoids unnecessary parent-row contention while still producing an accurate aggregate timestamp.

## 5. Repository and service design

### 5.1 Shared mutation guard

Introduce one repository helper used by every configuration mutation:

```text
lock_draft_version_for_configuration_mutation(
  tier_id,
  tier_version_id,
  expected_revision,
) -> TierVersionRecord
```

Inside the caller's transaction it must:

1. Select the version `FOR UPDATE` with both `tier_version_id` and `tier_id` in the predicate.
2. Return not found if absent.
3. Reject non-draft versions.
4. Compare `configuration_revision` to `expected_revision`.
5. Raise a distinct stale-configuration error before any row mutation when they differ.

After a successful row mutation, call one helper that performs:

```sql
UPDATE deltallm_tierversion
SET configuration_revision = configuration_revision + 1,
    updated_at = NOW()
WHERE tier_version_id = $1
RETURNING configuration_revision, updated_at;
```

All validation and the row mutation must occur while the version lock is held. The service must not perform pool-reference validation on an unlocked pre-transaction snapshot.

The locked path version is also the ownership boundary for child rows. Update and delete statements must use compound predicates:

```sql
UPDATE deltallm_tiermodelpolicy
SET ...
WHERE tier_model_policy_id = $policy_id
  AND tier_version_id = $tier_version_id
RETURNING ...;

DELETE FROM deltallm_tiercapacitypool
WHERE tier_capacity_pool_id = $pool_id
  AND tier_version_id = $tier_version_id
RETURNING ...;
```

Equivalent predicates apply to policy deletes and pool updates. If no child row is returned, respond with the same scoped `404` used for an unknown child; do not reveal that an ID exists in another Tier/version, do not mutate anything, and do not increment `configuration_revision`.

### 5.2 Model policy row operations

Add repository/service methods for:

- paginated list with total count;
- create one policy;
- update one policy by `tier_model_policy_id`;
- delete one policy by ID;
- bulk update RPM/TPM for a specified policy set or the complete filtered set.

Rules:

- `callable_key` is immutable after creation. Changing model identity is Add new + Remove old, preventing old prices/limits from silently moving to another model.
- Preserve all opaque metadata and unedited pricing keys.
- Continue enforcing one policy per `(tier_version_id, callable_key)`.
- Create/update capacity-pool references must resolve to a pool for the same callable key inside the locked transaction.
- Update/delete must match both `tier_model_policy_id` and the locked `tier_version_id`; the version must already have matched the path `tier_id`.
- Delete returns the new revision even though the resource is gone.
- Bulk update runs one SQL update and increments the revision once, not once per row.
- A selected-row bulk request first proves that every requested policy ID belongs to the locked version. If any ID is missing or belongs elsewhere, fail the complete request with no row update and no revision bump.
- An all-filtered bulk request resolves its target set inside the same locked transaction so the count and update use one consistent predicate.

Supported list parameters:

```text
search, enabled, access_mode, capacity_pool_key,
sort = callable_key | priority | updated_at,
order = asc | desc,
limit = 10..100,
offset >= 0
```

Search covers callable key and capacity-pool key. Use stable secondary ordering by policy ID.

### 5.3 Capacity pool row operations

Add repository/service methods for paginated list, create, update, and delete by `tier_capacity_pool_id`.

Rules:

- `pool_key` and `callable_key` are immutable after creation. Renaming uses Add new, rebind policies, Remove old.
- Preserve metadata.
- Continue enforcing unique `(tier_version_id, pool_key, callable_key)`.
- Delete a referenced pool returns a domain `409`; never rely only on a raw foreign-key error.
- Validate strategy-specific fields exactly as today.
- Update/delete must match both `tier_capacity_pool_id` and the locked `tier_version_id`; the version must already have matched the path `tier_id`.
- A row mismatch returns scoped `404`, leaves the row untouched, and does not bump the version revision.
- Every successful operation returns the new configuration revision.

Supported list parameters:

```text
search, strategy,
sort = pool_key | callable_key | updated_at,
order = asc | desc,
limit = 10..100,
offset >= 0
```

### 5.4 Version creation and listing

Refactor blank version creation to be transaction-safe:

1. Lock the Tier row.
2. Compute `MAX(version_number) + 1` inside that transaction.
3. Insert the draft with creator/source fields.

This removes the current read-then-insert race between two admins.

Add a paginated version-list repository method with status filters. The UI should fetch all active/draft summaries and paginate archived history in groups of 10. Stable order is `version_number DESC`.

### 5.5 Atomic bootstrap

Add `create_tier_with_initial_draft` as one repository transaction with an explicit serialization point:

1. Before opening the transaction, validate the header, derive `principal_scope`, normalize the request, and calculate `request_hash` and stable advisory-lock material.
2. Begin the database transaction.
3. Acquire `pg_advisory_xact_lock(hashtextextended($lock_material, 0))`. This happens before the replay lookup and before any Tier insert.
4. Look up `(principal_scope, idempotency_key)` while the lock is held.
5. If found with the same hash, load and return its Tier and Draft v1 as a replay, then commit/close the read transaction.
6. If found with another hash, raise the stable idempotency conflict and roll back without creating anything.
7. If absent, insert the Tier.
8. Insert Draft v1 with revision 0 and actor attribution.
9. Insert the durable idempotency record referencing that Tier.
10. Commit and return both resources.

The advisory lock is released automatically on commit or rollback. A second simultaneous request with the same principal/key waits, then observes the committed replay row; it never reaches Tier creation. A different key targeting an already-used `tier_key` remains a normal domain `409` and must not be converted into an idempotent replay.

Do not implement the normal concurrency path by catching a PostgreSQL unique violation and querying again inside the failed transaction: PostgreSQL marks that transaction aborted. Any defensive uniqueness handling must occur after rollback in a new transaction, but the advisory lock should make an idempotency-key uniqueness race unreachable in normal operation.

A failure at any point after transaction start rolls back the Tier, version, and request row together. No audit or cache invalidation is emitted until the commit succeeds.

Audit behavior:

- A newly committed bootstrap emits the existing Tier-create and Version-create mutation audits, correlated by request ID.
- An idempotent replay does not emit duplicate mutation events; log/audit metadata records `idempotency_resolution='replayed'` on the request-level event if one exists.

### 5.6 Activation preview and guarded activation

Add a service-level activation preview computed relative to the current active version.

Identity keys:

- model policy: `callable_key`;
- capacity pool: `(pool_key, callable_key)`.

Change categories:

- policy added/removed;
- access or enabled state changed;
- request/token/parallel/batch limits changed;
- pricing changed;
- capacity binding or priority changed;
- pool added/removed;
- pool capacities or strategy settings changed.

Return summary counts plus a bounded detail list (recommended 20 entries per category) and a `truncated` flag.

The preview also returns:

- draft configuration revision;
- current active version ID/number, if any;
- enabled non-expired unpinned assignment count;
- distinct affected organization count;
- enabled non-expired assignments pinned to the current active version;
- warnings such as zero enabled allow policies;
- `can_activate` and explicit blocker codes.

The canonical guarded mutation is a new activation operation. It requires both `expected_revision` and nullable `expected_active_version_id` from the preview. After locking Tier/current active/draft in the repository's established lock order, it rechecks both values. A changed draft or live version returns `409` and requires a fresh preview. Existing database lifecycle and pinned-assignment restrictions remain authoritative and are re-evaluated inside the activation transaction.

Do not make a JSON body mandatory on the existing `/publish` route in the additive API slice. The currently deployed UI sends a bodyless POST, so that would create a backend/UI incompatibility window. Instead:

1. Add `POST /.../activate` with mandatory guard fields and route it to the guarded repository method.
2. Leave bodyless `/publish` operational only during the migration stage and instrument its use by actor/client metadata.
3. Migrate all first-party UI paths to preview + `/activate`.
4. After telemetry confirms the current UI no longer calls `/publish`, enable the enforcement gate: bodyless `/publish` returns `428 tier_activation_precondition_required`. A legacy caller may send both guard fields, in which case `/publish` delegates to the same guarded method.
5. Declare the redesign generally available only after that enforcement gate is on.

Both routes must share one service/repository activation implementation; do not maintain a guarded and an unguarded publication code path after enforcement.

### 5.7 Catalog summary query

Replace additional per-row HTTP requests with one enriched server query. Return:

- active version summary;
- latest draft summary;
- draft count;
- total version count;
- historical assignment count for compatibility;
- enabled live/scheduled assignment count;
- distinct enabled live/scheduled organization count;
- `last_activity_at`.

Each version summary contains ID, version number, policy count, pool count, revision, creator summary, and updated time.

Implement the query with CTEs or lateral joins over indexed keys rather than stacking many independent correlated subqueries. Preserve the current paginated count/search/enabled filtering contract.

## 6. API contract

All endpoints remain `platform.admin` only and retain mutation audit/invalidation behavior.

### 6.1 Bootstrap

```text
POST /ui/api/tiers/bootstrap
Idempotency-Key: <client UUID>
```

Request: existing tier create fields. Response:

```json
{
  "tier": { "tier_id": "...", "version_count": 1 },
  "initial_version": {
    "tier_version_id": "...",
    "version_number": 1,
    "status": "draft",
    "configuration_revision": 0
  },
  "idempotency_resolution": "created"
}
```

Replay returns the same resources and `idempotency_resolution: "replayed"`.

Keep the existing `POST /ui/api/tiers` behavior for API compatibility; the redesigned UI uses bootstrap.

### 6.2 Paginated configuration reads

```text
GET /ui/api/tiers/{tier_id}/versions/{version_id}/model-policies
GET /ui/api/tiers/{tier_id}/versions/{version_id}/capacity-pools
GET /ui/api/tiers/{tier_id}/versions?status=archived&limit=10&offset=0
```

Page responses include the version's current configuration revision alongside standard pagination.

### 6.3 Row mutations

```text
POST   /.../model-policies
PATCH  /.../model-policies/{policy_id}
DELETE /.../model-policies/{policy_id}
POST   /.../model-policies/bulk-limits

POST   /.../capacity-pools
PATCH  /.../capacity-pools/{pool_id}
DELETE /.../capacity-pools/{pool_id}
```

Every request carries `expected_revision`. Every successful response carries the new revision and version `updated_at`.

For routes containing `{policy_id}` or `{pool_id}`, the service passes the path `tier_id`, `version_id`, and row ID into one repository transaction. A row ID that exists under another version is indistinguishable from an unknown row and returns scoped `404`; it never causes a revision increment on either version.

### 6.4 Structured conflicts

Return structured error details:

```json
{
  "detail": {
    "code": "tier_configuration_stale",
    "message": "This draft changed after you loaded it. Refresh before saving.",
    "expected_revision": 4,
    "current_revision": 5
  }
}
```

Other stable codes include:

- `tier_version_not_draft`;
- `tier_policy_duplicate_callable`;
- `tier_pool_duplicate_identity`;
- `tier_pool_in_use`;
- `tier_activation_active_changed`;
- `tier_activation_pinned_assignments`;
- `tier_bootstrap_idempotency_conflict`;
- `tier_configuration_precondition_required` (`428`);
- `tier_activation_precondition_required` (`428`).

Unknown or wrong-scope policy/pool IDs use the normal scoped `404` contract. Do not return an error that confirms the child exists under another Tier/version.

Update the UI API error parser to use nested `detail.message` while preserving current string-detail behavior.

### 6.5 Activation

```text
GET  /ui/api/tiers/{tier_id}/versions/{draft_id}/activation-preview
POST /ui/api/tiers/{tier_id}/versions/{draft_id}/activate
```

Activation request body:

```json
{
  "expected_revision": 4,
  "expected_active_version_id": "version-live-3"
}
```

`expected_active_version_id` is explicitly nullable for a Tier with no active version. The request schema remains strict: missing and explicit `null` are distinct, so the server can reject a client that never obtained a preview.

Compatibility contract for existing `POST /.../publish`:

- Additive stage: continue accepting its current bodyless request and emit a legacy-route metric/audit marker. If both guards are supplied, enforce them and delegate to the canonical activation service immediately.
- Migration stage: the redesigned UI never calls it.
- Enforcement stage: a missing body or missing guard field returns HTTP `428` with code `tier_activation_precondition_required`; a fully guarded body delegates to the canonical activation service.
- Removal, if desired, is a separate compatibility change after confirmed zero use.

### 6.6 Legacy replacement endpoints

- Mark full policy/pool replacement methods deprecated in TypeScript and endpoint documentation.
- The redesigned UI must not call them.
- During the additive/migration stages, accept optional `expected_revision` only to let callers migrate. Enforce it whenever supplied, and emit metrics for calls that omit it. Do not describe the system as concurrency-safe during this stage.
- At the GA enforcement gate, require `expected_revision` and return HTTP `428 Precondition Required` with code `tier_configuration_precondition_required` when it is absent.
- When the revision is present, run the replacement through the same path Tier/version lock, stale check, in-transaction validation, compound ownership/reference checks, and single revision bump used by row mutations.
- Keep guarded full replacement only for compatibility; it remains inefficient and is excluded from the redesigned UI.
- Remove it only in a separately approved compatibility change after usage is confirmed absent.

This is an intentional compatibility break for unversioned mutation callers at the enforcement gate. It is required because indefinite backward compatibility with an unguarded destructive replacement endpoint is logically incompatible with the acceptance criterion that admins cannot silently overwrite one another.

Use one temporary server setting, `tier_legacy_mutation_preconditions_required`:

- default `false` only for the additive and UI-migration stages;
- set `true` at the GA enforcement gate;
- when true, it governs both bodyless legacy `/publish` and missing-revision replacement calls;
- expose its effective value in startup diagnostics and deployment documentation;
- remove the setting and make enforcement unconditional in the later legacy-cleanup change.

## 7. Callable mode enrichment

Extend `CallableTarget` with optional model-mode information rather than paging through the deployment health endpoint.

For every model callable:

- collect normalized `model_info.mode` values across deployments;
- return the single resolved mode when they agree;
- return `mode: null` and `mode_conflict: true` when deployments disagree;
- route groups keep mode null unless a later route-group contract defines one.

Extend `/ui/api/callable-targets` and `CallableTargetListItem` with:

```text
mode: string | null
mode_conflict: boolean
```

The policy editor behavior becomes:

- resolved mode: select the inferred pricing profile;
- unknown/conflicting mode: require the admin to choose a pricing profile and show why inference is unavailable;
- existing policy: retain the saved/inferred pricing view and never clear hidden prices automatically.

Remove the `models.list({ limit: 500 })` dependency from Tier detail.

## 8. UI implementation plan

### 8.1 Shared state and API cache rules

- Treat server rows as paginated query results, not one giant version array.
- Store the selected version revision in a single workspace-level source of truth.
- Models & limits and Pricing are two views of the same policy resources. A mutation in either view invalidates/refetches both query caches and updates the workspace revision.
- Capacity-pool mutations invalidate pool pages and any policy page showing pool bindings.
- Never maintain independent editable copies of the same policy in both tabs.
- Close an editor only after a successful mutation.

### 8.2 Catalog

- Keep server search and enabled/disabled filtering.
- Use server page sizes 10/25/50 and numbered pagination.
- Render compact status badges using the approved truth table.
- Show exact live/scheduled organization count in the Organizations column; expose historical assignment count in tooltip/detail rather than calling assignments organizations.
- Show package counts from latest draft when present, otherwise live, with explicit accessible version context.
- Use `last_activity_at` for Updated.
- Keep Capacity health as a separate page-level view; load its data when selected and preserve refresh/degraded states.

### 8.3 Create tier

- Generate one idempotency UUID when the user starts submission.
- Disable duplicate submits but reuse the same key for network retries.
- Call bootstrap once and navigate directly to Draft v1.
- If the response is lost, retry with the same key and receive the same tier/version.
- A hash mismatch conflict is terminal for that key: generate a new key only after the admin changes the form and explicitly resubmits.
- Keep validation errors inside the drawer.

### 8.4 Version rail and draft chooser

- Fetch active and draft summaries first; fetch archived versions in pages of 10 with Load more.
- Show version number, state, creator, creation/update time, and source version when known.
- Primary behavior:
  - no draft + live: **Edit live configuration** clones live;
  - exactly one draft: **Continue draft vN**, including creator context;
  - multiple drafts: **Choose a draft** opens a chooser; do not auto-open latest;
  - archived selected: **Restore as draft** clones archived;
  - no version: **Create draft**.
- If a draft belongs to another account, it remains editable because authorization is platform-admin based, but the UI explicitly identifies that fact.
- After a version becomes active/archived elsewhere, a row mutation receives a conflict and the workspace switches to read-only after refetch.

### 8.5 Models & limits

- Server search/filter/sort/page state lives in the URL or stable component state.
- Page sizes 10/25/50, default 10.
- Table shows callable, enabled state, allow/deny, core limits, advanced-limit indicator, pool, priority, and actions.
- Disabled policies must never appear simply as Allowed.
- Add/Edit drawer uses row endpoints and carries the workspace revision.
- Callable identity is disabled during edit.
- Bulk action explicitly says whether it targets all filtered policies or selected rows and uses the bulk endpoint.
- Deleting the last row on a page moves to the previous valid page.

### 8.6 Pricing

- Reuse the policy list endpoint with pricing-focused columns and filters.
- Editing pricing PATCHes one policy and preserves limits, metadata, and hidden pricing keys.
- Continue distinguishing blank from explicit zero.
- Search and pagination are server-side; no full policy array is downloaded.
- Unknown/conflicting model mode requires explicit pricing-profile selection.

### 8.7 Capacity pools

- Server-side search/sort/pagination, default 10.
- Add/Edit drawer uses row endpoints.
- Pool and callable identities are disabled during edit.
- A referenced-pool delete conflict remains in the dialog with a link/filter to affected policies when feasible.
- Strategy help text interpolates the actual saturation threshold and burst multiplier instead of always describing defaults.

### 8.8 Stale-edit recovery

On `tier_configuration_stale`:

1. Keep the editor open and preserve the admin's unsaved field values locally.
2. Show a conflict banner explaining another update occurred.
3. Offer **Review latest** and **Discard my unsaved changes**.
4. Refetch the current row and revision for Review latest.
5. Display field-level differences between server and local form.
6. Require the admin to reapply/confirm changes against the new revision; never automatically retry a stale write.

### 8.9 Review & activate

- Fetch activation preview when the dialog opens.
- Show added/removed/changed models, pricing, limits, and pools with truncated-count indicators.
- Show exact affected organization/assignment counts and pinned-assignment blockers.
- Disable activation when preview says it cannot proceed.
- Submit the preview's draft revision and active-version ID to the new `/activate` endpoint; do not call legacy `/publish`.
- If either changed, keep the dialog open, refetch preview, and ask for confirmation again.
- Keep server errors inline and return focus to the trigger on close.

### 8.10 Accessibility and responsive behavior

- Implement real dialogs/drawers with `role="dialog"`, `aria-modal`, labelled titles, initial focus, focus trapping, Escape behavior, and focus restoration.
- Give every icon-only control an accessible name; `title` alone is insufficient.
- Use semantic tabs with keyboard navigation and selected state.
- Announce save, stale-conflict, pagination, and activation results through the existing toast/live-region system.
- Preserve compact badges as content-sized `inline-flex` elements.
- Keep tables horizontally scrollable and provide a narrow-screen summary/action layout.

## 9. Failure-mode matrix

| Scenario | Required behavior |
| --- | --- |
| Two admins save revision 4 | First succeeds with revision 5; second gets `409`, no row is overwritten |
| Policy/pool ID belongs to another Tier or version | Scoped `404`; neither child row nor either version revision changes |
| Policy is saved while its pool is concurrently removed | Version lock serializes operations; the later operation revalidates against current state |
| Draft is published while an editor is open | Next edit gets non-draft conflict; UI refetches and becomes read-only |
| Bootstrap response is lost | Same idempotency key returns the original tier and Draft v1 |
| Two requests simultaneously use the same principal/key | Second waits on the transaction advisory lock, then replays the first committed Tier/v1 |
| Bootstrap key is reused for different form data | `409` with idempotency-conflict code; no mutation |
| Different bootstrap key reuses an existing tier key | Normal tier-key `409`; it is not misreported as an idempotent replay |
| Two admins create drafts simultaneously | Tier lock assigns distinct sequential version numbers |
| Multiple drafts exist | Primary action opens chooser rather than selecting newest |
| Model deployments exceed 500 | Callable mode remains available without loading deployment health pages |
| Model deployments disagree on mode | Editor requires explicit pricing profile and shows conflict explanation |
| Referenced pool is deleted | `409 tier_pool_in_use`; policy references remain intact |
| Last item on a page is deleted | UI selects the nearest valid page and retains filters |
| Activation preview becomes stale | Guarded activation returns `409`; dialog refreshes and requires a new confirmation |
| New backend is deployed before redesigned UI | Existing bodyless `/publish` continues during the additive stage; no UI break |
| Enforcement is enabled after UI migration | Bodyless `/publish` and unversioned replacement PUTs return `428`; guarded callers continue safely |
| A UI rollback is required after enforcement | Disable the enforcement gate before rolling back to the bodyless legacy client |
| Redis capacity data is unavailable | Capacity health keeps configured pools and shows unavailable live values as em dash, not zero |

## 10. Implementation sequence

Each slice should be independently reviewable and leave tests green.

### Slice 0 — branch and baseline

- Commit only the planning documents, fetch, and correct the base branch.
- Prove the duplicate fair-share patch is gone and the merge base equals refreshed `origin/main`.
- Record the targeted backend, UI unit, UI build, and diff-check baseline.
- Stop if this gate fails; application work does not begin on the stale branch.

### Slice 1 — additive schema and record contracts

- Prisma and SQL migration for revision/provenance and principal-scoped idempotency uniqueness.
- Record mappers and serializers.
- Migration invariant and serialization tests.
- No UI behavior change.

### Slice 2 — safe repository primitives

- Draft/revision lock helper.
- Compound Tier/version/child ownership checks for every row mutation.
- Row CRUD, bulk limits, revision/timestamp bump.
- Transaction-safe version creation.
- Transaction-scoped bootstrap advisory lock and atomic create/replay repository operation.
- Repository unit and PostgreSQL integration tests.
- Keep legacy replace methods temporarily.

### Slice 3 — additive APIs

- Paginated version/policy/pool reads.
- Row mutation endpoints and structured conflicts.
- Atomic bootstrap.
- Callable mode enrichment.
- Activation preview and the new guarded `/activate` endpoint.
- Catalog summaries.
- API permission/audit/invalidation tests.
- Preserve the bodyless legacy `/publish` and unversioned replacement contracts in this additive slice; add usage telemetry and compatibility tests rather than tightening them yet.

### Slice 4 — shared UI infrastructure

- Typed API clients and structured-error helpers.
- Add the typed `activateVersion` client without changing the still-deployed UI call site yet.
- Compact version badge.
- Server pagination component.
- Accessible dialog/drawer foundation.
- Workspace revision state and stale-conflict workflow.
- Pure helper and browser-level tests.

### Slice 5 — catalog, create, and version workspace

- Catalog table/status/data corrections.
- Capacity health view.
- Idempotent Create tier flow.
- Attributed version rail, draft chooser, paginated archive history.
- Restore/edit-live actions.

### Slice 6 — configuration editors

- Models & limits server table and complete policy drawer.
- Pricing server table using the same policy resources.
- Capacity pool server table and drawer.
- Bulk limits.
- Identity immutability and cross-tab invalidation.

### Slice 7 — activation and compatibility cleanup

- Review & activate dialog with diff/impact preview.
- Switch every first-party UI activation call to preview + `/activate` and verify no bodyless `/publish` calls remain.
- Stop all new UI calls to replacement endpoints.
- Observe legacy-route metrics for an agreed verification period in staging and production-like smoke tests.
- Enable the enforcement gate only after UI migration is proven: bodyless `/publish` and replacement requests without `expected_revision` return `428`.
- Route guarded legacy calls through the same activation/configuration guards; no unsafe fallback remains.
- Add deprecation warnings and migration documentation for legacy callers.
- Update Tiers documentation and screenshots.

### Slice 8 — hardening and release verification

- Full targeted and regression suites.
- Mocked-browser accessibility/responsive workflows.
- Large-fixture query/mutation verification.
- Audit-log and cache-invalidation verification.
- Roll-forward and coordinated rollback rehearsal for the compatibility enforcement gate.
- Final dirty-worktree and diff review.

## 11. Test plan

### 11.1 Migration and repository tests

- Existing data receives revision 0 and unknown creator without loss.
- Invalid revision/creator-kind constraints fail.
- Creator/source foreign-key deletion behavior is correct.
- Bootstrap transaction rolls back tier, version, and idempotency record together.
- Bootstrap acquires the principal/key advisory transaction lock before replay lookup and before Tier insertion.
- Same principal/key/same hash replays; same principal/key/different hash conflicts; concurrent same-key requests converge.
- The concurrency integration test uses two independent database connections and proves the second request waits/replays rather than surfacing a Tier uniqueness error.
- The same idempotency key under a different principal scope never replays another principal's result: it may create independently when the Tier key differs, while an already-used `tier_key` returns the normal Tier conflict.
- An advisory-lock hash collision, simulated at the helper boundary, may serialize two requests but cannot return the wrong replay row.
- Row operations reject missing, archived, active, and stale versions.
- Row operations reject a version that does not belong to the path Tier before touching a child row.
- A policy/pool ID from another version or Tier returns scoped `404`; child rows and both versions' revisions/timestamps remain unchanged.
- Selected-row bulk operations fail atomically when any ID falls outside the locked version.
- Successful row/bulk operations increment revision exactly once and update timestamp.
- Stale operations leave all rows and revision unchanged.
- Policy/pool identity and reference invariants remain enforced.
- Concurrent pool/policy changes serialize and revalidate correctly.
- Paginated queries return stable ordering, totals, filters, and boundary pages.
- Catalog summary counts and `last_activity_at` are accurate.
- Activation preview diff categories and assignment counts are accurate.
- Guarded activation rejects changed revision/active version and pinned assignments.

### 11.2 API tests

- Platform admin and master-key success; non-admin rejection for every endpoint.
- Pagination validation and maximum limits.
- Structured conflict codes and error parser compatibility.
- Idempotency header missing/blank/too long/mismatch/replay cases.
- Bootstrap actor scoping for platform sessions and master-key authentication without storing or logging credential material.
- Row mutation request/response revision contracts.
- Wrong Tier/version/row path combinations return `404`, not an ownership leak or unrelated `409`.
- Additive-stage bodyless `/publish` compatibility and canonical guarded `/activate` behavior.
- Enforcement-stage bodyless `/publish` returns `428`; a fully guarded legacy publish delegates to the canonical activation method.
- Enforcement-stage replacement PUT without `expected_revision` returns `428`; a stale supplied revision returns structured `409`.
- Audit events include resource ID, before/after row, version revision, and actor.
- Policy invalidation runs once after committed mutations and not after stale/failed operations.
- Legacy endpoints remain compatible during the declared window.

### 11.3 UI helper tests

- Live/Draft badge truth table, including multiple drafts and disabled tiers.
- Pagination range/page-window and page correction after deletion.
- Draft primary-action selection rules.
- Structured stale-error detection.
- Pricing-mode resolved/unknown/conflict behavior.
- Activation diff grouping and truncation copy.
- Blank versus explicit-zero pricing preservation.

### 11.4 Browser workflow tests

Use the repository's Playwright dependency with mocked UI API routes or a disposable backend fixture.

- Create Tier retry returns the same tier/v1.
- Two intercepted/replayed Create Tier submissions display one resulting Tier/v1 and navigate to the same draft.
- List filters, numbered pagination, compact badges, and Capacity health switch.
- Multiple drafts open chooser with creator/source context.
- Add/edit/delete model policy across multiple server pages.
- Pricing edit appears in Models view without duplicate state.
- Add/edit/delete pool and referenced-delete conflict.
- Stale edit preserves local form and never auto-overwrites.
- Activation preview, stale-preview refresh, pinned blocker, and successful activation.
- Network assertions prove the redesigned UI calls `/activate` with both preview guards and never calls legacy `/publish` or collection replacement PUTs.
- Keyboard-only drawer/dialog/tab/pagination flow.
- Narrow viewport behavior and horizontal table fallback.

### 11.5 Scale verification

Seed at least 1,000 model policies, 500 capacity pools, and 200 archived versions in a disposable database.

- Read endpoints return only requested page sizes.
- A one-row edit issues no collection delete/reinsert and remains independent of collection length.
- Bulk updates use bounded SQL statements.
- Catalog summary remains one API request and uses indexed query paths.
- UI first render does not download full policy/pool/archive collections.
- GA smoke traffic contains no unguarded mutation or bodyless publish requests; enforcement counters confirm rejected legacy probes without successful writes.

Avoid brittle wall-clock assertions in normal CI. Verify query shape/count and optionally record local benchmark numbers for regression context.

## 12. Observability, audit, and documentation

- Count stale-write conflicts by resource type.
- Measure paginated read and row-mutation latency.
- Count legacy bodyless publish, guarded legacy publish, unversioned replacement, and `428` enforcement responses separately; label by route/auth kind without high-cardinality IDs.
- Expose the compatibility-enforcement setting in startup diagnostics and alert if it is disabled after GA.
- Log bootstrap idempotency resolution without logging the raw master key or sensitive authentication data.
- Include configuration revision in mutation audit metadata.
- Audit row before/after data according to existing audit-content policy.
- Continue policy-cache invalidation only after a committed mutation.
- Update `docs/admin-ui/tiers.md` with version immutability, restore-as-draft, concurrent-edit handling, draft creator/source, pagination, and activation review.
- Update UI screenshots only after the browser workflows pass.

## 13. Rollout and compatibility

Use four explicit rollout stages. Advancing a stage requires its exit evidence; calendar time alone is insufficient.

1. **Foundation/additive backend**
   - Apply the additive migration.
   - Return revision/provenance fields without removing existing response fields.
   - Add row CRUD, pagination, bootstrap, preview, and `/activate`.
   - Keep current bodyless `/publish` and unversioned replacement behavior temporarily while `tier_legacy_mutation_preconditions_required=false`.
   - Start metrics for legacy publish, unversioned replacement, stale conflicts, activation blockers, and endpoint latency.
   - Exit only when old UI/API regression tests and new additive contract tests pass.
2. **First-party UI migration**
   - Deploy the redesigned UI using bootstrap, row CRUD, paginated reads, preview, and `/activate` exclusively.
   - Prove with browser network assertions and server metrics that first-party traffic no longer uses bodyless `/publish` or replacement PUTs.
   - Publish migration instructions for any internal automation using `/ui/api` mutation routes.
3. **Concurrency enforcement and GA gate**
   - Set `tier_legacy_mutation_preconditions_required=true`.
   - Missing publish guards or replacement revision preconditions return `428`; guarded legacy calls enter the same safe implementation.
   - Run post-enable smoke tests, two-admin concurrency tests, and audit/invalidation checks.
   - Only now mark the redesign generally available and claim that silent overwrite is prevented across all enabled mutation routes.
4. **Later cleanup**
   - Observe legacy guarded-route usage for the announced compatibility window.
   - Remove legacy routes only in a separately approved change after confirmed zero use.

Rollback rules:

- The additive database migration remains in place during UI or API rollback; do not reverse it under live data.
- Before rolling the UI back to a bodyless legacy client, set `tier_legacy_mutation_preconditions_required=false`. Never deploy an old UI behind a backend that returns `428` to its required calls.
- Disabling enforcement reopens the known concurrency risk, so use it only as a time-bounded incident rollback, alert on it, and restore the new UI/enforcement promptly.
- If `/activate` itself fails, keep the old active version unchanged; do not fall back automatically to unguarded `/publish`.

## 14. Acceptance criteria

The remediation is complete only when all statements are true:

- The branch is based on current main with no duplicate fair-share commit.
- The branch contains an actual, intentionally scoped Tiers implementation diff before an implementation code review is requested.
- Tier creation produces exactly one Tier and Draft v1 across retries and failures.
- Simultaneous requests with the same principal/idempotency key serialize before resource insertion and converge on the same response.
- Two admins cannot silently overwrite one another's draft changes.
- Every enabled configuration mutation route, including retained replacement routes, rejects a missing revision precondition at GA.
- Every child update/delete proves `(path tier, path version, child row)` ownership in the locked transaction; mismatches mutate nothing and do not bump revision.
- Every configuration mutation updates revision and visible activity time.
- Policy, pricing, pool, and archived-version reads are server paginated.
- A one-row edit does not send or rewrite the complete collection.
- Multiple drafts are attributed and require explicit selection when ambiguous.
- Callable mode inference works beyond 500 deployments and exposes conflicts.
- Catalog Live/Draft/Disabled states and organization counts are semantically accurate.
- Active and archived versions remain immutable; restore creates a new attributed draft.
- Activation shows a current diff and exact impact/blocker information, then revalidates inside the guarded activation transaction.
- The redesigned UI uses guarded `/activate`; legacy `/publish` cannot perform an unguarded state change after the enforcement gate.
- Backend, UI, and enforcement rollout stages are contract-compatible, and the documented rollback order is rehearsed.
- The fairness dashboard retains all current healthy/partial/unavailable behavior.
- Dialogs, tabs, status, pagination, and icon actions meet the stated keyboard/screen-reader requirements.
- Backend, API, UI helper, browser workflow, migration, and scale tests pass.
- No unrelated source-checkout changes are included.

## 15. File-level implementation map

Expected core modifications:

- `prisma/schema.prisma`
- a new timestamped Prisma migration
- `src/db/tier_records.py`
- `src/db/tier_catalog_repository.py`
- `src/db/tier_version_repository.py`
- `src/db/tier_version_clone_repository.py`
- `src/db/tier_policy_repository.py`
- `src/services/tier_admin.py` and tier admin error/payload/serialization modules
- `src/api/admin/endpoints/tier_schemas.py`
- `src/api/admin/endpoints/tiers.py`
- `src/config.py` and `config.example.yaml` for the temporary compatibility-enforcement rollout setting
- `src/services/callable_targets.py`
- `src/api/admin/endpoints/callable_targets.py`
- `ui/src/lib/api.ts`
- `ui/src/lib/tiers.ts` and focused new tier workspace helpers
- `ui/src/pages/Tiers.tsx`
- `ui/src/pages/TierDetail.tsx`
- tier-specific list, version rail, pagination, dialog, policy, pricing, and pool components
- tier repository, service, API, migration, UI helper, compatibility-stage, concurrency integration, and browser workflow tests
- `docs/admin-ui/tiers.md`

Avoid broad changes to global admin styling or unrelated `DataTable` consumers unless the shared pagination/dialog primitive is proven compatible by existing-page regression checks.
