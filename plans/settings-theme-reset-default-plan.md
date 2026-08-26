# Settings Theme reset-to-default implementation plan

> Status: implementation-ready plan; no product code has been changed.
>
> Worktree: `.worktrees/settings-theme-reset-default`
>
> Branch: `feature/settings-theme-reset-default`
>
> Baseline: `ede81d01fb1d1fd6d4d65369746341fd46160b3b` (`docs/mkdocs-branding` at worktree creation)
>
> Prepared: 2026-08-25

## 1. Outcome

Add a platform-admin-only **Reset to DeltaLLM defaults** action to **Settings > Theme**. One confirmed action must restore the built-in instance name and colours, remove all custom logo/favicon references, permanently delete the stored branding asset BLOBs, update the open browser immediately, and propagate the effective default theme to every replica.

The reset must be one backend mutation. The browser must not approximate it by issuing the existing theme update plus three independent asset-delete calls, because that can expose a partially reset theme after a network, process, or database failure.

## 2. Product decisions

These decisions remove ambiguity from implementation and acceptance testing.

1. **“Default” means the built-in DeltaLLM factory theme**, not “the last saved theme” and not “reveal values from the deployment YAML.” The factory values are:

   - instance name: `DeltaLLM`;
   - simple logo, expanded logo, and favicon overrides: none;
   - primary colour: `#5B50D6`;
   - secondary colour: `#8B7CFF`;
   - menu-hover colour: `#F7F5FF`.
2. The UI label will say **Reset to DeltaLLM defaults** rather than only **Reset**, so an operator does not confuse it with the existing **Discard changes** action.
3. Reset is immediate after confirmation. It does not require a second click on **Save Changes**.
4. Reset also discards unsaved name/colour edits in the open form.
5. Uploaded asset bytes are permanently removed. They cannot be recovered by **Discard changes** and must be uploaded again if needed.
6. Existing file-based branding is not exposed by reset. The backend persists explicit factory values/null asset references as dynamic database overrides. This matches the documented rule that Admin UI theme values take precedence over file configuration until changed again.
7. The operation is globally scoped to the installation. Only a platform administrator or the existing master-key break-glass principal may invoke it. Organization/team administrators remain denied by the backend.
8. Repeating the reset is safe and converges on the same effective state. The UI will not automatically retry a failed mutation, but an operator can retry it safely.

## 3. Current-state findings

The implementation must extend, rather than duplicate, the existing branding path.

- `ui/src/pages/SettingsPage.tsx` has a **Discard changes** control that copies `persistedBranding` back into the form. It never applies factory defaults.
- `ui/src/lib/branding.ts` contains the browser fallback `DEFAULT_BRANDING` object.
- `src/config.py` declares the same defaults across `UIBrandingSettings`, `UIBrandingPayload`, `UIBrandingUpdatePayload`, and `GeneralSettings`.
- `PUT /ui/api/branding` updates only the instance name and three colours.
- Each custom asset is uploaded or deleted through its own `/ui/api/branding/assets/{asset_key}` mutation and is applied immediately.
- `DynamicConfigManager.update_config(..., transaction_mutation=...)` already serializes the singleton config row and commits a related asset-table mutation in the same PostgreSQL transaction.
- `UIBrandingAssetService.on_config_change` refreshes the bounded in-memory asset cache when the configured asset URLs change. Redis is only the wake-up path; database polling remains the convergence fallback.
- The reset path does not need a migration, a new setting, a new Redis key, or any data-plane work.
- `SettingsPage.tsx` is 916 lines and `ui/src/lib/api.ts` is 2,337 lines. The change should extract the touched theme view and branding API surface rather than grow both change magnets.

## 4. Invariants and ownership

