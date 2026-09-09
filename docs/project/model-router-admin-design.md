# Model router admin workflow (PR 5)

## Ownership and scope

PR 5 starts from remote `feature/issue-304-model-router` at `bd48400b`, including
merged PR 4. The durable route policy, existing publication service and immutable
routing generation remain the only configuration/activation owners. The UI adds
form conversion and feedback, never an alternative policy evaluator.

The primary workflow is **choose selector → assign answer lanes → Publish**.
Use the existing policy editor, default economy/quality lanes and bounded defaults.
Advanced lane editing/limits, raw JSON and evaluation are optional disclosures.
No mandatory evaluation wizard, separate activation switch, shadow mode or
background classification. The user's simplified workflow supersedes the issue's
proposed mandatory evaluation-results gate; normal publication still validates
the policy server-side. Removing the selector and publishing disables it.

## Independently verifiable slices

1. Pure selector form conversion, bounds, membership and opaque/null round trips;
   focused selector editor, data-path warning and active/history summary.
2. Bounded deterministic evaluation of labeled fixtures and supplied classifier
   outputs, using the canonical parser and exact cost aggregation. Local and admin
   entry points share one implementation. No provider call or charge is implicit.
3. Protected bounded cost reports through existing spend visibility/reporting
   allocation. Fix soft-operation answer-event interpretation before exposing it;
   missing/pending costs and counterfactual evidence are never represented as free.
4. Authorization, audit, cancellation/stale-response, contract and real-database
   tests; UI gates, OpenAPI, operator examples and rollback documentation.

Live paid evaluation is intentionally not implemented: it requires a
verified paying principal and explicit control-plane provider-capacity allocation.
Do not silently invent either or charge the platform/admin session. Offline
reports are evidence analysis, not live-model execution or measured answer quality.

## Invariants and failure modes

- Backend CONFIG permissions remain authoritative for group policies; spend reads
  also require existing spend permissions and server-derived visibility filters.
- Editors preserve opaque/server fields; explicit selector null is a tombstone,
  not omission. Stable group IDs identify operations. Existing mutation scoping
  protects route/principal changes; reports accept abort and reject stale results.
- Policy validation errors remain actionable; failed report refresh never undoes
  successful publication. Unknown model capabilities must not be guessed.
- Publication confirmation describes the submitted selector write intent:
  omission preserves the published selector, explicit null removes it, and a
  supplied configuration is validated by the backend. It never rewrites raw JSON
  or treats an unsupported selector as disabled.
- Guided selector state distinguishes an untouched absent selector from explicit
  removal. Choosing None records removal independently of imported JSON keys,
  emits `selector: null` and clears member lanes while retaining unrelated fields.
  Re-enabling replaces that intent; no additional publication step is introduced.
- Cost pagination accepts each successful page together with its exact request
  window through the existing scoped report owner. Failed, aborted or stale
  attempts cannot replace either the page or its pagination metadata. There is
  no separate attempted-window state, shared-hook change or additional read.
- Evaluation is request-bounded, deterministic, non-persistent and retry-safe;
  its input/output content is not added to logs/audit. Report scores are supplied
  evidence with coverage, not inferred real-world quality claims.
- Billing operations and spend events remain economic truth. Reporting is
  read-only and cannot mint receipts, estimate missing charges, double-bill or
  change soft budgets. Reference savings require explicit baseline and penalty.
- No new infrastructure, durable job, hot-path await, migration, pool or setting.
  Reporting shares the existing bounded reporting allocation, not a new queue.

## Alternatives, rollout and rollback

A multi-step wizard and required evaluation were rejected as unnecessary friction.
A paid admin provider loop without verified billing/capacity was rejected as an
economic/control-plane boundary violation. Pure fixture analysis is safe locally
and through a permission-protected explicit admin action.

Roll out additively after PR 4; old clients continue to publish through the same
API. Revert PR 5 UI/reporting code without changing stored policy semantics or
PR 4 execution. Disable a router with `selector: null` or existing policy history.
There is no new compatibility lifecycle to remove. Batch remains PR 6.

## Capacity and bounded extraction

Inference adds **zero SQL, Redis or provider awaits** in this PR. Fixture replay
uses at most 100 samples / 256 KiB, performs no dependency I/O, and has no queue,
provider retry, billing event or retained server report. The HTTP upload deadline
is five seconds. Remote replay requires the existing synchronous audit owner;
missing/failed required audit returns unavailable. The CLI needs no audit service.

