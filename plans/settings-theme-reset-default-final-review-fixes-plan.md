# Settings Theme reset final review-remediation plan

> Status: implementation-ready plan; this document does not change product code.
>
> Worktree: `.worktrees/settings-theme-reset-default`
>
> Branch: `feature/settings-theme-reset-default`
>
> Baseline: `ede81d01fb1d1fd6d4d65369746341fd46160b3b`
>
> Prepared: 2026-08-26
>
> This plan supersedes the affected audit-policy, cancellation, branding-convergence, audit-payload,
> frontend-test, and PostgreSQL-concurrency-test sections of
> `plans/settings-theme-reset-default-review-fixes-plan.md`. Product behavior that is not changed
> here remains governed by that plan and `plans/settings-theme-reset-default-plan.md`.

## 1. Outcome

Close all six final review findings while preserving the feature's existing contract:

1. a reset cannot bypass required audit because one replica has a stale hot-reloaded
   `audit_enabled` value;
2. request cancellation after the PostgreSQL commit cannot strand a partially applied local
   runtime, suppress the Redis wake-up attempt, lose commit classification, or skip the correlated
   outcome-audit attempt;
3. a focus/visibility refresh from a lagging replica cannot replace a newer branding mutation in the
   browser;
4. the required pre-effect audit record persists the safe branding state that is about to be
   destroyed;
5. the existing multipart-upload transport regression remains covered alongside the reset API test;
6. the real-PostgreSQL serialization test fails within a bounded time and always drains its tasks
   before closing database clients.

This remains control-plane-only. It adds one expand-only PostgreSQL column and an additive public
branding response field. It does not add a new Redis channel, worker, retry loop, per-request database
read, data-plane dependency, or second branding/config store.

## 2. Findings and closure evidence

| Finding | Root cause | Required closure evidence |
| --- | --- | --- |
| Reset can bypass required auditing on a stale replica | `audit_enabled` is hot-reloadable, but the audit service lifecycle is fixed at bootstrap; reset consults the mutable local `app_config` | `audit_enabled` is startup-only, bootstrap exposes a typed immutable runtime mode, reset fails closed on missing/mismatched runtime state, and tests simulate both stale directions |
| Cancellation escapes post-commit reconciliation | `asyncio.CancelledError` is outside `Exception`; apply/rollback/publish and endpoint audit use exception-only paths | An owned, bounded, shielded post-commit settlement, explicit cancellation types, cancellation-safe subscriber rollback, exactly one Redis publish attempt, correlated outcome audit, and no surviving task |
| Focus refresh can undo a committed reset | Request generations order requests in one process but do not order responses from different replicas | PostgreSQL increments a durable monotonic config revision; every branding response carries it; the provider rejects lower revisions and accepts equal/newer responses |
| Attempt audit omits before-state | `before` is used only to calculate `changed_fields`; only request/response payloads are persisted | The synchronous attempt request payload contains the redacted safe `before` projection, and the service-boundary test inspects the stored payload input |
| Multipart regression test was deleted | The manual UI test runner replaced `brandingApi.test.ts` with the reset test | Restore the upload test and register both files; unit output proves both test names ran |
| PostgreSQL concurrency test can hang | Event waits and task gather are unbounded; cleanup closes clients without first owning/draining tasks | Named timeout helpers bound every barrier/join; `finally` releases, cancels, and gathers tasks before manager/database shutdown |

## 3. Revised invariants and decisions

### 3.1 Audit enablement is a startup-owned capability

`general_settings.audit_enabled` controls whether bootstrap constructs an `AuditService`; therefore it
must be classified with the other startup-only audit settings. Dynamic configuration must reject a
change before persisting it and return the existing `409 restart_required` contract.

Bootstrap, not the mutable request-time `AppConfig`, owns one typed runtime mode:

- `disabled`: audit was explicitly disabled in the startup snapshot and no service is expected;
- `ready`: audit was enabled at startup and a started service is required.

Reset resolves that mode at the HTTP edge. Missing mode, `ready` without a service, or disagreement
between the immutable mode and the currently applied config is `503` and performs no audit or
mutation. Only an explicit `disabled` mode may skip reset audit. This preserves the operator's disable
setting without treating stale, partially initialized, or inconsistent state as disabled.