| Concern | Decision |
| --- | --- |
| Durable source of truth | The `proxy_config` row in PostgreSQL plus `deltallm_ui_branding_asset` rows commit in one transaction. |
| Policy owner | The backend computes factory defaults; the browser never sends a client-authored default payload. |
| Authorization | `Permission.PLATFORM_ADMIN` on the reset endpoint, using the existing admin dependency. |
| Tenant scope | Installation-wide; no client-supplied organization, team, or account identifier. |
| Atomicity | All known branding asset rows are deleted and all branding fields are reset in one `DynamicConfigManager` transaction. |
| Idempotency | Repeated calls leave the same config and an empty known-asset set. No external side effect is inside a retry/failover scope. |
| Cache/invalidation | Existing config subscriber, Redis notification, and DB poll paths apply the reset to local and peer replicas. |
| Audit owner | The reset endpoint emits one dedicated admin mutation audit event with safe before/after branding projections. |
| Failure mode | Validation or durable write failure leaves the transaction unchanged. A failed request keeps the current browser state and shows an error; it never pretends reset succeeded. |
| Latency | Control plane only: one serialized config transaction, one bounded delete statement, local subscriber refresh, and one Redis publish attempt. No inference-path call or new background task. |

## 5. Target request flow

```text
Theme panel
  -> confirmation dialog
  -> POST /ui/api/branding/reset
       -> platform-admin authorization
       -> construct server-owned factory payload
       -> DynamicConfigManager serialized transaction
            -> delete known branding BLOB rows
            -> persist factory name/colours and null asset URLs
       -> apply local config + refresh local asset cache
       -> publish existing config_updated signal
       -> emit ADMIN_UI_BRANDING_RESET audit event
  <- effective UIBrandingPayload
  -> replace form + persisted snapshot + BrandingContext
  -> close dialog and show success toast
```

## 6. Backend design

### 6.1 Canonical factory values

In `src/config.py`:

1. Introduce named backend constants for the factory instance name and three colours.
2. Use those constants as the defaults for `UIBrandingSettings`, `UIBrandingPayload`, `UIBrandingUpdatePayload`, and `GeneralSettings` so the backend no longer repeats literal values.
3. Continue representing default asset overrides as `None`.
4. Construct the reset target from `UIBrandingPayload()` on the server. Do not accept a request body and do not trust the frontend `DEFAULT_BRANDING` object as mutation input.

The TypeScript `DEFAULT_BRANDING` remains necessary for bootstrap/failure rendering, but reset correctness comes from the response returned by the backend.

### 6.2 Asset deletion seam

In `src/services/ui_branding_assets.py`:

1. Add `delete_all_in_transaction(db_client)` beside the existing single-asset deletion method.
2. Delete only the allowlisted `BRANDING_ASSET_KINDS` in one parameterized SQL statement. Do not loop over three individual database calls.
3. Return no per-row result; the operation is successful when zero rows already exist, preserving idempotency.
4. Keep cache mutation out of the transaction helper. The existing config-change subscriber remains the sole cache refresh owner after commit.

### 6.3 Reset endpoint

In `src/api/admin/endpoints/config.py`, add:

```text
POST /ui/api/branding/reset
Authorization: Permission.PLATFORM_ADMIN
Request body: none
Success: 200 UIBrandingPayload
```

Endpoint sequence:

1. Resolve the dynamic config manager and branding asset service using the existing guarded accessors; return `503` before mutation if either required dependency is unavailable.
2. Capture the safe effective `before` projection.
3. Construct `factory = UIBrandingPayload()`.
4. Call `dynamic_config.update_config` exactly once with:
   - `general_settings.instance_name = factory.instance_name`;
   - all `ui_branding` fields from `factory`, including the three `None` asset URLs;
   - `updated_by="admin_api"`;
   - a transaction mutation that calls `delete_all_in_transaction`.
5. Read the effective `after` projection from the newly applied app config.
6. Emit one `ADMIN_UI_BRANDING_RESET` audit event. Its request payload is a fixed safe marker such as `{"target": "factory_defaults"}`; before/after values use the allowlisted branding DTO.
7. Return `after`.

Do not implement reset by calling the existing update/delete endpoint functions internally. The reset route owns one mutation and one audit event.

### 6.4 Audit action

Add `ADMIN_UI_BRANDING_RESET` to `src/audit/actions.py`. Keep the existing update/upload/delete actions unchanged so audit consumers retain their current meanings.

### 6.5 Persistence and failure semantics

