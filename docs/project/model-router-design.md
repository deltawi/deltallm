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
| Projection, prompt, exact parser, decision, and request-local lifecycle | `src/router/selection/` |
| Shared direct chat resolution, signing/send/translation, bounded response | `src/providers/chat_upstream.py`, `src/providers/chat_hop.py`, existing adapters |
| Classifier-only request preparation, concrete target, and usage receipt | `src/router/selection/provider.py` |
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
dependency-free ingress admission and authentication
  -> cheap authenticated preflight capacity
  -> prompt rendering, hooks, guardrails, and transformed request validation
  -> final-model authorization, caller rate-limit and budget admission
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

PRs 1–3 deliberately fail closed before activation:

- saving and validating a selector draft is supported;
- direct publish and publish-latest-draft reject selector semantics explicitly;
- rollback rejects a version 3 selector revision;
- database and file-config runtime loading reject selector activation;
- deterministic policy simulation rejects selector policies; and
- a typed activation error is never converted into database-unavailable config fallback.

Silent acceptance is forbidden. PR 4 removes this temporary guard only after execution, cache,
capacity, accounting, deadline, cancellation, and telemetry prerequisites are connected.

## PR 2 isolated selector implementation

PR 2 implements a testable prerequisite, **not a live routing feature**. Nothing in bootstrap,
public endpoints, fallback planning, streaming, Responses, MCP, Batch, or simulation constructs the
selector service. Existing version-3 publication/runtime guards and fingerprints are unchanged.
There is no shadow execution, process-wide decision cache, new database schema, or Redis key.

### Bounded input and output

The pure projector accepts an already transformed and validated canonical chat request plus its
caller-computed token estimate. It inspects at most the newest 64 message positions and the first
four positions for system context, with at most 256 content blocks across the projection. It uses
the newest actual user message; it never substitutes an assistant or tool result. It retains up to
1,024 system-context characters and four recent user/assistant snippets totaling at most 2,048
characters. It carries only bounded token/tool counts, response-format kind, modality flags, and
explicit truncation/incomplete-feature flags. It never copies tool definitions, arguments/results,
media URLs/bytes, caller model IDs, credentials, tenant identifiers, metadata, or sampling settings.

Prompt contract version 1 has a fixed system instruction; all variable content, including lane
descriptions and request instructions, is quoted JSON data in a separate user message. The complete
message-content budget includes the system instruction, every lane, the JSON envelope, and escaping.
Newest-user text is allocated first, then system/recent context, using deterministic prefix/suffix
truncation. If even the envelope cannot fit (including valid policies with a 256-character limit),
selection defaults with `input_budget_insufficient` and makes no HTTP call. Missing/blank user input
and invalid Unicode have separate fixed default reasons. These controls limit routing authority;
they do not claim to eliminate semantic prompt injection or prove classification quality.

The provider bridge creates a fresh request for one non-streaming completion with a 64-token output
limit. It omits tools, caller defaults/metadata/sampling, and deployment `default_params`. Portable
mode requests JSON in the prompt and validates locally. Temperature zero and native JSON-object mode
require an explicit immutable provider capability profile; unknown capabilities omit these controls.
There is no selector-specific provider-name catalog. Existing adapters retain token-field conversion,
authentication, signing, and native wire translation.

The exact parser accepts only a JSON object with one string field, `lane`, matching a configured ID.
It rejects duplicate/extra keys, arrays/scalars, multiple objects, fences, coercion, unknown IDs, and
UTF-8 output over 256 bytes. It never repairs output. Rank comes exclusively from validated policy.
The bridge requires one completed plain-text assistant result with no tools/refusal/truncation;
provider-owned opt-in checks reject information that native normalization would otherwise discard.
Ordinary answer translation does not opt into these stricter checks.

### Provider ownership and resource bounds

The existing answer executor and classifier bridge share a single extracted direct-hop primitive.
The legacy Request-based resolver remains a compatibility facade over the Request-free resolver and
the existing adapter registry. The answer facade retains parameter defaults, serialization, metrics
labels/counts, and router-usage accounting. Its normal HTTP buffering and call count do not change.
The response-transform timing now measures canonical adapter translation, with final JSON model
serialization remaining in the answer facade; no extra phase observation is emitted.

