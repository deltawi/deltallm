---
title: Managed asset access
description: Ownership, visibility, roles, tier interaction, and migration for user-created assets.
status: accepted
audience: contributors, administrators
---

# Managed asset access

## Implementation status

This document is also the live implementation plan. Status reflects the code on this branch,
not the intended end state.

| Slice | Status | Exit criteria |
| --- | --- | --- |
| Schema and authorization domain | Complete | Additive tables, logical model identity, legacy backfill, typed capability resolver, migration-path verification |
| Named Credential control plane | Implemented and focused-tested | Transactional ownership/grant creation, scoped list/read, Reader/Editor/Owner enforcement, optimistic access updates, redacted responses |
| Logical Model control plane | Implemented and focused-tested | Logical model owns the policy for all deployments; creator writes require an accessible Named Credential and reject inline secrets |
| Model list/detail UI and Named Credential UI | Implemented and build-verified | Show visibility/effective role/capabilities and make actions capability-driven instead of platform-role-driven |
| Prompt Template integration | Implemented and focused-tested | Direct and binding-based runtime authorization, creator CRUD, visibility updates, and capability-driven UI |
| Route Group integration | Implemented and focused-tested | Creator CRUD, sharing, capability-driven UI, runtime visibility, and member/classifier dependency enforcement are active; global binding controls remain platform-admin-only |
| MCP integration | Implemented and focused-tested | Creator CRUD, sharing, capability-driven UI, immutable runtime discovery/invocation authorization, and legacy binding narrowing are active |
| Runtime creator-model authorization | Implemented and focused-tested | Creator policies are compiled into each immutable routing generation and unioned outside tier policy; explicit restrictive scopes still narrow access |
| Rolling-deploy link safety and health | Implemented and migration-verified | Database guards adopt writes from older binaries as platform-managed, bounded reconciliation repairs gaps, and readiness reports missing or mismatched links |
| Review hardening | Implemented and regression-tested | Credential/model audience invariant, creator namespaces, lifecycle cleanup, bounded snapshot compilation, stale-snapshot fail-closed guard, and independent UI saves |
| Second-review fixes | Implemented and focused-tested | Collision responses, administrator audience selectors, immutable model names, membership conflict responses, maximum-length prompt keys, and candidate-bounded model access lookups |
| Unified sharing and access UI | Complete and UI-tested | Model, Model Group, MCP, Prompt, and Named Credential creation and editing use the same collapsible multi-audience grant editor, pinned grants, role controls, summary, validation, retry, and save/discard experience |
| In-form Named Credential creation | Complete and UI-tested | Model create/edit credential selector opens the shared Named Credential dialog, locks the provider, defaults the credential to Private, and selects the new credential after creation |
| Friendly model name and API Model ID split | Complete and regression-tested | Preserve the user-entered display name, assign a stable creator namespace, expose an immutable callable API Model ID, and retain existing callable IDs without client breakage |
| Opaque model credential delegation | Complete and regression-tested | Model audiences can use a bound credential without receiving credential access or metadata; Editors can keep an inaccessible binding or replace it with an authorized credential; credential Owners can revoke; revocation and missing credentials fail closed |
| Production rollout and contract migration | Operationally pending | Observe reconciliation/readiness in production; make links required only in a later approved migration |

The Named Credential slice deliberately leaves the inline-credential inventory and conversion
operations platform-admin-only: both inspect or migrate platform-wide legacy configuration and
are not ordinary asset CRUD.

### Verification log

- 2026-09-26: managed-asset domain, Named Credential API, authentication visibility, and runtime
  scope focused suites passed (66 tests at the checkpoint; the Named Credential endpoint file
  subsequently passed all 16 tests after creator-sharing cases were added).
- 2026-09-26: existing model API, provider preset, repository, runtime configuration, deployment
  service, and infrastructure suites passed (142 tests). Creator-model Owner/Editor/outsider and
  no-inline-secret cases passed in a separate focused suite (2 tests).
- 2026-09-26: creator-model snapshot compilation and model-visibility suites passed (39 tests).
  The snapshot covers Owner, Team, Organization, and Public reads, bypasses organization tiers
  only for creator-governed models, preserves explicit restrictive scope policy, and publishes a
  fail-closed denial if a post-commit refresh cannot reload the changed policy. Model policy
  changes also increment the durable routing revision and publish the existing cross-instance
  governance invalidation signal; revision polling reconciles a missed pub/sub event.
- 2026-09-26: the UI unit suite passed all 256 tests before the model UI slice; the production UI
  build and changed-file ESLint checks passed after the model access controls were added. The
  repository-wide UI lint command still reports unrelated pre-existing findings and is not used
  as evidence for this slice.
- 2026-09-26: the repository-wide non-Redis Python suite passed (5,215 tests; 366 skipped),
  followed by all four cases in `tests/batch/test_chat_fallback_redis.py` against an isolated
  Redis instance. After the final tenant-scoped single-resource query hardening, the directly
  affected database, API, provider, and batch compatibility set passed all 47 tests and
  repository-wide Ruff passed.
- 2026-09-26: Prompt Template runtime authorization was added to the immutable routing
  generation. Explicit references fail before prompt lookup, inaccessible bindings fall through
  to the next eligible binding, and access-policy changes durably invalidate routing state. The
  creator Prompt API and UI now enforce Reader, Editor, Owner, outsider, and platform-admin
  capabilities while keeping global binding management platform-admin-only. The consolidated
  prompt/runtime/bootstrap/auth/migration suite passed all 149 tests; all 256 UI unit tests and
  the production UI build also passed.
- 2026-09-26: Route Group runtime authorization was added to the immutable routing generation.
  A creator group must be visible to the caller and every enabled member must independently be
  allowed by either creator-model policy or the caller's enforced organization tier. The focused
  route-group/model-visibility/runtime/bootstrap/batch suite passed all 142 tests.
- 2026-09-26: Route Group creator CRUD and UI were added with Reader, Editor, Owner, outsider,
  and platform-admin capability boundaries. Creation and deletion keep the asset and policy in
  one transaction when the durable repositories share a database. Member additions reject
  inaccessible creator models; published selector classifiers are included in runtime dependency
  checks. Global callable/prompt binding controls remain platform-admin-only. The consolidated
  Route Group/access/model/prompt compatibility set passed all 141 tests; all 256 UI unit tests
  and the production UI build passed.
- 2026-09-26: MCP creator access was compiled into an immutable in-memory snapshot used by both
  tool discovery and invocation. A legacy MCP binding may narrow a visible creator server's tool
  list, but it cannot grant an outsider access. Creator server CRUD now enforces Reader, Editor,
  Owner, outsider, and platform-admin capabilities; visibility changes invalidate local and peer
  MCP snapshots. Existing platform/organization binding behavior remains compatible. The first
  MCP runtime/bootstrap/invalidation checkpoint passed 56 tests, the existing MCP admin suite
  passed all 20 tests, and the creator role lifecycle test passed. All 256 UI unit tests and the
  production UI build passed after capability-driven MCP controls were added.
- 2026-09-26: rolling-deploy compatibility was added at the PostgreSQL boundary. Inserts and
  updates from an older binary that omits `managed_asset_id` or logical `model_id` are immediately
  adopted as platform-governed, so no temporary creator ownership is guessed. A bounded,
  lock-skipping reconciliation worker repairs pre-existing null links, validates typed links,
  exposes readiness state, and reports orphaned policies without deleting them. Migration-path,
  database-backed, and focused service/health tests passed: all 37 focused Python tests passed,
  and the disposable PostgreSQL verifier passed fresh-install, last-release upgrade, shared-feature
  upgrade, and post-upgrade old-client writes for all five asset kinds.