- The related asset delete and config write must use the existing PostgreSQL transaction path. A failure in either rolls back both.
- Redis publish failure follows current dynamic-config behavior: the committed reset succeeds locally, a warning is logged, and peer replicas recover through polling.
- An idempotent retry after an ambiguous response is safe: it rewrites the same factory values and finds zero or the same allowlisted asset rows.
- The implementation does not add a second config lifecycle, direct Redis mutation, or process-local durable state.
- No schema migration is required because reset only updates the existing JSON config and deletes existing asset rows.

## 7. Frontend design

### 7.1 Extract the branding API module

Create `ui/src/lib/brandingApi.ts`:

- move `UIBrandingResponse`, `UIBrandingUpdate`, `UIBrandingAssetKind`, and the `branding` client from the oversized `ui/src/lib/api.ts`;
- import `apiFetch` directly from `ui/src/lib/apiClient.ts`;
- preserve re-exports from `ui/src/lib/api.ts` during this migration so current consumers do not break;
- add `branding.reset()` which sends `POST /ui/api/branding/reset` with no JSON body and returns `UIBrandingResponse`;
- update `BrandingProvider` and the Settings theme workflow to consume the domain module directly.

All requests continue through the shared transport, preserving authentication, CSRF/origin behavior, structured errors, timeouts, and redaction.

### 7.2 Extract the theme view

Create `ui/src/components/settings/ThemeSettingsPanel.tsx` and move the theme-only presentational pieces out of `SettingsPage.tsx`:

- branding asset fields;
- colour fields;
- identity, colour, and preview cards;
- **Discard changes** and **Reset to DeltaLLM defaults** controls;
- the shared `ConfirmDialog` instance for reset.

The component remains below 400 lines and receives typed state/callback props. Route-level data loading and mutation orchestration remain in `SettingsPage.tsx`. Pure validation, equality, and form-to-update conversion move to a small tested module such as `ui/src/lib/settingsTheme.ts`.

This extraction must reduce `SettingsPage.tsx`; it must not create a second theme implementation or duplicate desktop/mobile business rules.

### 7.3 Mutation state and race prevention

Replace overlapping theme booleans with one discriminated mutation state:

```text
idle | save | reset | upload:logo_mark | upload:logo_full | upload:favicon
     | delete:logo_mark | delete:logo_full | delete:favicon
```

While a theme mutation is active:

- disable Save, upload, replace, remove, discard, and reset controls;
- keep the confirmation dialog open and locked during reset;
- prevent a second reset submission;
- do not apply stale results from another theme mutation, because no other theme mutation can start concurrently.

Keep non-theme Settings saving state separate so the header renders the correct busy/success state for the active tab.

### 7.4 Reset interaction

1. Render a visible text button, not an icon-only control.
2. Opening it shows the shared confirmation dialog with:
   - title: **Reset theme to DeltaLLM defaults?**
   - explanation that the name and colours will change immediately for the installation;
   - explicit warning that uploaded simple logo, expanded logo, and favicon files will be permanently deleted;
   - note that unsaved edits will also be discarded;
   - destructive confirm label: **Reset theme**.
3. On confirm, call only `branding.reset()`.
4. On success:
   - normalize the returned payload;
   - replace `brandingForm` and `persistedBranding` with that response;
   - call `setBranding(response)` so the current shell, title, and favicon update immediately;
   - clear prior theme errors;
   - close the confirmation dialog;
   - show a success toast.
5. On failure:
   - leave the form, persisted snapshot, and branding context unchanged;
   - keep or restore an actionable inline error;
   - show an error toast;
   - unlock the dialog for a retry.

**Discard changes** retains its current meaning: restore the last saved form values without touching PostgreSQL. Asset **Remove** retains its current immediate single-asset behavior.

### 7.5 Default-state behavior

Use a typed branding equality helper rather than `JSON.stringify` for dirty/default comparisons. Disable the reset button only when both the persisted theme and current form equal `DEFAULT_BRANDING`; otherwise it remains available. The backend response is still authoritative if the browser fallback constants ever drift.

## 8. Test plan

