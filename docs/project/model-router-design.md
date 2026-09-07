# Route-Group Model Router Design

Status: accepted design for [issue #304](https://github.com/deltawi/deltallm/issues/304).
The implementation is split into six reviewable PRs. PRs 1–3 define prerequisites and must not
activate a selector. PR 4 is the first activation boundary.

## Goal and non-goals

An optional Route-Group selector classifies a normalized text request into a bounded
capability/cost lane. The existing routing strategy then chooses an eligible deployment inside that
lane. Higher-ranked lanes are available for capability-preserving escalation.

The selector is not a routing strategy, health owner, retry engine, authorization policy, cache,
billing lifecycle, or deployment alias. It never chooses a deployment directly and never weakens a
deterministic eligibility constraint.

There is no shadow mode. Normal traffic is never duplicated for background classification.
Evaluation is an explicit, bounded operator action planned for PR 5. Publishing becomes the atomic
activation point only after PR 4 supplies the complete safe execution path.

## Ownership

| Concern | Canonical owner |
| --- | --- |
| Durable selector, lane, and member schema | `src/route_policy_contract.py` |
| File route-group and router settings | `src/route_group_config.py` |
| Selector activation guard and routing fingerprint | `src/router/selection/policy.py` |
| Route-policy normalization and group/member validation | `src/router/policy_validation.py` |
| Policy history, publish, and rollback transactions | `src/db/route_policy_lifecycle.py` |
| Immutable database runtime snapshot | `src/db/route_groups.py` |
| Request-scoped selector service and result | `src/router/selection/` in PR 2 |
| Hard eligibility and candidate planning | Existing router candidate owners in PR 4 |
| Provider capacity, spend, cache identity, and telemetry | Existing subsystem owners in PR 3 |

The neutral contract and focused route-group configuration modules are intentional. `src/config.py`
is already an oversized compatibility facade imported by router construction, so it re-exports the
route-group types while their implementation stays in `src/route_group_config.py`. Putting the
shared Pydantic schema behind the eager `src.router` package initializer would create a
configuration/router import cycle. Runtime behavior remains owned by `src/router/selection/`.

## Durable policy contract

Selector policies use route-policy semantics version 3:

```json
{
  "strategy": "least-busy",
  "selector": {
    "kind": "llm-tier",
    "classifier_deployment_id": "dep-mini-a",
    "timeout_ms": 750,
    "max_input_chars": 8000,
    "default_lane": "quality",
    "lanes": [
      {
        "id": "economy",
        "rank": 0,
        "description": "Routine extraction, formatting, classification, and simple Q&A"
      },
      {
        "id": "quality",
        "rank": 1,
        "description": "Complex reasoning, ambiguity, tools, and difficult long-context work"
      }
    ]
  },
  "members": [
    {"deployment_id": "dep-mini-a", "lane": "economy", "weight": 2},
    {"deployment_id": "dep-large-a", "lane": "quality"}
  ]
}
```

The initial bounds are:

| Field | Contract |
| --- | --- |
| `kind` | Exact literal `llm-tier` |
| `classifier_deployment_id` | Trimmed, 1–256 characters |
| `timeout_ms` | 100–5,000; default 750 |
| `max_input_chars` | 256–32,768; default 8,000 |
| lanes | 2–8 |
| lane ID | 1–32 characters matching `^[a-z][a-z0-9_-]{0,31}$` |
| lane description | Trimmed, 1–512 characters |
| lane rank | Unique, contiguous, zero-based, and less than 8 |
| `default_lane` | Configured lane; omitted input normalizes to the highest rank |

A selector is optional. Selector-free groups retain the existing policy contract and runtime
behavior. A selector policy must:

- target a `chat` Route Group;
- provide an explicit authoritative member list;
- reference a concrete enabled member of that same group as the classifier;
- use a classifier deployment whose workload mode is chat;
- assign every effectively enabled answer member exactly one configured lane; and
- leave no configured lane without an effectively enabled member.

Disabled policy members may omit a lane because they cannot answer traffic. A member lane without a
selector is rejected rather than stored as inert current-semantics configuration.

Selector, lane, and selector-enabled member objects reject unknown fields at the untrusted client
boundary. Durable documents use a separate stored-policy validator: it projects and validates only
routing-owned fields, while trusted opaque top-level and member fields remain byte-for-byte in the
document written back to PostgreSQL. Opaque data never gains routing authority, and nested selector
objects remain strict even when read from storage. Selector-free policy documents retain the
established opaque-field warning/preservation behavior, which protects forward-compatible
server-owned data.

## Versioning and historical compatibility

The existing database `DeltaLLM_RoutePolicy.policy_json` and `semantics_version` columns remain the
single durable source. No schema migration or second membership store is introduced.

- Version 1 retains legacy explicit-member widening.
- Version 2 retains authoritative explicit membership and treats `selector` and member `lane` as
  unknown opaque fields.
- Version 3 owns `selector` and member `lane` and applies the contract above.

History is never rewritten. Validation and member merging always use the semantics version stored
with the revision. In particular, selector-shaped opaque data in a version 1 or 2 policy never
becomes active merely because a newer binary reads or rolls it back. When a new version 3 revision
is written, newly claimed `selector` and `lane` keys are replaced only by explicitly validated
client data; they are not copied out of an older opaque document. Updating an existing version 3
document preserves an omitted selector. If that update supplies an authoritative member list,
omitted lane assignments are preserved by deployment ID while removed members stay removed and
new members without a historical lane must supply an explicit assignment. An explicit
`"lane": null` does not inherit an old lane and is rejected for an enabled answer member.
An explicit `"selector": null` is the unambiguous deletion tombstone:
it removes the selector and every member `lane`, but preserves the authoritative member list and
its non-lane settings when the update omits `members`.

All other unknown stored fields continue to round-trip through draft and publication replacement.
That is a deliberate compatibility exception to strict client-owned selector fields.

Mutation requests retain their original values, unknown keys, and field presence through the HTTP
and service boundaries. The repository locks the group, loads the latest draft (falling back to the
published revision) or the published revision for direct publication, validates authored fields
according to the effective selector, merges, and validates assignments against current database
membership before writing. A typed write result carries authoritative normalization warnings to
the API. No extra inference I/O or provider call is introduced. Standalone `/policy/validate`
validates the submitted document only; it does not implicitly inherit a stored draft.

`members` may be omitted but cannot be null. Invalid authored fields return HTTP 400, including
typed request validation, without echoing submitted values. Incompatible effective policy state
returns HTTP 409. Selector/context null tombstones and legacy selector-free normalization remain
supported. Publish/rollback preserve the explicit unsupported-selector activation error.

Runtime Route-Group snapshots carry explicit proof that the selector activation gate ran. The
database repository and validated file configuration are the only authorities that can mint an
`inactive` snapshot before PR 4. Redis is an optimization, not an authority: its bounded 4 MiB
`deltallm:<environment>:v2:route-group-runtime:r<revision>` entries are produced by the shared Redis
key builder and use a strict versioned envelope containing the schema version and selector-gate
state. Missing, legacy, malformed, oversized, or incompatible envelopes are cache misses and reload
PostgreSQL. Selector, policy JSON, or lane fields are incompatible with this inactive projection
and therefore also cause a cache miss. Only the durable loader can raise an activation error;
that error never falls back to file configuration. Owned mode, strategy, timeout, retry, and context
values are validated before a cache hit. Opaque nested extensions and valid historical numeric
strings retain their meaning. Invalid groups reject the entire envelope. Read and write failures
emit redacted structured logs and a fixed-reason `deltallm_route_group_cache_failures_total`
counter; repair failure does not fail a valid durable read. Cache schema v2 remains compatible for
valid entries. The previous cache namespace is left
to expire naturally; PR 4 must bump the envelope and namespace when the activation state changes.

## Request order after activation

PR 4 must preserve this order for chat, Responses, streaming, and managed continuation paths:

```text
authenticate and authorize
  -> caller rate-limit and budget admission
  -> prompt rendering, hooks, guardrails, and request validation
  -> deterministic workload/capability/residency/context eligibility
  -> whole-response cache lookup
  -> at most one bounded selector provider call on cache miss
  -> apply selected minimum lane and upward-only escalation
  -> existing strategy orders candidates inside each lane
  -> existing capacity, retry, failover, and fallback execution
  -> durable answer and selector accounting
```

The decision is request-scoped and immutable. Retry, failover, candidate-plan rebuilds, fallback
groups, streaming setup, and managed model hops reuse it. A cache hit performs no selector work.

## Failure behavior

After activation, timeout, provider failure, capacity denial, malformed output, and an unknown lane
all resolve to the configured safe default. The default itself is validated and defaults to the
highest-capability lane. A quality decision never permits a lower-ranked candidate.

Deterministic constraints always win. The selector cannot re-enable a disabled member, bypass
tenant/model authorization, loosen residency or tag constraints, exceed context or capability
limits, or select an arbitrary deployment. Classifier failures are isolated from answer-member
health and must not cool down answer deployments.

PR 1 deliberately fails closed before activation:

- saving and validating a selector draft is supported;
- direct publish and publish-latest-draft reject selector semantics explicitly;
- rollback rejects a version 3 selector revision;
- database and file-config runtime loading reject selector activation;
- deterministic policy simulation rejects selector policies; and
- a typed activation error is never converted into database-unavailable config fallback.

Silent acceptance is forbidden. PR 4 removes this temporary guard only after execution, cache,
capacity, accounting, deadline, cancellation, and telemetry prerequisites are connected.

## Capacity, latency, and accounting budget

The selector is one real provider call, not free control-plane work. PR 2 bounds it to one attempt,
a tiny typed output, `timeout_ms`, `max_input_chars`, and the remaining end-to-end deadline. PR 3
must acquire the classifier deployment's existing provider RPM, TPM, and concurrency ownership and
must not add an unbounded queue or retry loop.

The external request consumes caller RPM once. Preflight spend estimation includes a conservative
selector allowance. Actual classifier usage is recorded once as a purpose-specific child component
of the external request, with frozen pricing and the existing exact money representation. Public
OpenAI-compatible usage remains answer-only.

PR 6 must report selector-free dependency call counts, selector latency and TTFT impact, batch
fragmentation, and net savings after classifier cost. No selector-free database, Redis, or provider
await may be added.

## Routing fingerprint

The canonical `route-policy-v1:<sha256>` fingerprint includes only response-affecting normalized
routing semantics:

- workload mode and stored semantics version;
- effective routing strategy;
- normalized timeout and retry policy;
- normalized version-aware context-routing policy;
- selector kind, classifier, bounds, safe default, and rank-ordered lanes; and
- ordered effective members with enabled state, weight, priority, and version-aware lane.

It excludes policy IDs, revision numbers, timestamps, runtime-generation UUIDs, reload sources, and
opaque non-routing metadata. Equivalent normalized snapshots therefore share a fingerprint while a
member/lane/strategy/selector change produces a different identity. PR 3 adds this value to the
versioned response-cache key; PR 1 only defines and tests the pure canonical function.

## Database, API, and file configuration

Database draft validation uses one group/member/deployment-mode inventory query inside the existing
policy lifecycle. Client validation, opaque-field merge, stored-document revalidation, and the
activation gate all reuse that same in-transaction inventory snapshot; archive and revision changes
happen only afterward. Publish and rollback remain transactional. Runtime loading remains an
immutable snapshot swap and has a defensive activation guard.

The admin policy body exposes typed selector/lane/member schemas in OpenAPI while the response and
history representation remains an opaque-capable JSON document. This preserves historical and
server-owned fields.

File configuration uses the same selector/lane/member models and relationship validation in two
stages. Raw YAML for a selector-enabled group is validated strictly before Pydantic can coerce
quoted booleans or integers. Runtime snapshot construction then validates the already-loaded model
registry so the classifier and enabled members are concrete deployments with compatible workload
modes; this adds no I/O. A file selector must declare `mode: chat`; omitted legacy mode cannot prove
selector workload safety. Selector-free groups retain their pre-selector coercion, unknown-field,
identifier-length, weight, and priority behavior. During PRs 1–3, a fully validated selector file
still fails explicitly when converted into a runtime snapshot.

## Rollout and rollback

Development uses stacked PRs on the feature integration branch. PRs 1–3 must not be deployed as a
partially active feature. Before the first selector publish after PR 4, deploy a version containing
the complete execution path to every replica.

Safe rollback order after activation is:

1. Publish or roll back to a selector-free policy revision while all replicas understand version 3.
2. Confirm runtime reconciliation and selector-free traffic.
3. Roll back application binaries if needed.

Rolling application binaries back while a published version 3 selector remains active is unsafe
because older replicas treat those fields as opaque. The runtime activation guard prevents this
state before PR 4; the PR 4 operator documentation must retain this rollback ordering.

No database downgrade is needed because policy history is JSON plus its durable semantics version.

## Alternatives considered

Adding a new `RoutingStrategy` was rejected because semantic capability selection and deployment
placement have different authority and failure behavior. Letting the classifier return deployment
IDs was rejected because it exposes topology and bypasses policy validation. Recursive calls
through DeltaLLM's public HTTP endpoint were rejected because they complicate authorization,
capacity, accounting, deadlines, and recursion prevention.

A local learned router can later implement the same typed lane decision contract. The initial
bounded generative classifier is easier to evaluate across heterogeneous providers and gives the
project data needed to decide whether a trained/local selector is worthwhile.

## PR boundaries

1. Durable schema, validation, versioning, compatibility, activation guard, and fingerprint.
2. Bounded request-scoped selector service, still unreachable from production paths.
3. Capacity, budget, durable accounting, cache identity, and telemetry prerequisites.
4. Real-time chat/Responses integration and direct publish activation.
5. Admin UI, explicit evaluation, canary, and operator workflows.
6. Batch parity, performance qualification, and final support matrix.

## PR 1 review remediation — 2026-09-07

The four review findings are addressed within PR 1. The local commit slices are policy writes and
HTTP contracts, cache validation/recovery, real-service regression tests, and documentation.

- [x] Preserve authored values until locked effective-selector validation, including inherited
  selectors, unknown keys, and omitted-versus-null lane assignments.
- [x] Treat selector-bearing or otherwise incompatible Redis projections as observable misses;
  retain the fail-closed activation gate for PostgreSQL and file configuration.
- [x] Validate owned nested cache fields, retain valid historical representations and opaque
  extensions, and use the same schema for writes without failing valid durable reads.
- [x] Reject null member lists with HTTP 400 and retain the established selector-free context
  normalization and authorization contract.
- [x] Exercise PostgreSQL transaction serialization, history/rollback semantics, selector removal,
  durable activation rejection, and cross-replica Redis repair/TTL/outage recovery.
- [x] Regenerate OpenAPI and verify frontend request/error adapters and production build.

Verification was run in `.worktrees/issue-304-model-router`:

| Gate | Command | Result |
| --- | --- | --- |
| Full backend | `uv run pytest -q --tb=short --disable-warnings` | 3,758 passed; 205 environment-gated skips; 26,142 warnings; 264.73 seconds |
| Final request regressions, including additional authorization and simulation checks | `uv run pytest tests/test_route_policy_write_contract.py -q --tb=short --disable-warnings` | 24 passed |
| Focused policy/cache/API follow-up | `uv run pytest tests/db/test_route_policy_partial_writes.py tests/db/test_route_policy_repository.py tests/test_route_policy_write_contract.py tests/services/test_route_group_cache_contract.py tests/test_ui_route_groups.py -q --tb=short --disable-warnings` | 147 passed before the four final request tests were added |
| Real PostgreSQL/Redis | `uv run pytest tests/db/test_route_policy_selector_integration.py tests/db/test_route_policy_publication_invariants.py tests/test_route_group_redis_integration.py -q --tb=short --disable-warnings` | 37 passed; no skips |
| Prisma client | `uv run prisma generate --schema=./prisma/schema.prisma` | Passed |
| Fresh isolated test database | `uv run prisma migrate deploy --schema=./prisma/schema.prisma` | All 85 migrations applied; no schema changes in this remediation |
| UI tests | `npm --prefix ui run test:unit` | 198 passed |
| UI production build | `npm --prefix ui run build` | Passed; initial JavaScript 379.46 KB gzip and RouteGroupDetail 19.33 KB gzip, both unchanged |
| Full UI lint | `npm --prefix ui run lint` | Existing baseline unchanged: 118 errors and 4 warnings |
| Touched UI lint, from `ui/` | `./node_modules/.bin/eslint tests/routeGroupsApi.test.ts src/lib/api/routeGroups.ts` | Passed, zero findings |
| API artifact | `uv run python scripts/docs/export_openapi.py --check` | Current; 215 paths, 280 operations |
| Docs | `uv run mkdocs build --strict --site-dir /tmp/deltallm-pr1-fixes-docs` | Passed |
| Whitespace | `git diff --check` | Passed |

Both `uv run ruff check` and `uv run ruff format --check` passed on these 19 Python paths:

```text
src/api/admin/endpoints/route_groups.py
src/api/admin/request_validation.py
src/api/admin/route_group_contracts.py
src/db/route_policy_lifecycle.py
src/router/policy_validation.py
src/services/route_groups.py
src/services/route_policy_publication.py
src/services/route_group_cache_contract.py
src/metrics/route_group_cache.py
tests/db/test_route_policy_publication_invariants.py
tests/db/test_route_policy_repository.py
tests/db/test_route_policy_partial_writes.py
tests/db/test_route_policy_selector_integration.py
tests/services/test_route_groups.py
tests/services/test_route_policy_publication.py
tests/services/test_route_group_cache_contract.py
tests/test_route_group_redis_integration.py
tests/test_ui_route_groups.py
tests/test_route_policy_write_contract.py
```

Real-service tests used disposable PostgreSQL 15 and Redis 7 containers, with `DATABASE_URL` and
`DELTALLM_TEST_REDIS_URL` explicitly targeting those instances. They did not use a shared database.
The full backend run intentionally left other environment-gated integration suites skipped;
the 37 required real-service regressions ran separately without skips. The measured cache budget
remains one durable revision read per load, no Redis/snapshot read on an L1 hit, one Redis GET on an
L2 hit, and one durable snapshot read plus at most one SETEX on a miss. There is no retry loop or
provider call.

The feature branch remains based on `de2af0b8`; refreshed `origin/main` is `d628f169` (two commits
ahead of that base). No rebase, push, or merge was performed. Reconcile that branch divergence
before integration with main.