Changing this setting becomes a coordinated restart/rollout operation. Documentation must say that
operators should drain old replicas and complete the rollout before treating audit as enabled across
the deployment; the admin settings API does not persist a pending hot change.

### 3.2 PostgreSQL owns an orderable configuration revision

Add `revision BIGINT NOT NULL DEFAULT 0` to the singleton `deltallm_config` row. A database trigger
increments it in the same transaction for every insert or update; initial row creation writes revision
`1`. Keeping the increment in PostgreSQL also orders writes from an older binary during a rolling
upgrade. Rollback writes are new authoritative versions and therefore increment rather than decrement
the revision.

The runtime carries a cohesive `(config_value, revision)` snapshot:

- a replica advertises only its **applied** revision on normal branding reads;
- a transaction-coupled mutation that committed but failed local application returns the committed
  revision from the typed post-commit result;
- a replica that has read but failed to apply a newer config keeps advertising its last-known-good
  applied revision;
- process-local callback generation remains separate and continues serving its scheduler use cases.

Use a database counter instead of `updated_at`, a content hash, or a time-based browser pin. Timestamp
ordering can differ from commit ordering, a hash cannot distinguish older from newer legitimate
changes, and a timeout can expire before a lagging replica converges.

All branding response models gain additive `revision: int >= 0`. The reset response continues to add
`reconciliation_pending`; the revision is not accepted from any branding update request.

### 3.3 Browser branding is monotonic

`BrandingProvider` owns the highest applied server revision. A local mutation response raises that
floor immediately and invalidates older in-flight request generations. Bootstrap, focus, and
visibility reads may apply only when their revision is equal to or greater than the floor. A lower
response resolves as stale without modifying React state, CSS variables, title, favicon, or asset
revision.

An equal revision with equal branding is a no-op. A greater revision is accepted even when it came
from another administrator or changed only an unrelated config field, so the client never pins an
obsolete mutation forever. `sameBranding` remains visual/form equality and does not make a clean form
dirty merely because the global config revision advanced.

### 3.4 The post-commit phase must settle before cancellation escapes

The commit boundary is when the Prisma transaction context exits successfully. Before that boundary,
cancellation propagates through the transaction so PostgreSQL rolls back. After it, the manager must
settle the committed snapshot while still holding its update lock:

```text
PostgreSQL commit acknowledged
  -> owned post-commit task (bounded, retained, observed)
       -> apply subscribers
          -> on failure/cancellation: bounded rollback notification to last-known-good
       -> attempt Redis config_updated publish exactly once
       -> return applied or reconciliation-pending settlement
  -> if caller was cancelled, raise typed post-commit cancellation
  -> release update lock only after the owned task has been drained
```

Give `DynamicConfigManager` injectable control-plane deadlines with production defaults of 30 seconds
for subscriber settlement and 2 seconds for Redis publication; tests inject smaller deterministic
bounds. A timeout is classified as local apply failure, preserves PostgreSQL as authoritative,
leaves the manager on its last-known-good applied snapshot, attempts the wake-up, and emits a bounded
metric/log. Do not leave a task running after `update_config` returns or raises.

Do not catch `BaseException`. Add explicit `asyncio.CancelledError` branches where cleanup is required,
then re-raise or convert only to the typed cancellation subclass. Ordinary programmer/system exits
must retain their normal behavior.

### 3.5 Cancellation remains cancellation, while the commit state stays typed

Add `DynamicConfigPostCommitCancelledError(asyncio.CancelledError)` carrying only:

- a deep-copied committed `AppConfig`;
- the committed revision;
- whether local reconciliation remains pending.

The reset endpoint catches it before the general cancellation branch, attempts a success outcome
audit with `request_cancelled=true` and the committed after-state, then re-raises cancellation to the
ASGI server. A cancellation known to be before commit attempts an error outcome with no after-state
and re-raises. If cancellation arrives after the normal update result but during outcome audit, an
owned shielded audit finalizer makes one bounded attempt before cancellation escapes. Outcome audit
remains best-effort and never changes durable mutation truth; the synchronous pre-effect attempt is
still the compliance guarantee.

## 4. Target ownership