Cost reads reuse `SpendReportingCache.run_uncached`, its per-process limiter and
the same PostgreSQL advisory-lock reporting allocation as existing spend reads.
With the production profile's 12 maximum API replicas, one process per replica
and the default two local loads, at most `12 × 1 × 2 = 24` pooled reporting
transaction attempts can enter existing admission; expensive queries are limited
to `min(24, global limit 2) = 2` across the deployment. Surge/more processes
increase transient admission attempts, not the global query cap. Workers do not
serve these admin routes. PR 5 adds no connections or per-feature allocation to
the existing pool budget; operators must still reserve headroom as documented in
[general settings](../configuration/general.md).

The SQL page is materialized before two unique spend-event joins, with a maximum
1,001 fetched rows including the continuation sentinel. Existing scope/time
indexes are reused; no append-table index/write/retention changes are introduced.
Read-your-latest-receipt consistency comes from the primary, without a new result
cache. UI reports keep one bounded result and one owned, abortable request.

To avoid adding a concern to the oversized spend endpoint, the existing shared
HTTP reporting dependencies and SQL deadline/allocation implementation were moved
to `api/admin/spend_reporting_dependencies.py` and `db/reporting.py`. All old
callers use those same owners; no compatibility copy remains. The HTTP adapter
still resolves legacy dynamic app-state settings, and the SQL adapter preserves
the existing Prisma exception classifier. These bounded seams intentionally do
not redesign bootstrap settings or legacy exception types in this feature PR.
The admin reporting owner should remove those adapters when the corresponding
typed bootstrap/error contracts are migrated; new domain services have neither
reflection nor HTTP-state dependencies. Structural tests enforce their size,
typing and separation from provider execution.

## Verification record (2026-09-09)

Local checks use Python 3.11.13, Node 22.23.2, the frozen dependency locks, the
generated Prisma client, and an isolated PostgreSQL 15 database with all existing
migrations applied. No migration or dependency lock changed.

- `.venv/bin/pytest -q -m hermetic --durations=10 --tb=short`: 3,240 passed.
- `.venv/bin/pytest -q -m app --durations=10 --tb=short`: 1,219 passed.
  The subsequent required-audit presence guard and header-schema deduplication
  were verified with all 19 selector-report API tests plus the nine existing
  selector-address contract tests: 28 passed after those last changes.
- `DATABASE_URL=<isolated-test-db> .venv/bin/pytest -q -m postgres --durations=10 --tb=short`:
  274 passed. The seven new SQL tests include pending/late/unpriced receipts,
  idempotent economic effects, tenant/cursor isolation and a representative query
  plan. The plan uses `deltallm_billing_operations_org_time_idx`, reads one
  authorized operation among 10,000 background rows and returned in about 0.06 ms
  in the focused measurement (diagnostic evidence, not a production latency SLO).
- `.venv/bin/pytest tests/test_ui_selector_reports.py -q --tb=short`: 19 passed,
  including actual member-session denial, exact-cost HTTP contracts, sanitized
  overflow, missing/failed required audit and cancellation capacity release.
- `npm --prefix ui run test:unit`: 230 passed, including registered selector,
  adapter, confirmation/focus and abort/stale/principal-change regressions.
- `npm --prefix ui run build`: passed. Vite reports initial JS 375.20 kB gzip
  and the lazy group route 25.44 kB gzip. Same-runtime gzip measurement is 374,140
  initial bytes vs PR 4's 374,144 (no initial growth; the older 250 kB target is
  still repository debt). Hash/export ordering changes account for byte noise.
- Touched Python Ruff checks/format and touched-file ESLint pass.
- Full UI ESLint reports 118 existing errors / four warnings, exactly matching
  the PR 4 worktree baseline; it is not a green repository-wide lint gate.
- OpenAPI export/check: 229 paths / 297 operations. Strict MkDocs build passes.
- Full dependency-lane collection is exhaustive/exclusive: 3,240 hermetic,
  1,220 app (including the last audit-presence test), 274 PostgreSQL, 46 Redis
  and 59 Helm tests, 4,839 total. Redis/Helm execution was not repeated because
  this PR changes neither their runtime logic nor deployment configuration.