The classifier receives an immutable snapshot of one concrete deployment's allowlisted scalar
provider configuration. PR 4 must prove its authoritative membership/access/capability eligibility
before binding it. The bridge checks the requested deployment ID against that binding and performs
no alias lookup, public gateway call, fallback, or retry. It borrows the bootstrap-owned client and
adapters and never creates/closes a client. The canonical client has zero transport retries; bounded
hops explicitly disable redirects even if a borrowed client has redirect following enabled.

The translated request body is capped at 256 KiB; response wire bytes are capped at 64 KiB before
translation. Content-Length is only an early check, never the authority. Transport streaming bounds
a non-streaming model response, not SSE. Identity encoding is requested; unexpected compression is
rejected before decoding, while the existing error-body hook remains in place. Responses are closed
on success, failure, timeout, and cancellation. Cleanup is inline with a maximum 50 ms close grace,
never a detached task; this small resource-release allowance can follow deadline cancellation but
does not permit additional provider/answer work. A failed close is recorded with fixed redacted
messages; an ordinary cleanup error cannot replace the primary failure. Cancellation arriving
during cleanup propagates even after a classified read failure, aborting the owner and its joiners
without retaining or returning a default decision. A close failure without a prior failure becomes
`transport_error`. No answer deployment is cooled down by this isolated service.

### One decision per outer operation

`RequestSelectorState` owns `NEW -> RUNNING -> DECIDED | ABORTED`. Its first caller runs inline and
claims ownership before awaiting. At most eight duplicates may join one completion Future. Only
that notification is shielded from a joiner's cancellation; the provider/owner is never shielded.
Overflow is a typed local error. Owner cancellation closes the hop, aborts state, and wakes joiners
with cancellation; a cancelled joiner does not cancel the owner. Aborted states never retry.

Completed immutable decisions retain lane, policy-derived minimum rank, fixed cause, latency,
source policy version/fingerprint, prompt-contract version, and bounded usage. Repeated calls reuse
the exact decision even when supplied another payload/group policy; original identity is retained.
Separate outer operations never share decisions. State retains no request, prompt, raw completion,
HTTP response, or exception traceback. A notification carries no exception object.

The existing parent `RequestDeadline` is shared unchanged. Projection, request translation, HTTP,
and parsing use the earlier parent/selector expiry, with checks around bounded synchronous work.
Connect/pool/write/read limits are clamped to configured limits and the remaining budget. Local
selector timeout may default only while the parent is live. Parent expiry and caller cancellation
always propagate; expired operations cannot return a success/default. Unexpected invocation errors
and gateway authorization, budget, tenancy, or durability failures are not classified as provider
defaults. Future integration remains responsible for sanitized outer error handling.

Usage is explicitly `not_attempted`, `reported`, or `unknown`, never invented zero. Valid normalized
usage survives a failed lane parse and is retained in request state when observed. Invalid/unusable
provider envelopes and interrupted hops have unknown usage. PR 3 must attach canonical admission,
frozen pricing/attribution, stable component idempotency, and durable incurred-cost finalization,
including cancellation/unknown-usage recovery. In-memory single-attempt ownership does not provide
cross-process billing durability. PR 4 must propagate state across actual retries/streams/MCP and
enforce upward-only candidate eligibility. These are prerequisites to activation, not PR 2 claims.

### Regression budget

PR 2 adds zero production selector calls and zero selector-free SQL/Redis/provider calls, pools,
workers, or tasks. An isolated selector invocation performs zero SQL/Redis/filesystem I/O and at
most one provider HTTP request. A reused decision, insufficient input, or expired-before-start
operation performs no provider request. Structural tests enforce dependency direction, absent
production wiring, and the new-module/function size bounds. Provider mocks cover native adapters,
response bounds, signing, parameter isolation, cancellation, cleanup, and no replay.

The reproducible comparison profile is `tests/performance/selector_free_profile.yaml`, served by
the real gateway against disposable local PostgreSQL/Redis and `selector_free_mock.py`. The
`run_selector_free_profile` module reuses the canonical constant-arrival generator: 10 RPS for
60 seconds, maximum 32 in flight, 10-second client timeout, one warmup plus three measured rounds
per revision. It records raw samples, offered/received rates, in-flight slope, provider/SQL/Redis
counts, phase timings, and latency distributions. This is a PR-level parity check, not the proposed
50-RPS release certificate, streaming/TTFT certification, or a claim about real-model savings.

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

