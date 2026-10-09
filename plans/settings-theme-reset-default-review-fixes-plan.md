# Settings Theme reset review-remediation plan

> Status: implementation-ready remediation plan; this document does not change product code.
>
> Worktree: `.worktrees/settings-theme-reset-default`
>
> Branch: `feature/settings-theme-reset-default`
>
> Baseline: `ede81d01fb1d1fd6d4d65369746341fd46160b3b`
>
> Prepared: 2026-08-25
>
> Supersedes the failure, audit, frontend ownership, testing, and propagation-copy sections of
> `plans/settings-theme-reset-default-plan.md`. The original product decisions remain in force unless
> this document explicitly changes them.

## 1. Outcome

Close all five review findings without weakening the core reset behavior:

1. a committed reset must never be returned to the operator as a failed reset merely because local
   runtime application, Redis notification, or post-commit audit reconciliation failed;
2. the irreversible asset deletion must be covered by a required durable audit record before the
   effect is allowed;
3. PostgreSQL—not a fake transaction—must prove config/asset atomicity, rollback, serialization,
   idempotency, and recovery;
4. the frontend must have one implementation of each branding operation and one branding equality
   function;
5. the actual Settings Theme mutation coordinator must be tested, and UI/docs must describe
   replica convergence rather than promise instantaneous global application.

No schema migration, new Redis channel, background worker, or data-plane change is required.

## 2. Review findings and closure criteria

| Finding | Root cause | Closure evidence |
| --- | --- | --- |
| Committed reset can return `5xx` and omit its audit | `DynamicConfigManager` commits before subscriber application; transaction-coupled updates cannot be rolled back afterward; the endpoint audits only after `update_config` returns | A typed post-commit/apply exception, required pre-effect audit, non-fatal outcome audit, additive reset response state, and endpoint tests for committed-degraded success |
| Atomicity is fake-only | `tests/test_ui_branding.py` executes the callback sequentially against an in-memory fake | Real Prisma/PostgreSQL tests that inspect the two production tables after success and injected failure |
| Branding API/equality have duplicate owners | `brandingApi.ts` repeats the existing client/types in `api.ts`; `sameBranding` exists in two modules | Delete the duplicate full client, keep existing operations in their stable owner, add only a reset-specific API function, and export equality once from `branding.ts` |
| The route-level reset coordinator is untested | Panel tests exercise callbacks only; API tests exercise transport only | Extract a small controller hook and test state transitions with deferred/failing API promises |
| Copy overpromises immediate global effect | Success toast and docs conflate durable commit, local apply, Redis wake-up, and peer convergence | Copy distinguishes durable save, current-browser reconciliation, Redis wake-up, and the 30-second default DB-poll fallback |

## 3. Revised invariants

### 3.1 Commit boundary

There are three distinct outcomes, and the code must not collapse them:

| Outcome | Durable config/assets | Local runtime | HTTP result |
| --- | --- | --- | --- |
| Rejected before commit | unchanged | unchanged | failure |
| Committed and applied | factory config + no custom assets | factory theme | `200`, `reconciliation_pending=false` |
| Committed, local apply failed | factory config + no custom assets | last-known-good until reload | `200`, `reconciliation_pending=true` |

Once PostgreSQL commits, the mutation is successful from the API's durability perspective. A local
subscriber failure is a reconciliation problem, not permission to claim the irreversible delete did
not happen.

### 3.2 Audit boundary

- When `general_settings.audit_enabled` is `true`, reset requires a durable synchronous **attempt**
  event before entering the config transaction. Missing/unavailable required audit persistence returns
  a controlled service-unavailable error and performs no mutation.
- When audit is explicitly disabled, reset follows that operator policy and does not require an audit
  service. An unexpectedly missing service while audit is enabled is not treated as “disabled.”
- A post-effect **outcome** event is best-effort and must never turn a committed reset into a failed
  response. It is correlated to the attempt by an operation ID.
- This two-phase design works in both legacy and outbox ingestion modes, including deployments where
  audit and application state use separate databases. It does not pretend that a cross-database
  transaction exists.

### 3.3 Replica boundary

- PostgreSQL is authoritative.
- Local subscribers are the fast local apply path.
- Redis pub/sub is a best-effort wake-up and must still be attempted after a committed transaction,
  even when the writer's local subscriber fails.
- Each replica's database poll is the recovery path. The current default poll interval is 30 seconds;
  a healthy peer that misses pub/sub should converge on its next successful poll. Persistent database or
  subscriber failure can extend staleness and must remain visible as degraded, so copy must not promise
  that every replica has applied the reset when the response returns.