| Concern | Owner |
| --- | --- |
| Config JSON plus monotonic revision persistence | `DynamicConfigManager` and `deltallm_config` |
| Post-commit apply/rollback/publish settlement | `DynamicConfigManager` |
| Audit lifecycle mode | audit bootstrap, exposed through a typed service-layer enum |
| Reset transaction orchestration and committed/degraded result | new `UIBrandingResetService` |
| HTTP auth, dependency resolution, audit provenance, and response mapping | admin config endpoint |
| Highest browser revision and document-shell application | `BrandingProvider` |
| Theme form mutation state | `useThemeSettingsController` |

The new reset service accepts typed dependencies and values; it must not accept `Request`, reach into
`app.state`, emit HTTP errors, or contain SQL. The endpoint becomes the edge that resolves runtime
state, performs required audit, invokes the service, finalizes outcome audit, and maps cancellation.

## 5. Implementation slices

### Slice 1: make audit lifecycle policy coherent

Files:

- `src/config_runtime/dynamic.py`
- new `src/services/audit_runtime.py`
- `src/bootstrap/audit.py`
- `src/api/admin/endpoints/config.py`
- `tests/config/test_dynamic.py`
- `tests/bootstrap/test_optional_bootstrap.py`
- `tests/test_ui_branding.py`
- `config.example.yaml`
- `deploy/kubernetes/helm/values.yaml`
- `deploy/kubernetes/helm/values-production.yaml`
- `deploy/kubernetes/helm/values.schema.json`
- `tests/helm/test_telemetry_settings.py`
- `docs/configuration/general.md`

Changes:

1. Add `audit_enabled` to `_STARTUP_ONLY_GENERAL_SETTINGS`.
2. Define a small `AuditRuntimeMode` string enum in the focused `audit_runtime` service-layer module
   with `DISABLED` and `READY`; do not add this concern to the 1,000+ line audit service or create
   another audit policy implementation.
3. During audit bootstrap, initialize the app state to an unavailable sentinel, set `DISABLED` only
   for an explicitly disabled startup snapshot, and set `READY` only after `AuditService.start()`
   succeeds and the service is attached.
4. Replace `_audit_enabled(request)` with one edge resolver that returns either an available required
   service or an explicit disabled decision. It must fail closed for missing/unknown state, mode/config
   disagreement, or `READY` with no service.
5. Keep the reset's disabled behavior, but make tests set the bootstrap mode explicitly rather than
   editing only `app.state.app_config`.
6. Preserve the `true` default while adding the currently missing governed surfaces:
   `audit_enabled: true` in the example config and base/production Helm values, a boolean Helm schema
   property, and Helm assertions. No environment alias is introduced.
7. Document `audit_enabled` as startup-only and the coordinated rollout behavior.

Tests:

- hot update from `true` to `false` and `false` to `true` raises
  `DynamicConfigRestartRequiredError`, leaves the DB JSON/revision unchanged, does not notify
  subscribers, and publishes no Redis event;
- admin settings maps the error to `409 restart_required` without persisting;
- disabled bootstrap produces `DISABLED` and no service; enabled bootstrap produces `READY` only
  after service startup;
- `READY` + service + stale local `audit_enabled=false` fails closed before mutation;
- `DISABLED` + stale local `audit_enabled=true` fails closed before mutation;
- missing mode and `READY` + missing service fail closed;
- matching `DISABLED` permits reset without an audit service;
- matching `READY` requires the synchronous attempt before the transaction callback.
- Helm base and production render `audit_enabled: true`, the schema rejects non-booleans, and eval
  values inherit the safe default.

### Slice 2: add the durable config revision

Files:

- `prisma/schema.prisma`
- `prisma/migrations/20260826120000_dynamic_config_revision/migration.sql`
- `src/config_runtime/dynamic.py`
- `src/config.py`
- `tests/config/test_dynamic.py`
- `tests/test_ui_branding_db_integration.py`
- `scripts/verify_migration_paths.py`

Changes:

1. Add the expand-only Prisma field and migration. The migration adds the column plus a narrowly named
   `BEFORE INSERT OR UPDATE` trigger/function that owns revision advancement:

   ```sql
   ALTER TABLE "deltallm_config"
     ADD COLUMN "revision" BIGINT NOT NULL DEFAULT 0;

   -- The checked-in migration supplies a schema-qualified, collision-safe function name.
   -- INSERT maps a new/default-zero row to 1; UPDATE sets OLD.revision + 1.
   ```

   Do not edit the shared baseline migration. The trigger ignores any caller-supplied attempt to move
   revision backward and makes a legacy upsert that omits the new column advance correctly.
2. Introduce an immutable internal persisted snapshot type containing a deep-copied config mapping and
   non-negative integer revision. Load `config_value, revision` together, including `FOR UPDATE`.
3. Change the upsert to `RETURNING revision` and parse the returned PostgreSQL integer. Do not assign
   revision in application SQL or calculate the next durable revision in Python; the trigger is the
   single increment owner.
4. Track `_applied_revision` beside `_config`/`_db_config`. Update all three only after subscribers
   succeed or when a loaded snapshot is unchanged. Failed local apply leaves all applied values on the
   previous snapshot.
5. Add `get_applied_config_revision()` and a typed `DynamicConfigUpdateResult` containing the
   committed config and revision. Existing callers may ignore the additive return value.
6. Extend `DynamicConfigPostCommitApplyError` with the committed revision. Update fakes to construct
   the real typed result rather than inferring revision from process generation.
7. Change ordinary compare-and-restore to require the exact rejected revision as its compare-and-set
   token, not config-JSON equality alone. If another writer has advanced the row—even to identical
   JSON—the stale rollback must do nothing. A successful restore receives its own higher revision.
8. Add `revision` to `UIBrandingPayload`; reset inherits it. Factory/static fallback payloads use `0`,
   while database-backed responses use the manager's applied or committed revision.
9. Update the migration verifier to assert revision `0` for a preserved legacy row, revision `1` on a
   fresh insert, advancement by both the new upsert and a legacy-shaped update that omits revision,
   and successful migration on both paths.

Tests:

- first insert returns revision `1`; sequential writes return strictly increasing revisions;
- a raw legacy-shaped upsert that does not mention revision still advances it;
- two managers serialize writes and observe one global order;
- an ordinary failed apply followed by compare-and-restore advances the revision again and leaves the
  restored config authoritative;
- a concurrent writer that advances revision before compare-and-restore is never overwritten, even
  when its JSON happens to equal the rejected JSON;
- a transaction-coupled failed apply exposes the committed revision but retains the old applied
  revision locally;
- poll/pubsub convergence advances config and revision as one snapshot;
- real PostgreSQL asserts JSON and revision in the same transaction, including reset asset deletion;
- fresh-install and last-release upgrade migration paths preserve the config row and initialize the
  new column without destructive DDL.

### Slice 3: make post-commit settlement cancellation-safe

Files:

- `src/config_runtime/dynamic.py`
- `tests/config/test_dynamic.py`
- `tests/test_ui_branding_db_integration.py`

Changes:

1. Extract `_rollback_subscribers(...)` so ordinary exceptions, explicit cancellation, and timeout
   use one rollback path. Attempt every subscriber rollback, classify failures, and never update the
   applied snapshot after a failed forward apply.
2. Add an internal settlement result with applied/reconciliation and publish-attempt state.
3. After `_persist_merged_config` returns, create exactly one locally owned settlement task. Keep its
   strong reference, await it through `asyncio.shield`, observe every result, and do not release
   `_update_lock` until it is finished or bounded cancellation cleanup has completed.
4. Put the subscriber phase under the explicit settlement deadline. On deadline, cancel/drain the
   forward apply, run bounded rollback, record `post_commit_apply_timeout`, and proceed to publish.
5. Refactor `_publish_reload_event` to return a boolean delivery result and give the Redis call its own
   timeout. It remains best-effort, but every committed update attempts it exactly once even after
   apply failure, timeout, or caller cancellation.
6. If the caller is cancelled after commit, finish/drain settlement and raise
   `DynamicConfigPostCommitCancelledError` with committed revision/config and the truthful local
   reconciliation state. If cancellation occurs before commit acknowledgement, propagate the normal
   `CancelledError` and rely on the transaction rollback.
7. Preserve the existing semantics for transaction-coupled versus ordinary updates: only the latter
   attempts a durable compare-and-restore after local rejection.