## PR 2 verification record — 2026-09-07

Implementation branch: `issue-304-pr2-selector-service`; integration base:
`feature/issue-304-model-router` at `aa221e8d7a29474aa7dad0a8a315c1ec00fcdc1a`.
No commit, push, merge, or remote checklist update is part of this implementation handoff.
PR 1's separately identified P2 remains: the selector-free policy response member DTO can reject
legacy deployment IDs longer than 256 characters. This PR does not change or close that issue.

| Verification | Result |
| --- | --- |
| Pre-extraction chat/provider/Responses compatibility baseline | 235 passed |
| Expanded shared-path compatibility run | 323 passed |
| Full backend suite, `uv run pytest -q --tb=short --disable-warnings` | 3,896 passed, 205 environment-gated skips; 265.15 seconds |
| Final selector plus deterministic answer-facade tests | 139 passed; includes tests added after full-suite collection |
| Same answer-facade probe preloaded from actual PR 1 source | 3 passed; same request, response, phase counts, and usage writes |
| Required real PostgreSQL/Redis suites | 37 passed, no skips |
| Ruff check and format check, all touched Python paths | Passed |
| Public OpenAPI artifact check | Current; 215 paths, 280 operations |
| Strict MkDocs build and `git diff --check` | Passed |

The real-service run used disposable PostgreSQL 16 and Redis 7, with all 85 existing migrations
applied. It ran `tests/db/test_route_policy_selector_integration.py`,
`tests/db/test_route_policy_publication_invariants.py`, and
`tests/test_route_group_redis_integration.py` with explicit local dependency URLs. Other
environment-gated full-suite skips are not counted as integration proof. No schema, dependency
lockfile, UI, Helm, configuration contract, policy semantics, cache envelope, or activation-gate
file changed. Existing Python 3.14/Pydantic compatibility warnings were not suppressed by code edits.

### Sequential constant-arrival measurements

Both revisions used the same locked Python 3.14 environment, one real API process, local
PostgreSQL/Redis, `config_only` selector-free model routing, response cache disabled, the same
local-only master test key, and a fixed one-token mock with no artificial delay. Each revision had
one 60-second warmup followed by three 60-second measured rounds at 10 RPS, with 32 maximum in
flight and a 10-second client timeout. All six measured rounds received 600/600 HTTP 200s, sustained
10 started/completed RPS, and had zero generator drops. Observed in-flight maxima were 1–4 with
zero one-second sampled in-flight slope; this is not an internal queue-depth measurement.

| Revision / measured round | p50 ms | p95 ms | p99 ms | SQL calls in window | Provider calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| Base / 1 | 16.90 | 19.17 | 20.40 | 2,463 | 600 |
| Base / 2 | 17.21 | 20.92 | 45.95 | 2,462 | 600 |
| Base / 3 | 16.90 | 19.49 | 21.81 | 2,459 | 600 |
| PR 2 / 1 | 24.59 | 44.12 | 49.39 | 2,464 | 600 |
| PR 2 / 2 | 21.27 | 44.83 | 51.89 | 2,462 | 600 |
| PR 2 / 3 | 30.56 | 46.62 | 63.50 | 2,462 | 600 |

Every round recorded exactly 600 upstream HTTP observations, 600 router-usage observations, and
1,200 transform observations. Redis recorded 1,800 EVALs and 1,800 MULTI/EXEC pairs per round on
both revisions. Whole-process SQL/Redis counters also include existing polling, TTL maintenance,
and connection setup, so small window differences are not presented as exact per-request counts.
The deterministic facade probe separately establishes zero new SQL/Redis work, one HTTP request,
and exactly one existing usage write on success (zero when disabled or on provider error).

These sequential latency results **crossed the investigation threshold**; they are retained, not
discarded or relabeled as a pass. Mean prompt-phase time increased from 3.54 to 5.41 ms and budget
time from 3.73 to 6.65 ms, despite neither phase changing. Mean provider HTTP time increased from
1.18 to 2.49 ms. Host inspection showed substantial competing VM, security-agent, and other CPU
activity. This suggested shared-host drift, not sufficient evidence by itself to dismiss a regression.

### Alternating control and interpretation