## 4. Target request flow

```text
POST /ui/api/branding/reset
  -> authorize platform admin and resolve required dependencies
  -> capture safe before projection and create operation_id
  -> if audit enabled:
       require audit service
       synchronously persist RESET attempted / phase=attempt
  -> one serialized PostgreSQL transaction
       delete allowlisted branding BLOB rows
       write explicit factory branding config
  -> apply committed config to local subscribers
       success: publish Redis wake-up
       failure: restore local last-known-good as far as subscribers support,
                publish Redis wake-up, raise typed committed/apply error
  -> endpoint converts committed/apply error into a successful reset payload
  -> enqueue RESET success/error / phase=outcome best-effort
  -> return authoritative branding + reconciliation_pending
```

The frontend treats only a rejected/pre-commit request as reset failure. Both committed response
variants replace the form, persisted snapshot, and `BrandingContext` with the server response.

## 5. Backend implementation

### 5.1 Model the post-commit state explicitly

In `src/config_runtime/dynamic.py`:

1. Add `DynamicConfigPostCommitApplyError` (final name may be shortened, but it must say that commit
   already occurred). It carries a deep copy of the committed `AppConfig` and chains the original
   subscriber exception. It must not contain or log serialized secrets.
2. Preserve existing behavior for updates without `transaction_mutation`: if local application fails,
   attempt the existing compare-and-restore database rollback and re-raise the original failure.
3. For updates with `transaction_mutation`:
   - never attempt a fake database rollback after commit;
   - catch local `_apply_db_config` failure;
   - attempt `_publish_reload_event("config_updated")` after the commit regardless of local apply;
   - log/measure the bounded outcome without configuration payloads;
   - raise `DynamicConfigPostCommitApplyError(committed_app_config=next_app_config)`.
4. Keep `_publish_reload_event` best-effort. Its Redis error remains logged and cannot replace either a
   normal success or the typed committed/apply result.
5. Do not change the return contract for unrelated callers. The typed exception is opt-in knowledge:
   existing upload/delete transaction callers continue to see an exception unless they explicitly
   handle the committed state in a later, separately reviewed change. Audit every current
   `transaction_mutation` caller for exception-type branching and add regressions showing that existing
   upload/delete HTTP behavior remains unchanged.

Add unit tests in `tests/config/test_dynamic.py` for:

- transaction-coupled commit + failing subscriber leaves the durable next config in place, retains the
  prior local manager config, publishes exactly one wake-up, and raises the typed error;
- a subsequent source reload applies the committed config after the subscriber recovers;
- the existing non-transaction rollback behavior remains unchanged;
- Redis failure does not mask normal success or change the typed committed/apply classification.

### 5.2 Keep branding SQL behind a repository

The current feature added another raw SQL operation to `UIBrandingAssetService`. While touching this
seam, move branding persistence into `src/db/ui_branding_assets.py`:

- `list_known()`;
- `upsert(asset, updated_by=...)`;
- `delete(asset_key)`;
- `delete_all_known()` using one fixed, parameterized statement for `logo_mark`, `logo_full`, and
  `favicon`.

`UIBrandingAssetService` remains the validation and bounded in-memory-cache owner and consumes the
repository for refreshes. Transaction callbacks construct the repository with the transaction-scoped
Prisma client. No service or endpoint retains raw SQL, and no method opens a nested transaction.

Repository tests keep the existing parameterization/allowlist assertions. Service tests continue to
cover content validation and cache refresh independently.

### 5.3 Add an explicit reset response

In `src/config.py`, add an additive public response DTO:

```text
UIBrandingResetPayload:
  all UIBrandingPayload fields
  reconciliation_pending: bool = false
```

Use it only as the `POST /ui/api/branding/reset` response model. Existing get/update/upload/delete DTOs
remain unchanged. The field means that this request's serving replica failed to apply the committed
configuration locally; it does not claim knowledge of every peer replica.

Add a helper in `src/api/admin/endpoints/config.py` that builds the safe branding projection from an
arbitrary `AppConfig`. The normal path may still read the applied request state; the committed-degraded
path must build its response from `DynamicConfigPostCommitApplyError.committed_app_config`, not from
stale `app.state`.

### 5.4 Make destructive audit two-phase

Extend the shared audit helpers with backwards-compatible optional parameters:

- `event_id: str | None = None`, copied to `AuditEventInput.event_id`;
- `critical: bool = True` on `emit_admin_mutation_audit`;
- `force_sync: bool = False` on the control/admin helper path.

All existing callers retain current defaults. `force_sync` bypasses the configurable async control
mode for this one pre-effect event and calls `record_event_sync`; it does not change the global
`audit_control_sync_enabled` policy for other actions. Add focused coverage in
`tests/test_control_audit_mode.py` for the new default-preserving and forced-sync behavior.

For reset, generate one UUID operation ID and deterministic per-phase event IDs. Use the existing
`ADMIN_UI_BRANDING_RESET` action for both records:

- attempt: `status="attempted"`, `phase="attempt"`, required + forced synchronous, safe `before`, fixed
  request marker `{"target": "factory_defaults"}`;
- outcome: `status="success"` or `"error"`, `phase="outcome"`, `critical=false`, safe `after` when
  committed, `reconciliation_pending`, and only sanitized error classification when rejected.

Wrap outcome emission in a narrow best-effort helper that logs/counts audit enqueue failure and then
returns. Never swallow or replace the original pre-commit mutation error, and never replace a committed
success response.

### 5.5 Reset endpoint sequence

Revise `reset_ui_branding` in `src/api/admin/endpoints/config.py` in this order:

1. authorize through the existing dependency;
2. resolve the dynamic manager and asset service/repository before auditing or mutating;
3. capture the current safe branding projection and create the operation ID;
4. when audit is enabled, call `require_audit_service` and durably emit the attempt;
5. call `dynamic_config.update_config` once with server-owned factory values and a transaction callback
   that calls `UIBrandingAssetRepository(tx).delete_all_known()`;
6. on normal return, construct `UIBrandingResetPayload(..., reconciliation_pending=false)`;
7. on `DynamicConfigPostCommitApplyError`, construct the same payload from its committed config with
   `reconciliation_pending=true` and continue as success;
8. on any pre-commit error, emit an error outcome best-effort and re-raise;
9. emit the committed success outcome best-effort and return `200`.

Do not catch or convert arbitrary exceptions into committed success. Only the typed exception created
after a known completed transaction has that meaning.

### 5.6 Backend observability

- Add a finite config-reload outcome for post-commit local-apply failure (for example,
  `source="admin_update"`, `result="post_commit_apply_failed"`) using the existing metric family if its
  label contract permits it; otherwise add one bounded counter.
- Log operation ID, action, and exception class only. Do not log config values, asset contents, auth
  headers, or audit payloads.
- Audit consumers must count `phase=outcome` for completed reset totals so the new attempt event does
  not double-count operations.

## 6. Real PostgreSQL proof

Create `tests/test_ui_branding_db_integration.py`, following the repository's existing Prisma
integration-test bootstrap:

- require `DATABASE_URL` plus the generated Prisma client;
- fail in CI when either is unavailable and skip explicitly in local environments;
- use the production `DynamicConfigManager`, `UIBrandingAssetRepository`, and asset service;
- use two Prisma clients where replica concurrency must use separate database sessions;
- snapshot the singleton `proxy_config` row and the three branding asset rows before each test and
  restore them in `finally`, including the row-absent case;
- never truncate shared tables or leave factory/custom test state behind.

Required cases:

1. **Atomic success:** seed custom config, unrelated config fields, and all three real BLOB rows; run
   reset; assert the explicit factory fields and asset deletion committed together and unrelated fields
   survived.
2. **Config-write failure after delete:** wrap the real transaction client so branding DELETE is sent to
   PostgreSQL but the subsequent `INSERT INTO deltallm_config` deterministically raises. Let the real
   Prisma transaction exit with that exception; assert the original config and all BLOB rows remain.
   Do not alter schema constraints or install a database trigger for fault injection.
3. **Callback failure:** make the transaction callback delete the real asset rows and then raise; assert
   the config and assets both roll back.
4. **Idempotency:** run the successful mutation twice and assert identical config plus zero known asset
   rows.
5. **Replica serialization:** start two managers on two connections; hold the first transaction after it
   acquires the advisory lock, start a disjoint update from the second, release the first, and assert the
   final JSON contains both updates. Include reset in one side and verify unrelated fields are preserved.
6. **Committed/apply recovery:** install a failing subscriber, perform the real reset, assert the typed
   committed result while PostgreSQL contains factory config/no assets, recover the subscriber, invoke
   the production source-reload path, and assert local config/cache convergence.

