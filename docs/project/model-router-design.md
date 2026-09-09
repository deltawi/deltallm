# Route-Group Model Router Design

Status: accepted design for [issue #304](https://github.com/deltawi/deltallm/issues/304).
The implementation is split into six reviewable PRs. PRs 1–3 define prerequisites and must not
activate a selector. PR 4 is the first activation boundary.

PRs 1–3 are integrated into the feature branch, not `main`. PR 4 implements the
real-time activation boundary locally, including the user-approved soft-budget
contract. Its [completion record](#pr-4-completion-record-2026-09-09) supersedes the
historical partial checkpoints below; the [PR 3 record](#pr-3-completion-decision-2026-09-08)
remains implementation history, not the current verification status.

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
supported. Publish/rollback report a stable activation error if the complete selector
runtime or its required deployment metadata cannot be qualified.

Runtime Route-Group snapshots carry explicit validation provenance. PR 4 advances the
bounded 4 MiB cache envelope and shared-key-builder namespace to v3 with `validated-v3`
provenance. Database and validated file configuration remain authoritative. Canonical
selector/lane data now survives L1/L2 round trips; runtime generation construction
independently qualifies deployment capabilities, context, pricing and capacity before
swapping the snapshot. Missing, old `inactive`, malformed, oversized or incompatible
envelopes are observable misses and reload PostgreSQL. Loader validation failures never
fall back to file configuration. Owned mode, strategy, timeout, retry, context and
selector fields are validated before a cache hit; opaque historical fields keep their
versioned meaning. Invalid groups reject the entire envelope. Cache failures retain the
fixed-reason `deltallm_route_group_cache_failures_total` counter and redacted logs;
repair failure does not fail a valid durable read. Old namespaces expire naturally.

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

### PR 3 cache identity decision

The routing generation owns a read-only map of `route-response-v1` fingerprints, computed
synchronously while constructing that generation, before publication. Each response identity
composes the existing canonical `route-policy-v1` fingerprint with concrete deployment configuration,
inherited execution settings, and configured fallback dependencies. The durable policy fingerprint
and its version-aware selector/lane normalization are unchanged. Legacy callable model groups also
receive an identity. This projection does not execute eligibility, strategy, or fallback policy.

Concrete deployment identity includes normalized provider/upstream model, endpoint and provider
version/region, configured timeouts/output bounds, authentication-header configuration, stable
credential reference, workload mode, forwarded defaults, context limits, tags, token/request prices
used for ordering, and provider RPM/TPM limits. Raw credential values, health incarnation, live
health/capacity, and opaque operator metadata are excluded. Only digests survive publication; no
configuration or topology is added to public headers or logs. The existing default-parameter facade
and identity projection share `src/providers/request_defaults.py`, so UI-only `available_voices`
does not invalidate responses or reach providers. Caller parameters still take precedence.

`src/router/fallback_identity.py` projects ordinary, context-window, and content-policy fallback
edges from the generation's `FallbackConfig`. Target order and edge kind matter. Missing targets
participate so later target creation invalidates ancestors. The graph conservatively includes
transitive configured dependencies; the failover owner still decides which can actually execute.
Iterative strongly-connected-component condensation handles cycles and deep/shared chains without
recursion or path enumeration. Graph work and temporary storage are O(V + E), plus canonical sorting
of cycle members; hashing occurs once per component and once per callable group. Truly unrelated
groups do not affect a root's identity. No new global cache or lifecycle is introduced.

After authenticated preflight, cache middleware resolves the final model through the pinned router
and supplies its fingerprint as a mandatory response-identity dimension. Custom cache keys replace
only the caller-selected payload portion; they cannot remove the server's routing dimension. Both
custom content and routing identity are hashed, preserving existing tenant/key, endpoint, and
stream/JSON separation without adding topology to response headers. The response-cache namespace
moves from `v2` (and the incomplete local `v3` checkpoint) to `v4`. Equivalent generations and
rollback to equivalent policy can reuse `v4` entries; reload UUIDs, revision numbers, and unrelated
groups do not invalidate an entry.

This projection has no SQL, Redis, provider, filesystem, queue, or task work. Its retained size is
one digest per configured callable group in the existing disposable generation; each request does
one map lookup instead of serializing all members. Cache reads/writes and their failure policy
retain their existing owner and call counts. Snapshot construction is the only producer; consumers
must not mutate policy/registry objects inside a published generation to update cache identity.

Rollout naturally leaves old `v2`/`v3` entries to expire under their existing TTL; no wildcard deletion
or database migration is required. The namespace bump causes a one-time cold response cache and
must be included in capacity planning. Rolling back this prerequisite binary restores its old
namespace; this is not a supported rollback of an activated selector (see the rollout section).
This slice does not activate selectors or implement atomic budget reservation or unknown-usage
reconciliation. The subsequently approved charging policy is recorded below.

### PR 3 selector charging decision — 2026-09-08

The product owner confirmed that **customers pay the selector's cost**, in addition to answer
cost. Interpret this as pass-through actual provider cost with no new markup. Keep provider cost
and customer charge as separate exact monetary amounts, even when they are equal. Freeze the
concrete selector deployment, provider/model, pricing source/version, USD token rates, attribution,
and `NUMERIC(38,18)` / `ROUND_HALF_EVEN` rounding inputs with the component event. Do not look up
current prices when replaying a receipt. Existing answer-pricing policy is unchanged.

Only a provider-reported receipt establishes a selector charge; invalid selector output does not
erase incurred usage. Answer failure, cancellation, disconnect, or retry must not erase or repeat
the selector charge. The selector uses a deterministic `selector:v1` child identity derived from
the server-owned outer billing event, not the client correlation ID. Public OpenAI response usage
continues to describe the answer only; admin reporting must distinguish components from external
requests.

Unknown usage remains durably pending reconciliation and conservatively retains its allowance;
it is neither zero actual cost nor permission to charge an estimated upper bound as observed cost.
Recovery must not re-execute the provider call to reconstruct a lost receipt. A not-attempted
component can release its selector allowance only when non-dispatch is durably established.
Budget admission must cover the answer plus selector across every applicable scope. Existing
read-only budget checks are soft controls, not a substitute for atomic reservation. Activation
remains blocked until these economic and recovery prerequisites have been implemented and proven.

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

## PR 3 partial implementation record — 2026-09-08

Only the response-cache identity slice was implemented at this first checkpoint. It did not
complete PR 3, activate a selector, introduce billing changes, or settle who pays the selector's
cost. The subsequent charging decision above and receipt checkpoint below supersede that open choice.
Capacity integration, atomic budget reservation, durable component accounting/reconciliation,
the typed miss-only execution boundary, and selector/admin telemetry remain outstanding.
The temporary implementation plan is retained until those parts and their verification finish.

The cache slice includes the generation-owned canonical fingerprint map, final-model alias
resolution from the pinned generation, hashed routing-aware ordinary/custom keys, and the `v3`
response-cache namespace. Tests cover effective weights, strategy/context/retry/timeout changes,
selector/lane semantics without activation, historical opaque-field behavior, equivalent reloads,
rollback reuse, alias retargeting, in-flight preflight reload, Chat/Responses/Completions,
stream replay, old namespace isolation, and the existing no-production-selector-wiring guard.

Verification completed:

- Unchanged PR 2 tree (`ff15a5f4`, tree-identical to merge base `eb33826e`):
  `uv run --frozen --extra dev pytest tests/test_cache.py tests/test_routing_runtime_generation.py -q --tb=short --disable-warnings`
  — 30 passed, 272 warnings.
- New focused cache/runtime suite:
  `uv run --frozen pytest tests/test_routing_cache_identity.py tests/test_cache.py tests/test_routing_runtime_generation.py -q --tb=short --disable-warnings`
  — 61 passed, 371 warnings. The first run exposed insufficient RPM in the new multi-request
  fixture; only that fixture's key allowance was increased, with rate admission still enabled.
- Affected suite:
  `uv run --frozen pytest tests/router/selection tests/test_route_policy_selector_contract.py tests/test_route_policy_validation.py tests/test_routing.py tests/test_routing_runtime_generation.py tests/bootstrap/test_routing_bootstrap.py tests/test_chat.py tests/test_text_endpoints.py tests/test_cache.py tests/test_routing_cache_identity.py tests/test_metrics.py -q --tb=short --disable-warnings -o faulthandler_timeout=45`
  — 455 passed, 2,471 warnings. These counts overlap the focused suite; they are not additive.
- Real Redis, using the existing disposable loopback test container:
  `DELTALLM_TEST_REDIS_URL=redis://127.0.0.1:56390/0 uv run --frozen pytest tests/test_cache_routing_redis_integration.py tests/test_cache_redis_integration.py -q --tb=short --disable-warnings -o faulthandler_timeout=45`
  — 3 passed, 29 warnings, no skips. The container was stopped afterward, with data retained.
  This proves cache cross-client/version/policy isolation and TTL round trips, not selector
  capacity or billing recovery.
- Ruff check and format check passed for the eight touched Python paths. OpenAPI export check
  remained current at 215 paths / 280 operations. `git diff --check` passed.

No schema, dependency, configuration setting, admin API/UI shape, or deployment manifest changed.
The full backend/real-PostgreSQL economic gates have not been run for the unfinished PR 3 work.

The checked-in `tests/performance/routing_cache_profile.py` uses the canonical constant-arrival
generator with the existing ASGI test application, fake Redis, no database, and a fixed provider
mock. Run the same script in each checkout's locked environment with `PYTHONPATH` set to that
checkout, `--label before` or `--label after`, and `--output-dir` pointing to a temporary artifact
directory. Each cache-hit/miss case warms once, then offers 10 RPS for 20 seconds, bounded at 16
in flight. It records raw samples, actual offered/completed rates, latency distributions, provider
time, in-flight slope, and dependency method counts; assertions enforce the baseline counts.
This is a narrow cache-path regression profile, not production latency, SQL/Redis load, TTFT,
multi-replica capacity, or the proposed 50-RPS release certificate.

Raw samples are retained locally under `/private/tmp/deltallm-pr3-cache-profile.QnToYX`;
`control/` contains the fresh comparison. Every measured case completed 200/200 requests at
10 RPS with no generator drops, maximum observed in-flight of one, and zero sampled queue slope.
The mock dependency counts were identical between revisions: a cache miss made one cache get,
one cache set, and one provider post per request; a hit made one cache get and no provider post.
Fake-Redis method counts also matched exactly, including nested fake implementation calls:
misses used `get=1, eval=6, hgetall=1, mget=1, zadd=2, incr=1, expire=2, incrby=1,
zremrangebyscore=1, pexpire=1`; hits used `get=1, eval=3` per request. These are not claims about
real Redis wire-command counts or real SQL operations.

The original before/after pair was separated by almost nine hours and showed p95 increasing
from 3.13 to 14.10 ms on misses and 2.20 to 6.89 ms on hits. It is retained, not discarded.
A fresh sequential control, taken a few minutes apart on 2026-09-08, measured:

| Cache path | Revision | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | ---: | ---: | ---: |
| Miss | PR 2 base | 2.98 | 7.44 | 8.69 |
| Miss | PR 3 cache slice | 4.59 | 6.20 | 7.14 |
| Hit | PR 2 base | 4.54 | 6.73 | 11.51 |
| Hit | PR 3 cache slice | 3.17 | 6.18 | 7.58 |

The control supports unchanged dependency counts and shows no p95/p99 increase in this narrow
profile, but miss p50 was higher. Variation in the unchanged baseline means the original latency
difference cannot be attributed solely to this change. These short, sequential, in-process runs
do not establish a production SLO or replace the remaining PR 3 integration/performance gates.

### Selector receipt checkpoint — 2026-09-08

The approved pass-through charging policy now has an isolated, typed receipt-to-outbox path.
This is **partial PR 3 implementation**, not completed durable selector execution or permission
to activate the feature.

- `src/billing/selector_charge.py` owns immutable pricing, reported token receipt, frozen
  attribution, deterministic UUIDv5 child identity (`selector:v1` within the server operation
  UUID), and mapping to the existing spend payload. Customer charge equals provider cost.
  Unknown/unattempted usage is not a billable receipt. Missing rates, invalid counts, unsupported
  currency, unrepresentable amounts, and missing cached-input usage when a discount applies
  fail validation. An explicitly configured zero price remains valid.
- Monetary arithmetic uses `Decimal` with a local precision of 80 and canonical
  `NUMERIC(38,18)` rounding. The legacy answer calculator rounds float results to ten decimals;
  reusing it would lose the approved receipt's precision. The new exact calculation is limited
  to frozen token/request rates, including observed input-cache discounts, inside the existing
  billing owner. It neither resolves live prices nor changes answer pricing. Provider-specific
  or tiered pricing without enough frozen evidence remains unsupported; the future binding
  must reject it before dispatch, not omit it from the receipt and guess a charge.
- `SpendIngestionService.log_selector_charge` requires durable ingestion, a database, an open
  service, and a supplied monotonic acceptance deadline. Acceptance is bounded by the earlier
  supplied deadline and a fixed two-second ceiling. It reuses the same bounded outbox and
  synchronous transactional overload fallback. Capacity denial propagates. Other dependency
  failures become a sanitized `selector_accounting_unavailable` error, never the provider
  `TimeoutError` caught by safe-default routing. Cancellation propagates without detached work.
- The method introduces no worker, pool, independent ledger, schema, or settings. Normal enqueue
  reuses the existing advisory admission lock plus bounded insert/capacity statement in one
  transaction; fallback and consumption reuse the existing event writer and five scoped ledger
  updates. Database statement/lock-deadline and integration evidence remain open. No production
  request calls this method, so selector-free request dependency counts are unchanged.
- Spend preparation was extracted from the oversized writer into
  `src/billing/spend_preparation.py`; the original method delegates to the single mapper.
  Its historical dynamic outbox representation is a named compatibility boundary, not a new
  domain interface. Keep it bounded and remove that dynamic shape when accepted spend events
  migrate together to a typed representation. The new receipt contract has no dynamic fields
  or HTTP/database dependencies. Size and dependency tests guard both seams.
- Accepted `cost_exact` and `provider_cost_exact` are authoritative; float columns are only
  reporting mirrors. Batch ledger addition also uses an explicit Decimal context so ambient
  precision cannot drop fractional units before PostgreSQL receives them. The transactional
  writer and outbox fencing/idempotency ownership are unchanged.

Verification at this checkpoint (overlapping suites; do not add their counts):

- `.venv/bin/python -m pytest tests/test_selector_charge.py tests/test_spend_ingestion.py -q
  --tb=short --disable-warnings`: **68 passed**, 371 warnings, 1.30 seconds.
- Affected suite: **632 passed**, 3,344 warnings, 36.22 seconds, with the command below.
- Ruff check and format check passed for all 14 touched Python paths. OpenAPI parity passed:
  215 paths / 280 operations. `git diff --check` passed. No API/UI shape changed.
- `DATABASE_URL=postgresql://pr2:pr2-local-only@127.0.0.1:55440/deltallm_pr2_test
  .venv/bin/python -m pytest tests/test_selector_charge_db_integration.py -q --tb=short
  --disable-warnings -o faulthandler_timeout=30`: **4 setup errors**, 38 warnings, 0.33 seconds.
  Prisma's engine cannot bind its local socket in the current sandbox (`PermissionError:
  Operation not permitted`). No integration test body ran; this is not PostgreSQL proof.
  The tests cover five-scope exact ledger effects, duplicate/concurrent delivery, fresh-service
  outbox recovery followed by answer failure, and transactional error/cancellation rollback.
  They must be executed in a session permitting the local Prisma engine before acceptance.
  The disposable test database was stopped again afterward, preserving its data.
- The `uv run --frozen` retry was also blocked by the default user-cache directory's permissions;
  local checks above used the worktree environment installed from the frozen lock at the cache
  checkpoint. No dependencies or lockfiles were changed.

```sh
.venv/bin/python -m pytest \
  tests/test_selector_charge.py tests/test_spend_ingestion.py \
  tests/test_billing.py tests/test_billing_tier_pricing.py \
  tests/test_telemetry_ingestion_admin.py tests/test_spend_visibility.py \
  tests/test_spend_reporting_cache.py tests/router/selection \
  tests/test_route_policy_selector_contract.py tests/test_route_policy_validation.py \
  tests/test_routing.py tests/test_routing_runtime_generation.py \
  tests/bootstrap/test_routing_bootstrap.py tests/test_chat.py tests/test_text_endpoints.py \
  tests/test_cache.py tests/test_routing_cache_identity.py tests/test_metrics.py \
  -q --tb=short --disable-warnings -o faulthandler_timeout=30
```

Still required before PR 3 is complete: pre-dispatch durable intent and unknown-usage
reconciliation, conservative answer-plus-selector allowance and atomic reservations, provider
capacity integration, typed miss-only execution eligibility, and component-aware admin
aggregation/metrics. In particular, `model_router_selector` is a child spend event, not an
additional external request: existing request-count aggregations must become component-aware
before this writer is activated. The receipt mapper alone does not guarantee recovery if a
process dies before durable acceptance, and its type alone does not authorize attribution IDs.
Future composition must supply verified attribution and durably freeze it before the provider.
Publication/runtime guards remain closed; the local plan remains until implementation and
required verification finish. No PR 3 commit, push, remote issue update, or PR has been made.

### Cache review remediation — 2026-09-08

Scope: the two cache-invalidation P2 findings, not the unfinished capacity/economic/telemetry
prerequisites. The generation remains the source of truth and the only identity publisher.
Tenant/key scoping, preflight ordering, cache degradation, retry ownership, and selector activation
guards are unchanged. There are no new settings, schema changes, dependency clients, tasks, or
request-path SQL/Redis/provider awaits.

- [x] Invalidate when an unchanged concrete deployment ID is retargeted or its response-affecting
  configuration changes; preserve equivalent reload and rollback reuse.
- [x] Invalidate originating groups when ordinary or specialized fallback targets, membership,
  policy, or transitive dependencies change; preserve unrelated-group reuse.
- [x] Bump response-cache identity to `v4`, excluding both old `v2` and incomplete local `v3` entries.
- [ ] Rerun real-Redis cache integration in an environment permitting loopback socket connections.
- [ ] Update remote issue #304 when GitHub write approval is available. No issue edit was made.

The original 12 HTTP reproductions failed before the implementation, with an unexpected cache hit
after deployment retargeting or fallback member replacement. The final tests cover ordinary/custom
keys, chat/Responses/completions, streaming, embeddings, equivalent reloads, rollback, and the
admin registry-rebuild path. Pure tests cover concrete configuration/defaults, secret and opaque
metadata exclusion, inherited execution settings, fallback edge kinds/order/removal, missing target
creation, cycles, and 2,000-node shared chains with linear hash counts and no recursive traversal.
Configured fallback target spelling is preserved because failover looks plans up with that spelling;
the cache projection must not normalize a spelling change into an equivalent execution contract.

Final verification (overlapping suites, not additive):

- Focused command:
  `.venv/bin/python -m pytest tests/test_routing_identity_dependencies.py
  tests/test_routing_cache_invalidation.py tests/test_routing_cache_identity.py tests/test_cache.py
  tests/test_routing_runtime_generation.py -q --tb=short --disable-warnings
  -o faulthandler_timeout=45` — **122 passed**, 515 warnings, 12.92 seconds.
- Expanded affected suite below — **1,025 passed**, 6,152 warnings, 69.18 seconds. This includes
  hot reload, admin models, provider/default-parameter consumers, multimodal execution,
  billing/ingestion, selector guards, and cache/routing regressions.
- Ruff check and format check passed for all 19 touched Python paths. OpenAPI check
  (`.venv/bin/python scripts/docs/export_openapi.py --check`) reports the existing artifact
  current: 215 paths / 280 operations. `git diff --check` passed.
- Without a Redis URL, the cache integration suite reported four environment-gated skips.
  The explicit attempt below instead reported **4 failures**, 38 warnings, 0.82 seconds:
  socket connections to loopback were denied with `PermissionError: Operation not permitted`.
  Both existing Redis tests and the version-isolation tests failed for the same environmental
  reason; no Redis round-trip proof is claimed. The existing disposable Redis container was
  started for this attempt and stopped afterward, preserving its data. No production dependency
  was used and no sandbox permission was bypassed.

```sh
.venv/bin/python -m pytest \
  tests/test_routing_identity_dependencies.py \
  tests/test_routing_cache_invalidation.py \
  tests/test_routing_cache_identity.py \
  tests/test_selector_charge.py \
  tests/test_spend_ingestion.py \
  tests/test_billing.py \
  tests/test_billing_tier_pricing.py \
  tests/test_telemetry_ingestion_admin.py \
  tests/test_spend_visibility.py \
  tests/test_spend_reporting_cache.py \
  tests/router/selection \
  tests/test_route_policy_selector_contract.py \
  tests/test_route_policy_validation.py \
  tests/test_routing.py \
  tests/test_context_routing.py \
  tests/test_routing_runtime_generation.py \
  tests/bootstrap/test_routing_bootstrap.py \
  tests/config/test_dynamic.py \
  tests/test_chat.py \
  tests/test_text_endpoints.py \
  tests/test_embeddings.py \
  tests/test_embeddings_batch.py \
  tests/test_cache.py \
  tests/test_metrics.py \
  tests/test_chat_hop_compatibility.py \
  tests/test_provider_compat.py \
  tests/test_provider_resolution.py \
  tests/test_ui_models.py \
  tests/test_ui_legacy_models.py \
  tests/test_route_decision_multimodal.py \
  tests/test_elevenlabs_tts.py \
  tests/test_uniformity_hardening.py \
  tests/test_data_plane_audit_extended.py \
  -q --tb=short --disable-warnings -o faulthandler_timeout=45

DELTALLM_TEST_REDIS_URL=redis://127.0.0.1:56390/0 \
  .venv/bin/python -m pytest tests/test_cache_routing_redis_integration.py \
  tests/test_cache_redis_integration.py -q --tb=short --disable-warnings \
  -o faulthandler_timeout=30
```

The existing constant-arrival profile was run before and after the fix using:

```sh
.venv/bin/python -m tests.performance.routing_cache_profile --label before \
  --output-dir /private/tmp/deltallm-routing-identity-fix.mK7sVJ
.venv/bin/python -m tests.performance.routing_cache_profile --label after \
  --output-dir /private/tmp/deltallm-routing-identity-fix.mK7sVJ
```

Each case offered 10 RPS for 20 seconds: all 200 requests succeeded, zero generator drops,
maximum one observed in-flight request, and zero sampled in-flight slope. Raw samples and
dependency counts remain in the output directory. Per-request dependency counts were identical:
a miss made one cache get, one cache set, and one provider post; a hit made one cache get and
zero provider posts. The profile's exact fake-Redis command-count assertions also passed.

| Case | Before p50 / p95 / p99 (ms) | After p50 / p95 / p99 (ms) |
| --- | --- | --- |
| Miss | 7.88 / 11.99 / 13.28 | 9.10 / 11.90 / 15.01 |
| Hit | 4.75 / 6.96 / 9.70 | 4.48 / 6.15 / 7.96 |

These are short sequential local-mock samples, not a production latency or TTFT certificate.
Miss p50 and p99 increased (p99 by about 1.72 ms); hit latency decreased. No general latency
improvement is claimed. The new graph work remains off requests and adds only snapshot-owned
digests; the small shared-defaults extraction adds no dependency calls.

The broader PR 3 completion checklist above remains open. The implementation plan is retained
because PR 3 and its required verification are not finished. No commit, push, or merge was made.
## PR 3 completion decision — 2026-09-08

The remaining prerequisites are implemented as dormant, request-free capabilities, not
production selector activation. The existing selection service, router attempt owner,
spend-ingestion lifecycle, response cache and protected spend reporting remain the owners.

Implementation slices and verification gates:

- [x] Extend canonical attempt admission with optional atomic RPM/TPM consumption and a
  concurrency ceiling. Selector admission fails closed on coordination failure, uses an
  owner token known before acquisition, and never consumes caller RPM or changes health.
- [x] Extend canonical billing with durable all-scope operation reservations and component
  dispatch intent; frozen exact pricing, ownership and server operation identity survive
  process loss. No provider request runs inside a database transaction.
- [x] Bind the isolated selector hop to admission, intent and receipt finalization. An
  uncertain dispatch is never replayed; unknown usage remains visibly pending and retains
  its conservative hold until authoritative reconciliation. A proved unattempted component
  releases its hold. Actual selector cost is passed through without markup.
- [x] Require typed cache miss/bypass evidence for the future execution boundary; missing
  evidence and a response hit cannot invoke selection. The existing cache owns this signal.
- [x] Add enum-only metrics, redacted protected decision metadata and component-aware
  reporting, with unknown/partial savings rather than invented counterfactuals.
- [ ] Verify isolated prerequisites, closed activation guards, affected application paths,
  real dependency/migration behavior and before/after dependency/latency budgets.

Budget ownership is a prerequisite, not a claim that legacy read-only budget checks become
hard caps before PR 4 integrates reservation into real execution. PR 4 must route every
cost-incurring attempt in a reserved operation through the same dispatch/finalization
boundary, including answer retries and managed continuations. Configurations without a
provable complete answer-plus-selector cost bound must fail closed; token estimates are
not cost ceilings. Existing unrelated selector-free execution is not activated or migrated
by this PR. Reservation and intent persistence belong to billing and use the existing
PostgreSQL client and spend worker, without another queue, ledger or background lifecycle.

The capacity extension is opt-in, preserving existing attempt behavior and dependency
counts. Its counters share the existing routing keyspace and supported standalone Redis
topology. Bounded selector work reserves provider usage conservatively before dispatch;
unknown usage does not refund consumption. Leases expire after the bounded attempt plus
cleanup grace, and release is owner guarded even after an ambiguous acquisition response.

Database rollout is additive through the normal release migration owner. Do not drop
economic state on rollback; drain/reconcile it using compatible code before removing the
schema. Pending uncertain records must not be expired into zero spend. Keep their storage
bounded by admission capacity, emit pending visibility, and use operator-authorized,
receipt-backed reconciliation rather than automatic provider replay or estimated charges.

### Implemented ownership and economic transitions

The isolated composition is `admitted_selector_service`: cache/budget admission, canonical
Redis attempt capacity, durable selector dispatch intent, direct provider invocation,
receipt acceptance and owner-guarded release. It constructs no clients or tasks. Production
bootstrap does not construct this factory or the optional billing recovery capability.
There is no new endpoint, setting, UI workflow, selector-decision cache or shadow mode.

`OperationReservation` freezes the full selector-plus-answer allowance before any paid
selector work. `ProviderEnforcedSelectorCeiling` is a **trusted, qualified deployment
capability**, not a user-provided token estimate: the quote reserves at least the provider's
entire enforced context window, including its hidden framing. It also requires a provider
contract that enforces the existing 64-output-token cap and has only the supported input,
output, cache-read and per-request billing dimensions. Unknown provider limits, custom
templates without a qualified bound, cache-write pricing or uncapped reasoning/output
cannot be enabled by merely supplying `max_input_chars`. PR 4 must bind this capability
to the actual frozen deployment configuration; PR 3 does not infer or certify provider
limits from model names. Test fixtures explicitly qualify only their deterministic mock.

Answer quotes cover maximum rates across eligible candidates and every allowed attempt
and continuation. Each attempt is rounded upward to 18 decimal places before multiplying
by its attempt bound. Input allowance uses the larger cached/uncached rate. Actual
selector charges retain canonical half-even rounding and equal actual provider cost,
without customer markup. A reported provider violation of its ceiling is durably accepted
before rejecting further work; it is not discarded to make the reservation look correct.

The additive billing-operation journal extends canonical spend accounting; it does not
replace the spend ledger or outbox. State and authoritative receipts belong in PostgreSQL.

| Component transition | Economic effect |
| --- | --- |
| New operation → reserved | Atomically hold selector plus complete answer allowance across key, user, team, organization and team/model scopes |
| Reserved → dispatched | Fenced, non-replayable intent; provider may run only after commit |
| Reserved → unattempted | Release only this component's hold; no charge |
| Dispatched → unattempted | Only the same owner with explicit pre-send rejection evidence may release |
| Dispatched/pending → accepted | Freeze an authoritative selector receipt independently of answer outcome |
| Accepted → settled | Canonical outbox writes the unique event and all ledgers; release the reserved component once |
| Expired reserved → unattempted | Reclaim work proved not dispatched |
| Expired dispatched → pending | Keep the hold and unknown cost; never replay the provider or finalize estimated/zero usage |

Every mutation checks the operation ID, owner token and frozen snapshot. Replaying an
accepted receipt is idempotent; changing its receipt, ownership or prices fails closed.
The parent operation ID and deterministic `selector:v1` UUID are distinct unique events.
The operation lifetime is database-bounded to 15 minutes; longer execution plans are not
supported by this prerequisite. Expiry controls execution ownership, not financial truth.
The final closed row retains economic deduplication identity.

The existing spend worker optionally runs recovery, prioritizing one known receipt and
then one other open operation in separate short transactions. Each lane excludes durable
quarantines, and a transient failure in the first lane does not skip the second or revisit
the same selected row in that slice. Cancellation stops immediately. Each transaction
is capped at 250 ms; the two-operation slice is at most 500 ms. Dependency failure is
reported after both lanes and does not prevent the canonical outbox from draining.
Receipt/outbox capacity exhaustion retains the original receipt. Fair open-row rotation
finds late answer receipts, including direct-writer
receipts. A failed-request log or default zero-token record is **not** a reported answer
receipt: settlement requires a server-owned `usage_snapshot.kind=reported` spend record.
PR 4 must supply that evidence for actual answer execution. Unknown costs remain pending
until authoritative receipt-backed reconciliation, even if the user disconnects.

Reservations acquire locks in this order: unique operation, canonical key/user/team/
organization/team-model rows, then global operation capacity. Concurrent duplicate
insertion uses `ON CONFLICT DO NOTHING RETURNING`; the losing caller locks and compares
the existing frozen snapshot without changing holds or capacity. A full/missing capacity
row rolls back insertion and all five holds. Recovery reconciles accounts **before**
outbox admission, so it cannot hold spend capacity while waiting for a ledger row.
The canonical spend writer preserves operation -> ledger -> operation capacity ->
outbox/spend capacity order. No global capacity lock precedes an operation/account wait.

Missing maintained counters
fail closed; no request-path history scan initializes or repairs them. PR 4 activation
readiness must verify/backfill missing team/model counters off-path against canonical
spend history. Revocation can block/expire a key immediately, but physical deletion of a
row with a nonzero economic hold is database-guarded until reconciliation. Financial
history is retained like the canonical ledger; this is not a prompt-content archive.

Legacy selector-free traffic still uses its existing admission implementation. **These
reservations cannot establish hard shared budgets while other traffic in the same budget
scope bypasses reservation.** PR 4 must qualify the common admission boundary for all
traffic sharing a protected scope before claiming hard-budget support. It must not just
open the selector gate and treat this prerequisite as proof of whole-gateway hard caps.

**PR 4 decision, 2026-09-09:** the user chose non-strict budgets. Do not extend atomic
reservation admission to selector-free traffic. Concurrent requests may exceed a budget;
there is no measured frequency or guaranteed overshoot bound. This supersedes the
requirement to qualify whole-gateway hard-budget admission, not durable billing: incurred
selector and answer usage still has one economic effect, and unknown usage remains pending
for reconciliation. The PR 3 migration is shared and remains immutable. These paragraphs
describe the reservation prerequisite, not a claim that production uses it or enforces
hard shared budgets.

### Dependency, storage and rollout budgets

- Existing selector-free requests: zero added SQL, Redis or provider awaits. All new
  execution/recovery dependencies are optional and unconstructed in production.
- One isolated reported selector: three bounded billing transactions (reserve, dispatch,
  accept), eight application SQL statements total on the fresh-success path, plus transaction
  protocol; two router Redis EVALs (acquire/release); at most one provider POST. No awaited
  request-path loop or provider retry. Known receipt acceptance may repeat identically once
  on cancellation within the same 250 ms cleanup deadline.
  The review fix reduces fresh reservation from six to four statements including timeout
  setup; duplicate reservation uses three. Hermetic repository tests assert the count and
  ordering. These counts are not a substitute for real PostgreSQL contention/latency data.
- Admission/dispatch transactions use the earlier request/selector deadline and a 250 ms
  local maximum including pool wait, statements, locks and transaction duration. Provider
  work remains inside the earlier selector/parent deadline. Receipt cleanup has 250 ms;
  capacity cleanup has 50 ms and a token-owned TTL fallback. These cleanup bounds must be
  included in PR 4 streaming/disconnect/shutdown budgets.
- Provider RPM consumes one and TPM consumes the conservative token allowance atomically
  with concurrency admission. Consumption is not refunded after uncertain dispatch and
  must not be incremented again by the ordinary provider-usage recorder. Existing minute
  buckets retain their 120-second TTL. This extends the existing **standalone Redis**
  contract, not Redis Cluster support. The owner uses EVAL directly, so NOSCRIPT is not a
  new script-cache failure mode.
- No new pool or worker is created: added production connection/task count is currently
  zero. PR 4 must allocate admission/recovery/reporting capacity from the existing owners,
  validate replica × process × pool arithmetic and measure contention before activation.
  The operation-capacity row is intentionally a single atomic admission serialization
  point; throughput at maximum replica count is an open real-PostgreSQL qualification gate.
- Pending operations are capped at 100,000 by durable admission capacity; a full journal
  denies admission. Each completed operation adds one retained financial identity/snapshot.
  The table has nine indexes including its primary/unique identities, two recovery
  predicates, four visibility/time access paths and terminal history. The added write
  amplification must be measured with real database samples before production enablement.
  Provision storage using selector RPS × 86,400 × measured bytes per operation/day, including
  indexes/WAL, in addition to canonical spend storage. Active-count limits are **not** a
  bound on lifetime terminal-history size.
- Retention decision: retain operation identities and frozen financial evidence with the
  canonical spend ledger; do not independently TTL/delete them and thereby permit replay.
  This version has no automatic archival or partitioning. Operators must monitor volume,
  index growth and dead tuples, keep autovacuum/analyze enabled, and qualify retention/
  archive capacity before activation. Future archival must preserve idempotency lookup
  and cannot release unknown holds. No new prompt/body content is stored.
- Migration `20260908100000_billing_operation_reservations` is an additive transaction,
  with a 2-second DDL lock timeout and 30-second statement timeout. It adds exact hold
  counters, the operation journal, predicates and deletion guards. No history backfill
  runs inline. Coordinate migration through the existing release job; a timeout rolls
  back for a later coordinated retry. Fresh, last-release and shared-feature verifier
  paths now explicitly check its objects and monetary precision. Roll back application
  activation, not the economic schema; retain a compatible spend/recovery owner until all
  accepted and pending operations have been reconciled.

### PR 3 review correction: durable recovery isolation

`deltallm_recover_operation` remains strict: a frozen receipt mismatch raises SQLSTATE
`PBR01`, rolling back a spend event and every ledger delta in the writer transaction.
The existing spend batch error classifier treats this as record-specific, so valid
neighbors can commit while the conflicting outbox record follows its existing bounded
retry/blocked lifecycle. Infrastructure failures are not classified as bad records.

Background recovery alone uses `deltallm_recover_operation_isolated`. Its PostgreSQL
exception subtransaction rolls back all failed reconciliation effects before persisting
`recovery_blocked_at` and one fixed `recovery_error_code`: `receipt_conflict` or
`integrity_failure`. Data/constraint/missing-counter failures can be quarantined;
connection failures, deadlocks, lock/statement timeouts and cancellation propagate.
The operation row remains locked through this decision. Both existing partial recovery
indexes exclude blocked rows; their index count is unchanged. No provider is replayed,
no receipt is discarded, and no unknown hold or global operation capacity is released.
Quarantines count toward the existing 100,000 pending-operation ceiling.

Committed quarantines emit `billing_operation_recovery_blocked` with an allowlisted
`cause`, and `deltallm_spend_ingestion_failures_total` with fixed stages
`operation_recovery_receipt_conflict` / `operation_recovery_integrity_failure`. Neither
logs nor metric labels include operation IDs, keys, tenant identity or receipt contents.
Uncommitted quarantine attempts do not emit successful-quarantine observations.

Operator runbook (protected control plane, not an inference-path repair):

1. Investigate the blocked operation under existing billing/reporting authorization;
   retain its receipt, frozen snapshot, canonical events and exact balances as evidence.
2. Reconcile the inconsistency through the canonical billing owner. Do not release a hold,
   invent zero usage, alter frozen prices/attribution, or call a provider to clear the alert.
3. Only after authoritative evidence is consistent, requeue the exact operation in an
   audited bounded transaction by clearing both quarantine fields with an operation-ID
   and observed-quarantine-state fence. This changes scheduling only. A still-conflicting
   operation is quarantined again; idempotent event identity prevents duplicate charging.

No new admin repair endpoint is introduced in this dormant prerequisite. PR 4 must retain
operator visibility and a compatible recovery owner before activation. Roll back activation,
not the financial journal or quarantine evidence. The quarantine columns/function/predicates
are included in the original **unshared and unapplied** local PR 3 migration; once shared,
any further database change must use a new migration. Fresh/last-release/shared-feature
verification now checks the paired fields and both index predicates as well as the helper.

### Review-fix verification — 2026-09-08

The two review fixes are implemented; real-database qualification is still an open gate.
No selector activation, external issue mutation, commit, push or PR creation was performed.

Final affected billing/selector/admin/reporting run after Prisma regeneration:

```bash
.venv/bin/python -u -m pytest \
  tests/test_billing_operation_repository.py \
  tests/test_operation_recovery.py \
  tests/test_billing_receipt_errors.py \
  tests/test_operation_reservation.py \
  tests/test_spend_ingestion.py \
  tests/test_billing.py \
  tests/test_billing_cost.py \
  tests/test_billing_tier_pricing.py \
  tests/test_selector_charge.py \
  tests/test_selector_observability.py \
  tests/test_routing_costs.py \
  tests/test_migration_verifier.py \
  tests/test_selector_prerequisite_structure.py \
  tests/router/selection \
  tests/test_billing_operations_postgres.py \
  tests/test_billing_operation_locking_postgres.py \
  tests/test_billing_operation_quarantine_postgres.py \
  tests/test_selector_charge_db_integration.py \
  tests/test_telemetry_ingestion_admin.py \
  tests/test_telemetry_ingestion_db_integration.py \
  tests/test_spend_visibility.py \
  tests/test_spend_reporting_cache.py \
  tests/test_spend_reporting_readiness.py \
  -q --tb=short --disable-warnings -rs -o faulthandler_timeout=45
```

Result: **453 passed, 55 skipped, 2,399 warnings in 5.51 seconds**. All 55 skipped
cases require PostgreSQL: 19 earlier selector/billing cases, 16 new locking/quarantine
cases, and 20 existing telemetry-ingestion cases. `DATABASE_URL` and
`MIGRATION_TEST_ADMIN_DATABASE_URL` were unset. No database connection or migration
was attempted through an alternative mechanism after the previously recorded denial.
These skips do not prove SQL constraints, contention, crash recovery or query plans.

Additional completed checks:

- `.venv/bin/ruff check` and `.venv/bin/ruff format --check` over all 69 touched Python
  paths passed. The focused pre-integration run had 46 passing tests; the broader
  intermediate run had 407 passing tests and 31 PostgreSQL skips.
- With this worktree's `.venv/bin` prepended to `PATH`, `.venv/bin/prisma generate
  --schema=./prisma/schema.prisma` passed (Prisma Client Python 0.15.0, generator 815 ms).
  The existing Python 3.14/Pydantic-v1 compatibility warning remains; it was not suppressed.
- `git diff --check` passed. The temporary review-fix plan was removed after implementation;
  ownership, lock order, quarantine semantics and the operator runbook remain above.
- An earlier test invocation accidentally named the nonexistent
  `tests/test_spend_ingestion_service.py` and exited 4 before running tests. The corrected
  final command above includes the actual spend-ingestion suite and passed.

Before merge/activation, run the checked-in real PostgreSQL tests and the existing
fresh/last-release/shared-feature migration verifier in an authorized disposable database.
Qualify the four-statement admission bound, both lock-contention schedules, quarantine
rollback/isolation, partial-index plans and recovery throughput there. No fresh real-DB
latency or contention certificate is claimed by this fix; the earlier performance gates
remain open. Redis/provider/API/UI protocols and the selector-free execution path were
not changed by these review fixes.

### Cache and protected reporting

Cache v4 uses the immutable effective response fingerprint, including transitive fallback
dependencies, rather than a reload counter. Equivalent snapshots/rollback reuse the same
identity. The cache owner emits typed hit/miss/bypass evidence after the existing policy
preflight. A hit or unresolved outcome cannot enter admitted selector execution.

Fixed-enum metrics cover decision/rank, defaults, provider/parse/capacity failures,
cancellation, deadline, invariant/accounting failures and bounded cleanup failure.
Terminal-lane/escalation/streaming contribution hooks exist but do not emit production
answer observations until PR 4 supplies those lifecycle boundaries. Protected decision
metadata is allowlisted; no classifier output, prompt, credentials or topology headers
are added. Public OpenAI usage stays answer-only.

Cost-report prerequisites use canonical `SpendVisibility`, a time range of at most 31
days and a cursor page of at most 1,000 operations before unique selector/answer event
joins. They expose exact provider/customer component costs, answer-model distribution,
pending reconciliation and explicit coverage/truncation. Savings are a labeled
**counterfactual estimate**, using frozen reference-model prices applied to reported
answer token counts with an uncached-input basis, minus actual answer/selector provider
cost and a known measurable switch penalty. Missing evidence stays unknown; negative
savings are not clamped. Arbitrary answer metadata cannot set the baseline. This is
internal admin aggregation support, not the PR 5 analytics endpoint or UI.

Existing spend summary/timeseries/grouped reports include component spend/tokens but
exclude selector child events from external request/success/failure counts. Their report
cache schema is bumped; no public response shape changed. Reporting remains off the
inference path and must use the existing bounded reporting owner when wired in PR 5.

### Completion verification and remaining sign-off gates

Local checks:

- Broad affected routing/chat/Responses/cache/billing/providers/reporting and activation
  regression run: **1,099 passed**, 6,287 warnings, 72.60 seconds.
- Final focused selector/economics/recovery/spend/migration-verifier and structural run:
  **247 passed**, 1,139 warnings, 4.70 seconds. This includes the final regression proving
  a reservation timeout cannot default into answer execution without confirmed admission.
- Full collection before the final added migration-verifier test: **4,345 tests collected**.
  The feature branch predates main's dependency-lane classifier. New real-service tests
  carry explicit postgres/redis markers; this PR does not copy the newer classifier or
  rewrite unrelated main-branch CI. Existing full-suite CI is retained and now also runs
  for `feature/issue-304-model-router`, with both Redis URL variables supplied.
- Focused runs include unchanged activation rejection, cache hit/unresolved refusal,
  receipt cancellation, malformed output with billable usage, unknown/partial cost,
  strict shared-capacity bounds and outbox progress after recovery failure.
- Prisma generation succeeded with the worktree virtualenv on PATH. OpenAPI check remains
  current: 215 paths, 280 operations. Final Ruff check and format check passed for all
  63 touched Python files; `git diff --check` passed. The final Prisma client generation
  succeeded with v0.15.0 after schema changes; unrelated formatter churn was removed
  after verifying identical schema tokens and attributes.
  The environment is Python 3.14.3 and warns about Prisma's Pydantic-v1 compatibility;
  generated-client import/GC time is not included in pytest's reported test duration.

The final focused command, run from the PR 3 worktree using the existing virtualenv:

```bash
.venv/bin/python -u -m pytest \
  tests/test_operation_recovery.py tests/test_operation_reservation.py \
  tests/test_routing_costs.py tests/test_selector_prerequisite_structure.py \
  tests/test_spend_ingestion.py tests/test_migration_verifier.py \
  tests/router/selection -q --tb=short --disable-warnings -o faulthandler_timeout=45
.venv/bin/python -u scripts/docs/export_openapi.py --check
```

The broader 1,099-test command additionally covered `tests/test_selector_observability.py`,
`tests/test_provider_token_receipt.py`, `tests/test_cache_execution_eligibility.py`,
`tests/test_selector_charge.py`, `tests/test_billing.py`, `tests/test_billing_tier_pricing.py`,
`tests/test_spend_visibility.py`, `tests/test_spend_reporting_cache.py`, `tests/test_routing.py`,
`tests/test_context_routing.py`, `tests/test_routing_runtime_generation.py`,
`tests/test_routing_cache_identity.py`, `tests/test_routing_cache_invalidation.py`,
`tests/test_routing_identity_dependencies.py`, `tests/test_cache.py`, `tests/test_chat.py`,
`tests/test_text_endpoints.py`, `tests/test_embeddings.py`, `tests/test_embeddings_batch.py`,
`tests/test_route_policy_selector_contract.py`, `tests/test_route_policy_validation.py`,
`tests/test_metrics.py`, `tests/test_chat_hop_compatibility.py`, `tests/test_provider_compat.py`,
`tests/test_provider_resolution.py`, `tests/bootstrap/test_routing_bootstrap.py`,
`tests/config/test_dynamic.py`, `tests/test_telemetry_ingestion_admin.py`,
`tests/test_ui_models.py`, `tests/test_ui_legacy_models.py`,
`tests/test_route_decision_multimodal.py`, `tests/test_elevenlabs_tts.py`,
`tests/test_uniformity_hardening.py` and `tests/test_data_plane_audit_extended.py`, with the
same pytest flags. It preceded the last recovery/migration-verifier test additions, which
are covered by the final focused run rather than counted twice as new coverage.

**Not verified / not a merge-readiness certificate:**

- Real-service collection contains 25 new/affected cases (19 PostgreSQL, 6 Redis).
  With dependency URLs unset these all skip; that is not correctness evidence. Against
  the existing disposable loopback services, Prisma migrate deploy failed P1001 and Redis
  failed with `PermissionError: Operation not permitted`. No migration was applied.
  The disposable containers were stopped again; no data was deleted. Do not use production
  services or route around the environment's connection restriction.
- Run fresh/last-release/shared-feature migration verification and all affected real
  PostgreSQL/Redis suites in an authorized environment. Added tests cover five-scope
  concurrent reservations, rollback/cancellation, frozen-owner/receipt rejection,
  exactly-once settlement, unknown failure retention, late receipts, physical deletion
  guards, and a representative recovery-index plan; these database test bodies have
  **not run here**. Redis tests include multi-client admission, owner replay, actual lease
  expiry and client reconnect; its transport-outage case injects the connection failure
  against retained real Redis state, not a full Redis server restart.
- Qualify actual SQL lock/statement/transaction bounds, deadlocks/contention, recovery
  drain capacity, storage/index amplification, reporting visibility plans and maximum
  replica arithmetic. No fake-based test substitutes for these gates.

Constant-arrival artifacts: `/private/tmp/deltallm-pr3-completion.gfnlbh/`.
The checked-in harnesses reproduce 10 RPS, 20-second hit/miss profiles with a fixed provider
mock and fake Redis; the selector harness uses fake billing and a fixed 1 ms provider.
Both routing cases received 200/200 requests with no generator drops and zero sampled
queue slope. Dependency counts are unchanged: cache hit has zero provider calls; miss
has one provider POST, one cache get/set, and the same Redis operation counts as PR 2.

| Initial local profile | Before p50/p95/p99 ms | After p50/p95/p99 ms |
| --- | --- | --- |
| Cache miss | 7.842 / 11.613 / 15.916 | 9.601 / 13.483 / 20.659 |
| Cache hit | 3.495 / 4.795 / 6.038 | 4.993 / 8.760 / 13.616 |

These short sequential measurements show a latency increase, not latency parity. Host
noise is a possible contributor, not a demonstrated explanation. They establish unchanged
dependency counts but do not certify the release SLO. The isolated selector profile had
200/200 successes, exactly one reserve/dispatch/accept/provider call per operation, zero
sampled queue slope and p50/p95/p99 of 4.884 / 7.179 / 8.653 ms. This is selector contribution,
not a streaming TTFT or real-database benchmark. Production latency and capacity gates
remain open independently of code completion.

A repeat pair is retained under `repeat/`, not substituted for the initial samples:
miss before p50/p95/p99 was 9.514 / 14.517 / 17.610 ms and after was
3.500 / 5.163 / 7.077 ms; hit before was 4.246 / 8.532 / 11.354 ms and after was
2.064 / 2.563 / 2.936 ms. Both again completed 200/200 per case with identical dependency
counts, no drops, maximum observed in-flight of one and zero sampled queue slope. The
opposite latency movement between short pairs demonstrates that this local setup is too
variable to attribute a latency change confidently; it does not replace production
qualification. Raw runs and every comparison are preserved.

The temporary ignored kickoff plan has been deleted as requested. This permanent record
retains implementation decisions and the outstanding verification gates; no commit,
push, merge, production activation or remote issue mutation was performed in this turn.

## PR 4 — candidate-planning integration (historical first checkpoint)

The first implementation slice adds immutable lane/rank information to
`RouteCandidatePlan`. Publication, file-load and runtime activation guards remain closed;
this slice alone does not make any production request invoke a selector. The remaining
real-time execution, capability qualification, billing, streaming and Batch rejection
work must land before activation. No shadow mode is introduced.

### Ownership and compatibility

`src/router/group_policy.py` now owns the existing strategy enum, group policy and runtime
policy projection; `src/router/router.py` preserves their import facade. The group policy
is frozen. `SelectorLaneRouting` holds frozen canonical selector/member contracts, using
the existing assignment validator rather than a new configuration source. Historical
pre-selector semantics ignore opaque selector fields as before. Selector-free members
are not subjected to stricter selector validation.

The router owns hard eligibility and one batched state read. The focused
`src/router/selection/planning.py` owner applies the existing strategy and context ordering
independently within each permitted lane. Health, cooldown, tags, workload compatibility,
context capacity and configured pre-call checks run before the decision is applied.
Missing/unhealthy/full lanes can escalate upward, never below the chosen minimum rank.
A later selector-enabled group reuses the rank even when its lane names differ; a group
with no sufficient rank has no executable candidates. Selector-free groups retain their
existing equivalence contract and ordering.

The existing `RequestSelectorState` remains the sole operation decision owner. The new
internal request-context bridge cannot replace an attached owner, is separate from caller
metadata, and does not start or join provider work. Before selection, a plan exposes hard
eligibility by lane but has **no executable deployments**. Once the decision exists,
pre-decision selector plans are invalidated; ordinary cached plans remain reusable.
Explicit/context-demand invalidation discards candidate ordering without discarding the
decision. Cancellation, failure and deadline expiry cannot return a previously cached
executable selector plan. This is request-local state, not a new Redis or process cache.

The previously oversized planning method is split at the eligibility/ordering boundary.
Structure tests ratchet both methods below 80 lines and the router module below 800, and
prohibit selector execution in planning. No clients, workers, queues, SQL, Redis commands,
schema changes, public fields, headers or UI settings are added in this slice. The existing
provider-execution activation guard is retained, including its structural regression.

### Verification scope

Tests cover all supported lane counts (2–8) and every rank, per-lane ordering for all nine
existing strategy values, safe-default ranking, hard-filter precedence, no downgrade when
the selected lane is unavailable, differently named fallback lanes, decision retention
through re-planning, immutable projections, caller-metadata isolation, and terminal-state
rejection. The cache fingerprint fixture now has a valid nonempty two-lane membership;
its member-change, timeout-change, historical-semantics and equivalent-snapshot assertions
are preserved.

Dependency regression tests assert one health/cooldown batch per fresh plan; active,
usage and latency state is loaded once only when required by the strategy/pre-call policy.
All lanes share that snapshot, with no additional SQL/Redis/provider round trips. Cached
plans perform no additional state reads. Selector-free ordering is compared with the
previous strategy invocation under the same random state, not merely an unordered set.

Full PR 4 acceptance remains open: these tests do not yet prove HTTP selector execution,
lazy fallback execution, MCP continuation wiring, stream disconnect cancellation, actual
answer-attempt accounting, capability/data-placement qualification or production activation.

### Local verification — 2026-09-09

Base: local feature integration `37929ea740704653730df1740bb0f236402bd51e`.
Worktree: `.worktrees/issue-304-pr4-realtime-routing`. The Python 3.11 environment was
installed from the frozen lock with dev/docs extras and its Prisma client generated.

- `uv run --frozen --no-sync pytest -q -m hermetic --tb=short --durations=5`:
  **3,179 passed** (final run, including all 89 additional cases).
- `.venv/bin/pytest -q -m app --tb=short --durations=10`:
  **1,156 passed**, 22 existing deprecation warnings, 328.39 seconds.
- `.venv/bin/pytest -q -m postgres --tb=short --durations=10`, with `DATABASE_URL`
  targeting the feature's isolated migrated PostgreSQL 15 database:
  **235 passed**, 4 deprecation warnings, 69.82 seconds.
- `.venv/bin/pytest -q -m redis --tb=short --durations=10`, with both Redis test
  variables pointing to the isolated Redis 7 instance: **46 passed**, 2 deprecation
  warnings, 8.38 seconds. Both owned containers were stopped afterward; no data deleted.
- Full `--collect-only -qq --dependency-lane-report`: **4,675 tests**, partitioned into
  3,179 hermetic / 1,156 app / 235 postgres / 46 redis / 59 Helm. No classifier, fixture
  lane or CI selection changes. The unaffected Helm lane and UI gates were not rerun.
- `.venv/bin/ruff check src/router tests/router/selection tests/test_routing_cache_identity.py`
  passed; `.venv/bin/ruff format --check` on all 14 changed Python files passed.
- `.venv/bin/python scripts/docs/export_openapi.py --check`: current, 227 paths /
  295 operations. `git diff --check` passed. No migration or generated-artifact changes.

The new failover test also exercises the real `FailoverManager` with zero, one and two
retries, followed by upward escalation, while invalidating plans between attempts.
Its mock explicitly requests retry-or-next behavior without health damage; local
fail-fast errors must not be treated as provider-retry failures. A zero configured
backoff cap makes this a deterministic ordering/ownership test, not a wall-clock test.
No production retry behavior or deadline was relaxed.

The unchanged `tests/performance/routing_cache_profile.py` harness ran sequentially
against the clean base worktree and this implementation:
`.venv/bin/python -m tests.performance.routing_cache_profile --label before|after
--output-dir /private/tmp/deltallm-pr4-planning.5jEuge`.
The raw JSONL samples and summaries are retained in that directory. Each profile
offered 10 RPS for 20 seconds, received **200/200 successes**, dropped no requests,
and recorded zero sampled in-flight slope. This is ASGI with fake Redis and a fixed
local provider mock, not a database, streaming TTFT or release-capacity certificate.

| Profile | Before p50 / p95 / p99 (ms) | After p50 / p95 / p99 (ms) |
| --- | --- | --- |
| Cache miss | 6.819 / 9.263 / 11.279 | 5.468 / 10.725 / 17.549 |
| Cache hit | 4.390 / 6.666 / 12.173 | 1.716 / 3.406 / 3.960 |

Redis/cache/provider call counts are identical before and after: misses make one
provider POST and one response-cache get/set; hits make zero provider calls and one
cache get. Existing Redis method counts match the harness's exact assertions. The
after-miss run had a 225.506 ms maximum, 121.565 ms maximum scheduling lag, and maximum
in-flight of three (one in the other profiles). The miss-tail increase is recorded,
not dismissed as proved host noise or presented as latency parity. Longer controlled
performance qualification remains required before PR 4 activation.

### PR 4 execution composition and soft admission (2026-09-09)

The implementation extends the existing billing-operation journal with a typed
`SoftSelectorOperation`. This is not a weakened `OperationReservation` or an
invented provider-enforced quote. It acquires no spending holds and does not own
answer accounting. The journal's answer component is `unattempted` because that
component is not dispatched through this journal; the existing answer spend owner
continues to record the answer. This does not describe the answer as free.

Before the selector dispatches, one bounded transactional ownership/budget snapshot
checks the verified key, user, team, organization and team-model scope with a
conservative configured-context allowance. Concurrent admissions may overshoot.
When a team-model budget is configured, its maintained spend counter must exist;
missing counter data returns accounting unavailable before provider dispatch. No
append-only spend-history scan or rollup repair runs in this new admission path.
Actual reported selector usage is accepted even above the estimate. A stable child
event and frozen provider-price snapshot carry its exact pass-through customer
charge; unknown usage stays pending. There is no provider replay for reconciliation.
The existing spend worker recovers journal receipts into its existing outbox and
settles them in the same ledger transaction. Ordinary answer events do not gain
operation-journal settlement queries, and selector-free requests acquire no holds.
No shared migration, new client/pool, new queue or new worker is introduced.

The authenticated Chat/Responses edge binds a single operation only when the pinned
fallback topology can reach a selector. It supplies the final normalized request,
whole-response-cache eligibility, verified attribution and original execution
deadline. Initial hard-rejected groups do not justify a paid classification.
Failover owns attempts and retries, but defers selector groups until execution
reaches them. All subsequent groups and MCP model phases reuse the decision's
minimum rank; payload/context replanning does not reset the decision or deadline.
The edge owns and joins both the selector task and its blocking disconnect monitor;
neither is detached. A real ASGI disconnect cancels the provider and unwinds the
middleware with a local `499 client_disconnected` error before SSE headers. Receipt
and permit cleanup remain owned by the existing selector prerequisites.

Activation requires explicit `model_info.chat_capabilities` for every enabled
member, known positive context metadata and `context.unknown_capacity: exclude`.
The common floor is text chat. Operators must explicitly qualify tools, JSON
object/schema output, image/audio/file input, multiple choices and streaming; undeclared optional
features are not inferred from provider/model names. The classifier also requires
explicit input/output provider prices and positive canonical RPM/TPM limits.
Its concurrency ceiling is conservatively its RPM ceiling, on the same shared
deployment lease owner. Unsupported billing dimensions reject qualification.
Classifier tags use the same static tag filter as answer candidates.

Publication readiness and metadata qualification run within the locked policy
transaction. File/database runtime generations independently compile qualified,
immutable selector projections before publication. Disposable route-group cache
envelopes/keyspaces advance to v3 with `validated-v3` provenance; older inactive
envelopes are misses. Simulation remains explicit unsupported until PR 5's offline
evaluation work. Batch rejects both direct selectors and selector-bearing fallback
topologies with `batch_model_router_selector_unsupported` until PR 6.

The completion record below covers the expanded execution scope; the earlier
candidate-only counts are retained as historical evidence, not added to final totals.

## PR 4 completion record — 2026-09-09

The complete real-time implementation is local on `issue-304-pr4-realtime-routing`,
based on feature integration `37929ea740704653730df1740bb0f236402bd51e`. This record
supersedes the incomplete PR 4 checkpoint above and the old dormant-only activation
restrictions. No commit, push, PR, remote issue edit or merge to `main` is part of
this implementation turn. Production defaults do not configure a selector.

### Issue #304 delivery checklist

- [x] Immutable lane-aware `RouteCandidatePlan`; no classifier execution in planning.
- [x] Authoritative membership, workload, capability, tags, health, capacity and
  context checks precede decision application; authorization precedes paid selection.
- [x] Selected rank followed by upward-only escalation, including differently named
  lanes in later groups; no lower-rank candidate after a quality decision.
- [x] All existing strategies and context ordering apply within each eligible lane.
- [x] Existing retries, classified/ordinary/local-context fallbacks, cycle/attempt
  limits, one decision, MCP continuation and pinned-generation semantics are retained.
- [x] Chat Completions and Responses work in streaming and non-streaming modes.
- [x] Classification and durable known receipt precede stream headers/body; actual
  ASGI disconnect cancels and joins the classifier, without opening an answer stream.
- [x] Policy qualification, soft budget admission, exact durable billing, shared
  provider capacity, cache eligibility, bounded telemetry and execution are composed.
- [x] Atomic publish activates a qualified selector; removal and versioned rollback
  disable/revalidate it. Invalid metadata changes cannot archive the working policy.
- [x] Batch explicitly rejects direct and fallback-reachable selectors with
  `batch_model_router_selector_unsupported`, pending PR 6.
- [x] Selector-free groups and response-cache hits perform no classifier work;
  ordinary requests retain their dependency counts and answer usage contract.

The issue's legacy long deployment-ID P2 is also fixed: response DTOs preserve valid
selector-free identifiers over 256 characters, while selector-enabled writes retain
their strict bound. The HTTP regression covers validation and draft persistence.
Guided editing, explicit evaluation and analytics UI remain PR 5; per-item Batch
classification remains PR 6. Neither is an unfinished PR 4 item. There is no shadow mode.

Main regression owners are `tests/router/selection/test_{planning,lane_bounds,
planned_failover,planning_dependencies,realtime,realtime_fallbacks,realtime_lifecycle,
realtime_admission,disconnect,execution_contracts}.py`,
`tests/db/test_selector_activation.py`, `tests/bootstrap/test_selector.py`,
`tests/test_soft_selector_operations_postgres.py`, and
`tests/test_ui_route_policy_legacy_identifiers.py`. Tests include accounting outage,
capacity/tag/context denial, unsupported features outside the bounded prompt projection,
one selector across MCP tool phases, policy reload during classification, both provider-
classified fallback kinds, local context fallback without primary dispatch, stale L2
cache provenance, and one terminal metric even when a deployment belongs to several groups.

### Ownership, resource and dependency budgets

The user-approved soft budget decision supersedes strict cross-traffic reservation
qualification. It does not waive durable charges, attribution or fail-closed accounting.
The new admission query joins five unique scope/counter identities, never scans spend
history, and returns at most one row. A configured team-model budget with a missing
counter is unavailable, not zero. The real query-plan regression seeds 10,001 counters,
uses `deltallm_teammodelspend_pkey` for exactly one row/loop, and has no event-table scan.
Its focused sample measured 0.089 ms execution / 0.494 ms planning; the other identity
tables in that fixture are small, so this is not an all-tenant production-scale profile.

One reported selector adds four application statements for fresh admission, two for
dispatch intent and two for receipt acceptance, including transaction timeout setup
but excluding begin/commit protocol. These three transactions borrow the existing
telemetry database pool. Each has a 250 ms ceiling subordinate to the execution deadline;
receipt cleanup has a shared 250 ms grace and permit cleanup 50 ms with owner-token TTL
recovery. One request owns at most two joined selector/disconnect tasks, no detached
work, and at most one classifier provider call. Billing denial returns unavailable;
provider/format/context/capacity failure uses the safe lane without answer cooldown.

Selector provider acquisition/release adds two canonical Redis EVALs. Eligible-lane
planning shares the existing batched health/capacity snapshot across every lane. After a
decision, replanning reads the canonical snapshot again rather than sharing mutable
state across operations. The integrated mock profile measures the resulting full-path
delta: per request, `eval +2`, `hgetall +2`, `mget +1`, `zadd +1`, with all other fake
Redis method counts unchanged. These include fake implementation internals, not wire
command counts. Baseline operations make zero selector billing calls; selector operations
make exactly one reserve/dispatch/accept. Ordinary answer outbox batches do not acquire
new operation-journal settlement queries. Existing spend recovery remains off requests.

There are no new pools, clients, workers, queues, indexes or migrations. Selector work
borrows the existing provider/telemetry/Redis owners; increased request work does not
increase their configured connection ceilings. With the current production chart's
12 API replicas, one process per pod and one rolling-surge pod, the existing database
allocation is `(12 + 1) * (20 + 5) = 325` connections, including 65 telemetry connections;
the existing upstream HTTP ceiling is `13 * 500 = 6,500`. Enabling two split workers
plus their one surge adds 75 database / 1,500 upstream connections, not a second selector
allocation. Migration/admin headroom and external provider/server limits still need an
operator-approved deployment budget; these sums are ceilings, not throughput claims.
The existing shared Redis client remains unchanged; this PR does not certify or redesign
its deployment-wide pool policy. Provider RPM/TPM and lease state are deployment-scoped
in shared Redis, not multiplied per API replica. Selector admission uses one RPM and
`classifier context allowance + 64` TPM units; the concurrency ceiling is its configured
RPM limit. Account for classifier and answer traffic on the same physical deployment.

The existing 100,000-pending-operation journal bound, canonical retention policy,
idempotent recovery, storage/index amplification and rollout runbook above remain in
force. Soft operations hold zero spending allowance and leave answer accounting with
the existing answer owner; they do not mislabel unknown selector usage as free. No
production-scale storage or 50-RPS release certificate is claimed by local completion.

### Final correctness and compatibility verification

Python 3.11.13 uses the frozen lock and generated Prisma 0.15 client. Full collection
is **4,774 tests**, assigned exclusively to 3,212 hermetic / 1,195 app / 262 postgres /
46 redis / 59 Helm. All five lanes pass with no skipped tests. JUnit and collection
artifacts are retained at `/private/tmp/deltallm-pr4-final.18iuAt/` on the implementation
host; this temporary path is evidence, not a durable repository artifact store.

```sh
.venv/bin/pytest --collect-only -qq --dependency-lane-report
.venv/bin/pytest -q -m hermetic
.venv/bin/pytest -q -m app
DATABASE_URL="$PR4_TEST_DATABASE_URL" .venv/bin/pytest -q -m postgres
REDIS_URL="$PR4_TEST_REDIS_URL" DELTALLM_TEST_REDIS_URL="$PR4_TEST_REDIS_URL" .venv/bin/pytest -q -m redis
.venv/bin/pytest -q -m helm
git ls-files -m -o --exclude-standard -z -- '*.py' | xargs -0 .venv/bin/ruff check
git ls-files -m -o --exclude-standard -z -- '*.py' | xargs -0 .venv/bin/ruff format --check
.venv/bin/python scripts/docs/export_openapi.py --check
.venv/bin/mkdocs build --strict --site-dir /tmp/deltallm-pr4-docs-site
git diff --check
```

Use isolated migrated PostgreSQL 15 and Redis 7, not production URLs. Hermetic tests
include an existing loopback webhook test: sandbox-only execution skipped it; the final
socket-enabled run passed all 3,212 in 16.36 seconds. The app lane passed in 482.83 seconds
with 22 existing deprecation warnings; PostgreSQL passed all 262 in 92.72 seconds with
four existing deprecation warnings. Schema and all shared migrations are unchanged;
the feature/main alignment already qualified fresh, last-release and shared-feature
migration paths. PR 4 runs against that same migrated schema. No CI/lane selection
or test-discovery rules were weakened or changed.

UI verification uses actual Node 22.23.2: **206 unit tests pass**, type-check/production
build passes, touched-file ESLint passes. Full lint remains the clean feature base's
**118 errors / 4 warnings**, with identical normalized findings and no new errors.
The complete generated `ui/dist` is byte-for-byte identical to the clean base: no
initial-bundle or route-chunk growth. OpenAPI is synchronized at 227 paths / 295
operations. No provider credentials or live third-party calls were used.

### Constant-arrival and TTFT evidence

Raw JSONL and summaries are in `/private/tmp/deltallm-pr4-final.18iuAt/{cache,
cache-repeat,realtime}`. Every case offers 10 RPS for 20 seconds, completes 200/200
successfully with zero generator drops and zero sampled in-flight slope. The existing
cache harness runs unchanged in the clean feature-base and PR 4 checkouts. The repeat
reverses execution order (after, then before) and preserves the initial results.

| Cache case | Before p50 / p95 / p99 ms | After p50 / p95 / p99 ms |
| --- | --- | --- |
| Initial miss | 10.398 / 18.016 / 21.547 | 10.530 / 16.514 / 27.063 |
| Initial hit | 5.775 / 9.940 / 16.645 | 5.877 / 7.702 / 8.943 |
| Repeat miss | 10.675 / 17.793 / 22.339 | 12.017 / 19.863 / 24.021 |
| Repeat hit | 6.225 / 9.133 / 10.523 | 5.265 / 7.658 / 11.136 |

All cache/Redis/provider count assertions match the base exactly: a miss has one
cache get/set and one provider post; a hit has one get and no provider post. Miss tail
latency is higher, not demonstrated parity: initial/repeat PR 4 maxima were 329.166 /
312.172 ms with 223.542 / 205.932 ms maximum scheduling lag and four observed in-flight
requests (one in baseline cache cases). These short in-process samples do not attribute
the stalls to a specific cause or certify a deployment SLO.

The new `tests.performance.realtime_selector_profile` compares the complete admitted
Chat path with/without selection, using fixed 1 ms provider hops, fake billing and
fake Redis. TTFT is measured at the first ASGI body, not a buffered client's read.

| Integrated path | p50 / p95 / p99 ms | Stream TTFT p50 / p95 / p99 ms |
| --- | --- | --- |
| Baseline non-streaming | 12.587 / 17.900 / 46.752 | — |
| Selector non-streaming | 19.155 / 27.616 / 32.606 | — |
| Baseline streaming | 14.352 / 20.697 / 24.267 | 10.752 / 16.662 / 19.579 |
| Selector streaming | 20.898 / 27.217 / 33.198 | 17.317 / 24.469 / 30.761 |

Median selection overhead was 6.568 ms non-streaming and 6.565 ms for streaming TTFT,
including the mock selector provider time. Each selector case made 200 selector and
200 answer calls, and 200 reserve/dispatch/accept operations; both baseline cases made
200 answer calls and zero selector/billing operations. Total selector provider time
was 249.923 ms non-streaming / 297.959 ms streaming; answer provider totals were
252.480 / 248.601 ms. All integrated cases observed maximum in-flight one except the
baseline non-streaming case (four, 347.995 ms maximum latency). SQL/real-Redis wire
latency, real-provider quality, net savings and deployment saturation are not measured
by this harness; real-service correctness is covered separately by the full lanes.

Reproduce with the same locked environment in each checkout:

```sh
.venv/bin/python -m tests.performance.routing_cache_profile --label before --output-dir /tmp/pr4/cache
.venv/bin/python -m tests.performance.routing_cache_profile --label after --output-dir /tmp/pr4/cache
.venv/bin/python -m tests.performance.realtime_selector_profile --output-dir /tmp/pr4/realtime
```

### PR 4 review corrections — 2026-09-09

Four reproduced review findings are corrected locally without changing the soft
budget decision, public answer-only usage, settings, schema, UI or deployment
contracts:

- Streaming MCP requests fail with the existing 400 message after canonical
  transformed-payload preflight and before binding/admitting a selector. Both Chat
  and Responses reject tools supplied directly or introduced by a hook with zero
  selector reserve, dispatch, receipt or provider calls. Failure logging uses the
  existing preflight error owner, before any deployment is chosen.
- Initial local-context fallback planning shares the original request deadline.
  Only the existing batched planning await is deadline-bounded: deferred selector
  work retains its own bounded receipt/permit cleanup, without an enclosing timeout
  that could cancel cleanup a second time. Event-barrier tests on Chat and Responses
  prove cancellation/join and 408 while planning is stalled, before any paid call.
- A typed prepared-write result keeps the canonical validation projection separate
  from the stored document. Direct publication qualifies the projection and persists
  the preserved document, matching draft publication and rollback. PostgreSQL tests
  prove nested opaque member metadata survives direct publish, draft, removal and
  rollback; new client-authored unknown fields still fail without a revision change.
- Activation uses the existing canonical member merge to qualify effective enabled
  members. Group-disabled and policy-disabled members need not have activation
  metadata; disabled inventory can lack a lane or concrete model. The classifier
  must remain enabled and both active lanes populated. PostgreSQL tests cover
  publication, draft, rollback and active-group edits, plus atomic rejection of
  re-enabling an unqualified member without changing policy history or revision.

No SQL, Redis, provider call, retry, task owner or pool is added. Ordinary routing
still performs no selector work; the existing local-fallback batch planner now has
the same deadline bound as other selector-reachable planning. Publication remains
inside the existing group-locked transaction and runtime revision lifecycle. No
migration is needed; the earlier fresh/upgrade and UI/Helm evidence still applies
to these unchanged surfaces.

#### Review-fix verification

The four HTTP MCP variants, two blocked-planner variants and three failing
PostgreSQL publication variants reproduced the findings before the fixes. Fourteen
new regressions are collected automatically: three hermetic, six app and five
PostgreSQL. Focused routing/publication/HTTP tests passed **399**; the selector
activation PostgreSQL module passed **15**. All five full lanes then passed:

| Lane | Passed | Runtime |
| --- | ---: | ---: |
| Hermetic | 3,215 | 16.79 s |
| App | 1,201 | 644.98 s |
| PostgreSQL | 267 | 101.62 s |
| Redis | 46 | 9.49 s |
| Helm | 59 | 7.61 s |
| Total, mutually exclusive | 4,788 | — |

No failures or skips; app/PostgreSQL/Redis retain 22/4/2 existing deprecation
warnings. Ruff lint and format checks pass for all **82** changed/new Python files.
OpenAPI remains current at 227 paths / 295 operations; strict MkDocs and
`git diff --check` pass. No test-classifier or shared-fixture contract was weakened.
The isolated PostgreSQL/Redis containers were stopped afterward, retaining data.
UI sources/build inputs and schema/migrations did not change in this follow-up;
their earlier verification is not represented as a new run.

Raw collection, JUnit and logs: `/private/tmp/deltallm-pr4-review-fixes.O7zXhG`.
Reproduction commands use the existing frozen-lock `.venv`; set the two task-local
test URLs to isolated services (Docker may assign new ports after restart):

```sh
.venv/bin/pytest -q tests/router/selection tests/services/test_route_policy_publication.py tests/db/test_route_policy_repository.py tests/test_ui_route_groups.py
DATABASE_URL="$PR4_TEST_DATABASE_URL" .venv/bin/pytest -q tests/db/test_selector_activation.py
.venv/bin/pytest --collect-only -qq --dependency-lane-report
.venv/bin/pytest -q -m hermetic --durations=10
.venv/bin/pytest -q -m app --durations=10
DATABASE_URL="$PR4_TEST_DATABASE_URL" .venv/bin/pytest -q -m postgres --durations=10
REDIS_URL="$PR4_TEST_REDIS_URL" DELTALLM_TEST_REDIS_URL="$PR4_TEST_REDIS_URL" .venv/bin/pytest -q -m redis --durations=10
.venv/bin/pytest -q -m helm --durations=10
git ls-files -m -o --exclude-standard -z -- '*.py' | xargs -0 .venv/bin/ruff check
git ls-files -m -o --exclude-standard -z -- '*.py' | xargs -0 .venv/bin/ruff format --check
.venv/bin/python scripts/docs/export_openapi.py --check
.venv/bin/mkdocs build --strict --site-dir /tmp/pr4-review-docs
git diff --check
```

The unchanged cache harness was repeated sequentially on the clean feature base
and final PR 4 worktree, with no test suites running (`cache-isolated/`). All four
cases completed 200/200 at 10 RPS for 20 seconds, zero generator drops and zero
sampled in-flight slope. Redis/cache/provider call dictionaries match exactly:
misses have one cache get/set and one provider call; hits have one get and no
provider call. The normal path adds no selector economic work.

| Isolated cache case | Before p50 / p95 / p99 ms | After p50 / p95 / p99 ms |
| --- | --- | --- |
| Miss | 6.199 / 12.124 / 15.479 | 6.241 / 13.045 / 34.259 |
| Hit | 3.115 / 6.021 / 8.165 | 2.585 / 6.064 / 10.571 |

Tail latency is not proven equivalent: the isolated after-miss maximum was
242.487 ms with 137.569 ms maximum scheduling lag and three in flight, versus one
in flight in the other cases. An earlier comparison overlapping the app suite is
retained in `cache/`: its after-miss run failed the 200-success assertion with
196 successes/four 429s, 705.018 ms maximum scheduling lag and nine in flight.
Do not silently discard that failed run or attribute its stall to a specific cause.

The integrated real-time profile (`realtime/`) also repeated all four cases:
200/200 successes each, zero generator drops, 200 answer calls each, and exactly
200 reserve/dispatch/receipt operations only in each selector case (zero in both
baselines). Every sampled in-flight slope was non-positive. Stream TTFT p50/p95/p99
was 10.605/14.771/20.937 ms baseline and 17.033/25.325/31.080 ms with selection.
This run overlapped the application suite: its baseline non-streaming p95/p99 was
202.215/257.600 ms, so it is call-count evidence, not a clean latency-overhead
comparison. Both profiles remain local fake-dependency regression checks, not a
production performance or real-model savings certificate. Use the same harness
commands from the preceding section with the review artifact directory.