- 2026-09-26: final branch consolidation passed the full non-external Python suite (5,169 tests;
  442 infrastructure-dependent tests deselected). The eight real-loopback streaming-accounting
  cases also passed when run with localhost socket access. All 256 UI unit tests passed and the
  production UI build succeeded after the Route Group access/settings panel extraction.
- 2026-09-26: Prompt and MCP access-policy create, update, and delete now advance the same durable
  runtime revision used by Model and Route Group authorization. Revision reconciliation reloads
  the MCP snapshot too, so a missed peer invalidation cannot leave stale sharing indefinitely.
  The final managed-asset/config/UI compatibility selection passed all 295 focused Python tests.
- 2026-09-26: the complete-review hardening pass made a Named Credential's audience a live
  dependency of every linked creator model. A model cannot be shared more broadly than its
  credential, and a credential cannot be narrowed while a linked model depends on the removed
  audience. Platform administrators can no longer attach platform deployments to a creator's
  logical model by reusing its name; the rolling-deploy database trigger rejects the same
  implicit capture from older binaries.
- 2026-09-26: Named Credential display names are unique inside a platform or creator namespace,
  and all new creator callable keys receive a stable account-derived namespace. Account deletion
  is blocked while it owns managed assets. Direct team deletion is blocked with a clear message
  while access grants still target it. The explicit organization-deletion workflow removes its
  audience grants, makes affected assets private, detaches credentials that would no longer be
  authorized, increments policy versions, and advances the durable runtime revision.
- 2026-09-26: runtime policy compilation is capped by
  `managed_asset_snapshot_max_policies` (default and maximum 50,000), and resource reads are
  batched in groups of 500. A peer refresh failure starts the configured
  `managed_asset_authorization_max_staleness_seconds` clock (default 60 seconds)
  shared with the published runtime generation; after the bound, creator-asset authorization
  fails closed and readiness reports stale until a successful rebuild. Model, Route Group,
  Prompt, and Named Credential editors now save content and access independently, eliminating
  misleading partial-save failures.
- 2026-09-26: the complete-review fixes passed repository-wide Ruff, whitespace validation,
  all 256 UI unit tests, the production UI build, and focused authorization, bounded-snapshot,
  account/team/organization-deletion, configuration, bootstrap, and API regression suites. The
  broad Python run completed 5,238 passing tests with 369 skips; its only 12 failures required
  unavailable external test facilities (four require `DELTALLM_TEST_REDIS_URL`, and eight require
  localhost socket access denied by the sandbox). Those four Redis cases and the complete
  28-case streaming-accounting file passed when rerun with the required local access. New UI
  access components also pass ESLint in isolation; the repository-wide UI lint command retains
  its pre-existing backlog. The final disposable-PostgreSQL verifier passed clean-install,
  `origin/main` upgrade, shared-feature upgrade, fixture preservation, and compatibility-write
  checks against the finished migration.
- 2026-09-27: the second-review pass fixed all six identified issues. Platform administrators
  now get Team and Organization choices from the administrative catalogs rather than their own
  empty membership snapshot. Logical model names are explicitly immutable in the editor and API.
  Membership removals that would break a creator model's Named Credential access return a clear
  conflict instead of a generic server error. Creator prompt keys now honor the documented
  128-character input limit after namespacing. Model reads query access only for the current
  runtime candidates, and creator-name collisions now return the intended conflict response.
  The production UI build, all 256 UI unit tests, repository-wide Ruff, whitespace validation,
  and 59 directly affected Python tests passed after these changes.
- 2026-09-27: the UI access journey was consolidated into shared fields, summary, and edit-panel
  components used by Models, Model Groups, MCP servers, Prompt Templates, and Named Credentials.
  All creation paths now show the same Private, Team, Organization, and admin-only Public choices,
  validate the audience beside the controls, and offer an audience-load retry. All edit paths show
  current access and use the same explicit Discard and Save access actions. Named Credential settings
  and access are separate saves, and the settings request no longer carries a stale access document.
  The production UI build and all 258 UI unit tests passed; the shared access components and directly
  changed clean UI files passed ESLint. Existing lint debt in the large Prompt and MCP pages remains
  unchanged and is not counted as verification for this slice.
- 2026-09-27: a full Docker startup smoke test exposed that the new raw PostgreSQL access queries
  used the reserved word `grant` as a table alias. The alias was replaced across access lookup and
  organization-cleanup queries, with a repository regression assertion added. The focused 35-test
  managed-asset suite and Ruff passed, migrations completed, the app reached healthy state, and
  browser and API login checks passed against the current worktree image.
- 2026-09-27: Model create/edit gained a `Create new credential` choice inside the Named Credential
  selector. Both the standalone Named Credentials page and the model journey now use the same
  create-dialog and form components. The model journey locks the credential to the selected provider,
  carries across the model audience with the least-privilege Reader role, refreshes the list, and
  selects the newly created credential without losing the in-progress model form. The new interaction
  regression test passed with all 259 UI unit tests; the production UI build, changed-file ESLint,
  and whitespace validation also passed. The local Docker image was rebuilt, returned healthy, and
  the creator-account browser smoke test confirmed the drop-down action, provider-locked shared
  dialog, Private default, and cancellation path at `http://localhost:4002/models/new`.
- 2026-09-27: multi-audience sharing is complete. The requested UI could not be enforced by the
  original single-grant policy, so the policy now supports multiple Team and Organization grants
  with independent Reader or Editor roles, plus an optional admin-only Public Reader grant.
  “Show three” is a UI result-window limit, not a cap on the number of granted audiences. Existing
  grants remain pinned and editable. The same collapsible Sharing & Access component is used for
  create and edit across all five managed asset kinds. Strongest matching access wins, and model
  sharing is rejected unless its Named Credential covers every selected audience. The additive
  migration was applied to the local PostgreSQL service, where a rollback smoke test confirmed one
  asset can store Team and Organization grants together. The production UI build, all 259 UI unit
  tests, changed-file ESLint, Ruff, and whitespace validation passed. The focused 158-test backend
  set passed after a deterministic Team/Organization/Public grant-order regression was corrected.
  The rebuilt Docker application reached healthy state and the creator browser journey confirmed
  collapsed Private default, three audience tabs, three-result search window, pinned editable grants,
  organization selection, and restricted Public access.
- 2026-09-27: the friendly model name and callable API Model ID are now separate. New creator
  models use `<creator-namespace>/<model-slug>`, where the namespace is suggested from the account
  email label plus a stable short discriminator, can be changed before first use, and is then
  locked for that account. The API Model ID is visible before creation and immutable afterward;
  the friendly name is stored independently and changes only through an explicit edit. Existing
  `creator-<account-hash>-...` callable IDs remain canonical so deployed clients and saved requests
  do not break, while their friendly names are backfilled without the generated prefix. The schema
  and namespace-invariant migrations applied successfully in Docker and the application reached
  healthy state. The focused backend compatibility sets passed (including the 140-test model set),
  all 261 UI unit tests passed, and the production UI build, Prisma schema validation, Ruff, and
  whitespace validation passed. Browser verification covered both administrator and creator forms:
  friendly-name slug generation, a short namespaced creator ID, and preservation of manual API ID
  edits when the friendly name changes.