Endpoint unit tests remain responsible for auth, audit ordering, HTTP response shape, and wiring the
transaction callback. The DB suite is responsible for PostgreSQL transaction behavior; neither suite
stands in for the other.

## 7. Frontend ownership and controller

### 7.1 Remove duplicate API ownership without expanding legacy lint debt

`ui/src/lib/api.ts` already owns get/update/upload/delete branding operations and currently has 106
pre-existing `no-explicit-any` errors. Do not touch that file in this remediation and do not introduce a
lint suppression or baseline exception.

Instead:

1. delete the new duplicate `ui/src/lib/brandingApi.ts`;
2. add `ui/src/lib/brandingResetApi.ts` containing only:
   - the typed `UIBrandingResetResponse = UIBranding & { reconciliation_pending: boolean }` contract;
   - `resetBranding()` issuing the one new POST through `apiFetch`;
3. return `BrandingProvider` and existing save/upload/delete flows to the stable `branding` object and
   types exported by `ui/src/lib/api.ts`;
4. let the controller depend on the stable client plus the reset-specific function; it must not wrap or
   reimplement any existing HTTP operation.

This leaves one implementation per operation and zero duplicate branding types. A full extraction of
the 2,337-line legacy API barrel is worthwhile but is a separate migration that must budget removal of
all changed-file lint errors; it is not hidden inside this reset fix.

### 7.2 Export equality once

Move `sameBranding` to `ui/src/lib/branding.ts`, beside `UIBranding`, `DEFAULT_BRANDING`, and
normalization. Import it from both `BrandingProvider` and `settingsTheme`; delete both private copies.
Keep `settingsTheme.ts` focused on validation, update-payload construction, asset-size checks, and reset
eligibility.

### 7.3 Extract and test the actual mutation coordinator

Create `ui/src/components/settings/useThemeSettingsController.ts` and move the theme-specific form,
persisted snapshot, error, dialog, mutation state, and save/upload/delete/reset handlers out of
`SettingsPage.tsx`. The route supplies loaded branding and the global `setBranding` callback; the panel
remains presentational.

The controller must:

- use a discriminated `ThemeMutation` state for rendering;
- also use a synchronous in-flight ref so two confirms in the same React turn cannot issue two POSTs;
- assign each operation a generation and ignore stale completion after supersession or unmount;
- update form, persisted state, and global branding only for the current operation;
- preserve current save/upload/delete/discard behavior;
- keep the reset dialog locked while pending;
- leave all browser state unchanged on a rejected/pre-commit reset so retry is safe;
- apply both reset response variants as successful committed state and close the dialog.

Response messaging:

- normal committed response: success tone, “DeltaLLM defaults were saved. Other replicas will converge
  through normal configuration refresh.”
- `reconciliation_pending=true`: informational tone, “Defaults were saved in PostgreSQL, but this
  replica has not applied them yet. It will retry automatically.”
- rejected/pre-commit request: error tone and inline error, dialog unlocked for retry.

Do not add an automatic mutation retry. A user retry is idempotent, while automatic retry could create
confusing duplicate audit attempts after an ambiguous network response.

## 8. Frontend test matrix

### 8.1 Transport contract

Replace `ui/tests/brandingApi.test.ts` with `ui/tests/brandingResetApi.test.ts` and verify:

- exact `POST /ui/api/branding/reset`;
- no JSON body;
- structured response, including `reconciliation_pending`, passes through shared transport;
- structured transport errors are not converted to success.

Existing branding API tests, if any, continue to cover the stable `api.ts` implementation. Register the
new filename in `ui/scripts/run-unit-tests.mjs` and remove the deleted duplicate-module test entry.

### 8.2 Controller tests

Add `ui/tests/themeSettingsController.test.tsx` with an injected/deferred API dependency and a minimal
hook harness. Cover:

1. normal reset issues one POST, applies response to form/persisted/global branding, closes dialog, and
   emits the convergence-safe success toast;
2. committed `reconciliation_pending` response applies state, closes the dialog, and emits info—not
   error—copy;
3. rejected request leaves form, persisted snapshot, and global branding untouched, keeps the dialog
   available, and a manual retry succeeds;
4. two confirm calls in the same turn invoke reset once;
5. a completion after unmount performs no local/global state update or toast;
6. save, upload, delete, and discard keep their existing semantics after extraction;
7. validation failures never start a network mutation.

### 8.3 Presentational and interaction tests

Keep `ui/tests/themeSettingsPanel.test.tsx` for the view contract:

- confirmation text names permanent asset deletion and unsaved-edit loss;
- Escape/cancel work only while idle and focus returns to the trigger;
- all controls are locked while a mutation is pending;
- reset is disabled only when form and persisted state both equal factory defaults;
- keyboard activation and button labeling remain accessible;
- below and above `768px`, long text/buttons wrap without horizontal overflow.

The panel test must not be cited as coordinator coverage.

## 9. Backend test matrix

Extend `tests/test_ui_branding.py` and audit/dynamic focused suites with:

- required attempt audit occurs before the transaction callback;
- enabled audit + missing service returns service unavailable with zero config/asset mutation;
- explicitly disabled audit permits reset without an audit service;
- required attempt persistence failure leaves config/assets unchanged;
- normal reset returns `reconciliation_pending=false` and emits correlated attempt + success outcome;
- typed committed/apply failure returns `200`, factory branding, and
  `reconciliation_pending=true` while the fake durable store is already reset;
- outcome audit failure is logged/non-fatal after commit;
- pre-commit config failure returns failure, emits an error outcome best-effort, and retains browser/durable
  state;
- dependency/auth/idempotency/unrelated-field/cache tests from the first implementation remain intact.

Use call-order assertions, not only call counts, for `attempt audit -> transaction -> outcome audit`.

## 10. Documentation and operator copy

Update `docs/admin-ui/settings.md` and `docs/configuration/general.md` to say:

- reset durably commits factory config and BLOB deletion in one PostgreSQL transaction;
- the response updates the current browser from authoritative committed values;
- the writer normally applies locally before responding, and a degraded response can report automatic
  reconciliation pending;
- Redis normally wakes peer replicas promptly, while healthy peers retry from PostgreSQL every 30
  seconds by default; a persistent DB/subscriber outage can delay convergence beyond that interval;
- uploaded bytes cannot be restored by rollback or by reverting the application version;
- a failed UI result means the request was rejected before commit; a committed-degraded result is shown
  as saved with reconciliation pending.

Replace “now active across the installation,” “immediate global effect,” and equivalent promises in the
Theme toast/docs. Also revise the Settings information card if it still says every broadcast has taken
effect on all replicas immediately.

## 11. Implementation slices and gates

### Slice 1: dynamic commit semantics and audit primitives

Files:

- `src/config_runtime/dynamic.py`
- `src/api/audit.py`
- `src/api/admin/endpoints/common.py`
- `tests/config/test_dynamic.py`
- `tests/test_control_audit_mode.py`

Gate:

- typed post-commit behavior is proven without changing legacy update semantics;
- force-sync/event-ID helper additions preserve every existing caller default;
- focused Ruff and pytest pass.

### Slice 2: repository, endpoint contract, and backend unit coverage

Files:

- `src/db/ui_branding_assets.py` (new)
- `src/services/ui_branding_assets.py`
- `src/config.py`
- `src/api/admin/endpoints/config.py`
- `tests/test_ui_branding.py`

Gate:

- reset has required pre-audit and explicit committed-degraded response;
- raw SQL is repository-owned;
- auth, dependency, ordering, failure, idempotency, and outcome-audit tests pass.

### Slice 3: real PostgreSQL integration proof

Files:

- `tests/test_ui_branding_db_integration.py` (new)

Gate:

- real success, two rollback faults, idempotency, cross-replica serialization, and recovery pass against
  migrated PostgreSQL;
- fixture cleanup restores the exact prior singleton config/assets on both pass and failure.

### Slice 4: frontend consolidation and controller coverage

Files:

- delete `ui/src/lib/brandingApi.ts`
- add `ui/src/lib/brandingResetApi.ts`
- `ui/src/lib/branding.ts`
- `ui/src/lib/settingsTheme.ts`
- `ui/src/components/BrandingProvider.tsx`
- `ui/src/components/settings/useThemeSettingsController.ts` (new)
- `ui/src/components/settings/ThemeSettingsPanel.tsx`
- `ui/src/pages/SettingsPage.tsx`
- replace/extend the UI tests listed in section 8
- `ui/scripts/run-unit-tests.mjs`

Gate:

- no duplicated HTTP operation/type/equality implementation remains;
- controller success/failure/degraded/double-submit/stale-completion tests pass;
- every changed UI file has zero ESLint errors and production build succeeds.

### Slice 5: docs and full verification

Files:

- `docs/admin-ui/settings.md`
- `docs/configuration/general.md`

Gate:

- no copy promises instantaneous fleet-wide application;
- full focused test/lint/build results and any unrelated baseline failures are recorded exactly.

