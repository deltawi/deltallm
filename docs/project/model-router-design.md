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
client data; they are not copied out of an older opaque document.

All other unknown stored fields continue to round-trip through draft and publication replacement.
That is a deliberate compatibility exception to strict client-owned selector fields.

Runtime Route-Group snapshots carry explicit proof that the selector activation gate ran. The
database repository and validated file configuration are the only authorities that can mint an
`inactive` snapshot before PR 4. Redis is an optimization, not an authority: its bounded 4 MiB
`deltallm:routegroup:v2:runtime:r<revision>` entries use a strict versioned envelope containing the
schema version and selector-gate state. Missing, legacy, malformed, oversized, or incompatible
envelopes are cache misses and reload PostgreSQL. A valid current envelope is still checked for an
active selector and raises the typed activation error rather than falling back to configuration.
The v1 cache namespace is left to expire naturally; PR 4 must bump the envelope and namespace when
the activation state changes.

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