- 2026-09-27: planning analysis for model/credential decoupling found the current audience rule in
  the service layer, managed-access mutation path, organization cleanup, and deferred PostgreSQL
  triggers. It also found that model control responses are assembled from the secret-resolved
  runtime registry: secret values are masked, but credential identifiers and non-secret connection
  metadata are still exposed to every model reader. Finally, a creator deployment whose credential
  is detached can currently pass through platform connection defaults during runtime construction.
  The planned correction below replaces audience equality with an explicit credential binding,
  makes the control-plane representation principal-aware, and makes missing or revoked creator
  bindings fail closed. This entry records analysis only; no behavior was changed in this step.
- 2026-09-27: opaque model credential delegation was implemented with durable binding mode/state,
  conservative legacy-write adoption, owner-delegated and audience-scoped authorization, and a
  replacement deferred PostgreSQL invariant. Private credential metadata is now principal-filtered
  from model responses, model settings are read from unresolved persisted records, and an Editor can
  keep an opaque binding or replace it with a credential they own. Creator deployments with revoked,
  missing, or unresolved credentials are excluded from routing and cannot inherit platform defaults;
  persisted unroutable models remain visible in the control plane for repair. In-form credentials now
  default to Private, the provider stays locked while retaining an opaque binding, and the shared form
  explains the owner-managed state. Prisma schema validation, repository-wide Ruff, whitespace
  validation, the production UI build, all 262 UI unit tests, and 68 focused backend tests passed.
  The full forward migration chain applied to a fresh PostgreSQL 16 database. Database smoke tests
  proved same-owner delegation and conservative older-writer adoption, proved clearing the live
  reference transitions the binding to revoked, and proved an audience-scoped binding is rejected
  when its credential does not cover the model audience.
- 2026-09-27: explicit credential-owner revocation was added to the Model detail journey. It uses an
  exact compare-and-revoke write, advances the routing revision, refreshes the runtime fail-closed,
  leaves the model visible for repair, and requires an authorized replacement before further edits.
  Non-owners receive no binding-existence disclosure. The focused credential/model/runtime/repository
  set passed all 52 tests, all 263 UI unit tests passed, the production UI build succeeded, and Ruff
  passed. The full disposable-PostgreSQL verifier then passed fresh installation, `origin/main`
  upgrade, shared-feature upgrade, fixture preservation, and older-client compatibility writes.
  A final ownership-index correction makes revocation depend on the credential's current Owner,
  including audience-scoped bindings, rather than the account that originally attached it. The
  consolidated managed-access, runtime, model API, migration, and repository set passed all 82 tests.
- 2026-09-27: the final worktree image rebuilt successfully and its PostgreSQL migration chain
  completed in an isolated Docker stack. Health reported database, Redis, routing runtime,
  managed-asset links, and creator authorization ready. A real private Named Credential was bound
  to a model shared with separate Editor and Reader teams. Both principals could read the model
  while credential id, name, connection summary, and credential-list entry remained hidden. The
  Editor changed a non-connection setting while keeping the opaque binding; the Reader had no edit
  action. The credential Owner revoked the exact binding, the Editor then received a replacement-
  required conflict, and Owner repair restored routing without changing the model audience. Browser
  verification confirmed the Owner revoke action, the Editor's owner-managed keep/replace journey,
  the Reader-only view, and the common collapsed Private sharing and in-form credential UI.
- 2026-09-27: post-review remediation made tiers the only availability authority for platform
  models. Their create, edit, and detail journeys now describe tier-managed availability instead of
  showing the creator sharing editor, and both initial creation and later-deployment API paths reject
  non-empty platform-model grants. The creator authorization snapshot ignores platform-model grants,
  including stale Public grants, so they cannot widen or narrow tier access. The same pass made
  named-to-inline credential clearing explicit, repaired stale platform binding metadata, replaced
  the global credential-audience validation lock with affected-deployment checks, bounded audience
  search on the server, restored shared in-form credential creation, aligned API Model ID length
  validation, and completed keyboard navigation for sharing tabs. A fresh PostgreSQL database applied
  all 97 migrations and the repair fixture passed. The full backend run completed with 5,283 passes
  and only the four explicitly configured Redis cases failing without their URL; those four then
  passed against an isolated Redis instance. All 266 UI unit tests, the production UI build, Ruff,
  changed-file ESLint, Prisma validation, and whitespace checks passed.
- 2026-09-30: PR CI remediation removed an unmatched parenthesis from the organization-deletion
  asset-grant cleanup query. The original PostgreSQL error left the deletion job pending; the later
  test cleanup then surfaced the misleading `organization still has referenced teams` guard error.
  The exact regression, all nine organization-deletion integration tests, and the complete CI
  PostgreSQL selection passed afterward (323 tests; 5,355 deselected). The OpenAPI and complete
  General Settings generated references were refreshed with the CI Python 3.12 environment. All
  generated-reference checks, documentation health checks, nine documentation tests, strict MkDocs
  build, public-artifact containment verification, Ruff, and whitespace validation passed.

### Remaining implementation sequence

The five-asset access model and model credential delegation are implemented. The remaining work is
operational rollout and a later, separately approved contract cleanup:

1. [Done] Add the forward-only binding metadata migration and fail-closed runtime handling described
   below. Neither already-applied managed-asset migration was edited.
2. [Done] Model create/edit/access APIs, principal-aware serialization, lifecycle handling, the
   shared model form, and explicit credential-owner revocation are binding-aware. Existing request
   fields remain supported as a compatibility contract.
3. [Done] Service, API, database-trigger, migration-path, runtime, UI, Docker, and multi-persona
   browser verification passed before the slice was marked complete.
4. [Pending] Deploy with delegation creation and revocation feature-gated, wait until every application
   instance reports binding-aware readiness, then enable the new behavior. Retain compatibility
   handling for older writes throughout the rolling-deploy window.
5. [Pending] Make managed-asset links and binding metadata required only in a later contract migration after
   reconciliation is clean and every supported application version understands both. Removing the
   compatibility path requires explicit approval before implementation.

The platform-admin-only legacy `Owner Scope` metadata control is intentionally still present where
it existed before this work. It overlaps conceptually with Organization visibility, but removing it
could change compatibility behavior and requires a separate, explicit approval.

## Decision

PostgreSQL owns one access policy for models, route groups, MCP servers, prompt templates,
and named credentials. Each asset has a managed-asset record with an immutable asset kind,
a governance source, an optional owner account, a monotonic policy version, and lifecycle
state. A creator-governed asset must have an owner account. Platform bootstrap and legacy
assets may have no human owner.

The creator is the implicit `owner`; Owner is never stored as a grant. An asset can have multiple
audience grants:

| Visibility | Stored audience | Allowed role |
| --- | --- | --- |
| Private | no grants | creator is Owner |
| Team | one or more teams | Reader or Editor per team |
| Organization | one or more organizations | Reader or Editor per organization |
| Public | one platform-wide grant | Reader only; only a platform admin may set it |

Team, Organization, and Public grants may coexist. When more than one matching grant applies to a
principal, the most capable role wins (`Editor` over `Reader`). Public therefore provides a
read-only baseline without preventing a specifically granted team or organization from editing.
Duplicate grants for the same audience are rejected by the database and service layer.

Reader permits safe inspection and use/reference. Named credential reads remain redacted;
Reader permits attaching the credential to an allowed asset, never reading secret material.
Editor adds asset mutation. Only the Owner may change the audience, delete, or transfer the
asset. A platform administrator has an audited operational override but does not become the
Owner. Account deletion must transfer or archive creator-governed assets before the account
row can be removed.