## 12. Verification commands

Backend focused gates:

```bash
uv run ruff check src/config_runtime/dynamic.py src/api/audit.py src/api/admin/endpoints/common.py src/db/ui_branding_assets.py src/services/ui_branding_assets.py src/config.py src/api/admin/endpoints/config.py tests/config/test_dynamic.py tests/test_control_audit_mode.py tests/test_ui_branding.py tests/test_ui_branding_db_integration.py
uv run ruff format --check src/config_runtime/dynamic.py src/api/audit.py src/api/admin/endpoints/common.py src/db/ui_branding_assets.py src/services/ui_branding_assets.py src/config.py src/api/admin/endpoints/config.py tests/config/test_dynamic.py tests/test_control_audit_mode.py tests/test_ui_branding.py tests/test_ui_branding_db_integration.py
uv run pytest tests/config/test_dynamic.py tests/test_control_audit_mode.py tests/test_ui_branding.py
uv run pytest tests/test_ui_branding_db_integration.py
```

Frontend focused gates:

```bash
npm --prefix ui run test:unit
./ui/node_modules/.bin/eslint ui/src/lib/brandingResetApi.ts ui/src/lib/branding.ts ui/src/lib/settingsTheme.ts ui/src/components/BrandingProvider.tsx ui/src/components/settings/useThemeSettingsController.ts ui/src/components/settings/ThemeSettingsPanel.tsx ui/src/pages/SettingsPage.tsx ui/tests/brandingResetApi.test.ts ui/tests/settingsTheme.test.ts ui/tests/themeSettingsController.test.tsx ui/tests/themeSettingsPanel.test.tsx ui/scripts/run-unit-tests.mjs
npm --prefix ui run build
```

Repository gates:

```bash
npm --prefix ui run lint
git diff --check
git status --short
```

Record whether the PostgreSQL suite ran or skipped and why. CI must not skip it. For full UI lint, report
the complete result; regardless of unrelated baseline debt, every file changed by this remediation must
have zero errors. Record the production bundle summary and investigate a material initial-bundle increase.

## 13. Rollout and rollback

- The reset response adds one field on a new endpoint, so existing consumers are unaffected.
- The dynamic-config exception and audit-helper parameters are internal and backwards-compatible by
  default.
- Deploy normally; no migration ordering or feature flag is needed.
- A code rollback does not restore deleted asset bytes. Operators must upload the assets again. This is
  explicitly documented and is why the pre-effect audit is mandatory.
- If post-deploy metrics show local apply failures, keep the durable config as source of truth, diagnose
  the failing subscriber, and let pub/sub/poll reconciliation retry. Do not write old config back merely
  to silence a degraded response.

## 14. Acceptance criteria

The review is fully addressed only when all of the following are true:

- A reset rejected before commit leaves config and assets unchanged and is shown as failure.
- A reset committed before local apply failure returns `200`, authoritative defaults, and
  `reconciliation_pending=true`.
- Redis wake-up is attempted after every committed reset, and DB reload demonstrably recovers the local
  manager/cache.
- With audit enabled, a durable attempt exists before any BLOB deletion; a missing required audit service
  fails closed before mutation.
- Post-effect audit failure cannot change a committed reset response.
- Real PostgreSQL proves atomic success, rollback after delete, callback rollback, idempotency,
  serialization, unrelated-field preservation, and recovery.
- Exactly one implementation owns each branding HTTP operation, and `sameBranding` has one definition.
- The Settings Theme coordinator—not only its panel callbacks—has deterministic tests for success,
  rejection, committed degradation, retry, double submit, and stale completion.
- UI and docs describe convergence accurately and never claim all replicas are already active at return.
- Focused backend tests, real DB tests in CI, UI unit tests, changed-file Ruff/ESLint, UI build, full lint
  result, and `git diff --check` are reported honestly.

## 15. Out of scope

- Cleaning all 106 pre-existing lint errors in `ui/src/lib/api.ts` or completing a full domain-by-domain
  API barrel migration.
- Adding a durable reconciliation job; the existing Redis wake-up plus PostgreSQL poll is sufficient for
  this bounded config mutation.
- Restoring or versioning deleted branding BLOBs.
- Changing factory colours/assets, adding tenant branding, or resetting non-theme settings.
- Changing existing upload/delete HTTP behavior when their post-commit local subscriber fails; those
  endpoints continue to surface failure as they do today, while the new typed exception makes a later
  explicitly scoped reconciliation fix possible.
