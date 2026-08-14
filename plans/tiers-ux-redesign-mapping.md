# Tiers UX redesign: functionality and theme map

> Status: historical discovery and visual/functionality map. No production UI or API implementation has started. For implementation sequencing, concurrency, idempotency, endpoint compatibility, rollout, and acceptance criteria, the authoritative document is `tiers-ux-redesign-remediation-plan.md` v2; it supersedes any conflicting assumption here.

## Workspace

| Item | Value |
| --- | --- |
| Worktree | `.worktrees/tiers-ux-redesign` |
| Branch | `feature/tiers-ux-redesign` |
| Base commit at worktree creation | `e1e7420cf9be42550a7ae95bcbed9e0afed1d8d8` |
| Base branch at creation | `fix/batch-fair-share-ci-flake` |
| Required implementation base | Freshly fetched `origin/main`, established in remediation Slice 0 |

The worktree was created from the current committed `HEAD`. Untracked files in the source checkout, including the standalone Tiers prototype, were not copied, modified, or deleted.

## Product outcome

The redesign should let a platform admin answer four questions without learning the storage model first:

1. Which tier configuration is live, and is a newer draft waiting?
2. How do I safely change a live tier?
3. Where do model access, limits, pricing, and shared capacity live?
4. What will happen when I activate or discard a version?

The implementation must improve those workflows without removing current capabilities, weakening version immutability, or changing authorization and audit behavior.

## Current implementation map

### Routes and screens

| Area | Current implementation | Behavior that must be preserved |
| --- | --- | --- |
| Tier catalog | `ui/src/pages/Tiers.tsx` | Debounced server search, enabled/disabled filter, server pagination, create tier, tier links, assignment/version counts |
| Capacity operations | `TierCapacityDashboardPanel` on the catalog page | Live-data health, pool saturation, fair-share state, limit hot spots, top consumers, boosts, refresh, partial/unavailable states |
| Tier detail | `ui/src/pages/TierDetail.tsx` | Tier metadata edit/delete, version selection, mutation race guards, status/read-only handling |
| Version overview | `TierVersionOverview.tsx` | Create blank draft, clone active, publish draft, archive, inspect every version |
| Model policies | `TierModelPolicyGrid.tsx` | Add/edit/remove policies, bulk RPM/TPM, all limit fields, pricing, pool binding, priority, enabled and allow/deny controls |
| Capacity pools | `TierCapacityPoolEditor.tsx` | Add/edit/remove pools and configure hard-cap, weighted-fair, and reserved-burst strategies |
| Pricing fields | `TierPricingFields.tsx` and `tierPricing.ts` | Token, cache, batch, character, second, image, audio-token, and per-request pricing profiles; blank and explicit zero have different meanings |

### Data and mutation model

- A tier is metadata plus zero or more immutable version records.
- Only a `draft` version can be edited.
- Model policies and capacity pools are fetched as complete arrays for one version.
- Saving policies or pools uses a full collection replacement `PUT`, not per-row CRUD.
- Publishing a draft atomically archives the prior active version and activates the draft.
- Publishing is blocked while enabled assignments are pinned to the current active version.
- An active version cannot be archived while enabled, non-expired assignments follow the tier or are pinned to that version.
- Any version, including an archived version, can already be cloned to a new draft. The user-facing action should therefore be **Restore as draft**, while the immutable archived record remains intact.
- More than one draft can exist. Version numbers are monotonically increasing, and the detail response is sorted newest first.
- Every Tiers endpoint requires `platform.admin`; the UI also permits the existing master-key admin session. Mutations already emit audit events and trigger policy invalidation where required.

## Proposed information architecture

### `/tiers`: catalog and capacity operations

Use two page-level views so configuration and live operations remain easy to find without competing for attention:

- **Tiers** — the default catalog, filters, table, pagination, and Create tier action.
- **Capacity health** — the existing fairness dashboard, unchanged in capability, with its refresh and degraded-data states intact.

The catalog table should use these columns:

| Column | Content |
| --- | --- |
| Tier | Name, stable key, and a compact neutral `Disabled` badge only when applicable |
| Configuration | Compact content-width `Live vN` and/or `Draft vN` badges according to the truth table below |
| Package | Model-policy and capacity-pool counts from the latest draft when one exists, otherwise from the live version |
| Assignments | Existing assignment count, labelled as assignments rather than organizations because one organization may have multiple assignments |
| Updated | Existing timestamp |
| Action | Open tier |

There will be no Work in progress column or badge. Draft state is the work-in-progress signal.

#### Configuration badge truth table

| Backend state | Catalog display |
| --- | --- |
| Active version, no draft | `Live vN` |
| Draft, no active version | `Draft vN` only |
| Active plus draft | `Live vN` and `Draft vM`, adjacent |
| Multiple drafts | Show the latest `Draft vM`; add a quiet `+N drafts` label when more exist |
| Archived history only | `No live version` |
| No versions | `No configuration` |

`Live` is presentation language for backend status `active`; backend lifecycle values do not need to change. Tier `enabled/disabled` is a separate state and must not be conflated with version status.

### Create tier

Use a focused drawer or modal with the real fields already supported by the API:

- Display name
- Tier key, suggested from the display name but always editable
- Description
- Enabled toggle, with an explanation that enabled assignments still require a live version

Recommended submission flow:

1. Create the tier with the existing endpoint.
2. Immediately create blank Draft v1 with the existing version endpoint.
3. Open the new tier with v1 selected and the model-policy workspace visible.
4. If step 2 fails, keep the created tier, navigate to it, show a persistent recoverable error, and offer **Create draft**. Do not leave the admin on a closed dialog with an apparently missing record.

This preserves the existing API contract. If Draft v1 must be transactionally atomic with tier creation, that is a separate backend contract change and should be approved before implementation.

### `/tiers/:tierId`: version workspace

Replace the split Overview/Policy Editor journey with one persistent workspace:

- A compact left version rail lists the newest draft, live version, and archived history.
- The main header identifies the selected version and clearly says `Editable draft` or `Read-only`.
- The main content uses three real tabs: **Models & limits**, **Pricing**, and **Capacity pools**.
- Tier metadata edit, blank New draft, clone-version, active-version archive, and tier delete remain available under appropriately labelled secondary or overflow actions.
- Do not ship an empty **Advanced** tab. Advanced values belong in the relevant policy or pool form until a distinct advanced-settings domain exists.

#### Primary action rules

| Selected/context state | Primary action | Existing operation |
| --- | --- | --- |
| Latest draft exists | **Continue draft vN** | Select draft |
| Live exists, no draft | **Edit live configuration** | Clone live version to a new draft |
| Archived selected | **Restore as draft** | Clone selected archived version |
| No versions | **Create draft** | Create blank draft |
| Draft selected | **Review & activate** | Publish selected draft |

The version rail still exposes all drafts. The primary action reuses the newest draft rather than creating accidental draft sprawl. A deliberate **Clone as new draft** action remains available for any version.

#### Lifecycle language and behavior

| Version state | Editable | Available operations | UX explanation |
| --- | --- | --- | --- |
| Draft | Yes | Edit, activate, discard/archive, clone | Changes are not live until activation |
| Live/active | No | Inspect, edit via clone, clone, restricted archive | Live versions are immutable to keep changes reviewable |
| Archived | No | Inspect, restore as draft/clone | Archived history is immutable; restoration creates a new draft |

Activating a draft must use a confirmation/review dialog that summarizes model, price, limit, and pool changes. It must not claim that `tier.assignment_count` is the number of organizations immediately affected: that field includes historical assignment records. Until an exact lifecycle-impact count is exposed, use accurate copy such as:

> Assignments that follow this tier will begin using the new live version. Assignments pinned to the current live version can block activation.

Backend conflicts should stay in the dialog as actionable inline errors rather than disappearing into a toast.

## Configuration workspace mapping

### Models & limits

This tab is a searchable, sortable projection of the selected version's complete model-policy array.

Table summary fields:

- Callable model
- Allow/deny and enabled state
- RPM and TPM
- Additional-limit indicator
- Capacity pool binding
- Priority
- Row actions