`DeltaLLM_ManagedAsset` and `DeltaLLM_AssetGrant` are the durable policy source. The shared
typed resolver in `src/services/managed_asset_access.py` is the only role-to-capability owner.
HTTP handlers will authenticate and pass a typed principal to an application service;
repositories will apply the same account/team/organization scope in their query. UI gating
is informative only.

Single-resource access reads follow that rule too: the resource id and principal visibility
predicate are evaluated in the same SQL statement. In particular, a creator's Named Credential
policy is authorized before its credential row is loaded, and inaccessible resources return the
same not-found/invalid response as nonexistent resources.

### Planned correction: opaque model credential delegation

Model access and Named Credential access are separate authorities. A model audience is allowed to
invoke the model through a server-side credential binding; it is not thereby allowed to discover,
select, reuse, or edit that credential. Sharing a model must never expand the Named Credential's
Team, Organization, or Public grants.

The target role behavior is:

| Model capability | Bound credential behavior |
| --- | --- |
| Reader | May inspect safe model metadata and invoke the model. Receives no private credential id, name, endpoint, region, auth-header metadata, or reusable reference. |
| Editor | May retain an opaque existing binding while changing non-connection model settings. May replace it only with a credential the Editor is authorized to bind. |
| Owner | Manages model access and deletion, but does not gain management rights over a credential owned by someone else. |
| Credential Owner | May explicitly delegate the credential to a model the Owner can edit and may later revoke that binding. |
| Platform administrator | Has an audited break-glass binding override; Public model access remains administrator-only and requires explicit confirmation and capacity controls. |

Reader model access is delegated consumption: calls still spend against the bound provider
credential even though its identity and secret remain private. Sharing UI must say that clearly and
the existing account, key, team, organization, model, and provider limits continue to apply.

#### Durable binding invariant

Keep `deltallm_modeldeployment.named_credential_id` as the single credential reference and add
binding metadata to that deployment instead of creating a second competing source of truth:

- binding mode: `audience_scoped`, `owner_delegated`, or `platform_override`;
- binding state: `active` or `revoked`;
- authorizing account, authorization timestamp, and revocation timestamp.

An `owner_delegated` binding may be created only by the Named Credential's implicit Owner. It is
independent of the model's audience, so it supports both the model creator's private credential and
an Editor explicitly contributing their own credential. An `audience_scoped` binding preserves the
existing behavior for a credential that the actor can merely read: every model audience must still
be covered by that credential's grants. This prevents a user from turning a team-shared credential
into a proxy for unrelated teams. Platform override is separately recorded and audited.

A new forward migration will backfill existing creator deployments as `owner_delegated` when the
model and credential have the same Owner, and as `audience_scoped` otherwise. Existing platform and
inline deployments retain their behavior. A replacement deferred trigger will serialize concurrent
model-access, credential-access, membership, and attachment changes, but enforce audience coverage
only for `audience_scoped` bindings. An old binary changing a credential reference is adopted safely
as `audience_scoped` unless same-owner delegation can be proven from durable rows. Existing applied
migrations remain byte-for-byte unchanged.

Revocation sets the binding to `revoked`, removes the live credential reference transactionally,
advances the routing revision, and leaves the model visible but unavailable until an authorized
Editor supplies a replacement. Credential deletion remains blocked until active bindings are
explicitly revoked, so deletion never silently reassigns provider authority.

#### Control-plane contract

Model update will distinguish credential intent rather than requiring the full credential id on
every save:

- omitted credential action means `keep` on update;
- the existing unchanged `named_credential_id` remains accepted as `keep` for older clients;
- a different id means `replace` and runs binding authorization plus provider compatibility checks;
- creator models cannot clear a binding into an implicit platform credential fallback.

Keeping an opaque credential does not require credential access. While keeping it, an Editor may
change the display name, upstream model for the same provider, routing, limits, pricing, capacity,
capabilities, and default parameters. Provider, API base, region, API version, authentication header,
and other connection fields remain locked. Changing provider requires an authorized replacement
credential. Live provider-model discovery continues to require direct credential access and is not
performed through an opaque binding.

Create and replace operations resolve the credential policy in the same transaction as the model
write. Credential Owner creates `owner_delegated`; platform administrator creates
`platform_override`; a non-owner with Reader or Editor credential access creates
`audience_scoped` and remains subject to the existing audience coverage rule.

Model list and detail responses will no longer serialize the secret-resolved runtime registry.
They will use persisted model parameters plus a batched credential-access index. A principal with
separate credential access may receive the redacted credential name and connection summary. Everyone
else receives only an opaque state such as `Owner-managed credential` and whether replacement is
required. Inaccessible credential ids, names, API bases, regions, API versions, auth headers, and
usage counts are omitted. Existing nullable response fields remain during compatibility but are
`null` when the binding is opaque.

#### Runtime and lifecycle behavior

Runtime construction must know the logical model's governance source and binding state. A creator
deployment with a missing, revoked, unresolved, or inactive credential is excluded from the runtime
candidate set and reported unhealthy/readiness-degraded; it must never call
`resolve_provider_connection_defaults` with platform credentials. Platform and legacy bootstrap
models retain their current explicit fallback behavior.

Credential rotation keeps the binding and reloads runtime as it does today. Credential access
narrowing ignores `owner_delegated` bindings; affected `audience_scoped` bindings retain the current
coverage guard until an explicit revoke path is selected. Organization deletion must preserve valid
owner-delegated bindings and transition invalid scoped bindings to `revoked` instead of merely
nulling the credential reference. Credential and model Owners can see dependency state, while model
readers cannot infer private credential metadata.

The feature is enabled only after binding-aware readiness confirms that every serving instance will
fail closed on a revoked binding. This prevents an older runtime from interpreting a removed creator
credential as permission to use platform defaults during a rolling deployment.

#### UI changes

- Creating a Named Credential inside the model form defaults it to Private; model audiences are no
  longer copied into credential grants.
- An Editor without credential access sees `Owner-managed credential (unchanged)` plus `Replace with
  one I can use`, not the hidden credential in the selector.
- Keeping the opaque binding permits safe model edits and locks provider/connection controls.
- Replacing it uses the existing accessible-credential selector and shared create dialog. A newly
  created private credential is automatically owner-delegated to the model after explicit consent.
- Model sharing no longer asks the Owner to share an owner-delegated credential. For an
  audience-scoped binding, the UI explains the narrower compatibility rule and offers replacement.
- The sharing summary warns that Readers can invoke the model and generate provider spend while the
  credential remains private.

#### Acceptance and regression matrix

- [x] A model Owner can share a model as Reader or Editor with multiple Teams and Organizations while
      keeping an owner-delegated credential Private.
- [x] A Reader can list, inspect, and invoke the model but cannot discover the credential through any
      model, credential, audit, health, or provider-discovery response.
- [x] An Editor can save every documented non-connection field with an opaque binding unchanged.
- [x] An Editor cannot change provider or connection fields while keeping an opaque binding, cannot
      attach a guessed credential id, and can replace only with an authorized compatible credential.
- [x] A credential Reader cannot broaden its use beyond covered audiences; its Owner can explicitly
      delegate it to a model and later revoke the binding.
- [x] Revoked, deleted, unresolved, or missing creator credentials remove the deployment from runtime
      without falling back to platform credentials across chat, embedding, image, audio, rerank, batch,
      route-group, and health-check paths.