Deterministic tests use events, never sleeps:

- cancel while the transaction callback is blocked before config persistence: no config/asset commit,
  normal `CancelledError`, no publish;
- cancel after commit while the first subscriber is blocked: settlement completes or times out,
  rollback runs, publish is attempted once, typed post-commit cancellation escapes, and no task
  survives;
- cancel while Redis publish is blocked: the publish deadline settles, the applied config remains
  coherent, typed post-commit cancellation escapes, and the update lock becomes available;
- subscriber explicitly raises `CancelledError`: it is classified as failed local application rather
  than bypassing rollback/publish;
- a second update waiting on `_update_lock` cannot interleave with the first settlement;
- a subsequent poll converges from PostgreSQL after the subscriber recovers;
- Redis timeout/failure never masks an ordinary successful apply or a typed apply failure.

### Slice 4: extract reset orchestration and complete audit payload/cancellation handling

Files:

- new `src/services/ui_branding_reset.py`
- `src/api/admin/endpoints/config.py`
- `tests/test_ui_branding.py`

Changes:

1. Add `UIBrandingResetService`, constructed from the existing dynamic manager and branding asset
   repository factory. It performs one update with one transaction-scoped `delete_all_known()`
   callback and returns a typed reset result. It converts only the known post-commit apply exception
   into `reconciliation_pending=true`; arbitrary failures remain failures.
2. Keep authorization and app-state dependency resolution at the endpoint. Resolve config manager,
   asset service/repository availability, audit runtime mode, and safe before projection before the
   required attempt.
3. Persist the attempt payload as:

   ```json
   {
     "target": "factory_defaults",
     "before": { "instance_name": "...", "revision": 7, "...": "safe branding fields" }
   }
   ```

   Continue passing `before` separately for change metadata, but do not rely on it as stored payload.
   The central redactor remains the only redaction owner.
4. Keep the same deterministic operation/phase event IDs. Attempt remains `attempted`, required, and
   forced synchronous. Outcome remains best-effort.
5. Add a reset-specific owned, shielded outcome-audit finalizer at the HTTP edge with an injectable
   2-second production deadline. It observes/drains its task and is used after a known commit so
   request cancellation cannot skip the attempt to emit the outcome. Timeout is logged/counted as
   best-effort audit failure; do not broaden the shared admin-audit helper contract.
6. Handle `DynamicConfigPostCommitCancelledError` before `asyncio.CancelledError`: build after-state
   from the committed config/revision, emit success outcome metadata with
   `request_cancelled=true`, then re-raise cancellation.
7. Handle ordinary pre-commit cancellation with an error/cancelled outcome, no after-state, and then
   re-raise. A normal committed response keeps `200` plus its existing
   `reconciliation_pending` field.
8. Log only operation ID, bounded classifications, and exception class. Never log branding payloads,
   BLOBs, request credentials, or full config.

Tests:

- the recording audit service retains payload inputs; the required sync call contains exactly one
  redacted request payload with the full safe before projection;
- timeline assertion is `attempt persisted -> asset/config transaction entered -> outcome attempted`;
- attempt persistence failure returns controlled `503` with zero config writes/deletes;
- injected pre-commit failure/cancellation leaves state unchanged and emits an error outcome attempt;
- committed apply failure/cancellation uses committed branding and revision, reports reconciliation
  truthfully, emits the success outcome attempt, and preserves cancellation to the caller;
- cancellation during outcome enqueue still drains the owned finalizer and leaves no task;
- outcome audit failure remains non-fatal after commit;
- disabled audit creates neither phase while all fail-closed runtime-state cases perform no mutation.

The endpoint test does not need to duplicate the audit repository's generic payload persistence suite.
It must prove that this endpoint passes the before payload to `record_event_sync`; the existing audit
service repository tests continue proving that supplied payloads are stored in legacy and outbox
modes.

### Slice 5: make branding responses and the browser revision-aware

Files:

- `src/api/admin/endpoints/config.py`
- `ui/src/lib/api.ts`
- `ui/src/lib/branding.ts`
- `ui/src/lib/brandingResetApi.ts`
- `ui/src/lib/brandingContext.ts`
- `ui/src/components/BrandingProvider.tsx`
- `ui/src/components/settings/useThemeSettingsController.ts`
- `ui/src/pages/SettingsPage.tsx`
- `tests/test_ui_branding.py`
- `ui/tests/branding.test.ts`
- `ui/tests/brandingProvider.test.ts`
- `ui/tests/brandingResetApi.test.ts`
- `ui/tests/themeSettingsController.test.tsx`
- `docs/admin-ui/settings.md`

Changes:

1. Return the applied durable revision from GET, update, upload, and delete. Return the committed
   revision from reset even when local reconciliation is pending.
2. Add `revision: number` to the single UI branding contract and reset response. Normalize only finite,
   non-negative integers; invalid/missing legacy data falls back to `0` at the browser boundary.
3. Keep visual form equality independent of revision. If naming would otherwise be ambiguous, rename
   the helper to `sameBrandingAppearance` in one mechanical caller migration rather than maintaining
   two equality definitions.
4. Add `appliedRevisionRef` to `BrandingProvider`. Centralize all server/local application through one
   function that enforces revision monotonicity before changing state or document effects.
5. A mutation `setBranding` advances both request generation and revision floor. A read below the floor
   returns the current applied branding to callers and performs no side effect; equal/newer reads may
   apply normally.
6. Ensure `refreshBranding()`'s resolved value matches what the provider accepted, not a discarded
   stale response.
7. Stop reconstructing Theme branding from `/ui/api/settings`, which has no branding revision and can
   create a second stale input. Initialize/reconcile the controller from `BrandingContext`. If the form
   is clean, replace value and persisted snapshot together; if it is dirty, preserve the draft but
   advance the persisted/discard target to the accepted external branding; if a mutation is active,
   defer reconciliation until it settles. Keep this policy in the controller rather than the page.
8. Keep reset UI behavior unchanged apart from carrying the revision through normalization and global
   state. Do not add focus suppression timers, polling, or automatic mutation retries.
9. Document that responses are revision-ordered in the browser and that replica convergence still
   uses Redis wake-up plus PostgreSQL polling.

Tests:

- backend contract tests assert a non-negative revision on every branding response;
- a committed-degraded reset response exposes the committed revision, while a normal GET from the
  stale serving snapshot exposes the older applied revision;
- provider bootstrap at revision `4`, mutation at `6`, and focus response at `5` keeps revision `6`
  in the document shell;
- a later response at `6` converges without duplicate effects and a legitimate response at `7`
  applies;
- an older request generation remains ignored even if its numeric revision is otherwise acceptable;
- equal appearance with a higher revision does not mark the form dirty;
- an accepted external revision preserves a dirty draft, becomes its new discard target, and an
  update received during a mutation is reconciled after that mutation settles;
- reset controller preserves the response revision in form, persisted snapshot, and global branding;
- missing/invalid revision normalization remains compatible with a legacy/static fallback payload.

### Slice 6: restore upload coverage and bound the real-PostgreSQL test

Files:

- restore `ui/tests/brandingApi.test.ts` (or rename it once to
  `ui/tests/brandingUploadApi.test.ts`)
- `ui/scripts/run-unit-tests.mjs`
- `tests/test_ui_branding_db_integration.py`

Changes:

1. Restore the multipart test unchanged in intent: exact PUT path, `FormData` body, same `File`
   instance, and no manually supplied `Content-Type` header so the browser owns the boundary.
2. Register both upload and reset API test files in the current manual runner. Do not replace one with
   the other. Automatic discovery remains repository debt outside this PR.
3. In the PostgreSQL serialization test, initialize `first_task` and `second_task` to `None` before
   entering the body.
4. Use a 10-second named barrier timeout and a 20-second join timeout with descriptive helpers around
   `first_locked.wait()`, `second_lock_attempted.wait()`, and the final gather. Timeout failures must
   identify the barrier; do not add sleeps or retry loops.
5. In `finally`, set `release_first`, cancel every unfinished owned task, and gather all created tasks
   with `return_exceptions=True` before closing either manager or database client. Then restore the
   database snapshot and disconnect in the existing safe order.
6. Apply the same task-ownership pattern to any new cancellation integration case added in Slice 3.

Tests/closure:

- `npm --prefix ui run test:unit` output includes both named API regressions;
- the PostgreSQL test passes normally with two real connections;
- deliberately breaking a barrier while developing produces a bounded assertion/timeout failure and
  no pending-task or closed-client warning (do not commit the deliberate break).

## 6. API and migration contract

Branding responses become:

```json
{
  "instance_name": "DeltaLLM",
  "logo_mark_url": null,
  "logo_full_url": null,
  "favicon_url": null,
  "primary_color": "#5B50D6",
  "secondary_color": "#8B7CFF",
  "menu_hover_color": "#F7F5FF",
  "revision": 12
}
```

Reset adds its existing field:

```json
{
  "...": "same branding fields",
  "revision": 12,
  "reconciliation_pending": false
}
```

This is additive for API consumers. Update requests do not accept `revision`, so clients cannot forge
ordering. The migration is expand-only and compatible with the old application: old binaries ignore
the column, while new binaries accept legacy rows at revision `0`. Application rollback leaves the
unused column in place; no down migration drops it.

Deployment order:

1. apply the Prisma migration once through the coordinated migration job;
2. deploy the new backend to all replicas;
3. deploy the UI built from the same commit;
4. when changing `audit_enabled`, perform a separate coordinated rollout and drain old replicas before
   declaring the new audit policy active.

## 7. Observability and failure semantics

Use bounded existing metric dimensions or extend them with fixed enum values only:

- `post_commit_apply_failed`;
- `post_commit_apply_timeout`;
- `post_commit_cancelled_applied`;
- `post_commit_cancelled_reconciliation_pending`;
- Redis publish `delivered`, `failed`, or `timeout` if the existing metric family supports it.

Structured logs contain the operation ID for reset, config revision, source, result enum, and exception
class. They do not include config JSON, before/after branding, audit payloads, asset bytes, actor tokens,
or free-form exception text. PostgreSQL remains the recovery source when Redis publication fails.

## 8. Verification matrix

Run focused checks first from `.worktrees/settings-theme-reset-default`.

### Backend behavior and formatting

```bash
uv run ruff check src/config.py src/config_runtime/dynamic.py src/bootstrap/audit.py src/services/audit_runtime.py src/services/ui_branding_reset.py src/api/admin/endpoints/config.py tests/config/test_dynamic.py tests/bootstrap/test_optional_bootstrap.py tests/test_control_audit_mode.py tests/test_ui_branding.py tests/test_ui_branding_db_integration.py
uv run ruff format --check src/config.py src/config_runtime/dynamic.py src/bootstrap/audit.py src/services/audit_runtime.py src/services/ui_branding_reset.py src/api/admin/endpoints/config.py tests/config/test_dynamic.py tests/bootstrap/test_optional_bootstrap.py tests/test_control_audit_mode.py tests/test_ui_branding.py tests/test_ui_branding_db_integration.py
uv run pytest -q tests/config/test_dynamic.py tests/bootstrap/test_optional_bootstrap.py tests/test_control_audit_mode.py tests/test_ui_branding.py
uv run pytest -q tests/config tests/bootstrap/test_optional_bootstrap.py tests/test_control_audit_mode.py tests/test_ui_branding.py
```

### Real PostgreSQL, Prisma, and migrations

```bash
uv run prisma generate --schema=./prisma/schema.prisma
DATABASE_URL=<test-postgres-url> uv run pytest -q tests/test_ui_branding_db_integration.py
MIGRATION_TEST_ADMIN_DATABASE_URL=<postgres-admin-url> uv run python scripts/verify_migration_paths.py --base-ref v0.1.34
```

The database commands must use disposable test databases. The integration test must restore the
singleton config row, its revision, and all known branding asset rows even after failure.

### Config and Helm