**Add model policy** and **Edit policy** use one complete drawer/dialog, not a reduced form. It must preserve:

- Callable key with searchable selection
- Enabled and allow/deny state
- RPM, TPM, RPH, RPD, TPD
- Max parallel requests
- Batch RPM and batch TPM
- Capacity pool binding filtered to the same callable model
- Priority
- Every supported pricing field through the existing pricing component
- Existing opaque metadata, even though metadata is not directly edited

Keep the current bulk RPM/TPM action, moved into a labelled bulk-actions control. Removing a policy keeps its confirmation.

### Pricing

Pricing is not a separate backend collection. This tab is a pricing-focused view of the same model-policy array.

- One row per model policy, including policies with no configured price.
- Show pricing profile, concise price summary, and whether pricing is configured, partial, explicit zero, or blank.
- Editing opens the same policy drawer focused on Pricing, preserving all non-pricing fields.
- Blank continues to mean unconfigured; numeric zero continues to mean intentionally free.
- No new pricing endpoint or duplicate client state should be introduced.

### Capacity pools

This tab is a searchable, sortable view of the selected version's complete pool array.

The Add/Edit pool drawer must preserve:

- Pool key
- Callable key
- RPM and TPM capacity
- Max parallel requests
- Strategy: `hard_cap`, `weighted_fair`, or `reserved_burst`
- Saturation threshold for fair-share strategies
- Burst multiplier for `reserved_burst`
- Existing opaque metadata

Removing a pool must retain confirmation and the current validation that prevents removing a pool referenced by a model policy for the same draft.

## Pagination and growth strategy

### Tier catalog

- Keep server-side search and pagination.
- Add a page-size selector: 10, 25, or 50; default 25 to preserve current density.
- Add numbered page controls with previous/next and the exact `Showing X–Y of Z` range.
- Reset to page 1 when search, enabled state, or page size changes.
- Keep the current 250 ms search debounce.
- Never present current-page totals as global totals. Either label them `on this page` or remove them unless the API returns real filtered aggregates.

### Models, pricing, and capacity pools

- Use client-side search, sort, and pagination over the complete version arrays already loaded.
- Page sizes: 10, 25, or 50; default 10.
- Each tab owns its query, sort, page, and page-size state so switching tabs does not destroy the admin's place.
- Reset only the affected tab to page 1 when its query or page size changes.
- Always construct replacement payloads from the complete in-memory collection, never from the visible page. Filtering or pagination must not delete off-page records.

True server-side pagination for policies/pools is intentionally not part of this redesign because the current APIs replace whole collections. It would require per-row CRUD, concurrency/version controls, and a migration of the save model.

## Required list API enrichment

The current tier list response exposes only `active_version_id`, total version count, and total assignment count. Rendering exact Live/Draft versions and package counts by fetching every tier detail would create an N+1 request pattern.

Recommended additive list shape:

```ts
type TierListVersionSummary = {
  tier_version_id: string;
  version_number: number;
  model_policy_count: number;
  capacity_pool_count: number;
  updated_at?: string | null;
};

type Tier = {
  // existing fields remain
  active_version?: TierListVersionSummary | null;
  latest_draft_version?: TierListVersionSummary | null;
  draft_version_count: number;
};
```

Implement this in the existing paginated list query/serialization so the UI makes one list request. Preserve `active_version_id` for compatibility. Package counts are taken from `latest_draft_version ?? active_version` and labelled with that version in accessible text.

No new API is required for restore, edit-live, model-policy save, pricing save, capacity-pool save, publish, or archive; each maps to an existing operation.

## Theme mapping

The standalone prototype is a behavior and layout reference, not a new global visual theme. The real app's established admin system is the source of truth.

| Element | Real-app treatment |
| --- | --- |
| Page canvas | `bg-gray-50` |
| Header, panels, tables | White with `border-gray-200`; existing `rounded-xl`/`rounded-2xl` and restrained `shadow-sm` |
| Primary actions, selected tabs, focus | Blue 600/700 and blue focus rings, matching existing admin pages |
| Live | Compact green badge |
| Draft | Compact amber badge |
| Archived, disabled, empty | Neutral gray |
| Destructive actions | Red, separated from primary workflow and confirmed |
| Typography | Existing Inter/system stack and current type scale |
| Icons | Existing Lucide icons, decorative icons hidden from assistive technology where appropriate |