- [x] Concurrent sharing, replacement, credential narrowing, membership removal, and revocation leave
      a valid state under deferred PostgreSQL checks and optimistic policy versions.
- [x] Fresh install, upgrade from `origin/main`, upgrade from this branch's current migrations, and
      old-writer rolling-deploy fixtures preserve existing ids, policies, deployments, and secrets.
- [x] Focused Python and UI suites, the complete relevant regression sets, production UI build, Ruff,
      whitespace checks, Docker startup/migrations, and Owner/Editor/Reader browser journeys pass.

## Models and tiers

Models have a logical `DeltaLLM_Model` identity above deployments because multiple provider
deployments can implement one runtime model name. Access belongs to the logical model, not an
individual deployment.

Platform-created models retain `governance_source=platform`. Organization tiers select only
these platform models. A regular user's model uses `governance_source=creator`, is outside the
tier allowlist, and is controlled by its Owner and audience grant. Runtime candidates are:

`tier-allowed platform models UNION creator models visible to the principal`

Existing organization, team, key, and runtime-user restrictions then narrow that union. They
never expand it. The tier editor must not offer creator models. Creator-owned route groups may
initially reference only creator models whose audience is compatible with the group audience;
mixed platform/creator groups require member-level runtime authorization before they can be
enabled.

The authentication snapshot must carry `owner_account_id` in addition to user, key, team, and
organization scopes. Runtime policy compilation will load creator grants in a bounded snapshot
off the request path. Request authorization performs no per-request SQL. A missing, stale past
its declared bound, malformed, or unavailable security snapshot fails closed for creator
assets. Tier failure behavior remains unchanged for the independent platform-model branch.

## Security boundaries

- Non-admin asset creation must use named credential references. Inline provider secrets are
  not accepted from regular users.
- Named credential secret values stay write-only and encrypted or referenced through the
  existing secret resolver. Access responses expose only redacted configuration state.
- User-controlled model and MCP endpoints use the shared outbound URL resolution,
  rebinding, redirect, TLS, timeout, and private-network policy at actual execution time.
- Creator-supplied pricing is not trusted for billing. Global, organization, team, key, and
  account budgets still apply to creator models.
- Every policy mutation is transactional with the asset write, increments `policy_version`,
  emits a redacted audit event, and durably invalidates the compiled runtime snapshot before
  reporting full success.

## Failure and capacity behavior

Control-plane reads use bounded, tenant-scoped queries. Snapshot compilation has a configured
policy ceiling (default and maximum 50,000) and batches resource joins in groups of 500; list endpoints that already expose
pagination retain their cursor/limit contract. Mutations lock
one managed-asset row, check the expected policy version, update the asset and audience grant
in one short transaction, and enqueue invalidation/audit in that transaction. They perform no
external network work while holding locks.

The data plane receives an immutable compiled snapshot. The target budget after integration is
zero additional PostgreSQL calls and zero additional Redis calls per inference request. The
snapshot is bounded by the configured policy safety ceiling; compilation batches reads, rejects
oversized snapshots, and keeps last-known-good state for no more than the configured maximum
staleness after a failed refresh. Revocation uses the durable routing revision plus peer
invalidation rather than TTL alone.

## Migration and compatibility

The rollout is expand, migrate, enforce, then contract:

1. Add managed-asset, grant, and logical-model tables plus nullable links. Backfill all existing
   records as platform-governed so the release does not change current authorization.
2. Migrate create/update/delete paths one asset kind at a time. New regular-user assets are
   creator-governed; platform-admin and bootstrap behavior remains explicit.
3. Reconcile rows created by an older application during a rolling deployment and verify every
   compatibility triggers so an old binary's write is adopted immediately as platform-governed;
   a bounded background reconciler is defense in depth and publishes readiness health. Multiple
   new instances use row locks with `SKIP LOCKED`, so repair work does not duplicate or block the
   fleet. Link-type mismatches fail readiness and require operator review rather than automatic
   reassignment. Orphaned policies are reported but never deleted automatically.
4. Compile runtime creator access and switch reads/writes to the shared authorization service.
   Existing callable-target and tier shadow modes continue to compare the overlapping legacy
   restrictions. Creator ownership itself has no equivalent legacy allow decision: the legacy
   behavior was platform-admin-only, so link readiness and fail-closed snapshots are the rollout
   gates for the new creator-only branch.
5. Consider removing metadata `owner_scope` compatibility and make managed-asset links required
   only in a later contract migration after all supported versions write the new form. Because
   that would remove legacy behavior, it requires explicit user approval before implementation.

The additive foundation permits application rollback without data rollback before non-admin users
create assets: older application versions ignore the new tables and nullable columns. After creator
assets exist, do not roll back to a binary that does not understand managed ownership, because it
cannot enforce those creator policies. Forward-fix with a managed-asset-aware binary, and retain the
rows for recovery; do not delete grants or ownership. The compatibility triggers are intentionally
retained through the rolling-deploy window. Removing them is a later legacy cleanup and, like
removing `owner_scope`, requires explicit user approval.

## Post-review remediation plan

The 2026-09-27 review found seven issues that must be resolved before this work is ready to merge.
The fixes are ordered by authorization correctness, database safety, and then shared UI quality.

### 1. Keep platform-model access exclusively tier-controlled

- [x] Hide the managed `Sharing & Access` editor for platform-governed models on create and edit.
- [x] Replace it with a clear message that platform-model invocation is controlled through tiers.
- [x] Reject non-empty managed grants for platform models in both model creation and the generic
      managed-access mutation endpoint, so direct API callers cannot persist an ineffective policy.
- [x] Keep creator-model grants outside tiers and preserve the current union:
      `tier platform models UNION visible creator models`.
- [x] Add API and runtime tests proving that platform grants cannot widen or narrow tier access, while
      creator grants remain authoritative.
- [ ] Inventory any existing grants attached to platform-model assets. Do not delete those rows until
      their count and origin have been reviewed and explicit approval is given for a cleanup migration.

### 2. Make named-to-inline credential changes explicit and repair stale state

- [x] Replace nullable-as-omitted repository arguments with an explicit update contract, such as an
      `UNSET` sentinel or credential-binding operation, so `keep`, `replace`, `clear`, and `revoke`
      cannot be confused.
- [x] When a platform model changes to inline credentials, atomically clear the named credential id,
      binding mode/state, bound-by account, and binding timestamps while saving the inline fields.
- [x] Preserve the separate creator revocation state: creator models remain visibly revoked and
      unroutable until an editor chooses a replacement named credential.
- [x] Add an append-only repair migration for platform deployments with no named credential but stale
      binding metadata. The migration must not rewrite creator revocation records.
- [x] Add repository, API, runtime reload, and UI form regressions for named -> inline -> read -> edit,
      plus creator revoke -> read -> replace.

### 3. Replace the global credential-audience invariant lock with scoped validation

- [x] Split the single full-catalog trigger function into mutation-specific validators for deployment,
      managed asset, grant, membership, and team-organization changes.
- [x] Derive the affected deployment ids from `OLD` and `NEW`, lock only those rows in deterministic
      order, and validate only their model/credential pairs.
- [x] Remove the global advisory lock. If advisory locks remain necessary, key them by the affected
      model or credential rather than one installation-wide constant.
- [x] Confirm indexes cover named credential id, managed asset id, grant subjects, owner account,
      memberships, and team organization lookups before shipping the new query paths.
- [ ] Add real PostgreSQL tests proving unrelated policy changes commit concurrently, conflicting
      changes cannot violate the invariant, and multi-row membership/grant changes remain correct.