### 8.1 Backend contract and behavior (`tests/test_ui_branding.py`)

Add focused tests for:

1. unauthenticated reset returns `401` and performs no config or asset mutation;
2. organization admin reset returns `403` and performs no mutation;
3. platform admin session and master-key authentication can reset;
4. a fully customized theme with all three asset rows resets to the exact factory response;
5. the dynamic update contains the factory name, colours, and explicit null asset URLs and receives one transaction callback;
6. all known asset rows are removed by one bulk delete and old versioned asset URLs return `404` after local cache refresh;
7. unrelated general/router/deltallm settings survive the deep merge unchanged;
8. a second reset is successful and leaves the same state;
9. config-manager or branding-asset-service unavailability returns `503` before mutation;
10. captured audit arguments contain `ADMIN_UI_BRANDING_RESET`, the fixed request marker, and safe before/after projections;
11. a peer asset cache becomes empty when the reset config is applied, proving existing replica refresh behavior is reused.

Update the fake asset database so it models the one-statement allowlisted bulk delete without weakening the existing single-delete assertions.

The existing `tests/config/test_dynamic.py::test_dynamic_config_runs_related_database_mutation_inside_config_transaction` remains the transaction-boundary proof. Run the related dynamic-config tests even if they require no source edit.

### 8.2 Frontend API and domain tests

- Extend `ui/tests/brandingApi.test.ts` to assert that `branding.reset()` uses exactly `POST /ui/api/branding/reset`, has no JSON body, and returns the typed branding response through shared transport.
- Extend `ui/tests/branding.test.ts` or add `ui/tests/settingsTheme.test.ts` for typed equality, dirty/default detection, validation, and reset-response reconciliation.
- If a new test file is added, register it in `ui/scripts/run-unit-tests.mjs` in the same change, because the current runner has a hard-coded list.

### 8.3 Frontend interaction test

Add a JSDOM test for `ThemeSettingsPanel` that verifies:

- the reset button is visible when the theme is customized;
- activating it opens the confirmation text and does not mutate immediately;
- cancel and Escape close the dialog without invoking reset;
- confirm invokes reset once;
- controls are disabled and the dialog cannot close while reset is pending;
- the default state disables reset;
- the existing discard callback remains distinct from reset.

Because the shared dialog is reused rather than changed, no shared-modal refactor is needed.

### 8.4 Manual responsive/accessibility smoke

At widths below and above `768px`:

1. tab to Reset, open it with Enter/Space, and confirm initial focus is inside the dialog;
2. verify Tab/Shift+Tab remain trapped, Escape cancels only when idle, and focus returns to Reset;
3. verify the pending state cannot be double-submitted or dismissed;
4. verify long instance names, error text, and both footer buttons wrap without horizontal scrolling;
5. reset a theme with each asset kind and verify shell logo, browser title, favicon, buttons, and navigation update without a reload;
6. open a second browser/session and verify it converges through the existing focus/visibility refresh and replica update path.

## 9. Documentation changes

Update:

- `docs/admin-ui/settings.md` to distinguish **Discard changes** from **Reset to DeltaLLM defaults**, describe confirmation and immediate global effect, and state that uploaded asset bytes are deleted;
- `docs/configuration/general.md#ui-branding` to document that reset writes explicit factory overrides, does not reveal YAML branding, and uses the same transaction/replica propagation path.

No `.env.example`, Helm values/schema, or `config.example.yaml` change is required because the feature adds no setting and does not change the default values.

## 10. Implementation slices

### Slice 1: backend atomic reset

Files:

- `src/config.py`
- `src/services/ui_branding_assets.py`
- `src/api/admin/endpoints/config.py`
- `src/audit/actions.py`
- `tests/test_ui_branding.py`

Gate:

- server computes defaults;
- one authorized endpoint call resets config and deletes assets atomically;
- authorization, idempotency, failure, cache refresh, and audit tests pass.

### Slice 2: typed API and theme extraction

Files:

- `ui/src/lib/brandingApi.ts` (new)
- `ui/src/lib/api.ts`
- `ui/src/lib/settingsTheme.ts` (new)
- `ui/src/components/BrandingProvider.tsx`
- `ui/src/components/settings/ThemeSettingsPanel.tsx` (new)
- `ui/src/pages/SettingsPage.tsx`

Gate:

- existing branding callers compile through direct module imports or stable barrel re-exports;
- Settings page shrinks and has no new `any`;
- current save/upload/delete/discard behavior is preserved before reset is wired.

### Slice 3: reset UX and frontend coverage

Files:

- theme component/controller files from Slice 2;
- `ui/tests/brandingApi.test.ts`;
- `ui/tests/branding.test.ts` and/or `ui/tests/settingsTheme.test.ts`;
- `ui/tests/themeSettingsPanel.test.tsx` if kept separate;
- `ui/scripts/run-unit-tests.mjs` for every new test file.

Gate:

- confirmation, busy locking, response reconciliation, toasts, and responsive keyboard behavior satisfy the contract;
- API/domain/component unit tests pass.

### Slice 4: docs and full verification

Files:

- `docs/admin-ui/settings.md`
- `docs/configuration/general.md`

Gate:

- docs match factory-vs-file semantics and irreversible asset deletion;
- all proportionate repository checks pass with exact results recorded.

## 11. Verification commands

Run focused checks first:

```bash
uv run ruff check src/config.py src/services/ui_branding_assets.py src/api/admin/endpoints/config.py src/audit/actions.py tests/test_ui_branding.py
uv run ruff format --check src/config.py src/services/ui_branding_assets.py src/api/admin/endpoints/config.py src/audit/actions.py tests/test_ui_branding.py
uv run pytest tests/test_ui_branding.py tests/config/test_dynamic.py
npm --prefix ui run test:unit
./ui/node_modules/.bin/eslint ui/src/lib/brandingApi.ts ui/src/lib/settingsTheme.ts ui/src/components/BrandingProvider.tsx ui/src/components/settings/ThemeSettingsPanel.tsx ui/src/pages/SettingsPage.tsx ui/tests/brandingApi.test.ts ui/tests/branding.test.ts ui/tests/settingsTheme.test.ts ui/tests/themeSettingsPanel.test.tsx ui/scripts/run-unit-tests.mjs
```

Then run the required UI/repository gates:

```bash
npm --prefix ui run build
npm --prefix ui run lint
git diff --check
git status --short
```

For full lint, compare the result with the current recorded baseline and require zero errors in every touched file. Record Vite gzip output and confirm the extraction/reset code does not increase the initial bundle materially. Omit nonexistent optional test filenames from the touched-file ESLint command if the implementation extends existing test files instead.

## 12. Acceptance criteria

The work is complete only when all of the following are true:

- A platform administrator can see and keyboard-activate **Reset to DeltaLLM defaults** in Settings > Theme.
- Confirmation clearly distinguishes reset from discard and names permanent asset deletion.
- One backend request resets the complete theme; the browser never orchestrates multiple delete/update requests.
- Unauthorized and organization-scoped principals cannot reset branding.
- Factory values are computed on the server and returned through the typed public branding DTO.
- Config and all known asset BLOB deletions commit atomically or not at all.
- The operation is safe to repeat and preserves unrelated configuration.
- The current browser updates its shell, title, favicon, form, and persisted snapshot from the response.
- Other replicas converge through the existing config invalidation/reconciliation lifecycle.
- Reset emits a distinct, safe audit action.
- Existing save, discard, upload, replace, and single-asset remove behaviors remain intact.
- Loading, success, failure, retry, mobile layout, and keyboard/focus behavior are verified.
- Focused backend tests, UI unit tests, touched-file lint, production UI build, full lint baseline comparison, and `git diff --check` are reported honestly.

## 13. Out of scope

- Resetting routing, caching, reliability, authentication, or any non-theme setting.
- Adding tenant- or organization-specific themes.
- Restoring deleted asset bytes or keeping an asset history.
- Adding new branding fields, file formats, upload limits, or remote asset fetching.
- Changing the built-in DeltaLLM colours/assets themselves.
- Adding a new database table, migration, Redis namespace, background worker, or deployment setting.
