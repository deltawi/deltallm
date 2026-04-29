# Issue 106 Plan: Monthly Organization Budget Reset

## Objective

Expose and implement monthly budget reset for organizations.

Target outcome:

- organization admins can configure a monthly reset policy from the admin UI
- org API responses include reset configuration and next reset time
- runtime budget enforcement resets org spend when the configured reset boundary is reached
- monthly reset uses calendar-month semantics instead of a fixed 30-day approximation
- true unused-budget rollover remains out of scope and tracked separately

Related issues:

- [#106](https://github.com/deltawi/deltallm/issues/106): monthly reset
- [#123](https://github.com/deltawi/deltallm/issues/123): true rollover

Worktree:

- `/Users/mehditantaoui/Documents/Challenges/deltallm-issue-106-monthly-reset`

Branch:

- `issue-106-monthly-reset`

## Problem Statement

Budget reset columns exist in the database, but organization management screens do not expose them. The backend also has partial reset behavior, but it does not support a true monthly duration.

Current org UI can manage:

- `max_budget`
- `soft_budget`
- RPM/TPM/RPH/RPD/TPD limits
- audit content storage
- asset access controls

Current org UI cannot manage:

- `budget_duration`
- `budget_reset_at`

This means operators can set hard and soft org budgets, but they cannot configure a recurring monthly budget cycle from the UI.

## Current State

### Data model

The schema already includes budget reset columns on all major budget-bearing entities:

- `DeltaLLM_VerificationToken`
- `DeltaLLM_UserTable`
- `DeltaLLM_TeamTable`
- `DeltaLLM_OrganizationTable`

Relevant org columns:

```prisma
max_budget      Float?
soft_budget     Float?
spend           Float    @default(0)
budget_duration String?
budget_reset_at DateTime?
```

No schema migration is required for the first org-only monthly reset slice.

### Spend model

Spend is maintained in two forms:

1. append-only request events in `deltallm_spendlog_events`
2. cumulative `spend` counters on key/user/team/org rows

Budget enforcement uses the cumulative counters. Reporting uses the append-only event table.

This issue should preserve that model.

### Reset behavior

`BudgetEnforcementService` checks budgets before requests. During budget checks, it calls `_check_budget_reset()`.

Current reset behavior:

- requires both `budget_duration` and `budget_reset_at`
- only runs lazily when traffic arrives for that entity
- if `budget_reset_at <= now`, it sets `spend = 0`
- then it updates `budget_reset_at` to the next reset timestamp
- supports positive integer `h` and `d` suffixes

Current duration parser:

- `1h`
- `1d`
- `7d`
- `30d`

Missing:

- `1mo`
- calendar-month date handling
- durable monthly anchors for existing rows that predate the UI metadata
- focused tests for reset behavior

### Admin organization API

Organization list/detail/create/update currently include budget amounts but not reset fields.

Required API additions:

- select `budget_duration`
- select `budget_reset_at`
- validate write payloads
- persist write payloads
- return reset fields in responses

### Admin UI

The org create drawer and org detail settings panel currently only expose budget amount and soft alert.

Required UI additions:

- monthly reset toggle or selector
- next reset date/time input
- current reset policy display
- payload support for `budget_duration` and `budget_reset_at`

## Scope

In scope:

1. Organization-level monthly budget reset.
2. Admin API read/write support for org `budget_duration` and `budget_reset_at`.
3. Runtime support for `1mo`.
4. Calendar-safe next-month calculation.
5. Admin UI controls for org create and org detail settings.
6. Focused backend and frontend tests.
7. Documentation of intended semantics in this plan and public budget docs if implementation changes user-visible behavior.

Out of scope:

1. True rollover of unused budget.
2. Team/user/key reset UI.
3. Background scheduler for proactive resets.
4. New budget period ledger tables.
5. Rewriting spend enforcement to query event windows.
6. Recomputing historical cumulative counters from spend events.
7. Per-model budget reset behavior.

## Product Semantics

### Monthly reset means reset, not rollover

At the reset boundary:

- org `spend` becomes `0`
- `budget_reset_at` advances to the next monthly boundary
- `max_budget` remains unchanged
- `soft_budget` remains unchanged
- request spend events remain unchanged

Unused budget is not carried forward.

### Effective duration value

Use:

```text
1mo
```

Rationale:

- internal planning docs already mention `1mo`
- it is compact and consistent with existing `1h` and `1d`
- it avoids ambiguous labels such as `monthly` in the stored DB field

### Calendar-month calculation

Monthly reset must add calendar months, not days.

Examples:

- `2026-01-15T00:00:00Z` -> `2026-02-15T00:00:00Z`
- `2026-01-31T00:00:00Z` -> `2026-02-28T00:00:00Z`
- `2028-01-31T00:00:00Z` -> `2028-02-29T00:00:00Z`
- `2026-12-31T00:00:00Z` -> `2027-01-31T00:00:00Z`

### Next reset anchoring

For the first implementation, keep the existing service shape but improve the calculation.

Recommended behavior:

- if reset is due, advance from the previous `budget_reset_at`, not from `now`
- if multiple monthly windows were missed, advance repeatedly until the next reset is in the future

Reason:

- advancing from `now` causes reset drift
- advancing from the prior reset boundary preserves the operator-selected reset day/time

Example:

- previous `budget_reset_at`: `2026-01-01T00:00:00Z`
- request arrives: `2026-04-28T09:00:00Z`
- next reset should become `2026-05-01T00:00:00Z`

### Initial next reset value

The UI should ask for the next reset date/time when monthly reset is enabled.

Rules:

- `budget_duration = "1mo"` requires `budget_reset_at`
- `budget_reset_at` must parse as ISO 8601
- prefer future timestamps in validation
- allow server-side normalization to UTC

If product wants a shortcut later, we can add "first day of next month" as a UI helper, but the first slice should keep the stored behavior explicit.

## Target API Contract

### Organization response fields

Organization list and detail responses should include:

```json
{
  "budget_duration": "1mo",
  "budget_reset_at": "2026-05-01T00:00:00Z"
}
```

When reset is disabled:

```json
{
  "budget_duration": null,
  "budget_reset_at": null
}
```

### Create organization payload

Enabled:

```json
{
  "organization_name": "Acme",
  "max_budget": 5000,
  "soft_budget": 4000,
  "budget_duration": "1mo",
  "budget_reset_at": "2026-05-01T00:00:00Z"
}
```

Disabled:

```json
{
  "organization_name": "Acme",
  "max_budget": 5000,
  "budget_duration": null,
  "budget_reset_at": null
}
```

### Update organization payload

Enable or change reset:

```json
{
  "budget_duration": "1mo",
  "budget_reset_at": "2026-06-01T00:00:00Z"
}
```

Disable reset:

```json
{
  "budget_duration": null,
  "budget_reset_at": null
}
```

### Validation

Accept:

- `null`
- positive integer duration strings from `1` through `10000` ending in `h`, `d`, or `mo`
- examples: `"1h"`, `"7d"`, `"30d"`, `"1mo"`, `"3mo"`

For issue #106 UI, only expose monthly reset. Backend can preserve existing hour/day compatibility.

Reject:

- unsupported units, for example `"1m"` or `"monthly"`
- non-positive or out-of-range values

### Create Upsert Semantics

`POST /ui/api/organizations` already behaves as an upsert through `ON CONFLICT (organization_id) DO UPDATE`.

Reset fields follow the same omitted-field contract as `PUT`:

- omitted reset fields preserve existing `budget_duration`, `budget_reset_at`, and `_budget_reset` metadata on conflict
- explicit `null` reset fields clear reset columns and remove `_budget_reset`
- explicit reset values update reset columns and write or replace the monthly anchor metadata
- missing `budget_reset_at` when `budget_duration` is set
- `budget_reset_at` without `budget_duration`
- invalid datetime strings

## Implementation Plan

### Phase 1: Backend reset semantics

Files:

- `src/billing/budget.py`
- `tests/test_billing.py`

Tasks:

- [ ] replace `_next_reset(duration, now)` with a helper that can advance from an anchor timestamp
- [ ] support `1mo`
- [ ] preserve existing `h` and `d` behavior
- [ ] advance repeated missed windows until next reset is in the future
- [ ] add calendar-month helper without adding a new dependency
- [ ] add tests for month-end and leap-year behavior
- [ ] add tests for missed reset windows

Recommended helper shape:

```python
def _next_reset_after(*, duration: str, previous_reset_at: datetime, now: datetime) -> datetime | None:
    next_reset = _advance_reset(duration, previous_reset_at)
    while next_reset <= now:
        next_reset = _advance_reset(duration, next_reset)
    return next_reset
```

Recommended month helper:

```python
def _add_months(value: datetime, months: int) -> datetime:
    ...
```

Use `calendar.monthrange()` from the standard library to clamp day-of-month.

Risk control:

- keep helper functions pure and unit-test them directly
- avoid broad service refactors

### Phase 2: Organization API fields and validation

Files:

- `src/api/admin/endpoints/organizations.py`
- `tests/test_ui_organizations_assets.py`
- `tests/test_ui_rate_limits.py` if current fake DB coverage requires it

Tasks:

- [ ] add `budget_duration` and `budget_reset_at` to organization list SELECT
- [ ] add both fields to organization detail SELECT
- [ ] parse and validate both fields in create
- [ ] insert both fields on create
- [ ] preserve or update both fields on update
- [ ] include both fields in returned payloads
- [ ] update fake admin DB test utilities to store and return both fields
- [ ] add tests for create persistence
- [ ] add tests for update persistence
- [ ] add tests for invalid duration
- [ ] add tests for missing reset timestamp when duration is present
- [ ] add tests for reset timestamp without duration

Recommended helper names:

- `_optional_budget_duration(value, field_name)`
- `_optional_datetime(value, field_name)`
- `_resolve_budget_reset_fields(payload, existing=None)`

Important update semantics:

- omitted fields should preserve existing values
- explicit `null` should clear values
- duration and reset timestamp should be validated as a pair after resolving omitted fields

### Phase 3: UI type and payload support

Files:

- `ui/src/lib/api.ts`
- `ui/src/pages/OrganizationCreate.tsx`
- `ui/src/pages/OrganizationDetail.tsx`
- possibly `ui/src/pages/Organizations.tsx`

Tasks:

- [ ] define or extend organization type fields if this page currently uses `any`
- [ ] add form fields:
  - `budget_reset_enabled`
  - `budget_duration`
  - `budget_reset_at`
- [ ] on create, send `budget_duration: "1mo"` and ISO `budget_reset_at` only when enabled
- [ ] on edit, initialize form from existing org reset fields
- [ ] allow disabling reset by sending both fields as `null`
- [ ] display current reset policy in org settings summary
- [ ] keep the UI focused on monthly reset only

Recommended UX:

- add a "Monthly reset" toggle under budget limit controls
- when enabled, show "Next reset" date/time input
- store value as ISO string before sending to API
- show summary text:
  - `Monthly reset`
  - `Next reset: May 1, 2026, 00:00 UTC`

Avoid:

- exposing hour/day duration options in the first UI pass
- implying rollover or carry-forward
- hiding reset settings outside the budget section

### Phase 4: Documentation touch-up

Files:

- `docs/features/budgets.md`

Tasks:

- [ ] update "Soft Budgets and Resets" with supported duration values
- [ ] mention monthly reset is reset-only, not rollover
- [ ] mention UI support is org-level in this implementation
- [ ] link or note that true rollover is separate future work if appropriate

Keep this concise; the implementation plan remains the detailed internal source.

### Phase 5: Verification

Backend focused tests:

```bash
uv run pytest tests/test_billing.py tests/test_ui_organizations_assets.py tests/test_ui_rate_limits.py
```

Frontend validation:

```bash
npm run build --prefix ui
```

Optional broader checks:

```bash
uv run ruff check src/billing/budget.py src/api/admin/endpoints/organizations.py tests/test_billing.py tests/test_ui_organizations_assets.py
```

Manual smoke path:

1. create org with budget enabled and monthly reset enabled
2. verify detail page shows reset policy and next reset
3. edit next reset timestamp
4. disable reset
5. confirm API returns `null` for both fields after disable

## Efficient Delivery Order

Do the work in this order:

1. Backend pure reset helper tests first.
2. Implement `1mo` runtime reset support.
3. Add organization API validation and persistence tests.
4. Implement organization API changes.
5. Add UI form state and payload wiring.
6. Add UI display text for existing reset policy.
7. Run focused backend tests.
8. Run UI build.
9. Update public budget docs.

Reasoning:

- reset helper behavior is the riskiest logic and easiest to validate independently
- API persistence must be stable before UI wiring
- UI should not invent semantics that backend does not enforce
- docs should reflect the final implementation, not the initial intent

## Data Consistency Notes

### Existing rows

Existing orgs have `budget_duration = null` and `budget_reset_at = null`, so reset remains disabled.

### Partial updates

Organization update currently resolves omitted fields from the existing row. Preserve that pattern.

Examples:

- payload omits reset fields: keep existing reset config
- payload sets both fields to `null`: disable reset
- payload sets duration only: reject unless an existing or payload reset timestamp is available
- payload sets reset timestamp only: reject unless an existing or payload duration is available

### Timezone handling

Backend should parse ISO 8601 values and normalize them to UTC. Naive timestamps are treated as UTC for backward compatibility.

The database stores `budget_reset_at` as UTC-naive `TIMESTAMP(3)` because that is the existing schema shape. API responses must serialize `budget_reset_at` back as an explicit UTC ISO string ending in `Z`, so the UI never interprets offset-less values as browser-local time.

UI reset controls should use UTC date/time helpers and label reset inputs as UTC.

### Concurrency

Reset remains lazy, but the reset write must be guarded against stale rows.

This issue reduces drift but does not need a scheduler or full lock-based reset. However, the implementation should avoid making concurrency worse.

Use an update guarded by the previous reset timestamp:

```sql
UPDATE ...
SET spend = 0,
    budget_reset_at = $1,
    updated_at = NOW()
WHERE organization_id = $2
  AND budget_reset_at IS NOT DISTINCT FROM $3
```

If the guard updates zero rows, re-fetch or return without overwriting in-memory state. This prevents stale concurrent reset writes.

Do not broaden this into a full budget service redesign.

### Month-end semantics

For `1mo`, preserve the selected UTC day of month using an internal anchor stored in organization metadata:

```json
{
  "_budget_reset": {
    "monthly_anchor_day": 30
  }
}
```

The anchor is derived from the submitted UTC `budget_reset_at` when monthly reset fields are created or changed. Omitted reset fields preserve the existing anchor. Clearing reset removes the private metadata key. If a monthly reset row has no anchor metadata, infer the anchor from the current `budget_reset_at` day and persist it during the guarded reset update.

Examples:

- anchor day 30: `2026-01-30T00:00:00Z` -> `2026-02-28T00:00:00Z` -> `2026-03-30T00:00:00Z`
- anchor day 31: `2026-01-31T00:00:00Z` -> `2026-02-28T00:00:00Z` -> `2026-03-31T00:00:00Z`
- anchor day 31 in leap year: `2028-01-31T00:00:00Z` -> `2028-02-29T00:00:00Z` -> `2028-03-31T00:00:00Z`

Rows without anchor metadata use the previous fallback behavior.

## Test Matrix

### Budget helper tests

- [ ] `1h` advances from previous reset
- [ ] `1d` advances from previous reset
- [ ] `30d` continues to work
- [ ] `1mo` Jan 15 -> Feb 15
- [ ] `1mo` Jan 31 -> Feb 28 in non-leap year
- [ ] `1mo` Jan 31 -> Feb 29 in leap year
- [ ] `1mo` Dec 31 -> Jan 31 next year
- [ ] missed monthly windows advance until future
- [ ] unsupported duration returns `None`
- [ ] out-of-range duration returns `None`
- [ ] very old hourly and monthly reset windows advance without repeated window-by-window loops

### Budget enforcement tests

- [ ] due org monthly reset sets spend to zero
- [ ] after reset, hard budget check does not reject using stale pre-reset spend
- [ ] not-due reset does not update the row
- [ ] invalid reset config does not crash request path

### Organization API tests

- [ ] create org persists `budget_duration` and `budget_reset_at`
- [ ] detail returns both fields
- [ ] list returns both fields
- [ ] update org changes both fields
- [ ] update org clears both fields
- [ ] create/upsert omitting reset fields preserves existing reset config
- [ ] create/upsert explicit null reset fields clears existing reset config
- [ ] invalid duration returns `400`
- [ ] reset timestamp without duration returns `400`
- [ ] duration without reset timestamp returns `400`
- [ ] existing soft-budget validation still works

### UI validation

- [ ] create payload includes reset fields only when monthly reset is enabled
- [ ] edit form initializes from existing reset fields
- [ ] save sends explicit nulls when disabling reset
- [ ] org detail displays current monthly reset policy
- [ ] UI build passes

## Rollout Plan

This can ship as a backward-compatible feature:

- no migration needed
- existing orgs remain reset-disabled
- existing API clients continue to work
- new fields are additive in responses

Recommended release note:

> Organizations can now configure monthly budget resets from the admin UI. Monthly reset clears the tracked org spend counter at the configured reset time; unused budget does not roll over.

## Risks and Mitigations

### Risk: monthly reset drifts over time

Mitigation:

- calculate from previous reset boundary, not request time
- add missed-window tests

### Risk: month-end behavior is surprising

Mitigation:

- clamp to last valid day of target month
- document examples in tests

### Risk: UI implies rollover

Mitigation:

- use "Monthly reset" language
- do not mention carry-forward in UI
- keep rollover tracked in issue #123

### Risk: concurrent reset request races

Mitigation:

- prefer guarded update using previous `budget_reset_at`
- keep behavior lazy for now
- leave scheduler/ledger changes out of scope

### Risk: API null/omitted semantics regress existing update flows

Mitigation:

- preserve existing omitted-field behavior
- test explicit null clearing

## Definition of Done

- [ ] org create API accepts and persists monthly reset settings
- [ ] org update API accepts, changes, and clears monthly reset settings
- [ ] org list/detail API returns reset settings
- [ ] budget runtime supports `1mo`
- [ ] monthly reset advances without drift
- [ ] org create UI exposes monthly reset settings
- [ ] org detail settings UI exposes and displays monthly reset settings
- [ ] focused backend tests pass
- [ ] UI build passes
- [ ] public budget docs are updated

## Follow-up Work

Future issues:

- true rollover with carry-forward balance
- team/user/key reset UI
- proactive reset scheduler
- budget period ledger for auditability
- reset history display in UI