```bash
uv run pytest -q tests/helm/test_telemetry_settings.py
helm lint deploy/kubernetes/helm --set secret.values.masterKey=sk-ci-validation-1234567890abcdeA1 --set secret.values.saltKey=ci-validation-salt-0123456789abcdef0123456789abcdef
helm template deltallm deploy/kubernetes/helm --set secret.values.masterKey=sk-ci-validation-1234567890abcdeA1 --set secret.values.saltKey=ci-validation-salt-0123456789abcdef0123456789abcdef
helm lint deploy/kubernetes/helm -f deploy/kubernetes/helm/values-eval.yaml --set secret.values.masterKey=sk-ci-validation-1234567890abcdeA1 --set secret.values.saltKey=ci-validation-salt-0123456789abcdef0123456789abcdef
helm template deltallm deploy/kubernetes/helm -f deploy/kubernetes/helm/values-eval.yaml --set secret.values.masterKey=sk-ci-validation-1234567890abcdeA1 --set secret.values.saltKey=ci-validation-salt-0123456789abcdef0123456789abcdef
helm lint deploy/kubernetes/helm -f deploy/kubernetes/helm/values-production.yaml --set secret.values.masterKey=sk-ci-validation-1234567890abcdeA1 --set secret.values.saltKey=ci-validation-salt-0123456789abcdef0123456789abcdef --set runtime.database.url=postgresql://deltallm:deltallm@postgres:5432/deltallm --set runtime.redis.url=redis://redis:6379/0
helm template deltallm deploy/kubernetes/helm -f deploy/kubernetes/helm/values-production.yaml --set secret.values.masterKey=sk-ci-validation-1234567890abcdeA1 --set secret.values.saltKey=ci-validation-salt-0123456789abcdef0123456789abcdef --set runtime.database.url=postgresql://deltallm:deltallm@postgres:5432/deltallm --set runtime.redis.url=redis://redis:6379/0
```

### Frontend

```bash
npm --prefix ui exec -- eslint src/lib/api.ts src/lib/branding.ts src/lib/brandingResetApi.ts src/lib/brandingContext.ts src/components/BrandingProvider.tsx src/components/settings/useThemeSettingsController.ts src/pages/SettingsPage.tsx tests/branding.test.ts tests/brandingApi.test.ts tests/brandingProvider.test.ts tests/brandingResetApi.test.ts tests/themeSettingsController.test.tsx
npm --prefix ui run test:unit
npm --prefix ui run build
npm --prefix ui run lint
```

Touched files must have zero ESLint errors. Record the full-lint baseline and the Vite initial gzip
size; this change must not add a new initial dependency or increase the initial bundle materially.

### Final scope checks

```bash
git diff --check
git status --short
git diff --stat
```

Review the final diff for generated Prisma artifacts, duplicate branding types/equality, unowned tasks,
new raw SQL outside the config persistence/repository boundary, secrets in audit payloads, and any
deleted regression test.

## 9. Recommended implementation order

1. Audit startup ownership and fail-closed runtime-mode tests.
2. Expand-only revision migration, persisted snapshot type, and migration verification.
3. Cancellation-safe post-commit settlement plus deterministic unit/real-PostgreSQL coverage.
4. Reset service extraction, stored before payload, and cancellation-aware outcome auditing.
5. Add revision to backend branding contracts and all mutation responses.
6. Add browser revision floor and controller/settings reconciliation tests.
7. Restore the upload transport test and harden task cleanup in the PostgreSQL concurrency test.
8. Update docs, run focused gates, then full UI/migration gates and final diff review.

Each step should leave focused tests green. Do not combine the migration/runtime snapshot work with UI
changes in one unreviewable edit, and do not commit generated UI build output.

## 10. Definition of done

The remediation is complete only when all of the following are true:

- reset audit policy is derived from immutable bootstrap state, not a hot local boolean;
- changing `audit_enabled` dynamically is rejected before persistence and documents restart/rollout;
- all branding responses carry one PostgreSQL-backed monotonic revision;
- a lower-revision focus response cannot replace a reset/update/upload/delete response;
- every acknowledged config commit reaches a bounded, observed apply/rollback/publish settlement even
  when the request is cancelled;
- pre-commit and post-commit cancellation remain distinguishable, cancellation is re-propagated, and
  the reset outcome audit is attempted with truthful durable state;
- the required attempt audit stores the safe complete before projection before any asset/config
  mutation;
- upload and reset API transport regressions both execute in the UI unit suite;
- the real PostgreSQL concurrency test has bounded barriers and joins and drains tasks before clients;
- fresh-install and last-release migration verification pass;
- focused backend, real PostgreSQL, migration, Helm, frontend unit/build/lint, and
  `git diff --check` gates are reported exactly, with pre-existing failures separated from new
  failures.