Version badges must be content-sized: `inline-flex`, `w-auto`, `whitespace-nowrap`, approximately 11–12 px text with compact horizontal padding. They must never inherit full-width, flex-grow, or a table-cell minimum width. Live and Draft badges sit adjacent with a small gap.

The implementation should continue using `IndexShell`, `EntityDetailShell`, card primitives, and blue tab/focus patterns. A tier-scoped compact version badge and a reusable table pagination bar can be added without changing unrelated admin pages.

### Responsive and accessibility requirements

- Preserve the existing responsive shells; do not copy the desktop-only minimum width from the prototype.
- Tables may scroll horizontally, but primary identity, status, and actions must remain understandable at narrow widths.
- Drawers/dialogs need labelled headings, focus management, Escape/close behavior, and disabled/loading states.
- All icon-only controls need accessible names.
- Status cannot depend on color alone; every badge includes text.
- Confirmation dialogs return focus to their trigger and keep server validation visible.

## Functional parity checklist

| Capability | Redesign disposition |
| --- | --- |
| Search tiers | Keep, server-side |
| Filter enabled/disabled tiers | Keep |
| Paginate tier list | Upgrade controls; keep server-side |
| Create/edit/delete tier metadata | Keep; create flow adds initial-draft orchestration |
| View every version | Keep in version rail |
| Create blank draft | Keep as secondary action |
| Clone live | Relabel primary flow to Edit live configuration |
| Clone archived | Relabel to Restore as draft |
| Publish draft | Relabel to Review & activate; keep backend transaction |
| Archive/discard draft | Keep with explicit confirmation |
| Archive active | Keep as restricted secondary/danger action with backend errors explained |
| Add/edit/remove policy | Keep all fields; move to complete drawer/dialog |
| Bulk RPM/TPM | Keep |
| Add/edit/remove pool | Keep all strategies and validation; move to complete drawer/dialog |
| Pricing profiles and advanced pricing | Keep; expose through Pricing view and shared policy editor |
| Read-only active/archived inspection | Keep across all three tabs |
| Capacity fairness dashboard | Keep in Capacity health page view |
| Permission checks, audit, invalidation | Unchanged |
| Work in progress label | Do not implement; Draft is the signal |

## Implementation slices after approval

1. **Contract and helpers** — enrich list version summaries, update types/serialization, add lifecycle and badge-selection helpers with backend and UI unit tests.
2. **Shared tier UI primitives** — compact version badge, table pagination, empty/loading/error states, confirmation dialog behavior.
3. **Catalog and create flow** — new columns/status rules, page-size/numbered pagination, Tiers/Capacity health views, complete Create tier flow.
4. **Version workspace** — version rail, primary-action rules, restore-as-draft, review-and-activate flow, metadata/secondary actions.
5. **Configuration tabs** — paginated Models & limits, Pricing, and Capacity pools views plus complete Add/Edit drawers and full-array-safe saves.
6. **Verification and documentation** — backend tests, UI helper tests, build/type checks, responsive/accessibility pass, and update `docs/admin-ui/tiers.md` screenshots/copy.

## Approval gate

Implementation should not start until these recommended decisions are confirmed:

1. The redesign branch should remain based on commit `e1e7420c` from `fix/batch-fair-share-ci-flake`.
2. The existing capacity dashboard moves into a **Capacity health** view on `/tiers` rather than remaining above the catalog.
3. Create tier performs a recoverable two-step Tier then Draft v1 flow; transactional creation is out of scope unless explicitly requested.
4. The tier list API receives additive active/latest-draft summaries to avoid N+1 detail requests.
5. Detail-table pagination is client-side over complete arrays, with full-array replacement saves preserved.
6. The production app keeps its blue admin theme; the prototype's violet accents are not imported globally.
7. The empty prototype Advanced tab is omitted; advanced fields remain in their relevant complete editor.