DOM tests exercise loading/empty/error/stale/permission states at simulated
375/1,024-pixel window widths and keyboard confirmation behavior. These do not
claim CSS layout verification: the browser plugin could not initialize because it
references a missing older installation. A real-browser mobile/desktop smoke
check remains a review prerequisite. Real model quality, paid evaluation and
representative workload net-savings certification are not claimed.

### Review-fix verification (2026-09-09)

Both reported reproductions were added before the fixes: UI tests initially
reported 230 passes and two failures (false disable confirmation and mixed-window
pagination). After the fixes and expanded deterministic regression coverage:

- `npm --prefix ui run test:unit`: 239 passed. Coverage includes unchanged raw
  payloads, explicit removal, unsupported configurations, invalidated
  confirmations, failed refresh/pagination retries, exact frozen timestamps,
  successful window replacement, late responses and principal/group/unmount
  cancellation. No UI test-runner or shared report-hook change was needed.
- `.venv/bin/pytest tests/test_route_policy_validation.py tests/test_ui_route_group_selector_addresses.py tests/test_selector_admin_structure.py -q --tb=short`:
  57 passed. Backend policy merge, API, billing and reporting contracts did not
  change in these fixes; real-dependency suites were not repeated.
- From `ui/`, `node node_modules/eslint/bin/eslint.js src/lib/routeGroupSelector.ts src/components/route-groups/PolicyPublishControl.tsx src/components/route-groups/SelectorCostPanel.tsx tests/routeGroupSelector.test.ts tests/selectorAdmin.test.tsx`:
  passed with zero errors or warnings.
- `npm --prefix ui run lint`: unchanged 118 pre-existing errors / four warnings.
- `npm --prefix ui run build`: passed using Node 22.23.2. Initial JS remains
  375.20 kB gzip and the lazy group route is 25.37 kB gzip. A fresh comparison
  using Node `gzipSync` defaults measures 375,202 initial bytes vs PR 4's 375,205
  (three bytes smaller). The pre-fix PR 5 artifact measured 375,198 bytes; the
  four-byte delta accompanies changed lazy-chunk references, with no new initial
  imports or dependencies. This fresh measurement supersedes the earlier raw-byte
  figures above, whose compression options were not recorded.
- `.venv/bin/mkdocs build --strict` and `git diff --check`: passed.

Browser verification was retried using the currently installed browser plugin.
Connection still fails because its worker references a missing older
`browser-service.mjs`. No browser profile or plugin installation was modified;
the real-browser mobile/desktop review prerequisite remains outstanding.

### Explicit-removal follow-up verification (2026-09-09)

Regression tests reproduced the imported-partial-JSON → Guided → selector → None
bug before the fix: 239 passed and four failed. Explicit removal is now retained
in typed editor state independently of the imported selector key. Untouched
omission still preserves the published selector; the backend remains the policy
merge owner. No backend, billing, dependency or test-discovery changes were made.

- `npm --prefix ui run test:unit`: 243 passed under Node 22.23.2. The new tests
  cover the actual publication adapter's `selector: null` payload and disable
  confirmation at simulated 375/1,024-pixel widths, JSON and validation round
  trips, member-lane cleanup, unrelated-field preservation and re-enabling.
- `.venv/bin/pytest tests/test_route_policy_validation.py tests/test_ui_route_group_selector_addresses.py tests/test_selector_admin_structure.py -q --tb=short`:
  57 passed. Real-dependency suites were not repeated for this UI-only fix.
- From `ui/`, `node node_modules/eslint/bin/eslint.js src/lib/routeGroupSelector.ts src/components/route-groups/PolicySelectorSummary.tsx tests/routeGroupSelector.test.ts tests/selectorAdmin.test.tsx`:
  passed with zero errors or warnings.
- `npm --prefix ui run lint`: unchanged 118 pre-existing errors / four warnings.
- `npm --prefix ui run build`: passed. Vite reports 375.21 kB initial gzip and
  25.38 kB for the lazy group route. Using the same Node `gzipSync` defaults,
  the current initial asset is 375,207 bytes vs PR 4's 375,205: two bytes larger
  (five bytes above the preceding fix). Changed lazy-chunk references affect
  the initial asset; this fix adds no initial imports or dependencies.
- `.venv/bin/mkdocs build --strict`: passed. `git diff --check` and
  `git diff --no-index --check /dev/null <file>` for each of the six touched
  untracked files reported no whitespace errors.

The previously documented real-browser smoke-check prerequisite remains open;
the simulated-width DOM tests do not verify CSS layout.