- [ ] Compare query plans and transaction duration against a realistically sized catalog.

### 4. Move audience search to a bounded server-side contract

- [x] Add a tenant-aware audience search endpoint with subject type, search text, and a hard bounded
      limit. The UI intentionally refines search instead of paging beyond three results. Platform
      admins search the global catalog; owners search only their team and organization memberships.
- [x] Add a bounded lookup for already-selected ids so saved grants stay pinned even when they are not
      in the current search result.
- [x] Refactor the shared access component/hook to debounce search, abort stale requests, and render at
      most three matches without downloading every team and organization.
- [ ] Test stale-response rejection, loading/error/empty states, pinned selections, authorization
      scoping, and catalogs larger than one server page.

### 5. Restore credential creation from the model form before provider selection

- [x] Always render `Create new credential` in the named-credential selector.
- [x] If the model provider is unset, open the shared credential form with provider selection enabled;
      after creation, apply that provider and the new credential to the model form.
- [x] If the model provider is already set, keep the credential provider locked to it.
- [x] Reuse `NamedCredentialCreateDialog` and `NamedCredentialForm`; do not introduce a second form.
- [ ] Test both entry paths, cancellation, creation failure, provider propagation, and automatic
      selection of the newly created credential.

### 6. Align creator-namespace validation across UI, API, and database

- [x] Enforce an explicit 3-32 character length before the character-pattern check in both TypeScript
      and Python; do not rely on the current optional regex group.
- [x] Keep the database constraint as the final invariant and return a 400 validation response before
      attempting to claim an invalid namespace.
- [x] Add boundary tests for lengths 1, 2, 3, 32, and 33, along with leading/trailing and duplicate
      hyphens. Include a direct API test, not only a form test.

### 7. Complete the keyboard-accessible sharing tabs

- [x] Give each tab and panel stable ids with `aria-controls` and `aria-labelledby`.
- [x] Implement roving `tabIndex`, Left/Right arrow navigation, Home/End, focus movement, and activation
      using the shared component.
- [x] Preserve mouse/touch behavior and reset search consistently when tabs change.
- [x] Add DOM tests for focus order, keyboard activation, selected state, and the associated panel.

### Verification and rollout gates

- [x] Run the complete backend suite, complete UI suite, production UI build, Ruff, changed-file
      ESLint, fresh-database migration verification, Prisma validation, and whitespace checks. The
      repository-wide ESLint command still has its documented pre-existing backlog.
- [x] Apply every migration to a fresh PostgreSQL database and verify the stale platform credential
      repair with a representative bound deployment.
- [ ] Rehearse the migration on an upgrade database containing representative platform models,
      creator models, delegated credentials, revoked bindings, and multi-audience grants.
- [ ] Run the PostgreSQL concurrency tests under Docker rather than relying on repository fakes.
- [ ] Browser-test platform admin, creator Owner, Editor, and Reader journeys for all five asset kinds.
- [ ] Confirm tier-controlled platform models and creator-controlled models in actual inference calls,
      not only control-plane responses.
- [ ] Do not remove legacy callable ids, compatibility triggers, `owner_scope`, or existing platform
      model grant rows without a separate inventory and explicit approval.

## Follow-up code-review remediation plan (2026-09-27)

The latest review found two high-priority authorization/UI correctness issues and four medium-priority
consistency issues. This plan closes those findings without deleting legacy records or changing the
agreed product boundary between tiers and creator-managed sharing.

**Implementation status (2026-09-27):** the six code fixes are implemented. The complete backend
suite excluding its separately configured Redis module passes with 5,287 tests passed and 365
expected skips; the four Redis-only tests also pass against the local Docker Redis service. The
managed-asset API regression set (191 tests), all 269 UI unit tests, the production UI build,
changed-file Ruff/ESLint, whitespace checks, and the PostgreSQL fresh/upgrade migration rehearsal
also pass. After the full-suite run, selector discovery and direct route-group model references were
hardened to reuse the same scoped catalog; the final 94-test model/route-group regression set and
changed-file Ruff checks pass. The unchecked acceptance items below remain the explicit follow-up
matrix rather than being inferred from narrower coverage.

### Product and security invariants

The implementation must preserve these rules throughout the remediation:

1. A **platform model** is published for use through tier policy. A tier grants use/read access; it
   never grants edit, health-operation, sharing, or ownership rights over the platform model.
2. Only a platform administrator may create, change, operate, or retire a platform model.
3. A **creator model** stays outside tier policy. Its Owner controls Reader and Editor grants for
   teams and organizations through the managed-access policy.
4. A shared model may use its owner's private named credential without revealing that credential.
   Readers invoke the model through the gateway; Editors may retain the existing binding or replace
   it with a credential they are allowed to use.
5. Existing platform-model grant rows, compatibility triggers, legacy callable ids, and
   `owner_scope` metadata remain in place until separately inventoried. No cleanup or deletion is
   authorized by this plan.

### Delivery order

Implement the fixes in the following order so that authorization holes close before user-experience
work lands:

1. Block stale managed grants from changing platform models.
2. Make platform-model discovery follow effective tier assignments.
3. Fix the named-credential edit race.
4. Surface managed-access refresh warnings in the shared UI.
5. Harden the rolling-upgrade credential trigger with a forward migration.
6. Validate every team and organization grant target before writing policy.
7. Run the focused matrix, full suites, migration rehearsals, and browser journeys.

### 1. Enforce platform-model control-plane authority (P1)

**Problem:** historical managed grants can still satisfy the generic model capability check. A user
holding a stale Editor grant can therefore update a platform model or run a model health operation,
even though new platform grants are rejected and runtime inference ignores them.

**Implementation:**

- [x] Introduce one model-operation authorization helper in
      `src/api/admin/endpoints/models.py` that first reads the model's governance source.
- [x] For `platform` governance, require platform-admin authority before evaluating any managed
      capability. Apply this to update, delete, health check, credential changes, reload/enable
      operations, and any equivalent mutation discovered during the endpoint audit.
- [x] For `creator` governance, continue using managed Owner/Editor capability checks. Keep Reader
      limited to read and invocation paths.
- [x] Keep platform-model access-policy endpoints read-only/empty for non-admins, and continue
      rejecting non-empty platform grants from direct API callers.
- [x] Return the project's standard non-disclosing denial (`404` where that is the existing
      convention) so a denied caller cannot use mutation endpoints to enumerate models.
- [ ] Emit the existing authorization audit event for denied platform mutations, including action
      and actor but no secret or credential material.
- [x] Audit all call sites of the generic model capability helper so none can accidentally authorize
      a platform mutation from an old grant.

**Tests and acceptance:**

- [x] Seed a platform model with a historical Team Editor grant and prove the member cannot update,
      delete, change credentials, or run health operations.
- [ ] Prove the same user may invoke/read the platform model only when an effective tier permits it.
- [ ] Prove a platform administrator can still perform every supported operation.
- [ ] Prove creator-model Owner, Editor, and Reader behavior is unchanged.
- [x] Do not delete the historical grant used by the test; the code must safely ignore it.

### 2. Apply tier policy to platform-model discovery (P2)

**Problem:** the managed-asset access index currently treats every platform model as visible to every
authenticated control-plane user. Runtime inference still enforces tiers, but model lists, details,
and route-group candidates can disclose or offer models that the user cannot invoke.

**Implementation:**

- [ ] Add a single account-context catalog service for model discovery rather than reproducing tier
      logic in individual endpoints.