A separately recorded control ran both revisions simultaneously on the same host/dependencies,
alternating requests between ports 59442 (base) and 59440 (PR 2). It used a 10-second warmup and one
60-second measured run at 10 total RPS: 5 RPS and 300 requests per revision. No test suite was
running during this control. Both revisions returned 300/300 HTTP 200s, with no drops and a combined
maximum in flight of one.

| Alternating control | Mean ms | p50 ms | p95 ms | p99 ms |
| --- | ---: | ---: | ---: | ---: |
| Base | 18.02 | 17.48 | 22.28 | 27.74 |
| PR 2 | 18.14 | 17.61 | 21.73 | 27.14 |

The control did not reproduce a PR 2 tail-latency regression and supports the host-drift
explanation. It does not replace the original 10-RPS-per-revision measurements or certify a release
SLO. The extraction-parity investigation is recorded with that limitation; selector quality,
real-provider latency, streaming/TTFT, production capacity, and net savings remain unqualified.

Raw JSONL samples and JSON summaries, including both warmups and the alternating control, are
retained outside the source tree at `/private/tmp/deltallm-pr2-profile.O1aJIY` on the implementation
host. Sequential measured run IDs are `09174ba8ce1444d2b4b5cf3eac4eaf21`,
`9f6d3f5251b34ab29c697fdd220a7deb`, `c0afb37d45ae485ab1d6a99950e2ea9a`,
`0c2a4f1812974675ae934fbe0d6f0cda`, `0f44dd500dfb4c1ab833ac37483243f7`, and
`ed1c19939c444837af5669ad582c7ed4`; the control is `6742c48db45f409197b1efa5cc4292e9`.

To reproduce, use disposable loopback PostgreSQL/Redis with `pg_stat_statements` enabled, apply
existing migrations, and start `tests.performance.selector_free_mock:app` on 59441. Configure each
gateway with `tests/performance/selector_free_profile.yaml`, explicit local `DATABASE_URL`,
`REDIS_URL`, corresponding `DELTALLM_*` settings, and matching local-only master/salt values. Run
each revision on 59440 with `DELTALLM_LOAD_API_KEY` set to that test key:

```sh
uv run python -m tests.performance.run_selector_free_profile --label before --output-dir /tmp/pr2-profile
uv run python -m tests.performance.run_selector_free_profile --label after --output-dir /tmp/pr2-profile
```

For the separate control, keep PR 2 on 59440, start the base on 59442, then run
`uv run python -m tests.performance.compare_selector_free_gateways --output-dir /tmp/pr2-profile`.
The harness borrows the repository's canonical constant-arrival generator and rejects non-loopback
SQL/Redis measurement URLs. Its data-plane traffic always targets the fixed loopback gateway/mock.

### Cancellation review follow-up

The review found that an external cancellation during cleanup of a rejected provider response
could be swallowed and converted into a reusable default decision. The cleanup boundary now
suppresses only ordinary cleanup errors; cancellation propagates into the existing request-state
owner, which aborts and wakes joiners without caching a decision or allowing another provider call.
No tenant scope, public contract, admission/accounting owner, activation guard, cleanup grace,
dependency call count, or normal answer path changes. There is no new await, retry, client, or task.
The earlier performance evidence and its limitations remain unchanged; load and real-service
PostgreSQL/Redis profiles were not rerun for this exception-only correction.

The new event-barrier tests reproduced four failures before the fix (observed/declared oversized
bodies, unexpected encoding, and transport read failure), with the existing-cancellation control
passing. After the fix, this focused command passed all 65 tests:

```sh
uv run pytest tests/router/selection/test_cleanup_cancellation.py tests/router/selection/test_provider.py tests/router/selection/test_request_state.py -q --tb=short --disable-warnings
```

The tests assert owner/joiner cancellation, terminal `ABORTED` state, unknown usage, no retained
decision/exception, no replay, inline cleanup completion, and continued borrowed-client ownership.
An additional control verifies that an ordinary close failure cannot replace an existing
cancellation. Ruff check and format check passed for `src/providers/chat_hop.py` and
`tests/router/selection/test_cleanup_cancellation.py`.

The post-fix full backend command `uv run pytest -q --tb=short --disable-warnings` passed
3,908 tests with 205 environment-gated skips and 26,946 warnings in 271.87 seconds. Skipped tests
are not counted as real-service integration proof. `git diff --check` also passed.
