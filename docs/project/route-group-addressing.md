# Route-group admin addressing

Issue: [#308](https://github.com/deltawi/deltallm/issues/308).

## Decision

Use the existing PostgreSQL `route_group_id` UUID for canonical admin addresses:
`/route-groups/by-id/{route_group_id}` in the UI and
`/ui/api/route-groups/by-id/{route_group_id}` in the API.
Keep `group_key` as the exact runtime model name, prompt scope, and audit identity.
No database schema, key normalization, inference contract, or routing-policy change is required.

Encoding a key with `encodeURIComponent` only fixes the browser route. ASGI servers decode
`%2F` before matching backend path parameters. A greedy `{group_key:path}` would also
make names such as `team/policy` ambiguous with policy subresources. Replacing slashes
or slugifying names would lose identity. UUID addresses avoid both problems.

## Ownership and failure behavior

The existing admin permission dependencies run before ID resolution. The resolver makes
one additional indexed PostgreSQL group lookup on each ID-addressed control-plane request;
it introduces no Redis state or inference-path calls. It returns 404 for an unknown ID,
including an ID belonging to a deleted group, and preserves 401/403/503 failure meanings.
The UI decodes a legacy pathname once, resolves its name through a query parameter,
and replaces browser history with the canonical address.

A request-local repository clone pins the resolved primary key. Transactions retain this
identity, and subsequent reads, row locks, and writes use it. Deleting a group and recreating
its key cannot redirect an in-flight ID request to the replacement. Simulation lookups
for other fallback keys retain their independent identities. The shared repository is
never modified with request state.

Simulation checks the pinned UUID and key in the same SQL snapshot that supplies its
runtime groups, before the snapshot is converted to runtime keys. A missing identity
becomes the existing simulation 404. This adds no database round trips or locks and
retains other groups for fallback resolution. Unpinned runtime reloads and legacy
key-addressed simulation retain their existing behavior.

The new HTTP adapters reuse the established route-group operations, validators,
publication service, transactional deletion service, audit, and refresh behavior.
The existing endpoint module still contains orchestration; this is a bounded compatibility
seam owned by the route-group admin API. Extract those shared operations into application
services when that orchestration is next changed, then make both routers depend on the
same extracted owner. Duplicating policy or performing that broader extraction as part
of an addressing fix would make behavior preservation harder to review.

ID changes remount the detail page and cancel its reads and pending mutation handlers.
Late completions cannot navigate, toast, or refresh a different group. Duplicate clicks
are locked synchronously. Aborting a browser request does not undo a server-side mutation
that has already committed.

## Compatibility and rollout

Collection list/create URLs and existing key-addressed API endpoints remain available.
The `by-id` segment and a UUID path converter keep UUID-shaped keys distinct from IDs
and preserve legacy subresources of a group literally named `by-id`.
The query resolver preserves slashes, literal percent sequences, Unicode, spaces,
question marks, and fragments as data. Old links that already lost part of a key to
an unescaped query or fragment cannot recover that missing data.

Deploy the backend and UI together. Rollback requires only the previous application/UI
artifact; no data rollback is needed. ID bookmarks require the new backend. Keep legacy
API routes until a separately announced compatibility removal with a supported client
migration window; no removal is scheduled by this change.

Member weight and priority reject booleans before integer coercion. Integer, integer-string,
omitted, and null inputs retain their accepted behavior; rejected writes leave members intact.

## Verification

- HTTP regression tests exercise every ID operation, exact keys, legacy compatibility,
  authentication/permission denial, malformed/unknown IDs, typed OpenAPI responses,
  deletion before simulation snapshots, and member-number validation without mutation.
- Real PostgreSQL tests cover transaction identity, fallback independence, and deleting
  and recreating a key across two connections between lookup and snapshot;
  existing publication/concurrency invariants remain covered.
- UI tests exercise exact query encoding, legacy redirects, duplicate deletion clicks,
  navigation during pending deletion, stale completion, and permission/error recovery.
- Production build: initial gzip decreases from 379.49 KB to 374.14 KB by loading the
  group list as a route chunk. Full lint retains its 122-finding baseline; changed files
  must pass independently.

Validated on 2026-09-07:

- `uv run --no-sync pytest -q tests/test_ui_route_group_addresses.py tests/test_ui_route_group_simulation_identity.py tests/test_ui_route_groups.py tests/db/test_route_policy_repository.py tests/services/test_route_group_mutations.py tests/services/test_route_groups.py tests/services/test_route_group_refresh.py tests/services/test_route_policy_publication.py tests/test_route_policy_validation.py tests/test_routing_runtime_generation.py tests/docs/test_generated_references.py`: 153 passed.
- `uv run --no-sync pytest -q tests/db/test_route_group_identity.py tests/db/test_route_policy_publication_invariants.py`
  against a disposable PostgreSQL 16 database with current migrations: 7 passed.
- `uv run --no-sync python -m scripts.docs.export_openapi --check`: current; the validation
  fix preserves the existing generated schema.
- `npm --prefix ui run test:unit`: 201 passed.
- `npm --prefix ui run build`: passed; detail route chunk 19.81 KB gzip, list chunk 4.25 KB gzip.
- `npm --prefix ui run lint`: existing 118 errors and 4 warnings, unchanged from baseline.
  Direct ESLint on all 17 changed UI source/test/runner files: zero findings.
- `uv run --no-sync ruff check` and `uv run --no-sync ruff format --check` on all changed
  Python files passed; `git diff --check` passed.

Chromium smoke checks used the production bundle served by the existing FastAPI static
routes, with mocked API responses (HTTP and SQL behavior are verified separately above).
At 1280px and 390px: slash/percent/Unicode/reserved names, legacy redirects, direct refresh,
empty members, loading, 403/404/503 and retry, and settings updates passed. Keyboard Enter
opened Edit and the delete dialog; focus stayed in the dialog and Escape restored focus.
List navigation, create redirect, deletion, anonymous root redirect, and login returning
to a nested group address also passed. Screenshots were visually checked at both sizes.