- [x] Resolve all active organization memberships for the signed-in account, then resolve the
      effective tier assignment for each organization. The visible platform set is the union of the
      models allowed by those tiers.
- [x] Build the final catalog as:
      `tier-visible platform models UNION managed-access-visible creator models`.
- [x] Keep platform administrators able to see the complete platform catalog for administration,
      independent of tier assignments. This does not grant ordinary users administrative rights.
- [x] Make platform models with no effective tier invisible to ordinary users in list, detail,
      selector, and route-group candidate endpoints. An account with no applicable tier sees only
      creator models allowed by managed access.
- [x] Reuse the same resolved catalog for model lists, model details, route-group model candidates,
      and other model selectors so the UI cannot offer a choice that runtime later denies.
- [x] Batch-load membership/tier/model data once per request; do not add a per-model tier query.
- [x] Fail closed for non-admins if tier resolution is unavailable. Return the established service
      error, or a clearly degraded creator-only catalog if that is already the service convention;
      never fall back to showing every platform model.
- [x] Remove the unconditional `governance_source = 'platform'` visibility shortcut from the managed
      access index, or isolate it so it is used only after the tier-visible platform ids are known.
- [x] Document that a tier gives use/read access only. Platform model edits remain admin-only under
      remediation item 1.

**Tests and acceptance:**

- [x] Test one account in multiple organizations with different tiers; the platform-model result is
      the union of both effective allowlists with no duplicates.
- [ ] Test no tier, one tier, changed tier, inactive membership, and platform-admin cases.
- [ ] Test creator models alongside platform models to prove managed grants remain outside tiers.
- [ ] Test list, detail, route-group candidate, and actual inference results against the same matrix.
- [x] Test tier-service/database failure and confirm it never widens platform visibility.

### 3. Make named-credential editing request-identity safe (P1)

**Problem:** the API hook retains the previous response while a newly selected credential loads. When
the user quickly opens credential A and then credential B, the shared form can briefly receive A's
details under B's identity, reset typed fields when B arrives, or submit A's non-secret settings to B.

**Implementation:**

- [x] In `ui/src/pages/NamedCredentials.tsx`, pass detail data to the shared form only when
      `detail.credential_id === selectedCredential.credential_id`.
- [x] Key the editor by credential id so switching credentials creates an isolated form instance.
- [x] Show an explicit loading state and disable settings submission until matching detail data has
      loaded. On failure, show an error with retry rather than falling back to another credential's
      list-row data.
- [x] Initialise non-secret settings exactly once for each credential id. A later access-policy
      refresh must not reset unsaved provider/settings edits.
- [x] Keep secret fields write-only and empty on edit; no response or fallback object may populate a
      stored secret.
- [x] Audit other detail editors using the same retained-data hook. Fix only identity-confusion cases;
      do not broadly change hook semantics without verifying every consumer.

**Tests and acceptance:**

- [ ] Add a deferred-response test for A -> B where A resolves after B is selected.
- [ ] Add a test for typing before the matching detail arrives and verify submission is blocked rather
      than sent to the wrong credential.
- [ ] Add tests for load failure/retry, switching back to A, and access-policy refresh while settings
      are dirty.
- [ ] Prove both the Named Credentials page and model form continue using the same create/edit form
      components.

### 4. Preserve refresh warnings in the shared access UI (P2)

**Problem:** the backend commits access changes and can return warnings when local authorization
reload fails closed or a peer refresh is delayed. The UI response type drops those warnings and always
shows an unconditional success message.

**Implementation:**

- [x] Change `ui/src/lib/api/managedAssets.ts` to type the response as
      `{ access: ManagedAssetAccess; warnings?: string[] }`.
- [x] Update `ManagedAssetAccessPanel` to retain the successful saved state while showing a
      saved-with-warning outcome when warnings are present.
- [x] Use the existing shared mutation-outcome presentation used by model creation instead of
      introducing a second warning style.
- [x] Explain warnings in plain language: the policy was saved, but access may be temporarily
      unavailable on this instance or delayed on another instance. Do not imply the save rolled back.
- [x] Keep the panel open and preserve the returned grants so the user can verify what was committed.
- [x] Make the callback contract return or receive the full mutation outcome if consumers need to
      coordinate a refetch; do not silently discard warning metadata at another layer.

**Tests and acceptance:**

- [ ] Test clean success, local fail-closed warning, peer-refresh warning, multiple warnings, and hard
      request failure.
- [ ] Confirm warnings are announced accessibly and do not replace the committed policy with stale
      client state.
- [x] Confirm all five asset editors get the behavior through the shared component.

### 5. Harden named-to-inline rolling-upgrade compatibility (P2)

**Problem:** after the existing one-time repair migration runs, an older application instance can
still clear `named_credential_id` without clearing the newer binding fields. The compatibility trigger
currently returns early for platform models and can recreate stale binding metadata.

**Implementation:**

- [x] Add a new forward-only migration that replaces the compatibility trigger function. Do not edit
      already-created migration files or remove the trigger.
- [x] When an old writer leaves a platform model with `named_credential_id IS NULL`, clear binding
      mode, state, bound-by account, bound timestamp, and revoked timestamp before returning.
- [x] Keep creator behavior distinct: removing a creator model's named credential records a revoked,
      unroutable binding until an authorized Editor/Owner selects a replacement.
- [x] Include an idempotent data repair in the new migration for platform rows recreated in the stale
      state after the earlier repair migration.
- [x] Confirm the reverse compatibility path still populates the expected legacy fields for new
      writers during the supported rolling-deploy window.
- [x] Do not remove the compatibility trigger after this change. Its eventual removal is a separate
      legacy cleanup that requires explicit approval.

**Tests and acceptance:**

- [x] Apply all migrations, then simulate an old writer changing a platform deployment from named to
      inline credentials. Assert every binding field is cleared and the deployment remains routable.
- [x] Run the equivalent creator-model transition and assert it becomes revoked and unroutable.
- [x] Test migration from a representative upgrade database, not only a fresh schema.
- [ ] Run the migration twice through the project's rehearsal mechanism and confirm the repair logic
      is safe/idempotent where the migration framework permits.

### 6. Validate grant audience ids before policy writes (P2)

**Problem:** platform administrators bypass membership-scoping validation, so nonexistent team or
organization ids reach the database and fail as foreign-key errors. Those failures currently surface
as generic server errors instead of deterministic client validation responses.

**Implementation:**

- [x] Add a batched repository lookup that resolves every distinct Team and Organization subject id
      in a proposed policy.
- [x] Run authority validation first. For ordinary owners, keep inaccessible subjects indistinguishable
      from unauthorized subjects; do not reveal whether an out-of-scope id exists.
- [x] After authority validation, verify existence before create or replace for all asset types,
      including platform-admin requests.
- [x] Raise a dedicated managed-access audience validation error containing only safe subject/type
      information, and map it consistently to `400 Bad Request` (or the project's established
      validation status) in generic and asset-specific endpoints.
- [x] Retain database foreign keys as the race-condition backstop. Translate a matching race-time
      foreign-key failure into the same deterministic client response rather than a 500.
- [x] Keep policy replacement atomic: if any subject is invalid, write no asset, grant, or partial
      replacement state.
- [x] Share the validator across model, model-group, MCP, prompt, named-credential, and generic access
      write paths rather than adding endpoint-specific checks.

**Tests and acceptance:**

- [ ] Test missing Team and Organization ids on create and update as both platform admin and ordinary
      owner.
- [ ] Test a mixed valid/invalid multi-grant payload and prove no partial policy is committed.
- [ ] Test duplicate subjects, a subject deleted between validation and write, and an inaccessible but
      existing subject.
- [x] Confirm the response is deterministic, contains no stack trace, and does not leak unrelated
      tenant membership information.

### Cross-cutting verification matrix

- [x] Backend: run focused authorization, managed-access, tier-policy, model runtime, credential
      delegation, and PostgreSQL migration/concurrency tests, followed by the complete backend suite.
- [x] UI: run the named-credential race tests, shared access-panel tests, all asset create/edit tests,
      changed-file lint, complete UI tests, and production build.
- [ ] Database: verify fresh install and representative upgrade paths under PostgreSQL; inspect the
      new catalog and audience-validation queries for bounded plans and required indexes.
- [ ] Browser: exercise platform admin, creator Owner, Editor, Reader, tier member, multi-organization
      member, and no-tier user across model list/detail/create/edit, health, route-group selection, and
      inference.
- [ ] Security: attempt direct API calls that bypass UI controls, replay stale platform grants, submit
      fabricated audience ids, and force a tier-resolution failure. None may widen authority.
- [ ] Regression: verify the inline `Create new credential` journey still uses the shared named
      credential form and that a private credential can back a model shared Read or Read/Write without
      exposing credential contents.
- [ ] Documentation: update the authorization matrix and API response examples for tier-scoped model
      discovery and saved-with-warning access responses.

### Live managed-asset scenario run (2026-09-28)

The reusable runner at `scripts/e2e_managed_asset_sharing_scenarios.mjs` created two organizations,
three teams, five signed-in users, and representative records for every managed asset kind against
the Docker application on port 4002. Run `20260928070052` passed all of the following checks:

- [x] A private named credential was visible only to its creator.
- [x] Team Reader and Editor grants produced distinct read/write behavior on a prompt.
- [x] A user in two granted teams received the strongest matching role (Editor over Reader).
- [x] A creator model shared across two teams retained its owner's private credential as an opaque
      binding: the Reader could not edit, while the Editor could update other model properties
      without learning or replacing the credential.
- [x] A private model group could be changed after creation to Organization Editor; a member of that
      organization could edit it and a member of another organization remained denied.
- [x] An organization-shared MCP server was readable by the granted organization but not writable,
      and remained hidden from an unrelated organization.
- [x] An ordinary creator was denied Public sharing, while a platform-admin Public prompt was
      readable across organizations and remained non-editable to readers.
- [x] Editors could not change sharing policy, delete the asset, or take ownership.
- [x] The owner UI loaded the shared model with its friendly display name and API Model ID, with no
      browser console errors.

### Compact generated model-group keys (updated 2026-09-30)

- [x] Generate new creator group keys as `grp-XXXX-<group-slug>`, using a random four-character
      URL-safe code instead of embedding the creator namespace.
- [x] Check the complete generated key for collisions and retain the database uniqueness constraint
      as the final concurrency-safe guard.
- [x] Show `grp-XXXX-` before creation and validate only the user-controlled slug in the UI.
- [x] Use the creator's required entry after `grp-XXXX-` as the single user-facing input, store its
      exact text as the friendly name, and derive the immutable key suffix from the same value (for example,
      `Customer Support` becomes `grp-XXXX-customer-support`).
- [x] Present that single prefixed input as Group Key, remove the separate creator Display Name field,
      and explain that the exact entered text is retained as the friendly display name.
- [x] Keep platform-admin group keys unchanged.
- [x] Preserve every existing group key without renaming or migrating it; changing a callable
      key in place would break client requests, bindings, policies, and historical references.
- [x] Cover namespace stability, slug validation, creator permissions, route-group lifecycle, UI
      unit tests, and a production UI build.

### Single-field creator model identity (updated 2026-09-30)

- [x] Ask a creator for only the friendly Model Name during model creation.
- [x] Preserve that exact entry as the editable display name and derive the API-safe model slug from
      the same value.
- [x] Show the complete generated `<creator-namespace>/<model-slug>` as a read-only preview instead
      of a second editable field.
- [x] Keep the API Model ID immutable after creation so existing API clients remain stable.
- [x] Retain explicit API Model ID controls for platform administrators, whose shared catalog aliases
      are intentionally governed through tiers.
- [x] Preserve the separate display-name and API-ID fields in storage and API contracts even though
      creator creation now exposes a single input.

### Live model and group sharing verification (2026-09-30)

- [x] Run 130 live checks against the rebuilt Docker stack using isolated Owner, Team Reader, Team
      Editor, Organization Reader, and unrelated-organization principals.
- [x] Prove direct creator-model and model-group inference reaches an OpenAI-compatible mock for all
      authorized principals while both callable IDs return a denial for the outsider.
- [x] Prove Reader can inspect and invoke but cannot edit, Editor can edit content but cannot manage
      sharing, and Owner can change sharing for both Models and Model Groups.
- [x] Keep the model's Named Credential private while shared readers and editors can invoke through
      its opaque binding without seeing credential id or name.
- [x] Remove Organization sharing at runtime and verify the organization-only reader immediately
      loses model/group visibility and inference while Team Reader and Team Editor retain access.
- [x] Restore Organization sharing and verify access and inference return without restarting the app.
- [x] Remove the group's Team Editor grant and verify strongest-role fallback to Organization Reader:
      invocation remains allowed, editing becomes denied, and restoring the grant restores Editor.

### Compact generated prompt keys (2026-09-30)

- [x] Generate new creator prompt keys as `prm-XXXX-<template-key>`, using the same random
      four-character URL-safe code pattern as creator model groups.
- [x] Treat `<template-key>` as the user's Template Key input, never the account name or the optional
      human-friendly Prompt Name.
- [x] Check the complete generated key for collisions and retain the database uniqueness constraint
      as the final concurrency-safe guard.
- [x] Show `prm-XXXX-` before creation while keeping platform-admin prompt keys unchanged.
- [x] Preserve every existing prompt key without renaming or migrating it.

### Rollout and observation

1. Deploy the forward migration before or with the application version that depends on it.
2. During a rolling deployment, monitor compatibility-trigger errors, authorization denials,
   tier-catalog resolution failures, managed-access refresh warnings, and audience-validation errors.
3. Compare visible model counts and route-group candidate counts for representative tier users before
   enabling the change broadly; unexpected increases are a release blocker.
4. Retain stale platform grant rows for observation. Produce a count grouped by asset, subject type,
   and role, but do not delete or rewrite them.
5. Roll back only the application behavior if necessary. Do not roll back ownership/grant data or use
   an older binary that cannot understand creator-managed assets after users have created them.

### Definition of done

This remediation is complete only when all six findings have regression coverage, the focused and
full verification gates pass, platform model discovery matches actual tier invocation, no stale grant
can mutate a platform model, access warnings reach the user, old writers cannot recreate stale binding
state, invalid audiences never produce a 500 or partial policy, and no legacy record has been deleted
without separate approval.

## Alternatives rejected

- Extending the existing runtime callable-target bindings was rejected because those records
  narrow invocation and do not express creator ownership or edit/delete capabilities.
- Keeping ownership in each asset's metadata was rejected because it lacks foreign keys,
  transactional invariants, consistent querying, and one policy owner.
- Treating creator models as tier members was rejected because an organization tier is an
  administrator-curated platform catalog, not authority over a user's private assets.
- Performing grant SQL on every inference request was rejected because it adds security-critical
  latency and database amplification to the data plane.
